"""HTML report composition and export; inspection remains read-only data access."""
import json, hashlib
from pathlib import Path
from .inspection import summary, company, display_label
from .sources import load_sources
from .report_html import esc, table
from .report_view import missing_data_rows
from .methodology import rows as methodology_rows, summary as methodology_summary

def render_report(db,run_id,path,baseline=None):
    data = summary(db,run_id)
    from .overview import screening_rows,export_csv
    from .report_data import build_report
    from .web_logging import atomic_text
    payload, items = build_report(db,run_id,data['companies'],company,path.parent,baseline=baseline)
    model_reports=[]
    from .final_media_report import export_final_results
    latest_jobs=sorted({check['job_id'] for item in items.values() for check in item.get('web_checks',[]) if check.get('job_id')})
    for job_id in latest_jobs:
        model_reports.append(export_final_results(db,job_id,path.parent/('model-final-'+job_id+'.html')))
    overview=payload['sheets']['Overview']
    payload_path=path.with_suffix('.workbook.json')
    atomic_text(payload_path,json.dumps(payload,ensure_ascii=False,indent=2))
    csv_path=path.with_suffix('.csv')
    export_csv(overview,csv_path)
    evidence_links = {}
    data_dir = Path(db.execute('PRAGMA database_list').fetchone()[2]).parent
    for snapshot in db.execute("SELECT metadata_json FROM source_snapshots WHERE run_id=? AND source='vid_debt'",(run_id,)):
        for artifact in json.loads(snapshot[0]).get('artifacts',[]):
            if 'pdf' not in artifact:
                continue
            meta = artifact['pdf']
            source_path = (data_dir/meta['path']).resolve()
            if not source_path.is_relative_to(data_dir.resolve()):
                raise ValueError('Invalid PDF evidence path')
            content = source_path.read_bytes()
            if hashlib.sha256(content).hexdigest() != meta['sha256']:
                raise ValueError('PDF evidence hash mismatch')
            relative = Path('evidence')/(meta['sha256']+'.pdf')
            target = path.parent/relative
            target.parent.mkdir(parents=True,exist_ok=True)
            target.write_bytes(content)
            evidence_links[artifact['registration_number']] = relative.as_posix()
    missing_rows = missing_data_rows(items,payload)
    monitoring_rows = []
    if baseline:
        previous=json.loads((path.parent/'assessments'/(baseline+'.json')).read_text(encoding='utf-8'))
        for row in overview:
            reg=row['Registration number']; current=payload['companies'][reg]
            old=previous['companies'].get(reg)
            if not old: continue
            before=old['assessment']['score']; after=current['assessment']['score']
            changes=sum(r['Company']==current['name'] for r in payload['sheets']['Changes'])
            monitoring_rows.append({'Company':current['name'],'Registration number':reg,
                'Previous score':before,'Current score':after,'Score change':after-before,
                'Recorded changes':changes,'Result':'Changes detected' if changes else 'No changes detected in comparable data'})
    blocks = ['<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>Partner Monitor — Data</title>',
      '<style>body{font:15px system-ui;margin:32px;background:#f5f7fa;color:#172435}h1,h2{color:#133b55}table{border-collapse:collapse;background:white;width:100%}td,th{padding:9px;border:1px solid #dce3ea;text-align:left;vertical-align:top}th{background:#e8eff6}details{margin:12px 0;padding:12px;background:white;border:1px solid #dce3ea}summary{cursor:pointer;font-weight:600}.scroll{overflow:auto}.muted{color:#596574}a{color:#075c9a}</style>',
      '<h1>Partner Monitoring Report</h1>',
      '<p>Official-data run '+esc(run_id)+' · '+esc(data['run']['started_at'])+' · '+esc(data['run']['status'])+'</p>',
      '<details><summary>Scoring methodology</summary><p>'+esc(methodology_summary(payload['rules']))+'</p>'+table(methodology_rows(payload['rules']))+'</details>',
      '<p>Assessment '+esc(payload['id'])+' · '+esc(payload['created_at'])+' · '+esc(payload['version'])+'</p>',
      '<p><a href="'+esc(csv_path.name)+'">Download compact CSV</a></p>',
      '<h2>Overview</h2>',table(overview),
      '<p>Coverage is the percentage of seven available checks: UR identity, VID rating, VAT, financials, tax debt, sanctions screening and web analysis. A score of 100 with gaps means no penalty in available evidence, not proof of absence. Blank new-findings count means no earlier assessment baseline.</p>',
      '<h2>Scored events</h2>',table(payload['sheets']['Findings']),
      '<h2>Monitoring summary</h2>'+table(monitoring_rows) if baseline else '',
      '<h2 id=changes>Changes since previous assessment</h2>',
      '<p>Compared with assessment '+esc(payload['previous_id'])+'</p>' if payload['previous_id'] else '',
      table(payload['sheets']['Changes']) if payload['previous_id'] else '<p>First assessment baseline. Future reports will compare scores, events and source fields against it.</p>',
      '<h2>Missing data and unfinished checks</h2>',
      '<p>These are gaps in available evidence, not findings against the company. An official check with no matching records is not treated as missing data.</p>',
      table(missing_rows) if missing_rows else '<p>No missing data identified in the checks summarized here.</p>',
      '<details><summary>Data coverage summary</summary>',table(payload['sheets']['Data Quality']),'</details>',
      '<h2>Sanctions Screening</h2>',
      '<p>EU, UN and Latvian lists are supplied by FID, the agreed source for this stage. File publication dates are informational: an old date alone does not make screening incomplete. Download failures, missing inputs and invalid dates still require attention.</p>',
      '<p>Names of companies, former company names, owners, shareholders, beneficial owners and officers are compared. A name candidate requires identity review. Cross-script transliteration and sectoral restrictions are not checked. Related companies are screened separately; ownership or control does not automatically transfer a result to another company.</p>',
      table(screening_rows(data['sanctions_screening'])) if data['sanctions_screening'] else '<p>Not performed for this run.</p>',
      '<details><summary>Source dates and import details</summary>',
      table([{k:v for k,v in r.items() if k in {'source','status','rows_imported','retrieved_at','source_as_of','detail'}} for r in data['sources']]),'</details>',
      '<details><summary>Technical details and company evidence</summary>']
    if db.execute("SELECT 1 FROM sqlite_master WHERE name='web_jobs'").fetchone():
        jobs=[dict(r) for r in db.execute('SELECT job_id,created_at,finished_at,status FROM web_jobs WHERE run_id=? ORDER BY created_at DESC,rowid DESC',(run_id,))]
        from .web_logging import export_log
        log_links=[]
        for job in jobs:
            if (data_dir/'raw/web_logs'/(job['job_id']+'.jsonl')).exists():
                log_path=export_log(data_dir,job['job_id'],path.parent)
                log_links.append('<li><a href="'+esc(log_path.relative_to(path.parent).as_posix())+'">Tavily and model log — '+esc(job['job_id'])+'</a></li>')
        blocks.extend(['<h2>Adverse Media Jobs</h2>',
          '<p>Each company card shows its latest web job for this official-data run. Previously scored media events are retained until explicitly excluded. Failed or limited searches do not establish absence of adverse information.</p>',table(jobs)])
        if log_links:blocks.append('<ul>'+''.join(log_links)+'</ul>')
    for row in data['companies']:
        reg = row['registration_number']
        item = dict(items[reg])
        blocks.append('<details><summary>'+esc(reg)+' — '+esc(row['name'] or 'Not found in UR')+' ('+esc(row['role'])+')</summary>')
        assessment=payload['companies'][reg]['assessment']
        if row['role'] != 'ROOT':
            blocks.append('<p><strong>'+str(assessment['score'])+'/100 · '+esc(assessment['recommendation'])+'</strong></p>')
        if assessment['warnings']:
            blocks.append('<ul>'+''.join('<li>'+esc(w)+'</li>' for w in assessment['warnings'])+'</ul>')
        blocks.append(table(item.pop('quality')))
        evidence_sections = [source['table'] for source in load_sources() if source['format']=='csv'] + [
            'v_vat','v_insolvency','v_vid_activity','financials','financial_metrics','tax_debt',
            'web_checks','web_findings','web_articles','web_queries','web_quality','web_selection','web_judgments',
            'web_event_links','sanctions_screening','sanctions_candidates','sanctions_subjects']
        for name in dict.fromkeys(evidence_sections):
            records=item.get(name)
            if isinstance(records,list):
                if name=='sanctions_screening':records=screening_rows(records)
                blocks.append('<details><summary>'+esc(display_label(name))+' · '+str(len(records))+'</summary>')
                blocks.append(table(records,exclude={'run_id','raw_json','row_hash','record_key'}))
                if name=='tax_debt' and reg in evidence_links:
                    blocks.append('<p><a href="'+esc(evidence_links[reg])+'">Download official VID PDF</a></p>')
                blocks.append('</details>')
        blocks.append('</details>')
    blocks.append('</details></html>')
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text('\n'.join(blocks),encoding='utf-8')
    archive = path.parent/('report-'+payload['id']+'.html')
    if not archive.exists():
        archive.write_text(path.read_text(encoding='utf-8').replace(csv_path.name,'report-'+payload['id']+'.csv'),encoding='utf-8')
        (path.parent/('report-'+payload['id']+'.csv')).write_bytes(csv_path.read_bytes())
    return {'report':str(path),'csv':str(csv_path),'workbook_data':str(payload_path),'model_reports':model_reports,'run_id':run_id,
            'assessment_id':payload['id'],'assessment_status':'PROVISIONAL' if any(c['assessment']['provisional'] for c in payload['companies'].values()) else 'CALCULATED'}
