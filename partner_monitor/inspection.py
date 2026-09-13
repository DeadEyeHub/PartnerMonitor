import html
import json
import sqlite3
import hashlib
from pathlib import Path

from .database import quote
from .sources import load_sources

DISPLAY_LABELS = {
    'Registracijas_kods': 'Registration number', 'Nosaukums': 'Name',
    'Numurs': 'VAT number', 'Aktivs': 'Active (source value)',
    'Registrets': 'Registered on', 'Izslegts': 'Excluded on',
    'Buvniecibas_pazime': 'Construction indicator', 'PVN139_2_pazime': 'VAT section 139.2 indicator',
    'Lemuma_datums': 'Decision date', 'Aizliegts_veikt_darijumus_no': 'Transactions prohibited from',
    'Aizliegts_veikt_darijumus_lidz': 'Transactions prohibited until',
    'Lemuma_par_atjaunosanu_datums': 'Restoration decision date',
    'Reitings': 'Taxpayer rating', 'Skaidrojums': 'Explanation (source text)',
    'Informacijas_atjaunosanas_datums': 'Information updated on',
    'Taksacijas_gads': 'Tax year', 'Uznemejdarbibas_forma': 'Business form',
    'Pamatdarbibas_NACE_kods': 'Main activity NACE code',
    'Juridiska_adrese_ATVK_kods': 'Registered address ATVK code',
    'Juridiska_adrese_ATVK_nosaukums': 'Registered address territory',
    'Samaksato_VID_administreto_nodoklu_kopsumma_tukst_EUR': 'Total VID taxes paid (EUR thousands)',
    'Taja_skaita_IIN': 'Personal income tax (EUR thousands)',
    'Taja_skaita_VSAOI': 'Social insurance contributions (EUR thousands)',
    'Videjais_nodarbinato_personu_skaits_cilv': 'Average employee count',
    'v_vat': 'VAT status', 'v_insolvency': 'Insolvency status',
    'v_vid_activity': 'VID activity status',
}


def display_label(value):
    return DISPLAY_LABELS.get(value, value.replace('_', ' ').capitalize())


def open_database(data_dir):
    path = (data_dir/'monitoring.db').resolve()
    db = sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)
    db.row_factory = sqlite3.Row
    return db


def resolve_run(db,run_id=None):
    if run_id:
        if not db.execute('SELECT 1 FROM monitoring_runs WHERE run_id=?',(run_id,)).fetchone():
            raise ValueError('Run not found')
        return run_id
    row = db.execute('''SELECT r.run_id FROM monitoring_runs r WHERE EXISTS
      (SELECT 1 FROM source_checks c WHERE c.run_id=r.run_id) ORDER BY r.started_at DESC LIMIT 1''').fetchone()
    if not row:
        raise ValueError('No full collection runs. Run collect first.')
    return row[0]


def summary(db,run_id):
    run = dict(db.execute('SELECT * FROM monitoring_runs WHERE run_id=?',(run_id,)).fetchone())
    sources = [dict(r) for r in db.execute('SELECT * FROM v_source_status WHERE run_id=? ORDER BY source',(run_id,))]
    companies = [dict(r) for r in db.execute('''SELECT rc.registration_number,rc.role,rc.depth,
      r.name,r.type,r.terminated FROM run_companies rc LEFT JOIN registry r USING(run_id,registration_number)
      WHERE rc.run_id=? ORDER BY rc.role DESC,rc.registration_number''',(run_id,))]
    screening=[]
    if db.execute("SELECT 1 FROM sqlite_master WHERE name='sanctions_screening'").fetchone():
        screening=[dict(r) for r in db.execute('SELECT * FROM sanctions_screening WHERE run_id=?',(run_id,))]
    return {'run':run,'sources':sources,'companies':companies,'sanctions_screening':screening,
            'note':'LOADED means list imported. Screening results, coverage limits and review candidates are reported separately; no legal clearance is implied.'}


