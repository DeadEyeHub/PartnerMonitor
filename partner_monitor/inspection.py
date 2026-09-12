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
    if db.execute("SELECT 1 FROM sqlite_master WHERE name='sanctions_screening'").fetchone():
        for name in ('sanctions_screening','sanctions_candidates'):
            result[name] = [dict(r) for r in db.execute(f'SELECT * FROM {name} WHERE run_id=? AND registration_number=?',(run_id,registration_number))]
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


def report(db,run_id,path):
    data = summary(db,run_id)
    from .overview import overview_rows,screening_rows,export_csv
    overview=overview_rows(db,run_id,data['companies'],company)
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
        return '<div class="scroll"><table><thead><tr>'+''.join('<th title="'+esc(c)+'">'+esc(display_label(c))+'</th>' for c in columns)+'</tr></thead><tbody>'+''.join('<tr>'+''.join('<td>'+esc(row.get(c))+'</td>' for c in columns)+'</tr>' for row in rows)+'</tbody></table></div>'
    blocks = ['<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>Partner Monitor — Data</title>',
      '<style>body{font:15px system-ui;margin:32px;background:#f5f7fa;color:#172435}h1,h2{color:#133b55}table{border-collapse:collapse;background:white;width:100%}td,th{padding:9px;border:1px solid #dce3ea;text-align:left;vertical-align:top}th{background:#e8eff6}details{margin:12px 0;padding:12px;background:white;border:1px solid #dce3ea}summary{cursor:pointer;font-weight:600}.scroll{overflow:auto}.muted{color:#596574}a{color:#075c9a}</style>',
      '<h1>Partner Monitoring Report</h1>',
      '<p>Official-data run '+esc(run_id)+' · '+esc(data['run']['started_at'])+' · '+esc(data['run']['status'])+'</p>',
      '<p>Risk scores and risk classes have not yet been calculated. Blank scores and new-findings counts mean not assessed, not zero.</p>',
      '<p><a href="'+esc(csv_path.name)+'">Download compact CSV</a></p>',
      '<h2>Overview</h2>',table(overview),
      '<p>Coverage is the percentage of seven equally weighted data areas available: UR identity, VID rating, VAT lookup, financial data, tax debt, sanctions name screening and web analysis. It measures available checks, not reliability. Main reasons are selected recorded facts, not a complete risk assessment.</p>',
      '<h2>Sanctions Screening</h2>',
      '<p>EU, UN and Latvian lists are supplied by FID, the agreed source for this stage. File publication dates are informational: an old date alone does not make screening incomplete. Download failures, missing inputs and invalid dates still require attention.</p>',
      '<p>Names of companies, former company names, owners, shareholders, beneficial owners and officers are compared. A name candidate requires identity review. Cross-script transliteration and sectoral restrictions are not checked. Related companies are screened separately; ownership or control does not automatically transfer a result to another company.</p>',
      table(screening_rows(data['sanctions_screening'])) if data['sanctions_screening'] else '<p>Not performed for this run.</p>',
      '<details><summary>Source dates and import details</summary>',
      table([{k:v for k,v in r.items() if k in {'source','status','rows_imported','retrieved_at','source_as_of','detail'}} for r in data['sources']]),'</details>',
      '<h2>Company Evidence</h2>']
    for row in data['companies']:
        reg = row['registration_number']
        item = company(db,run_id,reg)
        blocks.append('<details><summary>'+esc(reg)+' — '+esc(row['name'] or 'Not found in UR')+' ('+esc(row['role'])+')</summary>')
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
    return {'report':str(path),'csv':str(csv_path),'run_id':run_id}
