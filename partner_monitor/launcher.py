"""Loopback-only desktop launcher; Docker remains the pipeline execution environment."""
import argparse
import base64
import html
import json
import mimetypes
import os
from pathlib import Path
import re
import secrets
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlsplit
import webbrowser
import zipfile

ROOT = Path(__file__).resolve().parent.parent
MAX_BODY = 8 * 1024 * 1024


def within(root, name):
    path = (root / name).resolve()
    if not path.is_relative_to(root.resolve()): raise ValueError('Invalid file path')
    return path


def result_json(output):
    # CLI emits a final pretty-printed JSON object after its progress lines.
    starts = [m.start() for m in re.finditer(r'^\{', output, re.M)]
    for start in reversed(starts):
        try: return json.loads(output[start:])
        except json.JSONDecodeError: continue
    return {}


class Launcher:
    def __init__(self, root=ROOT):
        self.root = root
        self.license = (root / 'LICENSE.md').read_text(encoding='utf-8')
        self.sessions = {}
        self.lock = threading.Lock()
        self.job = None
        self.log = ''
        self.process = None
        self.workbook_cache = None
        # Read locally solely for redaction; never return credentials to the browser.
        self.secrets = []
        env_file = root / '.env'
        if env_file.exists():
            for line in env_file.read_text(encoding='utf-8-sig').splitlines():
                key, separator, value = line.partition('=')
                if separator and not key.lstrip().startswith('#') and re.search('KEY|TOKEN|PASSWORD|SECRET', key, re.I):
                    # Redaction candidates only, not an environment-file interpreter.
                    # Docker Compose remains responsible for loading actual credentials.
                    self.secrets.extend([value.strip(),value.split(' #')[0].strip().strip('\"\'')])
        self.secrets += [v for k,v in os.environ.items() if v and re.search('KEY|TOKEN|PASSWORD|SECRET', k, re.I)]

    def clean(self, text):
        for secret in self.secrets:
            if len(secret) >= 4: text = text.replace(secret, '[REDACTED]')
        return re.sub(r'Bearer\s+\S+', 'Bearer [REDACTED]', text, flags=re.I)

    def session(self):
        token = secrets.token_urlsafe(32)
        with self.lock:
            now = time.time()
            self.sessions = {k:v for k,v in self.sessions.items() if now-v['created'] < 86400}
            self.sessions[token] = {'created':now}
        return token

    def authorize(self, token):
        with self.lock:
            session = self.sessions.get(token)
            if not session or time.time()-session['created'] >= 86400: raise PermissionError('Reload the launcher')

    def files(self):
        folder = self.root / 'data/input'
        return sorted(p.name for p in folder.glob('*') if p.is_file() and p.suffix.lower() in {'.csv','.xlsx'} and p.resolve().is_relative_to(folder.resolve()))

    def status(self):
        with self.lock:
            job = dict(self.job) if self.job else None
            log = self.log[-60000:]
        payload = self.root / 'data/reports/latest.workbook.json'
        summary = None
        if payload.exists():
            try:
                data = json.loads(payload.read_text(encoding='utf-8'))
                rows = data['sheets']['Overview']
                summary = {'run_id':data['run_id'],'date':data['created_at'], 'assessment_id':data['id'],
                    'companies':len(rows), 'not_recommended':sum(r['Reliability score'] < 70 for r in rows),
                    'provisional':sum(c['assessment']['provisional'] for c in data['companies'].values()),
                    'methodology':data['version']}
            except (ValueError,KeyError): pass
        reports = self.root / 'data/reports'
        artifacts = [name for name in ['latest.html','latest.csv','latest.xlsx'] if (reports/name).is_file()]
        if 'latest.xlsx' in artifacts:
            workbook = reports/'latest.xlsx'
            key = (workbook.stat().st_mtime_ns, summary['assessment_id'] if summary else None)
            if not self.workbook_cache or self.workbook_cache[0] != key:
                matches = False
                if key[1]:
                    try:
                        with zipfile.ZipFile(workbook) as archive:
                            # Exporter places the assessment ID in the Overview title.
                            for member in ('xl/sharedStrings.xml','xl/worksheets/sheet1.xml'):
                                if member in archive.namelist() and archive.getinfo(member).file_size < 10*1024*1024:
                                    matches = matches or key[1].encode() in archive.read(member)
                    except (OSError,zipfile.BadZipFile): pass
                self.workbook_cache = (key,matches)
            if not self.workbook_cache[1]: artifacts.remove('latest.xlsx')
        return {'job':job, 'log':log, 'inputs':self.files(), 'summary':summary,'artifacts':artifacts}

    def upload(self, request):
        suffix = Path(request.get('name','')).suffix.lower()
        if suffix not in {'.csv','.xlsx'}: raise ValueError('Choose a CSV or XLSX file')
        try: content = base64.b64decode(request.get('content',''), validate=True)
        except (ValueError,TypeError): raise ValueError('Invalid upload')
        if not content or len(content) > 5*1024*1024: raise ValueError('File must be between 1 byte and 5 MB')
        folder = self.root/'data/input'; folder.mkdir(parents=True,exist_ok=True)
        target = folder/('uploaded-'+secrets.token_hex(8)+suffix)
        target.write_bytes(content)
        try:
            from .inputs import read_companies
            count = len(read_companies(target))
        except Exception:
            target.unlink()
            raise ValueError('Invalid company file: require unique 11-digit registration_number values and valid CSV/XLSX headers')
        return {'name':target.name,'companies':count}

    def manual_registrations(self, request):
        if request.get('mode') not in {'collect', 'full'}:
            return None
        source = request.get('input_mode', 'file')
        if source not in {'file', 'single', 'list'}:
            raise ValueError('Invalid company input mode')
        if source == 'file':
            return None
        numbers = [request.get('registration_number')] if source == 'single' else request.get('registration_numbers')
        if not isinstance(numbers, list) or not 1 <= len(numbers) <= 100:
            raise ValueError('Add between 1 and 100 companies')
        if any(not isinstance(n, str) or not re.fullmatch(r'[0-9]{11}', n.strip()) for n in numbers):
            raise ValueError('Registration number must contain exactly 11 digits')
        numbers = [n.strip() for n in numbers]
        if len(set(numbers)) != len(numbers):
            raise ValueError('Duplicate registration numbers are not allowed')
        return numbers

    def saved_report(self, assessment_id):
        if not re.fullmatch(r'[a-f0-9]{24}',assessment_id): raise ValueError('Invalid report ID')
        data=json.loads((self.root/'data/reports/assessments'/(assessment_id+'.json')).read_text(encoding='utf-8'))
        esc=lambda value:html.escape(str(value if value is not None else ''))
        blocks=['<!doctype html><meta charset="utf-8"><title>Saved assessment</title><style>body{font:15px system-ui;margin:30px}table{border-collapse:collapse;width:100%}td,th{border:1px solid #ddd;padding:8px;text-align:left;white-space:pre-wrap}section{overflow:auto}</style>',
            '<h1>Saved assessment</h1><p>'+esc(data['created_at'])+' · '+esc(data['id'])+' · '+esc(data['version'])+'</p>']
        for name,rows in data['sheets'].items():
            blocks.append('<h2>'+esc(name)+'</h2>')
            if not rows: blocks.append('<p>No entries recorded.</p>');continue
            columns=list(rows[0]);blocks.append('<section><table><tr>'+''.join('<th>'+esc(c)+'</th>' for c in columns)+'</tr>')
            blocks.extend('<tr>'+''.join('<td>'+esc(row.get(c))+'</td>' for c in columns)+'</tr>' for row in rows)
            blocks.append('</table></section>')
        return ''.join(blocks)

    def monitoring_reports(self):
        reports=[]
        version=json.loads((self.root/'config/risk_rules.json').read_text())['version']
        for path in (self.root/'data/reports/assessments').glob('*.json'):
            if not re.fullmatch(r'[a-f0-9]{24}',path.stem): continue
            try:
                data=json.loads(path.read_text(encoding='utf-8'))
                rows=data['sheets']['Overview']
                if rows and data['version']==version:
                    reports.append({'id':data['id'],'date':data['created_at'],'companies':len(rows),
                        'names':', '.join(r['Company'] for r in rows[:3])})
            except (ValueError,KeyError): continue
        return sorted(reports,key=lambda r:r['date'],reverse=True)

    def monitoring_request(self, request):
        baseline=request.get('baseline','')
        if not any(r['id']==baseline for r in self.monitoring_reports()):
            raise ValueError('Select a saved report using the current scoring rules')
        data=json.loads((self.root/'data/reports/assessments'/(baseline+'.json')).read_text(encoding='utf-8'))
        numbers=[r['Registration number'] for r in data['sheets']['Overview']]
        if not numbers or any(not re.fullmatch(r'[0-9]{11}',n) for n in numbers) or len(set(numbers))!=len(numbers):
            raise ValueError('Invalid company list in saved report')
        return baseline,numbers

    def company_name(self, request):
        number = request.get('registration_number', '')
        if not isinstance(number, str) or not re.fullmatch(r'[0-9]{11}', number):
            raise ValueError('Registration number must contain exactly 11 digits')
        # Read the Docker volume through the collector; never query external providers.
        script = "import sqlite3,sys,json; c=sqlite3.connect('file:/data/monitoring.db?mode=ro',uri=True); r=c.execute('SELECT name FROM registry WHERE registration_number=? AND name IS NOT NULL ORDER BY rowid DESC LIMIT 1',(sys.argv[1],)).fetchone(); print(json.dumps({'name':r[0] if r else None}))"
        try:
            result = subprocess.run(['docker','compose','run','--rm','-T','--entrypoint','python','collector','-c',script,number],
                cwd=self.root, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=30,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
            if result.returncode: return {'registration_number':number,'name':None,'status':'unavailable'}
            return {'registration_number':number, **json.loads(result.stdout), 'status':'checked'}
        except (OSError, subprocess.TimeoutExpired, ValueError):
            return {'registration_number':number,'name':None,'status':'unavailable'}

    def command(self, request):
        mode = request.get('mode')
        if mode not in {'report','collect','media','full'}: raise ValueError('Invalid workflow')
        numbers = self.manual_registrations(request)
        args = ['docker','compose','run','--rm','-T','collector']
        if mode in {'media','full'}:
            limit = 0  # All root companies; per-company provider budgets still apply.
            args += ['pipeline','--limit',str(limit)]
        elif mode == 'collect': args += ['collect']
        else: args += ['report']
        if mode in {'collect','full'}:
            name = 'manual-companies.csv' if numbers else request.get('input')
            if not numbers and (not isinstance(name,str) or name not in self.files()): raise ValueError('Select an available input file')
            args += ['--input','/input/'+name]
        else:
            run = request.get('run','')
            if not isinstance(run,str): raise ValueError('Invalid collection run ID')
            run = run.strip()
            if run:
                if not re.fullmatch(r'[a-f0-9]{32}',run): raise ValueError('Invalid collection run ID')
                args += ['--run',run]
            elif mode == 'media': raise ValueError('Select a saved collection run')
        return args

    def start(self, request):
        request=dict(request)
        monitoring = request.get('mode') == 'monitor'
        baseline,numbers = self.monitoring_request(request) if monitoring else (None,None)
        if monitoring:
            # The saved Overview is the root scope; related companies are collected afresh.
            args=['docker','compose','run','--rm','-T','collector','pipeline','--limit','0',
                  '--baseline',baseline,'--input','/input/pending.csv']
            request['excel']=True
        else:
            args = self.command(request)
        with self.lock:
            if self.job and self.job['status'] == 'RUNNING': raise ValueError('A workflow is already running')
            numbers = numbers if monitoring else self.manual_registrations(request)
            if numbers:
                folder = self.root/'data/input'
                folder.mkdir(parents=True, exist_ok=True)
                name = 'manual-' + secrets.token_hex(8) + '.csv'
                (folder/name).write_text('registration_number\n' + '\n'.join(numbers) + '\n', encoding='utf-8')
                args[args.index('--input') + 1] = '/input/' + name
            self.log = ''
            self.job = {'id':secrets.token_hex(12),'mode':request['mode'],'status':'RUNNING','started_at':time.strftime('%Y-%m-%d %H:%M:%S'),'stage':'Starting'}
            job = dict(self.job)
        threading.Thread(target=self.worker,args=(args,request),daemon=True).start()
        return job

    def append(self, text):
        safe = self.clean(text)
        with self.lock: self.log = (self.log+safe)[-200000:]
        folder = self.root/'data/reports/launcher-logs';folder.mkdir(parents=True,exist_ok=True)
        with (folder/(self.job['id']+'.log')).open('a',encoding='utf-8') as stream: stream.write(safe)

    def execute(self, args, stage):
        with self.lock: self.job['stage'] = stage
        self.append('\n'+stage+'\n')
        flags = subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0
        process = subprocess.Popen(args,cwd=self.root,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,
            text=True,encoding='utf-8',errors='replace',creationflags=flags,shell=False)
        self.process = process
        output = ''
        for line in process.stdout:
            output = (output+line)[-2000000:]
            self.append(line)
        process.stdout.close()
        code = process.wait(); self.process = None
        return code,result_json(output)

    def worker(self, args, request):
        status = 'FAILED'
        try:
            code, result = self.execute(args,'Running '+request['mode']+' workflow')
            if not result.get('run_id'): raise RuntimeError('Workflow returned no collection run; inspect the execution log')
            outcome = result.get('status')
            if code != 0 and outcome != 'PARTIAL': raise RuntimeError('Workflow failed; inspect the execution log')
            if outcome == 'FAILED': raise RuntimeError('Collection failed')
            status = 'PARTIAL' if outcome == 'PARTIAL' else 'COMPLETED'
            if request['mode']=='collect':
                run = result.get('run_id')
                if not run: raise RuntimeError('Collection did not return a run ID')
                code,result = self.execute(['docker','compose','run','--rm','-T','collector','report','--run',run], 'Generating reports')
                if code: raise RuntimeError('Report generation failed')
            if request.get('excel') is True:
                shell = 'powershell.exe' if os.name=='nt' else 'pwsh'
                code,_ = self.execute([shell,'-NoProfile','-File',str(self.root/'scripts/Finish-Report.ps1'),'-SkipReport'], 'Exporting Excel')
                if code: raise RuntimeError('Excel export failed; HTML/CSV may still be available')
        except Exception as exc:
            self.append('\n'+self.clean(str(exc))+'\n')
            status = 'FAILED'
        finally:
            with self.lock:
                self.job.update(status=status,stage='Finished',finished_at=time.strftime('%Y-%m-%d %H:%M:%S'))


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args): pass

    def send(self, status, body, content_type='application/json; charset=utf-8'):
        if not isinstance(body,bytes): body = (json.dumps(body) if content_type.startswith('application/json') else body).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type',content_type);self.send_header('Content-Length',str(len(body)))
        self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('Referrer-Policy','no-referrer')
        self.end_headers();self.wfile.write(body)

    def guard(self):
        expected = '127.0.0.1:'+str(self.server.server_port)
        if self.headers.get('Host') != expected: raise PermissionError('Use the local launcher URL')
        origin = self.headers.get('Origin')
        if origin and origin != 'http://'+expected: raise PermissionError('Cross-origin requests are not allowed')

    def do_GET(self):
        try:
            self.guard()
            app = self.server.app; path = urlsplit(self.path).path
            if path == '/':
                source = (app.root/'ui/index.html').read_text(encoding='utf-8')
                self.send(200,source.replace('__SESSION_TOKEN__',app.session()),'text/html; charset=utf-8')
            elif path == '/LICENSE.md': self.send(200,app.license,'text/plain; charset=utf-8')
            elif path in {'/app.js','/style.css'}:
                self.send(200,(app.root/'ui'/path[1:]).read_bytes(),'text/javascript' if path.endswith('.js') else 'text/css')
            elif path == '/api/reports':
                app.authorize(self.headers.get('X-Session'))
                self.send(200,app.monitoring_reports())
            elif path == '/api/status':
                app.authorize(self.headers.get('X-Session'))
                self.send(200,app.status())
            elif path.startswith('/reports/'):
                target = within(app.root/'data/reports',unquote(path[len('/reports/'):]))
                match = re.fullmatch(r'report-([a-f0-9]{24})\.html',target.name)
                if not target.exists() and match:
                    self.send(200,app.saved_report(match[1]),'text/html; charset=utf-8');return
                if target.suffix.lower() not in {'.html','.pdf','.csv','.xlsx','.png'} or not target.is_file(): raise ValueError('Report not found')
                self.send(200,target.read_bytes(),mimetypes.guess_type(target.name)[0] or 'application/octet-stream')
            else: self.send(404,{'error':'Not found'})
        except PermissionError as exc: self.send(403,{'error':str(exc)})
        except (ValueError,OSError) as exc: self.send(404,{'error':'File unavailable'})

    def do_POST(self):
        try:
            self.guard(); app = self.server.app
            token = self.headers.get('X-Session')
            app.authorize(token)
            if self.headers.get('Content-Type') != 'application/json': raise ValueError('JSON required')
            size = int(self.headers.get('Content-Length','0'))
            if not 0 < size <= MAX_BODY: raise ValueError('Request too large or empty')
            request = json.loads(self.rfile.read(size))
            if not isinstance(request,dict): raise ValueError('JSON object required')
            if self.path == '/api/start': self.send(202,app.start(request))
            elif self.path == '/api/company-name': self.send(200,app.company_name(request))
            elif self.path == '/api/upload': self.send(200,app.upload(request))
            else: self.send(404,{'error':'Not found'})
        except PermissionError as exc: self.send(403,{'error':str(exc)})
        except (ValueError,TypeError,KeyError) as exc: self.send(400,{'error':str(exc)})
        except OSError: self.send(500,{'error':'Local operation failed; check Docker and folder access'})


class LocalServer(ThreadingHTTPServer):
    # Windows must not silently share a listening port with another launcher.
    allow_reuse_address = False


def main():
    parser = argparse.ArgumentParser(description='Partner Monitor local launcher')
    parser.add_argument('--port',type=int,default=18764)
    parser.add_argument('--no-browser',action='store_true')
    args = parser.parse_args()
    app = Launcher()
    try: server = LocalServer(('127.0.0.1',args.port),Handler)
    except OSError: parser.exit(1,'Launcher port unavailable. Choose another with --port.\n')
    server.app = app
    url = 'http://127.0.0.1:'+str(server.server_port)
    print('Partner Monitor launcher: '+url,flush=True)
    if not args.no_browser: webbrowser.open(url)
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally: server.server_close()


if __name__ == '__main__': main()