def company(db,run_id,registration_number):
    if not db.execute('SELECT 1 FROM run_companies WHERE run_id=? AND registration_number=?',(run_id,registration_number)).fetchone():
        raise ValueError('Company was not included in this run')
    result = {'registration_number':registration_number,'run_id':run_id}
    result['quality'] = [dict(r) for r in db.execute('SELECT source,status,row_count FROM company_source_checks WHERE run_id=? AND registration_number=?',(run_id,registration_number))]
    for source in load_sources():
        if source['format']=='csv':
            result[source['table']] = [dict(r) for r in db.execute(f'SELECT * FROM {quote(source["table"])} WHERE run_id=? AND registration_number=?',(run_id,registration_number))]
    result['financials'] = [dict(r) for r in db.execute('SELECT * FROM v_financials WHERE run_id=? AND registration_number=? ORDER BY year DESC',(run_id,registration_number))]
    for view in ['v_vat','v_insolvency','v_vid_activity']:
        result[view] = [dict(r) for r in db.execute(f'SELECT * FROM {quote(view)} WHERE run_id=? AND registration_number=?',(run_id,registration_number))]
    result['financial_metrics'] = [dict(r) for r in db.execute('SELECT * FROM financial_metrics WHERE run_id=? AND registration_number=?',(run_id,registration_number))]
    result['tax_debt'] = [dict(r) for r in db.execute('SELECT * FROM tax_debt WHERE run_id=? AND registration_number=?',(run_id,registration_number))]
    result['web_checks']=[{'search_status':'NOT_PERFORMED','analysis_status':'NOT_PERFORMED'}]
    if db.execute("SELECT 1 FROM sqlite_master WHERE name='web_jobs'").fetchone():
        job=db.execute('SELECT j.job_id FROM web_jobs j JOIN web_checks c USING(job_id) WHERE j.run_id=? AND c.registration_number=? ORDER BY j.created_at DESC,j.rowid DESC LIMIT 1',(run_id,registration_number)).fetchone()
        if job:
            for table in ('web_checks','web_findings'):
                result[table]=[dict(r) for r in db.execute(f'SELECT * FROM {table} WHERE job_id=? AND registration_number=?',(job[0],registration_number))]
            result['web_articles']=[dict(r) for r in db.execute('SELECT url,title,publication_date,content_kind,analysis_status,result_json,error_type FROM web_articles WHERE job_id=? AND registration_number=?',(job[0],registration_number))]
            result['web_queries']=[dict(r) for r in db.execute('SELECT query,status,error_type FROM web_queries WHERE job_id=? AND registration_number=? ORDER BY query',(job[0],registration_number))]
            for article in result['web_articles']:
                analysis=json.loads(article.pop('result_json') or '{}')
                article['identity']=analysis.get('identity')
                article['identity_reason']=analysis.get('identity_reason')
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='web_article_review'").fetchone():
                from .media_selection import quality_rows
                result['web_quality']=[r for r in quality_rows(db,job[0]) if r['registration_number']==registration_number]
                result['web_selection']=[dict(r) for r in db.execute('SELECT a.url,r.filter_reason,r.snippet,r.triage_json,r.excerpt_limited,r.duplicate_of FROM web_article_review r JOIN web_articles a USING(job_id,registration_number,article_id) WHERE r.job_id=? AND r.registration_number=?',(job[0],registration_number))]
                for selected in result['web_selection']:
                    triage=json.loads(selected.pop('triage_json') or '{}')
                    selected['triage_decision']=triage.get('decision')
                    selected['triage_reason']=triage.get('reason')
                    selected['verdict']={'да':'yes','нет':'no'}.get(triage.get('verdict'),triage.get('verdict'))
                if db.execute("SELECT 1 FROM sqlite_master WHERE name='web_article_judgment'").fetchone():
                    result['web_judgments']=[dict(r) for r in db.execute('SELECT a.url,j.explanation,j.verdict,j.date_check_json FROM web_article_judgment j JOIN web_articles a USING(job_id,registration_number,article_id) WHERE j.job_id=? AND j.registration_number=?',(job[0],registration_number))]
                    for judgment in result['web_judgments']:
                        judgment['verdict']={'да':'yes','нет':'no'}.get(judgment['verdict'],judgment['verdict'])
                        dates=json.loads(judgment.pop('date_check_json'))
                        judgment['publication_date']=dates['publication_date']
                        judgment['date_review_required']=dates['review_required']
                        judgment['historical_name_checks']='; '.join(r['name']+': '+r['status']+' (end: '+str(r['date_to'])+')' for r in dates['historical_matches']) or 'No historical name matched'
                if db.execute("SELECT 1 FROM sqlite_master WHERE name='web_event_links'").fetchone():
                    result['web_event_links']=[dict(r) for r in db.execute('SELECT l.*,a.url AS first_source,b.url AS second_source FROM web_event_links l JOIN web_articles a ON a.job_id=l.job_id AND a.registration_number=l.registration_number AND a.article_id=l.first_article JOIN web_articles b ON b.job_id=l.job_id AND b.registration_number=l.registration_number AND b.article_id=l.second_article WHERE l.job_id=? AND l.registration_number=?',(job[0],registration_number))]
    if db.execute("SELECT 1 FROM sqlite_master WHERE name='sanctions_screening'").fetchone():
        for name in ('sanctions_screening','sanctions_candidates'):
            result[name] = [dict(r) for r in db.execute(f'SELECT * FROM {name} WHERE run_id=? AND registration_number=?',(run_id,registration_number))]
        from .screening import subjects
        result['sanctions_subjects']=subjects(db,run_id,registration_number)
    return result


