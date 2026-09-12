"""Reproducible candidate screening, never an automatic sanctions determination."""
import json
import re
import unicodedata
from collections import defaultdict
from difflib import SequenceMatcher
import xml.etree.ElementTree as ET

ALGORITHM='names-v1-exact-token-fuzzy-092'
LISTS={'fid_eu','fid_lv','fid_un'}
INPUTS={'ur_register','ur_names','ur_members','ur_stockholders','ur_beneficial_owners','ur_officers'}


def canonical(value):
    value=unicodedata.normalize('NFKD',value.casefold())
    return ' '.join(re.sub(r'[^\w]+',' ',''.join(c for c in value if not unicodedata.combining(c)),flags=re.UNICODE).split())


def name_match(left,right):
    if left==right and len(left)>=4:return 'NORMALIZED_NAME',1.0
    if len(left.split())>1 and sorted(left.split())==sorted(right.split()):return 'REORDERED_NAME',1.0
    if min(len(left),len(right))<8 or not set(left.split()) & set(right.split()):return None
    score=SequenceMatcher(None,left,right,autojunk=False).ratio()
    return ('SIMILAR_NAME',score) if score>=.92 else None


def subjects(db,run_id,reg):
    specs=[('registry','COMPANY'),('company_names','HISTORICAL_NAME'),('members','OWNER'),
           ('stockholders','SHAREHOLDER'),('beneficial_owners','BENEFICIAL_OWNER'),('officers','OFFICER')]
    result=[]
    for table,role in specs:
        for row in db.execute(f'SELECT * FROM {table} WHERE run_id=? AND registration_number=?',(run_id,reg)):
            r=dict(row)
            name=(' '.join(filter(None,[r.get('forename'),r.get('surname')])) if table=='beneficial_owners' else r.get('name'))
            if not name:continue
            result.append(dict(key=table+':'+r['record_key'],name=name,role=role,
                registration_number=reg if table in {'registry','company_names'} else r.get('legal_entity_registration_number') if table!='beneficial_owners' else None,
                birth_date=r.get('birth_date'),snapshot_id=r['snapshot_id'],record_key=r['record_key']))
    return result


def birth_evidence(subject,raw_xml):
    birth=subject.get('birth_date')
    if not birth:return 'SUBJECT_DOB_UNAVAILABLE'
    dates=set()
    for element in ET.fromstring(raw_xml).iter():
        local=element.tag.rsplit('}',1)[-1]
        if local=='birthdate':
            if element.get('birthdate'):dates.add(element.get('birthdate'))
        elif local=='BirthDate' and element.text:dates.add(element.text)
        elif local=='INDIVIDUAL_DATE_OF_BIRTH' and element.findtext('DATE'):dates.add(element.findtext('DATE'))
    if not dates:return 'LIST_DOB_UNAVAILABLE'
    return 'DOB_AGREES' if birth[:10] in dates else 'DOB_DIFFERS_REVIEW_REQUIRED'


def screen(db,run_id):
    checks={r['source']:dict(r) for r in db.execute('SELECT * FROM source_checks WHERE run_id=?',(run_id,))}
    loaded={s for s in LISTS if checks.get(s,{}).get('status')=='COMPLETED'}
    missing=sorted((LISTS|INPUTS)-{s for s,c in checks.items() if c['status']=='COMPLETED'})
    stale=sorted(s for s in loaded if checks[s]['detail'] and not checks[s]['detail'].startswith('SOURCE_DATE_OLDER_THAN_7_DAYS'))
    entities={(r['source'],r['entity_id']):dict(r) for r in db.execute('SELECT * FROM sanction_entities WHERE run_id=?',(run_id,))}
    aliases=[]; exact=defaultdict(set); tokens=defaultdict(set)
    for row in db.execute('SELECT source,entity_id,name FROM sanction_names WHERE run_id=?',(run_id,)):
        if row['source'] not in loaded:continue
        value=canonical(row['name']);idx=len(aliases)
        aliases.append((row['source'],row['entity_id'],row['name'],value))
        exact[value].add(idx)
        for token in value.split():
            if len(token)>=3:tokens[token].add(idx)
    total=0
    for company in db.execute('SELECT registration_number FROM run_companies WHERE run_id=?',(run_id,)).fetchall():
        reg=company[0];items=subjects(db,run_id,reg);hits={}
        for subject in items:
            value=canonical(subject['name']);pool=set(exact[value])
            for token in value.split():pool.update(tokens.get(token,()))
            for idx in pool:
                src,entity_id,alias,other=aliases[idx]
                match=name_match(value,other)
                if not match:continue
                method,score=match;key=(subject['key'],src,entity_id)
                if key in hits and hits[key][1]>=score:continue
                entity=entities[(src,entity_id)]
                evidence={'subject':subject,'list_snapshot_id':entity['snapshot_id'],
                    'legal_url':entity['legal_url'],'entity_type':entity['entity_type'],
                    'dob_comparison':birth_evidence(subject,entity['raw_xml']),
                    'normalized_subject':value,'normalized_alias':other,
                    'note':'Name candidate only. Verify identity and applicable legal act; no automatic sanctions conclusion.'}
                hits[key]=(method,score,subject,alias,evidence)
        for (key,src,entity_id),(method,score,subject,alias,evidence) in hits.items():
            db.execute('INSERT INTO sanctions_candidates VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
                (run_id,reg,key,subject['name'],subject['role'],src,entity_id,alias,method,f'{score:.4f}',json.dumps(evidence,ensure_ascii=False),'NEEDS_REVIEW'))
        limitations={'missing_sources':missing,'source_date_warnings':stale,
            'company_name_missing':not any(s['role']=='COMPANY' for s in items),
            'scope':'Name screening of imported company, historical name, owner, shareholder, beneficial owner and officer records. No transliteration, ownership/control attribution, sectoral sanctions or legal clearance.',
            'related_companies':'Each imported related company has its own screening result; indirect ownership is not attributed to a root automatically.'}
        status='CANDIDATES_REQUIRE_REVIEW' if hits else 'INCOMPLETE' if missing or stale or not items or limitations['company_name_missing'] else 'NO_CANDIDATES'
        db.execute('INSERT INTO sanctions_screening VALUES (?,?,?,?,?,?,?)',
            (run_id,reg,status,len(items),len(hits),json.dumps(limitations),ALGORITHM))
        total+=len(hits)
    return {'candidates':total,'missing_sources':missing,'source_date_warnings':stale}
