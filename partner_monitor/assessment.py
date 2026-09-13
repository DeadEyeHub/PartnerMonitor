"""Versioned, deterministic business scoring over preserved source evidence."""
import hashlib
import json
import re
from decimal import Decimal, InvalidOperation
from pathlib import Path

RULES = json.loads((Path(__file__).resolve().parent.parent / 'config/risk_rules.json').read_text())
VERSION = RULES['version']
WEIGHTS = RULES['weights']


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:24]


def finding_key(finding):
    # Stable across jobs; an edited quotation is new evidence requiring a new review.
    return digest([finding['registration_number'], finding['source_url'], finding['evidence_quote']])


def load_reviews(path):
    if not path.exists():
        return {'findings': {}, 'sanctions': {}}
    value = json.loads(path.read_text(encoding='utf-8-sig'))
    if set(value) - {'findings', 'sanctions'}:
        raise ValueError('Unknown assessment review section')
    for section in ('findings', 'sanctions'):
        if not isinstance(value.get(section, {}), dict):
            raise ValueError('Review sections must be objects')
        for review in value.get(section, {}).values():
            if not review.get('reason') or not review.get('reviewer'):
                raise ValueError('Every review needs a reviewer and reason')
            if section == 'findings' and review.get('rule') not in {None, *WEIGHTS}:
                raise ValueError('Unknown scoring rule')
            if section == 'sanctions' and review.get('status') not in {'CONFIRMED_APPLICABLE', 'FALSE_POSITIVE'}:
                raise ValueError('Invalid sanctions review status')
    return value


def coverage(item):
    checks = {r['source']: r['status'] for r in item.get('quality', [])}
    web = (item.get('web_checks') or [{}])[0]
    screening = (item.get('sanctions_screening') or [{}])[0]
    areas = {
        'UR': checks.get('ur_register') == 'FOUND',
        'VID': checks.get('vid_rating') == 'FOUND',
        'VAT': checks.get('vid_vat') in {'FOUND', 'NO_RECORDS'},
        'Financials': bool(item.get('financials')),
        'Tax debt': any(r['query_status'] in {'PUBLISHED_DEBT', 'NO_PUBLISHED_DEBT_ABOVE_THRESHOLD'} for r in item.get('tax_debt', [])),
        'Sanctions': screening.get('status') in {'NO_CANDIDATES', 'CANDIDATES_REQUIRE_REVIEW'},
        'Web': web.get('search_status') == 'COMPLETED' and web.get('analysis_status') in {'COMPLETED', 'NO_RESULTS'},
    }
    return areas


def classify(f):
    text = (f['summary'] + ' ' + f['evidence_quote']).lower()
    # A petition is a court dispute, not established insolvency/nonpayment.
    if f['finding_type'] == 'insolvency' and re.search(r'petition|application|pieteikum', text):
        return 'court_dispute'
    if re.search(r'\bcartel\b|karte[lļ]', text):
        return 'cartel'
    if f['finding_type'] == 'legal_dispute':
        return 'court_dispute'
    if f['finding_type'] == 'sanctions':
        return None  # News cannot establish an applicable list identity match.
    return 'other_negative'


def equity_window(financials, warnings):
    """Select one finite equity value per consecutive reporting year, never bridge gaps."""
    years = {}
    for f in financials:
        year = str(f.get('year') or '')
        if not re.fullmatch(r'[1-9][0-9]{3}', year):
            warnings.append('Invalid financial reporting year; equity trend requires verification')
            return []
        years.setdefault(int(year), []).append(f)
    if not years: return []
    latest = max(years)
    selected = []
    for year in range(latest, latest - 3, -1):
        rows = years.get(year, [])
        if len(rows) != 1:
            warnings.append(('Multiple latest-year financial statements; negative equity requires statement selection'
                if year == latest and rows else 'Three-year equity check incomplete: missing or ambiguous statement for ' + str(year)))
            break
        f = rows[0]
        try:
            value = Decimal(str(f.get('equity')))
            if not value.is_finite(): raise InvalidOperation
        except InvalidOperation:
            warnings.append('Equity unavailable or invalid for ' + str(year))
            break
        if value >= 0: break
        selected.append(f)
    return selected


