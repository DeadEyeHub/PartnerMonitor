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
    from .report_renderer import render_report
    return render_report(db,run_id,path,baseline)
