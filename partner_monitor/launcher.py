from .launcher_jobs import LauncherJobs
from .launcher_history import LauncherHistory
from .launcher_http import Handler, LocalServer
from .launcher_utils import within, result_json
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


class Launcher(LauncherJobs, LauncherHistory):
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
