import base64
import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest
import zipfile
from unittest.mock import patch
from http.server import ThreadingHTTPServer
from partner_monitor.launcher import Launcher, Handler, LocalServer, within, result_json


class LauncherTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        (self.root/'LICENSE.md').write_text('Test-only license fixture',encoding='utf-8')
        (self.root/'.env').write_text('TAVILY_API_KEY=private-test-secret\n',encoding='utf-8')
        (self.root/'data/input').mkdir(parents=True)
        (self.root/'data/input/companies.csv').write_text('registration_number\n40000000001\n',encoding='utf-8')
        self.app=Launcher(self.root)

    def test_sessions_work_without_license_acceptance(self):
        self.app.authorize(self.app.session())
        with self.assertRaises(PermissionError):self.app.authorize('unknown-token')

    def test_command_validation_and_no_shell_input(self):
        self.assertEqual(self.app.command({'mode':'report'})[-1],'report')
        with self.assertRaises(ValueError):self.app.command({'mode':'full','input':'companies.csv','limit':2})
        args=self.app.command({'mode':'full','input':'companies.csv','limit':2,'paid':True})
        self.assertEqual(args[-2:],['--input','/input/companies.csv'])
        for request in [{'mode':'report','run':'a;echo secret'},{'mode':'collect','input':'../.env'},
                        {'mode':'media','run':'invalid','paid':True}]:
            with self.assertRaises(ValueError):self.app.command(request)
        with self.assertRaises(ValueError):within(self.root/'data/reports','../../.env')

    def test_single_company_input_and_web_limit(self):
        request={'mode':'full','input_mode':'single','registration_number':' 01234567890 ','paid':True,'limit':99}
        args=self.app.command(request)
        self.assertEqual(args[args.index('--limit')+1],'0')
        with patch('partner_monitor.launcher.threading.Thread') as worker:
            self.app.start(request)
        command=worker.call_args.kwargs['args'][0]
        path=self.root/'data/input'/Path(command[-1]).name
        from partner_monitor.inputs import read_companies
        self.assertEqual(len(read_companies(path)),1)
        self.assertEqual(path.read_text(),'registration_number\n01234567890\n')
        before=self.app.files()
        with self.assertRaises(ValueError):self.app.start(request)
        self.assertEqual(before,self.app.files())

    def test_manual_list_creates_one_batch_and_analyzes_every_company(self):
        request={'mode':'full','input_mode':'list','registration_numbers':['01234567890','40003248848'],'paid':True,'limit':1}
        with patch('partner_monitor.launcher.threading.Thread') as worker:
            self.app.start(request)
        args=worker.call_args.kwargs['args'][0]
        self.assertEqual(args[args.index('--limit')+1],'0')
        path=self.root/'data/input'/Path(args[-1]).name
        self.assertEqual(path.read_text().splitlines(),['registration_number','01234567890','40003248848'])

    def test_invalid_manual_lists_do_not_create_files(self):
        before=self.app.files()
        for numbers in [None, [], '40003248848', ['40003248848',' 40003248848 '], ['wrong'], ['40003248848']*101]:
            with self.subTest(numbers=numbers), self.assertRaises(ValueError):
                self.app.start({'mode':'collect','input_mode':'list','registration_numbers':numbers})
        self.assertEqual(before,self.app.files())

    def test_invalid_single_company_never_creates_input(self):
        before=self.app.files()
        for number in ['', '123', '123456789012', '1234567890x', '../12345678', 12345678901, chr(0xff11)*11]:
            with self.subTest(number=number), self.assertRaises(ValueError):
                self.app.start({'mode':'collect','input_mode':'single','registration_number':number})
        with self.assertRaises(ValueError):self.app.command({'mode':'collect','input_mode':'unknown'})
        with self.assertRaises(ValueError):self.app.command({'mode':'full','input_mode':'single','registration_number':'40003248848'})
        self.assertEqual(before,self.app.files())

    def test_upload_validation_and_secret_redaction(self):
        result=self.app.upload({'name':'../test.csv','content':base64.b64encode(b'registration_number\n40000000002\n').decode()})
        self.assertEqual(result['companies'],1)
        self.assertTrue(result['name'].startswith('uploaded-'))
        before=self.app.files()
        with self.assertRaises(ValueError):self.app.upload({'name':'bad.csv','content':base64.b64encode(b'bad\nwrong\n').decode()})
        self.assertEqual(before,self.app.files())
        self.assertNotIn('private-test-secret',self.app.clean('key private-test-secret'))

    def test_partial_workflow_preserved_and_failed_export_is_not_success(self):
        request={'mode':'media','excel':False}
        self.app.job={'id':'test','status':'RUNNING'}
        with patch.object(self.app,'execute',return_value=(2,{'run_id':'a'*32,'status':'PARTIAL'})):
            self.app.worker(['docker'],request)
        self.assertEqual(self.app.job['status'],'PARTIAL')
        request['excel']=True
        with patch.object(self.app,'execute',side_effect=[(0,{'run_id':'a'*32}),(1,{})]):
            self.app.worker(['docker'],request)
        self.assertEqual(self.app.job['status'],'FAILED')

    def test_no_overlapping_jobs_and_json_progress(self):
        self.app.job={'status':'RUNNING'}
        with self.assertRaises(ValueError):self.app.start({'mode':'report'})
        self.assertEqual(result_json('progress\n{\n"run_id":"r",\n"status":"COMPLETED"\n}\n')['status'],'COMPLETED')

    def test_stale_workbook_link_is_hidden(self):
        folder=self.root/'data/reports';folder.mkdir()
        payload={'run_id':'r','created_at':'today','id':'current-assessment','version':'v','sheets':{'Overview':[]},'companies':{}}
        (folder/'latest.workbook.json').write_text(json.dumps(payload))
        workbook=folder/'latest.xlsx'
        with zipfile.ZipFile(workbook,'w') as archive:archive.writestr('xl/sharedStrings.xml','old-assessment')
        self.assertNotIn('latest.xlsx',self.app.status()['artifacts'])
        with zipfile.ZipFile(workbook,'w') as archive:archive.writestr('xl/sharedStrings.xml','current-assessment')
        self.app.workbook_cache=None
        self.assertIn('latest.xlsx',self.app.status()['artifacts'])

    def test_http_acceptance_and_origin_guard(self):
        server=LocalServer(('127.0.0.1',0),Handler);server.app=self.app
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
        token=self.app.session()
        def post(path,body,origin=None):
            conn=http.client.HTTPConnection('127.0.0.1',server.server_port)
            headers={'Content-Type':'application/json','X-Session':token}
            if origin:headers['Origin']=origin
            conn.request('POST',path,json.dumps(body),headers)
            response=conn.getresponse();data=response.read();status=response.status;conn.close()
            return status,json.loads(data)
        self.assertEqual(post('/api/start',{'mode':'report'},'https://foreign.example')[0],403)
        with patch.object(self.app,'start',return_value={'status':'RUNNING'}):
            self.assertEqual(post('/api/start',{'mode':'report'})[0],202)
        with self.assertRaises(OSError):LocalServer(('127.0.0.1',server.server_port),Handler)


if __name__=='__main__':unittest.main()
