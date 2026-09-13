import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import Mock,patch

from partner_monitor.database import connect
from partner_monitor.sources import load_sources
from partner_monitor import media_selection as selection
from partner_monitor.web_media import post_json


class SelectionTests(unittest.TestCase):
    def test_historical_name_dates_are_review_flags_not_automatic_rejections(self):
        company={'name':'New Firm','name_history':[{'name':'Old Firm','date_to':'2020-01-01'}]}
        article={'title':'Old Firm court case','snippet':'A retrospective','publication_date':'2025-01-01'}
        check=selection.name_date_check(company,article)
        self.assertEqual(check['historical_matches'][0]['status'],'AFTER_HISTORICAL_NAME_END')
        self.assertTrue(check['review_required'])
        article['publication_date']='2019-01-01'
        self.assertEqual(selection.name_date_check(company,article)['historical_matches'][0]['status'],'START_DATE_UNKNOWN')
        article['publication_date']=None
        self.assertEqual(selection.name_date_check(company,article)['historical_matches'][0]['status'],'PUBLICATION_DATE_UNKNOWN')

    def test_verifier_uses_fresh_messages_and_explicit_explanation_only(self):
        company={'name':'Example Ltd','historical_names':[]}
        article={'title':'News','url':'https://example.org','snippet':'Example Ltd reported event'}
        response={'choices':[{'finish_reason':'stop','message':{'content':'yes','reasoning_details':[{'type':'reasoning.encrypted','data':'DO_NOT_FORWARD'}]}}]}
        with patch('partner_monitor.web_media.post_json',return_value=response) as post:
            selection.triage_api(company,article,'key','model')
            answer,_=selection.verify_api(company,article,'Explicit explanation only','key','model')
        self.assertEqual(answer,'yes')
        messages=post.call_args.args[2]['messages']
        self.assertEqual([m['role'] for m in messages],['system','user'])
        self.assertNotIn('DO_NOT_FORWARD',json.dumps(messages))
        self.assertIn('Explicit explanation only',messages[1]['content'])
        self.assertIn('name_date_check',messages[1]['content'])

    def test_name_queries_and_word_boundaries(self):
        company={'name':'SIA "SKONTO BŪVE"','historical_names':['AS "Old Firm"'],'registration_number':'12345678901'}
        queries=selection.queries_for(company)
        self.assertTrue(all('12345678901' not in q for q in queries))
        self.assertTrue(any('Old Firm' in q for q in queries))
        self.assertTrue(selection.mentions('SKONTO BUVE: article',company))
        self.assertFalse(selection.mentions('Skonto buvetajs',company))

    def test_triage_payload_never_contains_raw_page(self):
        with patch('partner_monitor.web_media.post_json',return_value={'choices':[{'finish_reason':'stop','message':{'content':json.dumps({'decision':'inspect','reason':'Review'})}}]}) as post:
            selection.triage_api({'name':'Example Ltd','historical_names':[]},
                {'title':'Title','url':'https://example.org','snippet':'s'*5000,'raw_content':'RAW_PAGE_SECRET'},'key','model')
        payload=post.call_args.args[2]
        self.assertNotIn('RAW_PAGE_SECRET',json.dumps(payload))
        self.assertEqual(len(json.loads(payload['messages'][1]['content'])['snippet']),1200)

    def test_extract_contract(self):
        with patch('partner_monitor.web_media.post_json',return_value={'results':[]}) as post:
            selection.extract_api('https://example.org/a','key')
        self.assertEqual(post.call_args.args[0],'https://api.tavily.com/extract')
        self.assertEqual(post.call_args.args[2]['urls'],['https://example.org/a'])

    def test_budget_counts_transport_retries_and_survives_resume(self):
        with tempfile.TemporaryDirectory() as tmp:
            db=connect(Path(tmp),load_sources());selection.initialize(db)
            limits={**selection.LIMITS,'max_model_requests':2}
            token=selection.REQUEST_BUDGET.set(selection.RequestBudget(db,'job','reg',limits,'triage'))
            try:
                with patch('partner_monitor.web_media.requests.post',return_value=Mock(status_code=429)) as post,patch('partner_monitor.web_media.time.sleep'),self.assertRaises(selection.BudgetExceeded):
                    post_json('https://example.org','key',{})
                self.assertEqual(post.call_count,2)
            finally:selection.REQUEST_BUDGET.reset(token)
            with self.assertRaises(selection.BudgetExceeded):selection.RequestBudget(db,'job','reg',limits,'analysis').reserve({})
            db.close()

    def test_excerpt_excludes_distant_text_and_retains_verbatim_evidence(self):
        raw='Navigation\n'+ '\n'.join('Unrelated paragraph '+str(i) for i in range(20))+'\nExample Ltd is investigated.\nNo decision has been made.\n'+ '\n'.join('Footer '+str(i) for i in range(20))
        excerpt,limited=selection.excerpt_for(raw,{'name':'Example Ltd'},1000)
        self.assertIn('Example Ltd is investigated.',excerpt)
        self.assertIn('No decision has been made.',excerpt)
        self.assertNotIn('Unrelated paragraph 0',excerpt)
        self.assertTrue(limited)

    def test_event_links_are_review_candidates_and_keep_findings_separate(self):
        from partner_monitor.web_media import initialize
        with tempfile.TemporaryDirectory() as tmp:
            db=connect(Path(tmp),load_sources());initialize(db);selection.initialize(db)
            db.execute("INSERT INTO monitoring_runs VALUES ('run','2026-09-13',NULL,'COMPLETED',NULL)")
            db.execute("INSERT INTO web_jobs VALUES ('job','run','2026-09-13',NULL,'COMPLETED','{}')")
            for article,event_date in [('a','2025-03-20'),('b','2025-03-20'),('c','2024-03-20')]:
                db.execute('INSERT INTO web_findings VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)',('job','reg',article,0,'insolvency','medium','reported_decision',event_date,
                    'Riga court declared legal protection proceedings for Example Ltd with a two year plan.',
                    'Evidence for '+article,0.8,'https://example.org/'+article,article,'NEEDS_REVIEW'))
            selection.link_events(db,'job','reg')
            links=db.execute('SELECT * FROM web_event_links').fetchall()
            self.assertEqual(len(links),1);self.assertEqual(links[0]['review_status'],'NEEDS_REVIEW')
            self.assertEqual(db.execute('SELECT COUNT(*) FROM web_findings').fetchone()[0],3)
            db.close()
