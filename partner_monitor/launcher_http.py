"""Loopback HTTP routes and origin/session guards."""
import json, mimetypes, re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlsplit
from .launcher_utils import within
MAX_BODY=8*1024*1024

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


