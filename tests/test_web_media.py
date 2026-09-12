import hashlib
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from partner_monitor.database import connect
from partner_monitor.sources import load_sources
from partner_monitor.web_media import run_web,validate_analysis,canonical_url,snapshot,queries_for
from partner_monitor.inspection import company,report

REG='40000000001'
BODY='Example Ltd is under investigation. No court judgment has been issued.'


def result():
    return {'identity':'match','identity_reason':'Registration number matches.',
      'findings':[{'finding_type':'legal_dispute','severity':'medium','event_status':'investigation',
        'event_date':None,'summary':'An investigation was reported; no judgment was reported.',
        'evidence_quote':'Example Ltd is under investigation.','confidence':0.8}]}


class WebMediaTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        db=connect(self.root,load_sources())
        db.execute("INSERT INTO monitoring_runs VALUES ('r','2026-09-12',NULL,'COMPLETED',NULL)")
        db.execute('INSERT INTO companies VALUES (?)',(REG,))
        db.execute("INSERT INTO run_companies VALUES ('r',?,'ROOT',0)",(REG,))
        db.execute("INSERT INTO source_snapshots VALUES ('s','r','fixture','{}')")
        db.execute("INSERT INTO registry (run_id,registration_number,snapshot_id,source_row,record_key,row_hash,raw_json,name) VALUES ('r',?,'s',1,'key','hash','{}','Example Ltd')",(REG,))
        db.commit();db.close()
        env=patch.dict(os.environ,{'TAVILY_API_KEY':'test-placeholder','OPENROUTER_API_KEY':'test-placeholder','OPENROUTER_MODEL':'fixture/model'})
        env.start();self.addCleanup(env.stop)

    def test_end_to_end_dedup_resume_report_and_evidence(self):
        response={'results':[{'url':'https://example.org/story?utm_source=x','title':'Investigation','raw_content':BODY}]}
        with redirect_stdout(io.StringIO()),patch('partner_monitor.web_media.search_api',return_value=response),patch('partner_monitor.web_media.analyze_api',return_value=(result(),{'response':result()})) as llm:
            output=run_web(self.root,'r')
        self.assertEqual(output['status'],'COMPLETED');self.assertEqual(output['findings'],1)
        self.assertEqual(llm.call_count,1)
        with redirect_stdout(io.StringIO()),patch('partner_monitor.web_media.search_api',side_effect=AssertionError('No calls on resume')),patch('partner_monitor.web_media.analyze_api',side_effect=AssertionError('No calls on resume')):
            resumed=run_web(self.root,job_id=output['job_id'])
        self.assertEqual(resumed['findings'],1)
        db=connect(self.root,load_sources());self.addCleanup(db.close)
        data=company(db,'r',REG)
        self.assertEqual(data['web_findings'][0]['event_status'],'investigation')
        self.assertEqual(data['web_findings'][0]['review_status'],'NEEDS_REVIEW')
        report(db,'r',self.root/'report.html')
        self.assertIn('An investigation was reported',(self.root/'report.html').read_text(encoding='utf-8'))
        for p in (self.root/'raw/web').glob('*.json'):
            self.assertEqual(hashlib.sha256(p.read_bytes()).hexdigest(),p.stem)

    def test_search_then_analyze_and_snippet_gap(self):
        response={'results':[{'url':'https://example.org/story','title':'News','content':BODY}]}
        with redirect_stdout(io.StringIO()),patch('partner_monitor.web_media.search_api',return_value=response):
            job=run_web(self.root,'r',mode='search')
        self.assertEqual(job['status'],'SEARCHED')
        with redirect_stdout(io.StringIO()),patch('partner_monitor.web_media.analyze_api',side_effect=AssertionError('Snippets not analyzed')):
            job=run_web(self.root,job_id=job['job_id'],mode='analyze')
        self.assertEqual(job['status'],'PARTIAL');self.assertEqual(job['findings'],0)

    def test_fabricated_quote_wrong_identity_bad_date_rejected(self):
        for change in ['quote','identity','date','confidence']:
            data=result()
            if change=='quote':data['findings'][0]['evidence_quote']='This is a fabricated quotation.'
            if change=='identity':data['identity']='uncertain'
            if change=='date':data['findings'][0]['event_date']='2026-02-30'
            if change=='confidence':data['findings'][0]['confidence']=True
            with self.subTest(change=change),self.assertRaises(ValueError):validate_analysis(data,BODY)

    def test_failed_search_does_not_become_no_risk(self):
        with redirect_stdout(io.StringIO()),patch('partner_monitor.web_media.search_api',side_effect=RuntimeError('offline')):
            job=run_web(self.root,'r')
        self.assertEqual(job['status'],'PARTIAL')

    def test_missing_key_fails_before_api(self):
        with patch.dict(os.environ,{'TAVILY_API_KEY':''}),patch('partner_monitor.web_media.search_api') as api,self.assertRaises(ValueError):
            run_web(self.root,'r')
        api.assert_not_called()

    def test_invalid_model_evidence_is_saved_but_not_published(self):
        response={'results':[{'url':'https://example.org/story','raw_content':BODY}]}
        invalid=result();invalid['findings'][0]['evidence_quote']='Invented evidence unsupported by the article.'
        with redirect_stdout(io.StringIO()),patch('partner_monitor.web_media.search_api',return_value=response),patch('partner_monitor.web_media.analyze_api',return_value=(invalid,{'response':invalid})):
            job=run_web(self.root,'r')
        self.assertEqual(job['status'],'PARTIAL');self.assertEqual(job['findings'],0)
        db=connect(self.root,load_sources());self.addCleanup(db.close)
        row=db.execute('SELECT analysis_status,analysis_path FROM web_articles').fetchone()
        self.assertEqual(row[0],'ERROR');self.assertTrue((self.root/row[1]).exists())

    def test_provider_request_contracts(self):
        from partner_monitor.web_media import analyze_api,search_api
        with patch('partner_monitor.web_media.post_json',return_value={'results':[]}) as post:
            search_api('company','placeholder',5)
        self.assertEqual(post.call_args.args[0],'https://api.tavily.com/search')
        self.assertTrue(post.call_args.args[2]['include_raw_content'])
        with patch('partner_monitor.web_media.post_json',return_value={'choices':[{'finish_reason':'stop','message':{'content':json.dumps(result())}}]}) as post:
            output,audit=analyze_api({'name':'Example'},{'content':BODY},'placeholder','fixture/model')
        self.assertEqual(output,result())
        self.assertEqual(post.call_args.args[2]['response_format']['type'],'json_schema')
        self.assertNotIn('Authorization',json.dumps(audit))

    def test_url_safety_and_historical_queries(self):
        self.assertEqual(canonical_url('https://Example.org/a?utm_source=x&id=1#fragment'),'https://example.org/a?id=1')
        for url in ['javascript:alert(1)','file:///etc/passwd','https://user:password@example.org']:
            with self.assertRaises(ValueError):canonical_url(url)
        queries=queries_for({'name':'New Name','historical_names':['Old Name'],'registration_number':REG})
        self.assertTrue(any('Old Name' in q for q in queries))
        self.assertLessEqual(len(queries),5)
