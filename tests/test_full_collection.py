import csv
import hashlib
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from partner_monitor.collection import collect
from partner_monitor.database import connect
from partner_monitor.debt import import_debt
from partner_monitor.downloads import replay,download
from partner_monitor.inspection import compare,open_database,report
from partner_monitor.normalize import date_value,number,import_csv,calculate_metrics
from partner_monitor.sanctions import import_xml
from partner_monitor.sources import load_sources

REG='40000000001'


class FullCollectionTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.sources=load_sources()
        self.input=self.root/'input.csv'
        self.input.write_text('registration_number,name\n'+REG+',Example\n',encoding='utf-8')

    def fixture(self,source,overrides=None):
        row={c:'' for c in source['columns']}
        row.update({source['key']:REG})
        row.update(overrides or {})
        path=self.root/(source['id']+'.csv')
        with path.open('w',encoding='utf-8',newline='') as stream:
            writer=csv.DictWriter(stream,fieldnames=source['columns'],delimiter=source['delimiter'])
            writer.writeheader()
            writer.writerow(row)
        return path

    def test_dates_and_exact_decimals(self):
        self.assertEqual(date_value('12.09.2026'),'2026-09-12')
        self.assertEqual(date_value('2020-11-05 11:41:58.94'),'2020-11-05T11:41:58.940000')
        self.assertEqual(number('1 234,50'),'1234.50')
        self.assertIsNone(number(''))
        self.assertEqual(number('0'),'0')
        with self.assertRaises(ValueError): number('NaN')
        with self.assertRaises(ValueError): date_value('31.02.2026')

    def test_offline_replay_and_source_failure_not_removal(self):
        source=self.sources[0]
        path=self.fixture(source,{'name':'Example','registered':'2000-01-01'})
        def fetch(s,data_dir):
            target=data_dir/'raw'/'fixture.csv'
            target.parent.mkdir(parents=True,exist_ok=True)
            target.write_bytes(path.read_bytes())
            return {'source':s['id'],'path':'raw/fixture.csv','sha256':hashlib.sha256(target.read_bytes()).hexdigest(),'size':target.stat().st_size,'origin':'fixture'}
        data=self.root/'data'
        with redirect_stdout(io.StringIO()),patch('partner_monitor.collection.download',side_effect=fetch):
            first=collect(self.input,data,['ur_register'],ownership_depth=0)
        with redirect_stdout(io.StringIO()),patch('partner_monitor.collection.download',side_effect=AssertionError('Network forbidden')):
            second=collect(self.input,data,['ur_register'],first['run_id'],0)
        db=open_database(data)
        self.addCleanup(db.close)
        rows=compare(db,second['run_id'],first['run_id'])
        self.assertEqual([r['status'] for r in rows if r['source']=='ur_register'],['UNCHANGED'])
        with redirect_stdout(io.StringIO()),patch('partner_monitor.collection.download',side_effect=RuntimeError('offline')):
            third=collect(self.input,data,['ur_register'],ownership_depth=0)
        rows=compare(db,third['run_id'],first['run_id'])
        self.assertEqual(next(r['status'] for r in rows if r['source']=='ur_register'),'NOT_COMPARABLE')
        self.assertEqual(third['status'],'PARTIAL')
        report(db,second['run_id'],self.root/'report.html')
        self.assertIn('Example',(self.root/'report.html').read_text(encoding='utf-8'))

    def seeded_db(self):
        db=connect(self.root/'db',self.sources)
        self.addCleanup(db.close)
        db.execute("INSERT INTO monitoring_runs VALUES ('r','2026-09-12',NULL,'RUNNING',NULL)")
        db.execute('INSERT INTO companies VALUES (?)',(REG,))
        db.execute("INSERT INTO source_snapshots VALUES ('s','r','test','{}')")
        db.commit()
        return db

    def test_financial_join_uses_statement_and_file_and_zero(self):
        db=self.seeded_db()
        for source_id,values in [
          ('ur_financials',{'id':'statement-1','file_id':'file-1','year':'2025','rounded_to_nearest':'ONES','currency':'EUR'}),
          ('ur_balance',{'statement_id':'statement-1','file_id':'file-1','total_assets':'100','total_current_assets':'0','current_liabilities':'20','non_current_liabilities':'0'}),
          ('ur_income',{'statement_id':'statement-1','file_id':'file-1','net_turnover':'0','net_income':'-10'})]:
            source=next(s for s in self.sources if s['id']==source_id)
            import_csv(db,'r',source,'s',self.fixture(source,values),{REG})
        calculate_metrics(db,'r')
        metrics={r['metric']:(r['value'],r['status']) for r in db.execute('SELECT * FROM financial_metrics')}
        self.assertEqual(metrics['current_ratio'],('0.00000000','CALCULATED'))
        self.assertEqual(metrics['net_margin'],(None,'NON_POSITIVE_DENOMINATOR'))
        source=next(s for s in self.sources if s['id']=='ur_cashflow')
        _,counts,_=import_csv(db,'r',source,'s',self.fixture(source,{'statement_id':'statement-1','file_id':'wrong-file'}),{REG})
        self.assertEqual(sum(counts.values()),0)

    def test_sanctions_schema_and_names(self):
        db=self.seeded_db()
        path=self.root/'list.xml'
        path.write_text('<LVlist><PublishInfo><PublishDate>2026-09-12</PublishDate></PublishInfo><Entity><Id>1</Id><Type>JP</Type><Name><WholeName>Example</WholeName></Name><Alias><AliasWholeName>Other</AliasWholeName></Alias></Entity></LVlist>')
        count,_,_=import_xml(db,'r',{'id':'fid_lv'},'s',path)
        self.assertEqual(count,1)
        self.assertEqual(db.execute('SELECT COUNT(*) FROM sanction_names').fetchone()[0],2)
        path.write_text('<!DOCTYPE x [<!ENTITY a "text">]><LVlist/>')
        with self.assertRaises(ValueError): import_xml(db,'r',{'id':'fid_lv'},'s',path)

    def test_missing_debt_never_becomes_zero(self):
        db=self.seeded_db()
        import_debt(db,'r',[{'registration_number':REG}],self.root/'db',probe=False)
        row=db.execute('SELECT query_status,published_debt_amount FROM tax_debt').fetchone()
        self.assertEqual(tuple(row),('NOT_CHECKED',None))

    def test_browser_evidence_import_replay_and_pdf_integrity(self):
        from partner_monitor.debt_browser import save_object
        db=self.seeded_db()
        data=self.root/'db'
        pdf=save_object(data,b'%PDF-fixture','.pdf')
        evidence=[{'registration_number':REG,'pdf':pdf,'status':'NO_PUBLISHED_DEBT_ABOVE_THRESHOLD'}]
        path=data/'debt.csv'
        path.write_text('registration_number,effective_date,published_debt_amount,publication_threshold,query_status,evidence_url\n'
                        +REG+',2026-09-09,,150,NO_PUBLISHED_DEBT_ABOVE_THRESHOLD,https://www6.vid.gov.lv/NPAR\n')
        with patch('partner_monitor.debt_browser.collect_browser',return_value=(path,evidence)):
            self.assertEqual(import_debt(db,'r',[{'registration_number':REG}],data),'COMPLETED')
        meta=json.loads(db.execute("SELECT metadata_json FROM source_snapshots WHERE source='vid_debt'").fetchone()[0])
        self.assertEqual(meta['origin'],'browser_evidence')
        self.assertEqual(meta['artifacts'],evidence)
        db.execute("INSERT INTO monitoring_runs VALUES ('replay','2026-09-12',NULL,'RUNNING',NULL)")
        with patch('partner_monitor.debt_browser.collect_browser',side_effect=AssertionError('No browser in replay')):
            import_debt(db,'replay',[{'registration_number':REG}],data,data/meta['path'],probe=False,replay_metadata=meta)
        copied=json.loads(db.execute("SELECT metadata_json FROM source_snapshots WHERE run_id='replay'").fetchone()[0])
        self.assertEqual(copied['artifacts'],evidence)
        out=self.root/'report'/'latest.html'
        report(db,'r',out)
        self.assertEqual((out.parent/'evidence'/(pdf['sha256']+'.pdf')).read_bytes(),b'%PDF-fixture')
        (data/pdf['path']).write_bytes(b'tampered')
        with self.assertRaises(ValueError): report(db,'r',out)

    def test_browser_failure_leaves_unknown_amount(self):
        db=self.seeded_db()
        with patch('partner_monitor.debt_browser.collect_browser',side_effect=RuntimeError('offline')):
            import_debt(db,'r',[{'registration_number':REG}],self.root/'db')
        row=db.execute('SELECT query_status,published_debt_amount,detail FROM tax_debt').fetchone()
        self.assertEqual(tuple(row[:2]),('NOT_CHECKED',None))
        self.assertIn('BROWSER_UNAVAILABLE',row[2])

    def test_replay_detects_tampering(self):
        path=self.root/'source.csv'
        path.write_text('tampered')
        with self.assertRaises(ValueError):
            replay({'id':'test'},self.root,{'test':{'path':'source.csv','sha256':'wrong'}})

    def test_incomplete_download_is_retried(self):
        from unittest.mock import MagicMock
        session=MagicMock()
        session.__enter__.return_value=session
        responses=[]
        for content in [b'bad',b'valid']:
            response=MagicMock()
            response.__enter__.return_value=response
            response.status_code=200
            response.headers={'Content-Length':'5'}
            response.iter_content.return_value=[content]
            responses.append(response)
        session.get.side_effect=responses
        with patch('partner_monitor.downloads.requests.Session',return_value=session),patch('partner_monitor.downloads.time.sleep'):
            meta=download({'id':'test','url':'https://example.com/public.csv','format':'csv'},self.root)
        self.assertEqual(session.get.call_count,2)
        self.assertEqual((self.root/meta['path']).read_bytes(),b'valid')
        self.assertEqual(list((self.root/'raw'/'objects').glob('*.part')),[])


if __name__=='__main__':
    unittest.main()
