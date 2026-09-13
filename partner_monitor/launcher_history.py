"""Local launcher history responsibilities."""
import html, json, os, re, subprocess, zipfile

class LauncherHistory:
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