def compare(db,current,previous):
    changes = []
    for source in load_sources():
        if source['format']!='csv':
            continue
        checks = db.execute('SELECT run_id,status FROM source_checks WHERE run_id IN (?,?) AND source=?',(current,previous,source['id'])).fetchall()
        if len(checks)!=2 or any(r['status']!='COMPLETED' for r in checks):
            changes.append({'source':source['id'],'status':'NOT_COMPARABLE'})
            continue
        maps = []
        for run_id in [previous,current]:
            maps.append({r['record_key']:(r['row_hash'],r['registration_number']) for r in db.execute(f'SELECT record_key,row_hash,registration_number FROM {quote(source["table"])} WHERE run_id=?',(run_id,))})
        before,after = maps
        common_companies = {r[0] for r in db.execute('SELECT registration_number FROM run_companies WHERE run_id=? INTERSECT SELECT registration_number FROM run_companies WHERE run_id=?',(previous,current))}
        for key in sorted(before.keys() | after.keys()):
            reg = (after.get(key) or before[key])[1]
            if reg not in common_companies:
                continue
            status = 'NEW' if key not in before else 'REMOVED_FROM_SOURCE' if key not in after else 'CHANGED' if before[key][0]!=after[key][0] else 'UNCHANGED'
            changes.append({'source':source['id'],'registration_number':reg,'record_key':key,'status':status})
    return changes


