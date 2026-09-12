"""Explicit evidence import when VID's interactive session cannot be automated."""
import csv
import hashlib
import json
import re
import uuid
from pathlib import Path
from urllib.parse import urlsplit

import requests

from .downloads import write_json
from .normalize import date_value, number
from .ur import utc_now

URL = 'https://www6.vid.gov.lv/NPAR'


def access_probe(company):
    try:
        with requests.Session() as session:
            response = session.get(URL,timeout=(10,30))
            response.raise_for_status()
            date = re.search(r'id="QueryDate"[^>]*value="([^"]+)',response.text)
            if not date:
                return 'FORM_CHANGED',None
            check = session.get('https://www6.vid.gov.lv/ReqCode',params={'check':'true','pageName':'NPARJP'},timeout=(10,30))
            check.raise_for_status()
            if check.text.strip()=='true':
                return 'HUMAN_VERIFICATION_REQUIRED',None
            if check.text.strip()!='false':
                return 'FORM_CHANGED',None
            result = session.post(URL+'/Data',data={'IsPhysicalPerson':'false','IsLegalPerson':'true',
                'Code':company['registration_number'],'Name':company.get('name',''),'Surname':'',
                'QueryDate':date.group(1),'From':'0','submit':'yes'},timeout=(10,60))
            result.raise_for_status()
            if 'sesijas noilgums' in result.text:
                return 'VID_SESSION_EXPIRED',result.content
            if result.text.startswith('check_code'):
                return 'HUMAN_VERIFICATION_REQUIRED',None
            return 'MANUAL_RESULT_REVIEW_REQUIRED',result.content
    except requests.RequestException:
        return 'VID_UNAVAILABLE',None


def import_debt(db,run_id,companies,data_dir,manual_file=None,probe=True):
    rows = {}
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
        detail = 'MANUALLY_SUPPLIED_VID_EVIDENCE'
    elif probe and companies:
        detail,content = access_probe(companies[0])
    snapshot_id = None
    if content:
        sha = hashlib.sha256(content).hexdigest()
        path = data_dir/'raw'/'objects'/(sha+('.csv' if manual_file else '.html'))
        path.parent.mkdir(parents=True,exist_ok=True)
        if not path.exists():
            path.write_bytes(content)
        meta = dict(source='vid_debt',download_url=URL,retrieved_at=utc_now(),source_as_of=None,
                    sha256=sha,size=len(content),path=path.relative_to(data_dir).as_posix(),
                    origin='manual_evidence' if manual_file else 'access_probe')
        snapshot_id = uuid.uuid4().hex
        db.execute('INSERT INTO source_snapshots VALUES (?,?,?,?)',(snapshot_id,run_id,'vid_debt',json.dumps(meta)))
    for company in companies:
        reg = company['registration_number']
        row = rows.get(reg,{})
        status = row.get('query_status','NOT_CHECKED')
        db.execute('INSERT INTO tax_debt VALUES (?,?,?,?,?,?,?,?,?,?)',(run_id,reg,row.get('effective_date'),
                   row.get('published_debt_amount'),row.get('publication_threshold','150'),status,utc_now(),
                   row.get('evidence_url',URL),snapshot_id,detail))
        db.execute('INSERT INTO company_source_checks VALUES (?,?,?,?,?)',
                   (run_id,reg,'vid_debt','FOUND' if row else 'NOT_CHECKED',int(bool(row))))
    imported = sum(c['registration_number'] in rows for c in companies)
    status = 'COMPLETED' if imported==len(companies) else 'MANUAL_REQUIRED'
    db.execute('INSERT INTO source_checks VALUES (?,?,?,?,?,?,?,?)',
               (run_id,'vid_debt',status,snapshot_id,len(rows),imported,None,detail))
    return status