def assess(item, reviews=None):
    reviews = reviews or {'findings': {}, 'sanctions': {}}
    reg = item['registration_number']
    events = {}
    warnings = ['Incomplete check: ' + name for name, ok in coverage(item).items() if not ok]
    warnings += ['Unavailable source: ' + r['source'] for r in item.get('quality', [])
        if r['status'] not in {'FOUND','NO_RECORDS','LOADED'}]

    def add(key, rule, title, date, source, evidence, status, review_key=None):
        event = events.setdefault(key, {'id': key, 'rule': rule, 'penalty': WEIGHTS[rule],
            'title': title, 'date': date, 'status': status, 'sources': [], 'evidence': [], 'review_keys': []})
        if WEIGHTS[rule] > event['penalty']:
            event.update(rule=rule, penalty=WEIGHTS[rule], title=title)
        if source and source not in event['sources']: event['sources'].append(source)
        if evidence and evidence not in event['evidence']: event['evidence'].append(evidence)
        if review_key and review_key not in event['review_keys']: event['review_keys'].append(review_key)

    for f in item.get('web_findings', []):
        key = finding_key(f)
        review = reviews.get('findings', {}).get(key, {})
        if review.get('exclude'): continue
        rule = review.get('rule') or classify(f)
        if not rule or rule == 'sanctions':
            warnings.append('Verify current sanctions applicability for media finding ' + key)
            continue
        case = review.get('case_id')
        # Exact quoted evidence only. Cross-publication case links require an explicit review.
        group = digest([reg, 'case', case]) if case else digest([reg, 'quote', f['evidence_quote'].strip()])
        add(group, rule, f['summary'], f.get('event_date'), f['source_url'], f['evidence_quote'],
            f['event_status'].replace('_', ' ').capitalize(), key)
        if not f.get('event_date'):
            warnings.append('Exact event date unavailable: ' + key)

    for r in item.get('tax_debt', []):
        if r['query_status'] == 'PUBLISHED_DEBT':
            add(digest([reg, 'vid_debt']), 'other_negative', 'Published tax debt: EUR ' + str(r['published_debt_amount']),
                r['effective_date'], r['evidence_url'], 'VID published debt above threshold', 'Official current snapshot')
            events[digest([reg, 'vid_debt'])]['source_id'] = 'vid_debt'
    for r in item.get('v_insolvency', []):
        if r['proceeding_state'] == 'ACTIVE':
            form = r.get('proceeding_form') or 'UNKNOWN'
            title = 'Active legal protection proceeding' if form == 'LEGAL_PROTECTION' else 'Active proceeding: ' + form.replace('_',' ').lower()
            add(digest([reg, 'insolvency', r.get('record_key')]), 'other_negative', title,
                r.get('proceeding_started_on'), 'UR insolvency register',
                'Proceeding ' + str(r.get('proceeding_id')) + '; form ' + form + '; court case ' + str(r.get('court_case_initial_number')) + '; started ' + str(r.get('proceeding_started_on')) + '; no end recorded', 'Official current snapshot')
            events[digest([reg, 'insolvency', r.get('record_key')])]['source_id'] = 'ur_insolvency'
    for r in item.get('v_vid_activity', []):
        if r['activity_state'] == 'SUSPENDED':
            add(digest([reg, 'vid_suspension']), 'other_negative', 'VID activity suspension',
                r.get('Aizliegts_veikt_darijumus_no'), 'VID activity restrictions', r.get('raw_json', ''), 'Official current snapshot')
            events[digest([reg, 'vid_suspension'])]['source_id'] = 'vid_suspensions'
    for r in item.get('vid_ratings', []):
        if r.get('Reitings') == 'C':
            key = digest([reg, 'vid_rating_c'])
            add(key, 'other_negative', 'VID taxpayer rating C', r.get('Informacijas_atjaunosanas_datums'),
                'VID taxpayer ratings', r.get('Skaidrojums') or 'Reitings=C', 'Official current snapshot')
            events[key]['source_id'] = 'vid_rating'
    financials = item.get('financials', [])
    if financials:
        negative = equity_window(financials, warnings)
        if negative:
            f = negative[0]
            rule = {1:'other_negative', 2:'two_year_negative_equity', 3:'persistent_negative_equity'}[len(negative)]
            title = ('Negative equity for ' + str(len(negative)) + ' consecutive reporting years (' + str(negative[-1]['year']) + '–' + str(f['year']) + ')'
                if len(negative) > 1 else 'Negative equity in latest annual statement (' + str(f['year']) + ')')
            key = digest([reg, 'negative_equity'])
            for statement in negative:
                add(key, rule, title, f.get('year_ended_on'), 'UR annual statements',
                    'Year=' + str(statement['year']) + '; equity=' + str(statement['equity']) + '; currency=' + str(statement.get('currency')) + '; scale=' + str(statement.get('rounded_to_nearest')) + '; statement=' + statement['statement_id'] + '; file=' + statement['file_id'], 'Official financial statement')
            events[key]['source_id'] = 'ur_balance'
            events[key]['source_ids'] = ['ur_financials','ur_balance']
    years = {}
    for f in financials:
        if re.fullmatch(r'[1-9][0-9]{3}', str(f.get('year', ''))):
            years.setdefault(int(f['year']), []).append(f)
    for year in range(max(years), max(years)-3, -1) if years else []:
        rows = years.get(year, [])
        if len(rows) != 1:
            warnings.append('Annual loss check: missing or ambiguous statement for ' + str(year))
            continue
        f = rows[0]
        factor = {'ONES':1, 'THOUSANDS':1000, 'MILLIONS':1000000}.get(f.get('rounded_to_nearest'))
        try:
            if f.get('currency') != 'EUR' or factor is None: raise InvalidOperation
            amount = Decimal(str(f.get('net_income'))) * factor
            if not amount.is_finite(): raise InvalidOperation
        except InvalidOperation:
            warnings.append('Annual loss unavailable in EUR for ' + str(year))
            continue
        if amount < -50000:
            key = digest([reg, 'large_annual_loss'])
            add(key, 'large_annual_loss', 'Annual loss above EUR 50,000 within the latest three reporting years',
                f.get('year_ended_on'), 'UR annual statements',
                'Year=' + str(year) + '; net income EUR=' + str(amount) + '; statement=' + str(f.get('statement_id')) + '; file=' + str(f.get('file_id')),
                'Official financial statement')
            events[key]['source_id'] = 'ur_income'
            events[key]['source_ids'] = ['ur_financials', 'ur_income']
    for r in item.get('sanctions_candidates', []):
        key = digest([reg, r['subject_key'], r['source'], r['entity_id']])
        # Pin confirmation to this exact run: a prior listing may no longer apply.
        review = reviews.get('sanctions', {}).get(key, {})
        if review.get('run_id') != item['run_id']: review = {}
        if review.get('status') == 'CONFIRMED_APPLICABLE':
            add(digest([reg, 'sanctions']), 'sanctions', 'Confirmed applicable sanctions', None,
                r['source'], review['reason'], 'Identity and applicability confirmed', key)
        elif review.get('status') != 'FALSE_POSITIVE':
            warnings.append('Sanctions identity/applicability not verified: ' + key)
    return finish(list(events.values()), warnings, coverage(item))


def finish(events, warnings, areas):
    events = sorted(events, key=lambda e: (-e['penalty'], e['id']))
    score = max(RULES['minimum_score'], RULES['start_score'] - sum(e['penalty'] for e in events))
    provisional = bool(warnings)
    risk = 'High' if score < RULES['not_recommended_below'] else 'Moderate' if score < RULES['start_score'] else 'Low'
    action = 'Not recommended' if risk == 'High' else 'Cooperate with caution' if risk == 'Moderate' else 'Eligible for cooperation'
    if provisional: action += '; provisional — manually verify the listed gaps'
    return {'version': VERSION, 'score': score, 'risk_class': risk, 'recommendation': action,
        'provisional': provisional, 'coverage': round(sum(areas.values()) * 100 / 7, 1),
        'areas': areas, 'warnings': sorted(set(warnings)), 'events': events,
        'reason': '; '.join(e['rule'].replace('_', ' ') + ' −' + str(e['penalty']) + ': ' + e['title'] for e in events)
            or 'No negative event identified in available evidence'}