def report(db,run_id,path,baseline=None):
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
    def esc(value):
        return html.escape('' if value is None else str(value),quote=True)
    def table(rows,exclude=()):
        if not rows:
            return '<p class="muted">No records. Check the source status.</p>'
        columns = [c for c in rows[0] if c not in exclude and not c.endswith('_json') and c!='limitations']
        def cell(value):
            return '<br>'.join('<a href="'+esc(line)+'">'+esc(line)+'</a>' if line.startswith(('https://','http://')) else esc(line)
                for line in str('' if value is None else value).split('\n'))
        return '<div class="scroll"><table><thead><tr>'+''.join('<th title="'+esc(c)+'">'+esc(display_label(c))+'</th>' for c in columns)+'</tr></thead><tbody>'+''.join('<tr>'+''.join('<td>'+cell(row.get(c))+'</td>' for c in columns)+'</tr>' for row in rows)+'</tbody></table></div>'
    missing_rows = []
    labels = {'UR':'Company registration details', 'VID':'VID taxpayer rating', 'VAT':'VAT registration check',
              'Financials':'Annual financial statements', 'Tax debt':'Tax debt amount or official no-published-debt result',
              'Sanctions':'Sanctions name screening', 'Web':'Completed news search and model analysis'}
    actions = {'UR':'Retry the company register import and verify the registration number.',
               'VID':'Retry VID taxpayer rating collection.', 'VAT':'Retry the VAT register check.',
               'Financials':'Check whether annual statements were filed and retry financial collection.',
               'Tax debt':'Retry the VID debt form or verify it manually.',
               'Sanctions':'Refresh sanctions sources and rerun name screening.',
               'Web':'Run news search and analysis for this company; review unresolved articles.'}
    from .assessment import coverage
    for reg, item in items.items():
        name = (item.get('registry') or {}).get('name') if isinstance(item.get('registry'), dict) else None
        name = name or payload['companies'][reg].get('name') or reg
        for area, available in coverage(item).items():
            if available: continue
            reason = 'No usable result was saved for this check.'
            if area == 'Web':
                checks = item.get('web_checks') or []
                reason = ('Search and analysis were not run for this collection.' if not checks else
                    'News checking is unfinished: search ' + str(checks[0].get('search_status', 'not started')).lower().replace('_',' ') +
                    ', analysis ' + str(checks[0].get('analysis_status', 'not started')).lower().replace('_',' ') + '.')
            if area == 'Tax debt' and item.get('tax_debt'):
                detail = item['tax_debt'][0].get('detail')
                reason = {'Company name required by VID':'The VID request could not start because the legal name was missing.',
                          'VID PDF download unavailable':'The VID certificate could not be downloaded.'}.get(detail, 'VID did not return a usable debt result; inspect the company evidence.')
            missing_rows.append({'Company':name,'Registration number':reg,'Missing data or unfinished check':labels[area],
                'What happened':reason,'Next step':actions[area]})
        source_labels = {'ur_names':'Historical company names', 'ur_members':'Company owners',
            'ur_stockholders':'Shareholders', 'ur_beneficial_owners':'Beneficial owners', 'ur_officers':'Company officers',
            'ur_insolvency':'Insolvency and legal protection', 'ur_liquidations':'Liquidation records',
            'ur_suspensions':'Register activity restrictions', 'ur_measures':'Registered restrictive measures',
            'ur_sanctions':'Register sanctions records', 'ur_income':'Income statements', 'ur_balance':'Balance sheets',
            'ur_cashflow':'Cash flow statements', 'vid_taxes':'Annual tax payments', 'vid_suspensions':'VID activity restrictions'}
        for check in item.get('quality', []):
            source = check.get('source')
            if source not in source_labels or check.get('status') in {'FOUND','NO_RECORDS'}: continue
            missing_rows.append({'Company':name,'Registration number':reg,'Missing data or unfinished check':source_labels[source],
                'What happened':'This source check did not finish with a usable result.',
                'Next step':'Retry official collection; if unavailable, check the original source manually.'})
        for f in item.get('financials', [])[:3]:
            absent = [label for key,label in [('net_income','profit after tax'),('equity','equity'),('net_turnover','revenue'),('total_assets','assets')] if f.get(key) is None]
            if absent:
                missing_rows.append({'Company':name,'Registration number':reg,'Missing data or unfinished check':'Financial fields ('+str(f.get('year'))+'): '+', '.join(absent),
                    'What happened':'The imported statement does not contain these values.', 'Next step':'Verify the original annual statement.'})
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
      '<p>Score starts at 100: applicable sanctions −100, cartel −30, court dispute −5, other negative event −15. Negative equity: one latest year −15, two consecutive years −20, three consecutive years −30 (not cumulative). Annual loss strictly above EUR 50,000 in any of the latest three reporting years: −50 once, additional to the equity penalty. Minimum 0. Below 70: not recommended. One case is charged once. Historical events do not automatically expire. Missing data does not reduce the score; incomplete checks make the recommendation provisional.</p>',
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
      '<h2>Company Evidence</h2>']
    if db.execute("SELECT 1 FROM sqlite_master WHERE name='web_jobs'").fetchone():
        jobs=[dict(r) for r in db.execute('SELECT job_id,created_at,finished_at,status FROM web_jobs WHERE run_id=? ORDER BY created_at DESC,rowid DESC',(run_id,))]
        from .web_logging import export_log
        log_links=[]
        for job in jobs:
            if (data_dir/'raw/web_logs'/(job['job_id']+'.jsonl')).exists():
                log_path=export_log(data_dir,job['job_id'],path.parent)
                log_links.append('<li><a href="'+esc(log_path.relative_to(path.parent).as_posix())+'">Tavily and model log — '+esc(job['job_id'])+'</a></li>')
        blocks[-1:-1]=['<h2>Adverse Media Jobs</h2>',
          '<p>Each company card shows its latest web job for this official-data run. Previously scored media events are retained until explicitly excluded. Failed or limited searches do not establish absence of adverse information.</p>',table(jobs)]
        if log_links:blocks[-1:-1]=['<ul>'+''.join(log_links)+'</ul>']
    for row in data['companies']:
        reg = row['registration_number']
        item = dict(items[reg])
        blocks.append('<details><summary>'+esc(reg)+' — '+esc(row['name'] or 'Not found in UR')+' ('+esc(row['role'])+')</summary>')
        assessment=payload['companies'][reg]['assessment']
        blocks.append('<p><strong>'+str(assessment['score'])+'/100 · '+esc(assessment['recommendation'])+'</strong></p><p>'+esc(assessment['reason'])+'</p>')
        if assessment['warnings']:
            blocks.append('<ul>'+''.join('<li>'+esc(w)+'</li>' for w in assessment['warnings'])+'</ul>')
        blocks.append(table(item.pop('quality')))
        for name,records in item.items():
            if isinstance(records,list):
                if name=='sanctions_screening':records=screening_rows(records)
                blocks.append('<details><summary>'+esc(display_label(name))+' · '+str(len(records))+'</summary>')
                blocks.append(table(records,exclude={'run_id','raw_json','row_hash','record_key'}))
                if name=='tax_debt' and reg in evidence_links:
                    blocks.append('<p><a href="'+esc(evidence_links[reg])+'">Download official VID PDF</a></p>')
                blocks.append('</details>')
        blocks.append('</details>')
    blocks.append('</html>')
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text('\n'.join(blocks),encoding='utf-8')
    archive = path.parent/('report-'+payload['id']+'.html')
    if not archive.exists():
        archive.write_text(path.read_text(encoding='utf-8').replace(csv_path.name,'report-'+payload['id']+'.csv'),encoding='utf-8')
        (path.parent/('report-'+payload['id']+'.csv')).write_bytes(csv_path.read_bytes())
    return {'report':str(path),'csv':str(csv_path),'workbook_data':str(payload_path),'model_reports':model_reports,'run_id':run_id,
            'assessment_id':payload['id'],'assessment_status':'PROVISIONAL' if any(c['assessment']['provisional'] for c in payload['companies'].values()) else 'CALCULATED'}
