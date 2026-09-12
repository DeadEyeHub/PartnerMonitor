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
from partner_monitor.web_media import run_web,validate_analysis,canonical_url,snapshot,queries_for,plan_web,web_status
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
        triage=patch('partner_monitor.media_selection.triage_api',return_value=({'decision':'inspect','reason':'Potential company event'},{}))
        triage.start();self.addCleanup(triage.stop)
        extract=patch('partner_monitor.media_selection.extract_api',return_value={'results':[],'failed_results':[]})
        extract.start();self.addCleanup(extract.stop)

    def test_end_to_end_dedup_resume_report_and_evidence(self):
        response={'results':[{'url':'https://example.org/story?utm_source=x','title':'Investigation','content':BODY,'raw_content':BODY}]}
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
        self.assertIn('Tavily and model log',(self.root/'report.html').read_text(encoding='utf-8'))
        log_path=Path(output['log_html'])
        self.assertTrue(log_path.exists())
        self.assertIn('ANALYSIS_VALIDATED',log_path.read_text(encoding='utf-8'))
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
        db=connect(self.root,load_sources());self.addCleanup(db.close)
        self.assertEqual(web_status(db,job['job_id'])['checks'][0]['analysis_status'],'PARTIAL')

    def test_dry_run_is_read_only_and_contains_no_keys(self):
        path=self.root/'monitoring.db';before=path.read_bytes()
        with patch('partner_monitor.web_media.post_json',side_effect=AssertionError('No network')):
            plan=plan_web(self.root,'r')
        self.assertEqual(plan['companies'][0]['company']['registration_number'],REG)
        self.assertNotIn('test-placeholder',json.dumps(plan))
        self.assertEqual(before,path.read_bytes())

    def test_full_text_upgrades_identical_snippet(self):
        for same_url in (True,False):
            responses=[{'results':[{'url':'https://example.org/snippet','content':BODY}]},
                       {'results':[{'url':'https://example.org/snippet' if same_url else 'https://example.org/full','content':BODY,'raw_content':BODY}]},
                       {'results':[]}]
            with self.subTest(same_url=same_url),redirect_stdout(io.StringIO()),patch('partner_monitor.web_media.search_api',side_effect=responses),patch('partner_monitor.web_media.analyze_api',return_value=(result(),{})) as llm:
                job=run_web(self.root,'r')
            self.assertEqual(job['status'],'COMPLETED' if same_url else 'PARTIAL');self.assertEqual(llm.call_count,1)

    def test_unrelated_snippet_does_not_send_raw_text_to_model(self):
        response={'results':[{'url':'https://example.org/unrelated','title':'Generic court homepage',
            'content':'General legal advice','raw_content':BODY*1000}]}
        with redirect_stdout(io.StringIO()),patch('partner_monitor.web_media.search_api',return_value=response),patch('partner_monitor.media_selection.triage_api') as triage,patch('partner_monitor.web_media.analyze_api') as llm:
            job=run_web(self.root,'r')
        triage.assert_not_called();llm.assert_not_called()
        db=connect(self.root,load_sources());self.addCleanup(db.close)
        self.assertEqual(web_status(db,job['job_id'])['quality'][0]['filtered'],1)

    def test_triage_reject_does_not_extract_or_create_findings(self):
        response={'results':[{'url':'https://example.org/a','content':BODY}]}
        with redirect_stdout(io.StringIO()),patch('partner_monitor.web_media.search_api',return_value=response),patch('partner_monitor.media_selection.triage_api',return_value=({'decision':'reject','reason':'Generic directory'},{})),patch('partner_monitor.media_selection.extract_api') as extract,patch('partner_monitor.web_media.analyze_api') as llm:
            job=run_web(self.root,'r')
        self.assertEqual(job['findings'],0);extract.assert_not_called();llm.assert_not_called()

    def test_selected_article_extracts_only_short_name_context(self):
        response={'results':[{'url':'https://example.org/a','title':'Example Ltd investigation','content':BODY}]}
        raw='Unrelated background.\n'*200+'\n'+BODY+'\nA related continuation.\n'+'Other material.\n'*200
        with redirect_stdout(io.StringIO()),patch('partner_monitor.web_media.search_api',return_value=response),patch('partner_monitor.media_selection.extract_api',return_value={'results':[{'url':'https://example.org/a','raw_content':raw}]}),patch('partner_monitor.web_media.analyze_api',return_value=(result(),{})) as llm:
            job=run_web(self.root,'r')
        sent=llm.call_args.args[1]['content']
        self.assertIn(BODY,sent);self.assertLess(len(sent),5000);self.assertNotEqual(sent,raw)
        self.assertEqual(job['findings'],1)

    def test_uncertain_triage_is_partial_without_extraction(self):
        response={'results':[{'url':'https://example.org/a','content':BODY}]}
        with redirect_stdout(io.StringIO()),patch('partner_monitor.web_media.search_api',return_value=response),patch('partner_monitor.media_selection.triage_api',return_value=({'decision':'uncertain','reason':'Ambiguous name'},{})),patch('partner_monitor.media_selection.extract_api') as extract:
            job=run_web(self.root,'r')
        self.assertEqual(job['status'],'PARTIAL');extract.assert_not_called()

    def test_resume_pins_model_and_does_not_repeat_successful_searches(self):
        response={'results':[{'url':'https://example.org/story','content':BODY,'raw_content':BODY}]}
        with redirect_stdout(io.StringIO()),patch('partner_monitor.web_media.search_api',return_value=response):
            job=run_web(self.root,'r',mode='search')
        with redirect_stdout(io.StringIO()),patch.dict(os.environ,{'OPENROUTER_MODEL':'changed/model'}),patch('partner_monitor.web_media.search_api',side_effect=AssertionError('No repeated search')),patch('partner_monitor.web_media.analyze_api',return_value=(result(),{})) as llm:
            run_web(self.root,job_id=job['job_id'])
        self.assertEqual(llm.call_args.args[-1],'fixture/model')

    def test_pipeline_drains_pending_articles_and_exports(self):
        from partner_monitor.workflow import run_workflow
        results=[{'url':'https://example.org/'+str(i),'content':BODY,'raw_content':BODY+' Article '+str(i)} for i in range(12)]
        responses=[{'results':results[i:i+5]} for i in range(0,12,5)]
        with redirect_stdout(io.StringIO()),patch('partner_monitor.web_media.search_api',side_effect=responses),patch('partner_monitor.web_media.analyze_api',return_value=(result(),{})) as llm:
            job=run_workflow(self.root,self.root/'report.html',run_id='r')
        self.assertEqual(job['status'],'PARTIAL');self.assertEqual(llm.call_count,5)
        self.assertTrue((self.root/'report.csv').exists())
        self.assertIn('Registration number matches',(self.root/'report.html').read_text(encoding='utf-8'))
        with redirect_stdout(io.StringIO()),patch('partner_monitor.web_media.search_api',side_effect=AssertionError('No network')),patch('partner_monitor.web_media.analyze_api',side_effect=AssertionError('No network')):
            resumed=run_workflow(self.root,self.root/'report.html',job_id=job['job_id'])
        self.assertEqual(resumed['web']['findings'],5)
        self.assertEqual(resumed['web']['quality'][0]['budget_limited'],7)

    def test_pipeline_partial_still_exports_and_search_failure_is_visible(self):
        from partner_monitor.workflow import run_workflow
        with redirect_stdout(io.StringIO()),patch('partner_monitor.web_media.search_api',side_effect=RuntimeError('offline')):
            job=run_workflow(self.root,self.root/'partial.html',run_id='r')
        self.assertEqual(job['status'],'PARTIAL')
        self.assertEqual(job['web']['errors'][0]['stage'],'search')
        self.assertTrue((self.root/'partial.html').exists())

    def test_pipeline_preflights_before_collection(self):
        from partner_monitor.workflow import run_workflow
        with patch.dict(os.environ,{'TAVILY_API_KEY':''}),patch('partner_monitor.workflow.collect') as collector,self.assertRaises(ValueError):
            run_workflow(self.root,self.root/'report.html',input_path=Path('input.csv'))
        collector.assert_not_called()

    def test_missing_key_fails_before_api(self):
        with patch.dict(os.environ,{'TAVILY_API_KEY':''}),patch('partner_monitor.web_media.search_api') as api,self.assertRaises(ValueError):
            run_web(self.root,'r')
        api.assert_not_called()

    def test_invalid_model_evidence_is_saved_but_not_published(self):
        response={'results':[{'url':'https://example.org/story','content':BODY,'raw_content':BODY}]}
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
        self.assertFalse(post.call_args.args[2]['include_raw_content'])
        with patch('partner_monitor.web_media.post_json',return_value={'choices':[{'finish_reason':'stop','message':{'content':json.dumps(result())}}]}) as post:
            output,audit=analyze_api({'name':'Example'},{'content':BODY},'placeholder','fixture/model')
        self.assertEqual(output,result())
        self.assertEqual(post.call_args.args[2]['response_format']['type'],'json_schema')
        self.assertNotIn('temperature',post.call_args.args[2])
        self.assertNotIn('Authorization',json.dumps(audit))

    def test_url_safety_and_historical_queries(self):
        self.assertEqual(canonical_url('https://Example.org/a?utm_source=x&id=1#fragment'),'https://example.org/a?id=1')
        for url in ['javascript:alert(1)','file:///etc/passwd','https://user:password@example.org']:
            with self.assertRaises(ValueError):canonical_url(url)
        queries=queries_for({'name':'SIA "New Name"','historical_names':['Old Name','Older Name'],'registration_number':REG})
        self.assertTrue(any('Old Name' in q for q in queries))
        self.assertTrue(any('Older Name' in q for q in queries))
        self.assertTrue(any('"New Name"' in q for q in queries))
        self.assertLessEqual(len(queries),6)
        self.assertTrue(all(REG not in query for query in queries))
