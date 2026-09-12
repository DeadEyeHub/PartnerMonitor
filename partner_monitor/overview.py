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


def overview_rows(db,run_id,companies,load_company):
    rows=[]
    for entry in companies:
        if entry['role']!='ROOT':continue
        reg=entry['registration_number'];item=load_company(db,run_id,reg)
        checks={r['source']:r['status'] for r in item['quality']}
        screening=item.get('sanctions_screening',[])
        web=item['web_checks'][0]
        coverage=[checks.get('ur_register')=='FOUND',checks.get('vid_rating')=='FOUND',
          checks.get('vid_vat') in {'FOUND','NO_RECORDS'},bool(item['financials']),
          bool(item['tax_debt']) and item['tax_debt'][0]['query_status']!='NOT_CHECKED',
          bool(screening) and screening[0]['status'] in {'NO_CANDIDATES','CANDIDATES_REQUIRE_REVIEW'},
          web.get('search_status')=='COMPLETED' and web.get('analysis_status') in {'COMPLETED','NO_RESULTS'}]
        reasons=[];actions=[]
        if screening and screening[0]['candidates']:
            reasons.append('Sanctions name candidates require review');actions.append('Verify candidate identities')
        if any(r.get('proceeding_state')=='ACTIVE' for r in item['v_insolvency']):
            reasons.append('Active insolvency proceeding');actions.append('Review insolvency records')
        if any(r.get('activity_state')=='SUSPENDED' for r in item['v_vid_activity']):
            reasons.append('VID activity restriction');actions.append('Review activity restrictions')
        for debt in item['tax_debt']:
            if debt['query_status']=='PUBLISHED_DEBT':
                reasons.append('VID published debt EUR '+debt['published_debt_amount']+' as of '+debt['effective_date'])
                actions.append('Review debt evidence')
        if not coverage[-1]:reasons.append('Web analysis not completed');actions.append('Complete web analysis')
        rows.append(dict(zip(FIELDS,[entry['name'] or '',reg,'','NOT_ASSESSED',round(sum(coverage)*100/7,1),
          '; '.join(reasons) or 'Risk assessment not performed','',
          '; '.join(actions+['Apply risk rules'])])))
    return rows


def export_csv(rows,path):
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('w',encoding='utf-8-sig',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=FIELDS);writer.writeheader()
        for row in rows:
            safe={k:("'"+v if isinstance(v,str) and v.lstrip().startswith(('=','+','-','@','\t','\r')) else v) for k,v in row.items()}
            writer.writerow(safe)
