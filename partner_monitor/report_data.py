"""One report payload for HTML, CSV, workbook and immutable assessment history."""
import json
import sqlite3
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from .assessment import assess, digest, finish, load_reviews, VERSION, RULES
from .overview import FIELDS
from .sources import load_sources
from .web_logging import atomic_text

HEADERS = {
    'Overview': FIELDS,
    'Findings': ['Company', 'Finding', 'Severity', 'Date', 'Source', 'Evidence', 'Rule', 'Status'],
    'Financials': ['Company', 'Year', 'Revenue', 'Profit', 'Equity', 'Assets', 'Employees', 'Ratios'],
    'Sanctions': ['Company', 'Checked entity', 'Entity type', 'Match status', 'List', 'Reason', 'Reviewed'],
    'Changes': ['Company', 'Field', 'Previous value', 'Current value', 'Date'],
    'Data Quality': ['Company', 'UR', 'VID', 'Tax debt', 'Sanctions', 'Financials', 'Web', 'Coverage'],
}


def numeric(value, multiplier=1):
    if value in (None, ''): return None
    try:
        amount = Decimal(str(value)) * multiplier
        return float(amount) if amount.is_finite() else None
    except InvalidOperation:
        return None


def facts(item):
    """Source rows retain natural keys; failures never look like deletions."""
    result = {}
    for table, rows in item.items():
        if not isinstance(rows, list): continue
        for row in rows:
            if 'record_key' in row:
                result[table + ':' + row['record_key']] = {k: v for k, v in row.items()
                    if k not in {'run_id', 'snapshot_id', 'row_hash', 'raw_json', 'source_row', 'record_key'}}
    return result


def persist(db, payload):
    if db is None: return
    location = db.execute('PRAGMA database_list').fetchone()[2]
    # Reports use a read-only source connection. Scoring writes through a separate
    # short transaction without changing official runs, matches or model outputs.
    owns_connection = not db.in_transaction
    writable = sqlite3.connect(location) if owns_connection else db
    try:
        writable.execute('PRAGMA foreign_keys=ON')
        writable.execute('''
            CREATE TABLE IF NOT EXISTS risk_assessments (
              assessment_id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES monitoring_runs,
              version TEXT NOT NULL, created_at TEXT NOT NULL, payload_json TEXT NOT NULL)''')
        writable.execute('''
            CREATE TABLE IF NOT EXISTS risk_events (
              assessment_id TEXT NOT NULL REFERENCES risk_assessments,
              registration_number TEXT NOT NULL, event_id TEXT NOT NULL,
              rule TEXT NOT NULL, penalty INTEGER NOT NULL, evidence_json TEXT NOT NULL,
              PRIMARY KEY(assessment_id,registration_number,event_id))''')
        writable.execute('INSERT OR IGNORE INTO risk_assessments VALUES (?,?,?,?,?)',
            (payload['id'], payload['run_id'], payload['version'], payload['created_at'], json.dumps(payload,ensure_ascii=False)))
        for reg, company in payload['companies'].items():
            for event in company['assessment']['events']:
                writable.execute('INSERT OR IGNORE INTO risk_events VALUES (?,?,?,?,?,?)',
                    (payload['id'],reg,event['id'],event['rule'],event['penalty'],json.dumps(event,ensure_ascii=False)))
        if owns_connection: writable.commit()
    finally:
        if owns_connection: writable.close()


