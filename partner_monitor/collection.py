import concurrent.futures
import json
import uuid
from pathlib import Path

from .database import connect
from .debt import import_debt
from .downloads import download, replay, write_json
from .inputs import read_companies
from .normalize import calculate_metrics, expand_ownership, import_csv
from .sanctions import import_xml
from .sources import load_sources
from .ur import utc_now


def collect(input_path, data_dir, selected=None, replay_run=None, ownership_depth=2, debt_file=None, refresh_debt=False, refresh_sanctions=False):
    companies = read_companies(input_path)
    all_sources = load_sources()
    if selected and set(selected) & {'ur_balance','ur_income','ur_cashflow'}:
        selected = list(set(selected) | {'ur_financials'})
    sources = [s for s in all_sources if not selected or s['id'] in selected]
    unknown = set(selected or []) - {s['id'] for s in all_sources} - {'vid_debt'}
    if unknown:
        raise ValueError('Unknown sources: ' + ', '.join(sorted(unknown)))
    replay_manifest = None
    if replay_run:
        if not replay_run.isalnum():
            raise ValueError('Invalid replay run ID')
        replay_manifest = json.loads((data_dir/'raw'/'runs'/(replay_run+'.json')).read_text(encoding='utf-8'))
        definitions = {s['id']:s for s in replay_manifest['definitions']}
        sources = [s if refresh_sanctions and s['id'].startswith('fid_') else definitions[s['id']]
                   for s in sources if s['id'] in definitions or refresh_sanctions and s['id'].startswith('fid_')]
    run_id = uuid.uuid4().hex
    db = connect(data_dir,all_sources)
    manifest = {'run_id':run_id,'input':companies,'definitions':sources,'sources':{},'ownership_depth':ownership_depth,
                'replay_of':replay_run,'warnings':[]}
    manifest_path = data_dir/'raw'/'runs'/(run_id+'.json')
    with db:
        db.execute('INSERT INTO monitoring_runs VALUES (?,?,NULL,?,NULL)',(run_id,utc_now(),'RUNNING'))
        for s in sources:
            db.execute('INSERT INTO source_checks(run_id,source,status) VALUES (?,?,?)',(run_id,s['id'],'PENDING'))
    write_json(manifest_path,manifest)
    snapshots,snapshot_ids = {},{}
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
            tasks = {pool.submit(replay,s,data_dir,replay_manifest['sources']) if replay_manifest and not (refresh_sanctions and s['id'].startswith('fid_')) else pool.submit(download,s,data_dir):s for s in sources}
            for task in concurrent.futures.as_completed(tasks):
                s = tasks[task]
                try:
                    meta = task.result()
                    snapshots[s['id']] = meta
                    snapshot_ids[s['id']] = uuid.uuid4().hex
                    manifest['sources'][s['id']] = meta
                    with db:
                        db.execute('INSERT INTO source_snapshots VALUES (?,?,?,?)',
                                   (snapshot_ids[s['id']],run_id,s['id'],json.dumps(meta)))
                        db.execute('UPDATE source_checks SET status=?,snapshot_id=? WHERE run_id=? AND source=?',
                                   ('DOWNLOADED',snapshot_ids[s['id']],run_id,s['id']))
                    print(f"{s['id']}: snapshot {meta['size']} bytes ({meta['origin']})",flush=True)
                except Exception as exc:
                    with db:
                        db.execute('UPDATE source_checks SET status=?,error_type=?,detail=? WHERE run_id=? AND source=?',
                                   ('ERROR',type(exc).__name__,str(exc) if isinstance(exc,ValueError) else 'Download/replay unavailable',run_id,s['id']))
                    print(f"{s['id']}: download ERROR ({type(exc).__name__})",flush=True)
                write_json(manifest_path,manifest)
        roots = {c['registration_number'] for c in companies}
        try:
            depths,truncated = expand_ownership(sources,snapshots,data_dir,roots,ownership_depth)
            if truncated:
                manifest['warnings'].append('OWNERSHIP_DEPTH_LIMIT_REACHED')
        except Exception:
            depths = {n:0 for n in roots}
            manifest['warnings'].append('OWNERSHIP_EXPANSION_FAILED')
        with db:
            for reg,depth in depths.items():
                db.execute('INSERT OR IGNORE INTO companies VALUES (?)',(reg,))
                db.execute('INSERT INTO run_companies VALUES (?,?,?,?)',(run_id,reg,'ROOT' if reg in roots else 'RELATED',depth))
        completed = set()
        # Registry order places statement headers before balance/income/cashflow.
        for s in sources:
            if s['id'] not in snapshots:
                with db:
                    for reg in depths:
                        db.execute('INSERT INTO company_source_checks VALUES (?,?,?,?,?)',(run_id,reg,s['id'],'ERROR',0))
                continue
            meta = snapshots[s['id']]
            try:
                if s['id'] in {'ur_balance','ur_income','ur_cashflow'} and 'ur_financials' not in completed:
                    raise ValueError('Financial statement source did not complete')
                with db:
                    if s['format']=='csv':
                        scanned,counts,as_of = import_csv(db,run_id,s,snapshot_ids[s['id']],data_dir/meta['path'],set(depths))
                        detail = None
                    else:
                        scanned,as_of,detail = import_xml(db,run_id,s,snapshot_ids[s['id']],data_dir/meta['path'])
                        if detail:
                            manifest['warnings'].append(s['id']+': '+detail)
                        counts = {}
                    if as_of:
                        meta['source_as_of'] = as_of
                        db.execute('UPDATE source_snapshots SET metadata_json=? WHERE snapshot_id=?',
                                   (json.dumps(meta),snapshot_ids[s['id']]))
                    for reg in depths:
                        status = ('FOUND' if counts.get(reg) else 'NO_RECORDS') if s['format']=='csv' else 'LOADED'
                        db.execute('INSERT INTO company_source_checks VALUES (?,?,?,?,?)',(run_id,reg,s['id'],status,counts.get(reg,0)))
                    db.execute('UPDATE source_checks SET status=?,rows_scanned=?,rows_imported=?,detail=? WHERE run_id=? AND source=?',
                               ('COMPLETED',scanned,sum(counts.values()) if s['format']=='csv' else scanned,detail,run_id,s['id']))
                completed.add(s['id'])
                print(f"{s['id']}: imported {sum(counts.values()) if s['format']=='csv' else scanned}",flush=True)
            except Exception as exc:
                with db:
                    db.execute('UPDATE source_checks SET status=?,error_type=?,detail=? WHERE run_id=? AND source=?',
                               ('ERROR',type(exc).__name__,str(exc) if isinstance(exc,ValueError) else 'Import failed',run_id,s['id']))
                    for reg in depths:
                        db.execute('INSERT INTO company_source_checks VALUES (?,?,?,?,?)',(run_id,reg,s['id'],'ERROR',0))
                print(f"{s['id']}: import ERROR ({type(exc).__name__})",flush=True)
            write_json(manifest_path,manifest)
        with db:
            calculate_metrics(db,run_id)
        if not selected or 'vid_debt' in selected:
            replay_debt = replay_manifest['sources'].get('vid_debt') if replay_manifest else None
            debt_replay_metadata = None
            if not refresh_debt and not debt_file and replay_debt and replay_debt.get('origin') in {'manual_evidence','browser_evidence'}:
                verified = replay({'id':'vid_debt'},data_dir,replay_manifest['sources'])
                for artifact in verified.get('artifacts',[]):
                    for kind in ('html','pdf'):
                        if kind in artifact:
                            replay({'id':kind},data_dir,{kind:artifact[kind]})
                debt_file = data_dir/verified['path']
                debt_replay_metadata = replay_debt
            with db:
                import_debt(db,run_id,companies,data_dir,debt_file,probe=not bool(replay_run) or refresh_debt,
                            replay_metadata=debt_replay_metadata)
            debt_meta = db.execute("SELECT metadata_json FROM source_snapshots WHERE run_id=? AND source='vid_debt'",(run_id,)).fetchone()
            if debt_meta:
                manifest['sources']['vid_debt'] = json.loads(debt_meta[0])
        if any(s['id'].startswith('fid_') for s in sources):
            from .screening import screen
            with db:
                screening = screen(db,run_id)
            manifest['sanctions_screening'] = screening
            if screening['missing_sources']:
                manifest['warnings'].append('SANCTIONS_SCREENING_INPUTS_MISSING')
            if screening['candidates']:
                manifest['warnings'].append('SANCTIONS_CANDIDATES_REQUIRE_REVIEW')
        remaining = db.execute("SELECT COUNT(*) FROM source_checks WHERE run_id=? AND status!='COMPLETED'",(run_id,)).fetchone()[0]
        status = 'PARTIAL' if remaining or manifest['warnings'] else 'COMPLETED'
        with db:
            db.execute('UPDATE monitoring_runs SET finished_at=?,status=? WHERE run_id=?',(utc_now(),status,run_id))
        manifest['status'] = status
        write_json(manifest_path,manifest)
        return {'run_id':run_id,'status':status,'root_companies':len(roots),'related_companies':len(depths)-len(roots),
                'sources_completed':len(completed),'sources_requiring_attention':remaining,'warnings':manifest['warnings']}
    except BaseException as exc:
        db.rollback()
        with db:
            db.execute('UPDATE monitoring_runs SET finished_at=?,status=?,error_type=? WHERE run_id=?',
                       (utc_now(),'FAILED',type(exc).__name__,run_id))
            db.execute("UPDATE source_checks SET status='ERROR', detail='Run interrupted' WHERE run_id=? AND status IN ('PENDING','DOWNLOADED')",(run_id,))
        raise
    finally:
        db.close()
