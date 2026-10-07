import json
import sqlite3
import tempfile
import unittest
from unittest.mock import patch, MagicMock
from partner_monitor.event_grouping import units_for, validate, group_company, enrich
from partner_monitor.assessment import assess
from tests.test_assessment import finding, item


class EventGroupingTests(unittest.TestCase):
    def evidence(self):
        a=finding(quote='The authority imposed a cartel fine in the bridge procurement.')
        b=finding('legal_dispute','The cartel appeal was rejected.','The court upheld the same bridge procurement cartel fine.')
        b['source_url']='https://example.org/appeal'
        c=finding('legal_dispute','Unrelated invoice claim.','A supplier sued over a separate unpaid invoice.')
        return [a,b,c]

    def test_partition_validation(self):
        units=units_for(self.evidence()); ids=[u['id'] for u in units]
        valid={'groups':[{'members':ids[:2],'reason':'Same proceeding'},{'members':ids[2:],'reason':'Separate'}]}
        self.assertEqual(validate(valid,units),valid)
        for members in (ids[:2],ids+[ids[0]],ids+['invented']):
            with self.assertRaises(ValueError):validate({'groups':[{'members':members,'reason':'Reason'}]},units)

    def test_group_persistence_reuse_and_scoring(self):
        db=sqlite3.connect(':memory:');db.row_factory=sqlite3.Row
        fs=self.evidence()
        db.execute('CREATE TABLE web_findings (job_id,registration_number,article_id,finding_index,source_url,summary,evidence_quote,event_date,event_status,finding_type)')
        for i,f in enumerate(fs):
            db.execute('INSERT INTO web_findings VALUES (?,?,?,?,?,?,?,?,?,?)',('j',f['registration_number'],str(i),0,*[f[k] for k in ('source_url','summary','evidence_quote','event_date','event_status','finding_type')]))
        ids=[units_for([f])[0]['id'] for f in fs]
        result={'groups':[{'members':ids[:2],'reason':'Initial cartel decision and appeal in the same procurement case'},{'members':ids[2:],'reason':'Separate invoice case'}]}
        response={'choices':[{'finish_reason':'stop','message':{'content':json.dumps(result)}}]}
        with tempfile.TemporaryDirectory() as root, patch('partner_monitor.web_media.post_json',return_value=response) as api:
            group_company(db,root,'j',fs[0]['registration_number'],{},'unused','model',{},MagicMock())
            group_company(db,root,'j',fs[0]['registration_number'],{},'unused','model',{},MagicMock())
            self.assertEqual(api.call_count,1)
        enrich(db,'j',fs[0]['registration_number'],fs)
        score=assess(item(fs))
        self.assertEqual(score['score'],65)
        self.assertEqual(len(score['events']),2)
        merged=next(e for e in score['events'] if e['penalty']==30)
        self.assertEqual(len(merged['sources']),2)
        self.assertEqual(len(merged['evidence']),2)
        self.assertIn('same procurement',merged['status'])
        # Changes to evidence invalidate saved links, rather than reusing stale model output.
        fresh=self.evidence();fresh[0]['evidence_quote']='Different evidence after a rerun'
        enrich(db,'j',fresh[0]['registration_number'],fresh)
        self.assertNotIn('model_event_id',fresh[0])

    def test_manual_case_override_takes_precedence(self):
        from partner_monitor.assessment import finding_key
        fs=self.evidence()[:2]
        for f in fs:f['model_event_id']='shared'
        reviews={'findings':{finding_key(f):{'case_id':str(i)} for i,f in enumerate(fs)}}
        self.assertEqual(len(assess(item(fs),reviews)['events']),2)