def build_report(db, run_id, companies, load_company, directory):
    directory = Path(directory)
    default = Path(__file__).resolve().parent.parent / 'config' / 'assessment_reviews.json'
    # User reviews are local, ignored data. Defaults contain only accepted public case links.
    reviews = load_reviews(default)
    local = load_reviews(directory / 'assessment-reviews.json')
    for section in ('findings', 'sanctions'):
        reviews.setdefault(section, {}).update(local.get(section, {}))
    items = {c['registration_number']: load_company(db, run_id, c['registration_number']) for c in companies}
    assessments = {reg: assess(item, reviews) for reg, item in items.items()}
    input_id = digest([RULES, run_id, items, reviews])
    history = directory / 'assessments'
    target = history / (input_id + '.json')
    if target.exists():
        payload = json.loads(target.read_text(encoding='utf-8'))
        persist(db, payload)
        return payload, items
    latest = history / 'latest.json'
    previous = json.loads(latest.read_text(encoding='utf-8')) if latest.exists() else None
    if previous and previous['version'] != VERSION: previous = None
    now = datetime.now(timezone.utc).isoformat()
    source_map = {s['table']: s['id'] for s in load_sources() if s['format'] == 'csv'}
    sheets = {name: [] for name in HEADERS}
    state = {}
    for c in companies:
        reg = c['registration_number']; item = items[reg]; name = c.get('name') or reg
        assessment = assessments[reg]
        before = (previous or {}).get('companies', {}).get(reg)
        if before:
            present = {e['id'] for e in assessment['events']}
            present_keys = {key for e in assessment['events'] for key in e['review_keys']}
            retained = []
            # A new search is not proof that an older event ceased to exist.
            excluded = {key for key, review in reviews['findings'].items() if review.get('exclude')}
            for event in before['assessment']['events']:
                if event['id'] not in present and event.get('review_keys') and event['rule'] != 'sanctions' and not present_keys.intersection(event['review_keys']):
                    if not all(key in excluded for key in event['review_keys']):
                        retained.append(dict(event, status='Previously reported; not revalidated in latest job'))
                elif event['id'] not in present and event.get('source_id'):
                    source_status = {r['source']: r['status'] for r in item['quality']}
                    if any(source_status.get(source) not in {'FOUND','NO_RECORDS'} for source in event.get('source_ids',[event['source_id']])):
                        retained.append(dict(event, status='Earlier official fact; current source unavailable'))
            if retained:
                assessment = finish(assessment['events'] + retained,
                    assessment['warnings'] + ['Earlier events retained; latest search does not establish resolution'], assessment['areas'])
        current_facts = facts(item)
        current_checks = {r['source']: r['status'] for r in item['quality']}
        old_events = {e['id'] for e in before['assessment']['events']} if before else set()
        new = len({e['id'] for e in assessment['events']} - old_events) if before else None
        state[reg] = {'name': name, 'assessment': assessment, 'facts': current_facts, 'checks': current_checks}
        if c['role'] == 'ROOT':
            sheets['Overview'].append(dict(zip(FIELDS, [name, reg, assessment['score'], assessment['risk_class'],
                assessment['coverage'], assessment['reason'], new, assessment['recommendation']])))
        for e in assessment['events']:
            sheets['Findings'].append(dict(zip(HEADERS['Findings'], [name, e['title'],
                'Critical' if e['rule'] == 'sanctions' else 'High' if e['penalty'] >= 30 else 'Medium' if e['penalty'] == 15 else 'Low',
                e['date'], '\n'.join(e['sources']), '\n'.join(e['evidence']), e['rule'] + ' (-' + str(e['penalty']) + ')', e['status']])))
        if before:
            def change(field, old, current):
                if old != current:
                    sheets['Changes'].append(dict(zip(HEADERS['Changes'], [name, field, old, current, now[:10]])))
            change('Reliability score', before['assessment']['score'], assessment['score'])
            change('Coverage', before['assessment']['coverage'], assessment['coverage'])
            change('Recommended action', before['assessment']['recommendation'], assessment['recommendation'])
            current_events = {e['id']: e for e in assessment['events']}
            for e in before['assessment']['events']:
                if e['id'] not in current_events:
                    change('Event no longer scored: ' + e['id'], e['title'], 'Not scored in current assessment; see evidence/review')
                elif e['title'] != current_events[e['id']]['title']:
                    change('Event details: ' + e['id'], e['title'], current_events[e['id']]['title'])
            for e in assessment['events']:
                if e['id'] not in old_events: change('New event: ' + e['id'], None, e['title'])
            for key in sorted(before['facts'].keys() | current_facts.keys()):
                source = source_map.get(key.split(':')[0])
                available = {'FOUND', 'NO_RECORDS'}
                if current_checks.get(source) not in available or before['checks'].get(source) not in available: continue
                old = before['facts'].get(key, {}); current = current_facts.get(key, {})
                for field in sorted(old.keys() | current.keys()):
                    change(key + ':' + field, old.get(field), current.get(field))
        metrics = {}
        for m in item.get('financial_metrics', []):
            metrics.setdefault((m['statement_id'], m['file_id']), []).append(m['metric'] + '=' + (m['value'] if m['value'] is not None else m['status']))
        for f in item.get('financials', []):
            factor = {'ONES': 1, 'THOUSANDS': 1000, 'MILLIONS': 1000000}.get(f.get('rounded_to_nearest'))
            amounts = [numeric(f.get(key), factor) if factor is not None and f.get('currency') == 'EUR' else None
                for key in ['net_turnover', 'net_income', 'equity', 'total_assets']]
            context = 'EUR units; statement ' + str(f['statement_id']) + ', file ' + str(f['file_id'])
            if factor is None or f.get('currency') != 'EUR': context = 'Amounts unavailable in EUR: source currency/scale ' + str(f.get('currency')) + '/' + str(f.get('rounded_to_nearest'))
            sheets['Financials'].append(dict(zip(HEADERS['Financials'], [name, numeric(f['year']), *amounts,
                numeric(f.get('employees')), context + '; ' + '; '.join(metrics.get((f['statement_id'], f['file_id']), []))])))
        candidates = item.get('sanctions_candidates', [])
        screening = (item.get('sanctions_screening') or [{}])[0]
        matched = {r['subject_key'] for r in candidates}
        subjects = item.get('sanctions_subjects') or [{'key':'unavailable','name':name,'role':'Company'}]
        for subject in subjects:
            if subject['key'] in matched: continue
            status = 'No name candidate' if screening.get('status') in {'NO_CANDIDATES','CANDIDATES_REQUIRE_REVIEW'} else 'Incomplete screening'
            sheets['Sanctions'].append(dict(zip(HEADERS['Sanctions'], [name, subject['name'], subject['role'],
                status, 'FID EU/LV/UN', 'Name screening; record ' + subject['key'], 'Not applicable'])))
        for r in candidates:
            key = digest([reg, r['subject_key'], r['source'], r['entity_id']])
            review = reviews['sanctions'].get(key, {})
            if review.get('run_id') != run_id: review = {}
            sheets['Sanctions'].append(dict(zip(HEADERS['Sanctions'], [name, r['subject_name'], r['subject_role'],
                review.get('status', 'Identity not verified'), r['source'], review.get('reason', r['method'] + '; review key ' + key), 'Yes' if review else 'No'])))
        areas = assessment['areas']
        sheets['Data Quality'].append(dict(zip(HEADERS['Data Quality'], [name, *['Available' if areas[k] else 'Incomplete' for k in ['UR', 'VID', 'Tax debt', 'Sanctions', 'Financials', 'Web']], assessment['coverage']])))
    payload = {'id': input_id, 'version': VERSION, 'rules': RULES, 'run_id': run_id, 'created_at': now,
        'previous_id': previous['id'] if previous else None, 'headers': HEADERS, 'sheets': sheets, 'companies': state}
    serialized = json.dumps(payload, ensure_ascii=False, indent=2)
    persist(db, payload)
    atomic_text(target, serialized)
    atomic_text(latest, serialized)
    return payload, items
