import json
import hashlib
import io
import tempfile
import unittest
from datetime import datetime,timezone,timedelta
from pathlib import Path
from unittest.mock import patch,MagicMock
from contextlib import redirect_stdout

from partner_monitor.database import connect
from partner_monitor.downloads import download
from partner_monitor.sanctions import import_xml,date_warning
from partner_monitor.screening import canonical,name_match,screen,LISTS,INPUTS
from partner_monitor.sources import load_sources
from partner_monitor.collection import collect

REG='40000000001'


class SanctionsTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)
        self.db=connect(self.path,load_sources());self.addCleanup(self.db.close)
        self.db.execute("INSERT INTO monitoring_runs VALUES ('r','2026-09-12',NULL,'RUNNING',NULL)")
        self.db.execute('INSERT INTO companies VALUES (?)',(REG,))
        self.db.execute("INSERT INTO run_companies VALUES ('r',?,'ROOT',0)",(REG,))
        self.db.execute("INSERT INTO source_snapshots VALUES ('s','r','fixture','{}')")

    def xml(self,content):
        path=self.path/'input.xml';path.write_text(content,encoding='utf-8');return path

    def seed_person(self):
        self.db.execute("INSERT INTO beneficial_owners (run_id,registration_number,snapshot_id,source_row,record_key,row_hash,raw_json,forename,surname,birth_date) VALUES ('r',?,'s',1,'owner','hash','{}','Alex','Example','1980-01-01')",(REG,))
        self.db.execute("INSERT INTO sanction_entities VALUES ('r','fid_eu','e','person','p',NULL,?, 's')",('<sanctionEntity><birthdate birthdate="1970-01-01"/></sanctionEntity>',))
        self.db.execute("INSERT INTO sanction_names VALUES ('r','fid_eu','e','Alex Example','alex example')")
        for source in LISTS|INPUTS:
            self.db.execute("INSERT INTO source_checks(run_id,source,status) VALUES ('r',?,'COMPLETED')",(source,))

    def test_un_sections_aliases_documents(self):
        path=self.xml('<CONSOLIDATED_LIST dateGenerated="2026-09-04T23:00:03Z"><INDIVIDUALS><INDIVIDUAL><DATAID>1</DATAID><FIRST_NAME>Alex</FIRST_NAME><SECOND_NAME>Example</SECOND_NAME><INDIVIDUAL_ALIAS><ALIAS_NAME>Other Name</ALIAS_NAME></INDIVIDUAL_ALIAS><INDIVIDUAL_DOCUMENT><NUMBER>123</NUMBER><TYPE_OF_DOCUMENT>Passport</TYPE_OF_DOCUMENT></INDIVIDUAL_DOCUMENT></INDIVIDUAL></INDIVIDUALS><ENTITIES><ENTITY><DATAID>2</DATAID><FIRST_NAME>Example Ltd</FIRST_NAME></ENTITY></ENTITIES></CONSOLIDATED_LIST>')
        count,generated,_=import_xml(self.db,'r',{'id':'fid_un'},'s',path)
        self.assertEqual(count,2);self.assertEqual(generated,'2026-09-04T23:00:03Z')
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM sanction_names').fetchone()[0],3)
        self.assertEqual(self.db.execute('SELECT number FROM sanction_identifiers').fetchone()[0],'123')

    def test_un_missing_section_rejected(self):
        with self.assertRaises(ValueError):
            import_xml(self.db,'r',{'id':'fid_un'},'s',self.xml('<CONSOLIDATED_LIST><INDIVIDUALS/></CONSOLIDATED_LIST>'))

    def test_name_rules_and_short_false_positives(self):
        self.assertEqual(canonical('Jānis   Bērziņš'),'janis berzins')
        self.assertEqual(name_match('alex example','example alex')[0],'REORDERED_NAME')
        self.assertEqual(name_match('alex example','alex exampl')[0],'SIMILAR_NAME')
        self.assertIsNone(name_match('sia','sia'))
        self.assertIsNone(name_match('john smith','john brown'))

    def test_candidate_retains_evidence_and_dob_conflict(self):
        self.seed_person();result=screen(self.db,'r')
        self.assertEqual(result['candidates'],1)
        row=self.db.execute('SELECT * FROM sanctions_candidates').fetchone()
        evidence=json.loads(row['evidence_json'])
        self.assertEqual(evidence['subject']['snapshot_id'],'s')
        self.assertEqual(evidence['dob_comparison'],'DOB_DIFFERS_REVIEW_REQUIRED')
        self.assertEqual(row['review_status'],'NEEDS_REVIEW')
        self.assertEqual(self.db.execute('SELECT status FROM sanctions_screening').fetchone()[0],'CANDIDATES_REQUIRE_REVIEW')

    def test_missing_list_not_reported_as_clear(self):
        self.seed_person()
        self.db.execute("UPDATE sanction_names SET name='Unrelated Name'")
        self.db.execute("DELETE FROM source_checks WHERE source='fid_un'")
        screen(self.db,'r')
        row=self.db.execute('SELECT status,limitations FROM sanctions_screening').fetchone()
        self.assertEqual(row['status'],'INCOMPLETE')
        self.assertIn('fid_un',json.loads(row['limitations'])['missing_sources'])

    def test_old_fid_date_is_informational(self):
        self.seed_person()
        self.db.execute("UPDATE sanction_names SET name='Unrelated Name'")
        self.db.execute("UPDATE source_checks SET detail='SOURCE_DATE_OLDER_THAN_7_DAYS' WHERE source='fid_eu'")
        self.db.execute("INSERT INTO registry (run_id,registration_number,snapshot_id,source_row,record_key,row_hash,raw_json,name) VALUES ('r',?,'s',1,'company','hash','{}','Local Company')",(REG,))
        result=screen(self.db,'r')
        self.assertEqual(result['source_date_warnings'],[])
        self.assertEqual(self.db.execute('SELECT status FROM sanctions_screening').fetchone()[0],'NO_CANDIDATES')

    def test_source_dates_missing_old_future(self):
        self.assertEqual(date_warning(None),'SOURCE_DATE_MISSING')
        self.assertIn('OLDER',date_warning('2018-03-29T15:10:00Z'))
        self.assertEqual(date_warning((datetime.now(timezone.utc)+timedelta(days=1)).isoformat()),'SOURCE_DATE_IN_FUTURE')

    def test_fid_uses_form_post_and_does_not_save_csrf(self):
        session=MagicMock();session.__enter__.return_value=session
        session.get.return_value.text='<form id="fullFileDownloadForm"><input name="csrf" value="ephemeral-token"></form>'
        response=session.post.return_value;response.__enter__.return_value=response
        response.status_code=200;response.headers={};response.iter_content.return_value=[b'<xml/>']
        with patch('partner_monitor.downloads.requests.Session',return_value=session):
            meta=download({'id':'fid_un','url':'https://sankcijas.fid.gov.lv/lejupieladet-sarakstu/un','format':'xml'},self.path)
        self.assertEqual(session.post.call_args.kwargs['data'],{'csrf':'ephemeral-token','fileType':'xml'})
        self.assertNotIn('ephemeral-token',json.dumps(meta))

    def test_stale_un_run_is_partial_and_replays_offline(self):
        data=self.path/'integration'
        input_path=self.path/'companies.csv'
        input_path.write_text('registration_number,name\n'+REG+',Example\n')
        content=b'<CONSOLIDATED_LIST dateGenerated="2018-01-01T00:00:00Z"><INDIVIDUALS/><ENTITIES><ENTITY><DATAID>1</DATAID><FIRST_NAME>Example</FIRST_NAME></ENTITY></ENTITIES></CONSOLIDATED_LIST>'
        def fetch(source,root):
            target=root/'raw'/'un.xml';target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(content)
            return {'path':'raw/un.xml','sha256':hashlib.sha256(content).hexdigest(),'size':len(content),'origin':'fixture'}
        with redirect_stdout(io.StringIO()),patch('partner_monitor.collection.download',side_effect=fetch):
            first=collect(input_path,data,['fid_un'])
        self.assertEqual(first['status'],'PARTIAL')
        self.assertFalse(any('SOURCE_DATE_OLDER' in s for s in first['warnings']))
        with redirect_stdout(io.StringIO()),patch('partner_monitor.collection.download',side_effect=AssertionError('No network')):
            second=collect(input_path,data,['fid_un'],first['run_id'])
        self.assertEqual(second['status'],'PARTIAL')
