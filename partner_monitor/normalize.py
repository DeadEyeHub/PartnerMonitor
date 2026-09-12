import csv
import hashlib
import json
import re
from collections import Counter, defaultdict
from datetime import datetime
from decimal import Decimal, InvalidOperation

from .database import quote

DATE_FIELDS = set('registered terminated closed date_to date_from registered_on last_modified_at proceeding_started_on proceeding_ended_on entry_created_on creditor_applications_deadline_date entry_date birth_date year_started_on year_ended_on created_at Lemuma_datums Aizliegts_veikt_darijumus_no Aizliegts_veikt_darijumus_lidz Lemuma_par_atjaunosanu_datums Registrets Izslegts Informacijas_atjaunosanas_datums'.split())
DECIMAL_FIELDS = set('number_of_shares share_nominal_value votes employees representation_with_at_least Samaksato_VID_administreto_nodoklu_kopsumma_tukst_EUR Taja_skaita_IIN Taja_skaita_VSAOI Videjais_nodarbinato_personu_skaits_cilv'.split())
FINANCIAL_PARTS = {'balance_sheets','income_statements','cash_flow_statements'}


def number(value):
    if value is None or not str(value).strip():
        return None
    cleaned = str(value).strip().replace('\u00a0','').replace(' ','').replace(',','.')
    try:
        result = Decimal(cleaned)
        if not result.is_finite():
            raise InvalidOperation
        return format(result, 'f')
    except InvalidOperation:
        raise ValueError('Invalid numeric field') from None


def date_value(value, partial=False):
    if not value:
        return None
    if partial and re.fullmatch(r'\d{4}(-\d{2})?', value):
        return value
    for fmt in ['%d.%m.%Y', '%Y-%m-%d']:
        try:
            return datetime.strptime(value,fmt).date().isoformat()
        except ValueError:
            pass
    # Python 3.10 fromisoformat only accepts 3/6 fractional digits.
    # UR timestamps also use 1/2/4/5 digits; preserve their value explicitly.
    for fmt in ['%Y-%m-%d %H:%M:%S.%f','%Y-%m-%dT%H:%M:%S.%f']:
        try:
            return datetime.strptime(value,fmt).isoformat()
        except ValueError:
            pass
    try:
        parsed = datetime.fromisoformat(value.replace('Z','+00:00'))
        return parsed.isoformat() if ('T' in value or ' ' in value) else parsed.date().isoformat()
    except ValueError:
        raise ValueError('Invalid date field') from None


def csv_rows(path, source):
    with path.open(encoding='utf-8-sig',newline='') as stream:
        reader = csv.DictReader(stream,delimiter=source['delimiter'],strict=True)
        if len(reader.fieldnames or []) != len(set(reader.fieldnames or [])) or not set(source['columns']).issubset(reader.fieldnames or []):
            raise ValueError('Source schema mismatch')
        for row in reader:
            if None in row or any(v is None for v in row.values()):
                raise ValueError(f'Malformed CSV at physical line {reader.line_num}')
            yield reader.line_num, row


def registration(value):
    value = (value or '').strip()
    if value.startswith('LV'):
        value = value[2:]
    return value if re.fullmatch(r'[0-9]{11}',value) else None


def normalize(row, source):
    result = {}
    for key in source['columns']:
        value = row[key].strip() or None
        if value is not None and key in DATE_FIELDS:
            value = date_value(value,partial=key=='birth_date')
        elif key in DECIMAL_FIELDS or (source['table'] in FINANCIAL_PARTS and key not in {'statement_id','file_id'}):
            value = number(value)
        result[key] = value
    if source['id']=='ur_register' and not result.get('name'):
        raise ValueError('Registry record has no official name')
    return result


