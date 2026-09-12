"""Import verified browser evidence or explicitly supplied VID evidence."""
import csv
import hashlib
import json
import uuid
from pathlib import Path
from urllib.parse import urlsplit

from .normalize import date_value, number
from .ur import utc_now

URL = 'https://www6.vid.gov.lv/NPAR'


def import_debt(db,run_id,companies,data_dir,manual_file=None,probe=True,replay_metadata=None):
    rows = {}
    browser_evidence = None
    if replay_metadata and replay_metadata.get('origin')=='browser_evidence':
        browser_evidence = replay_metadata.get('artifacts',[])
    browser_error = None
    if not manual_file and probe and companies:
        from .debt_browser import collect_browser
        try:
            manual_file,browser_evidence = collect_browser(companies,data_dir)
        except Exception as exc:
            browser_error = 'BROWSER_UNAVAILABLE: '+type(exc).__name__
    detail,content = ('NO_MANUAL_EVIDENCE_PROVIDED',None)
    if manual_file:
        content = Path(manual_file).read_bytes()
        with Path(manual_file).open(encoding='utf-8-sig',newline='') as stream:
            reader = csv.DictReader(stream)
            required = {'registration_number','effective_date','published_debt_amount','publication_threshold','query_status','evidence_url'}
            if not required.issubset(reader.fieldnames or []):
                raise ValueError('Tax debt evidence columns are missing')
            for row in reader:
                reg = row['registration_number'].strip()
                if reg in rows:
                    raise ValueError('Duplicate debt evidence')
                if row['query_status'] not in {'PUBLISHED_DEBT','NO_PUBLISHED_DEBT_ABOVE_THRESHOLD'}:
                    raise ValueError('Invalid debt evidence status')
                row['effective_date'] = date_value(row['effective_date'].strip())
                amount = number(row['published_debt_amount'])
                threshold = number(row['publication_threshold'])
                from decimal import Decimal
                if not row['effective_date'] or threshold is None or Decimal(threshold)<0:
                    raise ValueError('Debt date and nonnegative publication threshold required')
                if row['query_status']=='PUBLISHED_DEBT' and (amount is None or Decimal(amount)<=Decimal(threshold)):
                    raise ValueError('Published debt must exceed publication threshold')
                if row['query_status']=='NO_PUBLISHED_DEBT_ABOVE_THRESHOLD' and amount is not None:
                    raise ValueError('No published debt is not a zero debt amount; leave amount empty')
                evidence = urlsplit(row['evidence_url'])
                if evidence.scheme!='https' or evidence.username or evidence.password or evidence.query or evidence.hostname not in {'www6.vid.gov.lv','www.vid.gov.lv'}:
                    raise ValueError('Use a public VID evidence URL without credentials')
                row.update(published_debt_amount=amount,publication_threshold=threshold)
                rows[reg] = row
        detail = 'BROWSER_VERIFIED_VID_EVIDENCE' if browser_evidence is not None else 'MANUALLY_SUPPLIED_VID_EVIDENCE'
    elif browser_error:
        detail = browser_error
    snapshot_id = None
    if content:
        sha = hashlib.sha256(content).hexdigest()
        path = data_dir/'raw'/'objects'/(sha+('.csv' if manual_file else '.html'))
        path.parent.mkdir(parents=True,exist_ok=True)
        if not path.exists():
            path.write_bytes(content)
        meta = dict(source='vid_debt',download_url=URL,retrieved_at=utc_now(),source_as_of=None,
                    sha256=sha,size=len(content),path=path.relative_to(data_dir).as_posix(),
                    origin='browser_evidence' if browser_evidence is not None else 'manual_evidence',
                    artifacts=browser_evidence or [])
        snapshot_id = uuid.uuid4().hex
        db.execute('INSERT INTO source_snapshots VALUES (?,?,?,?)',(snapshot_id,run_id,'vid_debt',json.dumps(meta)))
    for company in companies:
        reg = company['registration_number']
        row = rows.get(reg,{})
        status = row.get('query_status','NOT_CHECKED')
        company_detail = detail
        if browser_evidence is not None:
            evidence = next((e for e in browser_evidence if e['registration_number']==reg),{})
            company_detail = evidence.get('reason',detail)
        db.execute('INSERT INTO tax_debt VALUES (?,?,?,?,?,?,?,?,?,?)',(run_id,reg,row.get('effective_date'),
                   row.get('published_debt_amount'),row.get('publication_threshold','150'),status,utc_now(),
                   row.get('evidence_url',URL),snapshot_id,company_detail))
        db.execute('INSERT INTO company_source_checks VALUES (?,?,?,?,?)',
                   (run_id,reg,'vid_debt','FOUND' if row else 'NOT_CHECKED',int(bool(row))))
    imported = sum(c['registration_number'] in rows for c in companies)
    status = 'COMPLETED' if imported==len(companies) else 'MANUAL_REQUIRED'
    db.execute('INSERT INTO source_checks VALUES (?,?,?,?,?,?,?,?)',
               (run_id,'vid_debt',status,snapshot_id,len(rows),imported,None,detail))
    return status
