"""Local launcher jobs responsibilities."""
import json, os, re, secrets, subprocess, threading, time
from .launcher_utils import result_json

class LauncherJobs:
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


