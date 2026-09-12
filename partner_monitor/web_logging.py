"""Durable, redacted provider events and live, script-free HTML logs."""
import html
import json
import os
import re
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path

ACTIVE = ContextVar('web_log', default=None)


def redact(value):
    if isinstance(value, dict):
        return {str(k): ('[REDACTED]' if re.search(r'authorization|cookie|password|secret|api[_-]?key|access[_-]?token',str(k),re.I) else redact(v)) for k,v in value.items()}
    if isinstance(value, list):
        return [redact(v) for v in value]
    if isinstance(value, str):
        for name,secret in os.environ.items():
            if re.search(r'KEY|TOKEN|PASSWORD|SECRET',name,re.I) and len(secret.strip())>=6:
                value=value.replace(secret,'[REDACTED]')
        return re.sub(r'Bearer\s+[^\s"<>]+','Bearer [REDACTED]',value,flags=re.I)
    return value


def atomic_text(path, text):
    path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_suffix(path.suffix+'.tmp')
    tmp.write_text(text,encoding='utf-8')
    tmp.replace(path)


def page(title,body):
    return ('<!doctype html><html lang="en"><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width">'
            '<title>'+html.escape(title)+'</title><style>'
            'body{font:15px system-ui;margin:28px;color:#172435;background:#f5f7fa}'
            'table{border-collapse:collapse;width:100%;background:white}td,th{padding:8px;border:1px solid #ccd6df;text-align:left}'
            'pre{white-space:pre-wrap;overflow-wrap:anywhere;background:white;padding:18px}'
            'a{color:#075c9a}</style><h1>'+html.escape(title)+'</h1>'+body+'</html>')


def render_event(event, folder):
    name=str(event['sequence']).zfill(6)+'.html'
    atomic_text(folder/name,page(event['event'], '<p><a href="index.html">Back to event log</a></p><pre>'+html.escape(json.dumps(event,ensure_ascii=False,indent=2))+'</pre>'))


def render_index(events,folder,job_id):
    rows=[]
    for e in events:
        values=[e['time'],e.get('company',''),e.get('stage',''),e.get('event',''),e.get('duration_ms',''),e.get('http_status','')]
        rows.append('<tr><td><a href="'+str(e['sequence']).zfill(6)+'.html">'+str(e['sequence'])+'</a></td>'+''.join('<td>'+html.escape(str(v))+'</td>' for v in values)+'</tr>')
    atomic_text(folder/'index.html',page('Tavily and Model Log — '+job_id,
        '<p>Updated after every event. Refresh this page during execution. Times are UTC. Open an event number for complete request/response details. Credentials are redacted. Only provider-returned data is available; internal model execution is not observable.</p>'
        '<table><thead><tr><th>Details</th><th>Time</th><th>Company</th><th>Stage</th><th>Event</th><th>Duration ms</th><th>HTTP</th></tr></thead><tbody>'+''.join(rows)+'</tbody></table>'))


def read_events(path):
    if not path.exists():return []
    events=[]
    for line in path.read_text(encoding='utf-8').splitlines():
        try:events.append(json.loads(line))
        except json.JSONDecodeError:continue  # A killed process can leave a partial final line.
    return events


def export_log(data_dir,job_id,report_dir):
    if not re.fullmatch(r'[A-Za-z0-9_-]+',job_id):raise ValueError('Invalid job ID')
    events=read_events(Path(data_dir)/'raw/web_logs'/(job_id+'.jsonl'))
    folder=Path(report_dir)/'web-logs'/job_id
    for event in events:render_event(redact(event),folder)
    render_index(events,folder,job_id)
    return folder/'index.html'


class WebLog:
    def __init__(self,data_dir,job_id,report_dir=None):
        if not re.fullmatch(r'[A-Za-z0-9_-]+',job_id):raise ValueError('Invalid job ID')
        self.job_id=job_id
        self.path=Path(data_dir)/'raw/web_logs'/(job_id+'.jsonl')
        self.folder=Path(report_dir or os.getenv('REPORT_DIR') or Path(data_dir)/'reports')/'web-logs'/job_id
        self.events=read_events(self.path)
        self.context={}
        for event in self.events:
            if not (self.folder/(str(event['sequence']).zfill(6)+'.html')).exists():
                render_event(redact(event),self.folder)

    @contextmanager
    def scope(self,**context):
        previous=self.context
        self.context={**previous,**context}
        token=ACTIVE.set(self)
        try:yield
        finally:
            ACTIVE.reset(token)
            self.context=previous

    def emit(self,event,**details):
        entry=redact({'sequence':len(self.events)+1,'time':datetime.now(timezone.utc).isoformat(timespec='milliseconds'),
                      'job_id':self.job_id,**self.context,'event':event,**details})
        self.path.parent.mkdir(parents=True,exist_ok=True)
        with self.path.open('a',encoding='utf-8') as stream:
            stream.write('\n'+json.dumps(entry,ensure_ascii=False)+'\n')
            stream.flush()
            os.fsync(stream.fileno())
        self.events.append(entry)
        render_event(entry,self.folder)
        render_index(self.events,self.folder,self.job_id)
        print('web log: '+entry['time']+' '+str(entry.get('company',''))+' '+event,flush=True)


def emit(event,**details):
    logger=ACTIVE.get()
    if logger:logger.emit(event,**details)
