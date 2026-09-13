"""Readable saved model outcomes, without prompts or provider metadata."""
import html
from pathlib import Path
from .web_logging import atomic_text, page


def export_final_results(db,job_id,path):
    job=db.execute('SELECT * FROM web_jobs WHERE job_id=?',(job_id,)).fetchone()
    if not job:raise ValueError('Web job not found')
    esc=lambda value:html.escape(str(value or ''),quote=True)
    blocks=['<p>Saved model outcomes for this test. Company summaries group article-level results; they are not a new model verdict or a legal determination. Reported historical events do not establish current involvement.</p>']
    checks=db.execute('SELECT c.*,r.name FROM web_checks c LEFT JOIN registry r ON r.run_id=? AND r.registration_number=c.registration_number WHERE c.job_id=? ORDER BY c.registration_number',(job['run_id'],job_id)).fetchall()
    for check in checks:
        reg=check['registration_number']
        findings=db.execute('SELECT * FROM web_findings WHERE job_id=? AND registration_number=? ORDER BY source_url,finding_index',(job_id,reg)).fetchall()
        blocks.append('<section><h2>'+esc(check['name'])+' · '+esc(reg)+'</h2>')
        if findings:
            blocks.append('<p><strong>Reported adverse events identified — manual review required.</strong></p>')
        else:
            blocks.append('<p><strong>No adverse event extracted in this test. Involvement is not established; this is not confirmation that no adverse activity exists.</strong></p>')
        blocks.append('<p>Check coverage: '+esc(check['analysis_status'])+'.</p>')
        for f in findings:
            blocks.append('<article><h3>'+esc(f['finding_type'].replace('_',' '))+' — '+esc(f['event_status'].replace('_',' '))+'</h3><p>'+esc(f['summary'])+'</p><p>Event date: '+esc(f['event_date'] or 'Not established')+' · Review: '+esc(f['review_status'])+'</p><p><a href="'+esc(f['source_url'])+'">Source publication</a></p></article>')
        judgments=db.execute('''SELECT a.title,a.url,a.analysis_status,j.verdict FROM web_articles a
          LEFT JOIN web_article_judgment j USING(job_id,registration_number,article_id)
          WHERE a.job_id=? AND a.registration_number=? AND a.analysis_status!='FILTERED'
          ORDER BY a.url''',(job_id,reg)).fetchall()
        if judgments:
            blocks.append('<h3>Final relevance decisions by publication</h3><p>Yes means sufficient relevance for further analysis, not proven involvement. No means insufficient grounds to proceed.</p><table><tr><th>Publication</th><th>Model verdict</th><th>Assessment outcome</th></tr>')
            for j in judgments:
                blocks.append('<tr><td><a href="'+esc(j['url'])+'">'+esc(j['title'] or j['url'])+'</a></td><td>'+esc({'да':'yes','нет':'no'}.get(j['verdict'],j['verdict']) or 'No final answer')+'</td><td>'+esc(j['analysis_status'])+'</td></tr>')
            blocks.append('</table>')
        blocks.append('</section>')
    atomic_text(Path(path),page('Final Model Results',''.join(blocks)))
    return str(path)
