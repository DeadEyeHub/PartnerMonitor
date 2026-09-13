"""Compact task Overview export; unavailable assessments are never invented."""
import csv
import json

FIELDS=['Company','Registration number','Reliability score','Risk class','Coverage',
        'Main reason','New findings','Recommended action']


def screening_rows(rows):
    result=[]
    for row in rows:
        limits=json.loads(row['limitations'])
        missing=limits.get('missing_sources',[])
        result.append({'Registration number':row['registration_number'],
          'Result':{'NO_CANDIDATES':'No name candidates found','INCOMPLETE':'Incomplete checks',
                    'CANDIDATES_REQUIRE_REVIEW':'Candidates require review'}.get(row['status'],row['status']),
          'Records screened':row['subjects_checked'],'Candidates':row['candidates'],
          'Missing sources':', '.join(missing) or 'None',
          'Data issues':', '.join(limits.get('source_date_warnings',[])) or 'None'})
    return result


def export_csv(rows,path):
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('w',encoding='utf-8-sig',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=FIELDS);writer.writeheader()
        for row in rows:
            safe={k:("'"+v if isinstance(v,str) and v.lstrip().startswith(('=','+','-','@','\t','\r')) else v) for k,v in row.items()}
            writer.writerow(safe)
