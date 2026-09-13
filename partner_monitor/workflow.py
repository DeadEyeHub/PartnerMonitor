"""Official collection, bounded media processing and report orchestration."""
import os
from pathlib import Path

from .collection import collect
from .inspection import open_database, resolve_run, report
from .web_media import run_web, web_status


def run_workflow(data_dir, output, *, run_id=None, job_id=None, input_path=None,
                 replay=None, limit=3, analysis_passes=3):
    if sum(value is not None for value in (run_id,job_id,input_path)) != 1:
        raise ValueError('Choose exactly one of --run, --job or --input')
    if replay and not input_path:
        raise ValueError('--replay requires --input')
    if not 1 <= limit <= 100 or not 1 <= analysis_passes <= 3:
        raise ValueError('Limit must be 1..100 and analysis passes 1..3')
    # Validate before an expensive collection starts. Never return key values.
    for name in ('TAVILY_API_KEY','OPENROUTER_API_KEY'):
        if not os.getenv(name,'').strip():
            raise ValueError('Set '+name+' in .env')
    if not os.getenv('OPENROUTER_MODEL','openai/gpt-4.1-mini').strip():
        raise ValueError('Set OPENROUTER_MODEL in .env')
    from .media_selection import configured_limits
    configured_limits()
    collection_result = None
    if input_path:
        collection_result = collect(input_path,data_dir,replay_run=replay,
                                    debt_file=os.getenv('TAX_DEBT_FILE') or None)
        run_id = collection_result['run_id']
    web = run_web(data_dir,run_id,job_id,limit=limit,log_dir=Path(output).parent)
    run_id, job_id = web['run_id'], web['job_id']
    # Continue the remaining articles; do not repeatedly retry permanent errors.
    for _ in range(1,analysis_passes):
        db = open_database(data_dir)
        try:
            pending = db.execute("SELECT COUNT(*) FROM web_articles WHERE job_id=? AND analysis_status='PENDING'",(job_id,)).fetchone()[0]
        finally:
            db.close()
        if not pending:
            break
        web = run_web(data_dir,job_id=job_id,mode='analyze',retry_errors=False,log_dir=Path(output).parent)
    db = open_database(data_dir)
    try:
        official = db.execute('SELECT status FROM monitoring_runs WHERE run_id=?',(resolve_run(db,run_id),)).fetchone()[0]
        diagnostics = web_status(db,job_id)
        artifacts = report(db,run_id,Path(output))
        from .final_media_report import export_final_results
        artifacts['final_model_results']=export_final_results(db,job_id,Path(output).parent/('model-final-'+job_id+'.html'))
    finally:
        db.close()
    return {'run_id':run_id,'job_id':job_id,
            'status':'COMPLETED' if official=='COMPLETED' and web['status']=='COMPLETED' else 'PARTIAL',
            'official_status':official,'web_status':web['status'],
            'collection':collection_result,'web':diagnostics,'artifacts':artifacts,
            'assessment_status':artifacts['assessment_status']}