def expand_ownership(sources, snapshots, data_dir, roots, max_depth):
    """Follow only owner IDs that also exist in the Latvian registry; cap depth and cycles."""
    by_id = {s['id']: s for s in sources}
    if 'ur_register' not in snapshots:
        return {n:0 for n in roots}, False
    registered = {row['regcode'].strip() for _,row in csv_rows(data_dir / snapshots['ur_register']['path'],by_id['ur_register'])}
    owners = defaultdict(set)
    for source_id in ['ur_members','ur_stockholders']:
        if source_id not in snapshots:
            continue
        for _,row in csv_rows(data_dir / snapshots[source_id]['path'],by_id[source_id]):
            parent = registration(row.get('legal_entity_registration_number'))
            company = registration(row.get('at_legal_entity_registration_number'))
            if parent and parent in registered and company:
                owners[company].add(parent)
    depths = {n:0 for n in roots}
    frontier = set(roots)
    for depth in range(1,max_depth+1):
        frontier = {p for n in frontier for p in owners[n] if p not in depths}
        depths.update({n:depth for n in frontier})
        if len(depths)>5000:
            raise ValueError('Ownership graph exceeds 5000 companies')
    truncated = any(p not in depths for n in frontier for p in owners[n])
    return depths,truncated


def import_csv(db, run_id, source, snapshot_id, path, numbers):
    counts, scanned = Counter(), 0
    source_as_of = None
    statement_map = {}
    if source['table'] in FINANCIAL_PARTS:
        statement_map = {(r['id'],r['file_id']):r['registration_number'] for r in db.execute(
            'SELECT id,file_id,registration_number FROM financial_statements WHERE run_id=?',(run_id,))}
    columns = ['run_id','registration_number','snapshot_id','source_row','record_key','row_hash','raw_json'] + source['columns']
    insert = f'INSERT INTO {quote(source["table"])} ({",".join(map(quote,columns))}) VALUES ({",".join("?" for _ in columns)})'
    seen = {}
    for line,raw in csv_rows(path,source):
        scanned += 1
        if source['table'] in FINANCIAL_PARTS:
            reg = statement_map.get((raw['statement_id'].strip(),raw['file_id'].strip()))
        else:
            reg = registration(raw.get(source['key']))
        if reg not in numbers:
            continue
        row = normalize(raw,source)
        raw_json = json.dumps(raw,ensure_ascii=False,sort_keys=True)
        row_hash = hashlib.sha256(json.dumps(row,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
        if source['table'] in FINANCIAL_PARTS:
            key = json.dumps([row['statement_id'],row['file_id']])
        elif row.get('id'):
            key = json.dumps([row['id'],row.get('file_id')])
        elif source['id']=='ur_register':
            key = reg
        elif row.get('proceeding_id'):
            key = row['proceeding_id']
        else:
            key = row_hash
        if key in seen:
            if seen[key] != row_hash:
                raise ValueError('Conflicting duplicate source record')
            continue
        seen[key] = row_hash
        db.execute(insert,[run_id,reg,snapshot_id,line,key,row_hash,raw_json]+[row[c] for c in source['columns']])
        counts[reg] += 1
        if source['id']=='vid_rating':
            value = row.get('Informacijas_atjaunosanas_datums')
            source_as_of = max(source_as_of or '',value or '') or None
    if not scanned:
        raise ValueError('Source has header but no records; cannot establish completeness')
    return scanned,counts,source_as_of


def calculate_metrics(db,run_id):
    for row in db.execute('SELECT * FROM v_financials WHERE run_id=?',(run_id,)).fetchall():
        def ratio(a,b):
            if a is None or b is None:
                return None,'MISSING_INPUT'
            if Decimal(b)<=0:
                return None,'NON_POSITIVE_DENOMINATOR'
            return format(Decimal(a)/Decimal(b),'.8f'),'CALCULATED'
        liabilities = None if row['current_liabilities'] is None or row['non_current_liabilities'] is None else str(Decimal(row['current_liabilities'])+Decimal(row['non_current_liabilities']))
        for metric,a,b in [('current_ratio',row['total_current_assets'],row['current_liabilities']),
                           ('debt_to_assets',liabilities,row['total_assets']),('net_margin',row['net_income'],row['net_turnover'])]:
            value,status = ratio(a,b)
            db.execute('INSERT INTO financial_metrics VALUES (?,?,?,?,?,?,?)',
                       (run_id,row['registration_number'],row['statement_id'],row['file_id'],metric,value,status))
