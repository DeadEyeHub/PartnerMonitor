import copy
import json
import tempfile
import unittest
from pathlib import Path
from partner_monitor.assessment import assess, finding_key, digest, load_reviews
from partner_monitor.report_data import build_report, numeric


def item(findings=()):
    return {'registration_number':'40000000001','run_id':'r', 'quality':[],
        'web_checks':[{'search_status':'COMPLETED','analysis_status':'COMPLETED'}],
        'web_findings':list(findings), 'financials':[], 'tax_debt':[]}


def finding(kind='regulatory', text='A cartel fine was imposed.', quote='Exact supporting source quotation', date='2021-07-30'):
    return {'registration_number':'40000000001','summary':text,'evidence_quote':quote,
        'source_url':'https://example.org/article','finding_type':kind,'event_date':date,
        'event_status':'reported_decision'}


class AssessmentTests(unittest.TestCase):
    def test_shipped_reviews_have_no_company_specific_overrides(self):
        path=Path(__file__).resolve().parent.parent/'config/assessment_reviews.json'
        self.assertEqual(load_reviews(path),{'findings':{},'sanctions':{}})

    def test_cartel_appeal_one_case_and_petition_only_five(self):
        a=finding();b=finding('legal_dispute','The court rejected the cartel appeal.','Separate exact quotation',None)
        c=finding('insolvency','An insolvency application was filed; the company disputed it.','Petition quotation','2018-06-15')
        reviews={'findings':{finding_key(f):{'case_id':'case-2021'} for f in [a,b]}}
        result=assess(item([a,b,c]),reviews)
        self.assertEqual(result['score'],65)
        self.assertEqual(len(result['events']),2)
        self.assertEqual(len(result['events'][0]['sources']),1)
        self.assertEqual(len(result['events'][0]['evidence']),2)
        self.assertTrue(result['recommendation'].startswith('Not recommended'))

    def test_exact_duplicate_distinct_cases_and_boundary(self):
        a=finding();result=assess(item([a,copy.deepcopy(a)]))
        self.assertEqual(result['score'],70)
        self.assertFalse(result['recommendation'].startswith('Not recommended'))
        b=finding(quote='A different case supporting quotation',date='2023-05-06')
        self.assertEqual(assess(item([a,b]))['score'],40)

    def test_explicit_case_number_merges_evidence_but_not_different_cases(self):
        a=finding('legal_dispute','A court dispute','Case No. C12345678: a claim was filed.')
        b=finding('legal_dispute','An appeal','Lietas Nr. C12345678: the appeal was dismissed.')
        b['source_url']='https://example.org/second'
        result=assess(item([a,b]))
        self.assertEqual(result['score'],95)
        self.assertEqual(len(result['events'][0]['sources']),2)
        b['evidence_quote']='Case No. C87654321: an unrelated claim.'
        self.assertEqual(assess(item([a,b]))['score'],90)
        b['evidence_quote']='Case No. C12345678 and Case No. C87654321 were discussed.'
        self.assertEqual(assess(item([a,b]))['score'],90)
        b['summary']='Case No. C12345678';b['evidence_quote']='A separate quoted event without a case number.'
        self.assertEqual(assess(item([a,b]))['score'],90)

    def test_quote_whitespace_duplicates_and_manual_override(self):
        a=finding(quote='Exact quotation with spaces.')
        b=finding(quote='Exact quotation  with\nspaces.')
        self.assertEqual(assess(item([a,b]))['score'],70)
        reviews={'findings':{finding_key(a):{'case_id':'one'},finding_key(b):{'case_id':'two'}}}
        self.assertEqual(assess(item([a,b]),reviews)['score'],40)

    def test_missing_data_is_provisional_not_penalty_or_zero_debt(self):
        result=assess(item())
        self.assertEqual(result['score'],100)
        self.assertTrue(result['provisional'])
        data=item();data['tax_debt']=[{'query_status':'NO_PUBLISHED_DEBT_ABOVE_THRESHOLD','published_debt_amount':None}]
        self.assertEqual(assess(data)['score'],100)
        self.assertTrue(assess(data)['areas']['Tax debt'])

    def test_sanctions_need_current_run_applicability_review(self):
        data=item();candidate={'subject_key':'s','source':'fid_eu','entity_id':'e'}
        data['sanctions_candidates']=[candidate]
        self.assertEqual(assess(data)['score'],100)
        key=digest([data['registration_number'],'s','fid_eu','e'])
        reviews={'sanctions':{key:{'status':'CONFIRMED_APPLICABLE','run_id':'r','reason':'Verified identity and applicability'}}}
        self.assertEqual(assess(data,reviews)['score'],0)
        reviews['sanctions'][key]['run_id']='old'
        self.assertEqual(assess(data,reviews)['score'],100)

    def test_floor_and_financial_zero_and_units(self):
        fs=[finding('fraud','Reported fraud '+str(n),'Quote '+str(n)) for n in range(8)]
        self.assertEqual(assess(item(fs))['score'],0)
        self.assertIsNone(numeric(None))
        self.assertEqual(numeric('0'),0)
        self.assertEqual(numeric('1.2',1000),1200)

    def test_history_is_idempotent_and_missing_web_does_not_erase_events(self):
        data=item([finding()]);companies=[{'registration_number':data['registration_number'],'name':'Example','role':'ROOT'}]
        with tempfile.TemporaryDirectory() as directory:
            first,_=build_report(None,'r',companies,lambda *args:data,Path(directory))
            repeat,_=build_report(None,'r',companies,lambda *args:data,Path(directory))
            self.assertEqual(first,repeat)
            self.assertIsNone(first['sheets']['Overview'][0]['New findings'])
            data=item();data['web_checks']=[{'search_status':'FAILED','analysis_status':'ERROR'}]
            second,_=build_report(None,'r',companies,lambda *args:data,Path(directory))
            self.assertEqual(second['sheets']['Overview'][0]['Reliability score'],70)
            self.assertEqual(second['sheets']['Overview'][0]['New findings'],0)
            self.assertIn('Previously reported',second['sheets']['Findings'][0]['Status'])

    def test_source_failure_not_field_removal(self):
        data=item();data['quality']=[{'source':'ur_register','status':'FOUND'}]
        data['registry']=[{'record_key':'reg','name':'Example'}]
        companies=[{'registration_number':data['registration_number'],'name':'Example','role':'ROOT'}]
        with tempfile.TemporaryDirectory() as directory:
            build_report(None,'r',companies,lambda *args:data,Path(directory))
            data['quality'][0]['status']='ERROR';data['registry']=[]
            second,_=build_report(None,'r',companies,lambda *args:data,Path(directory))
            self.assertFalse(any(row['Field'].startswith('registry:') for row in second['sheets']['Changes']))

    def test_review_requires_reason_and_reviewer(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'review.json'
            path.write_text(json.dumps({'sanctions':{'key':{'status':'CONFIRMED_APPLICABLE'}}}))
            with self.assertRaises(ValueError):load_reviews(path)

    def test_negative_equity_latest_statement_and_ambiguity(self):
        data=item();data['financials']=[{'year':'2024','equity':'-100','statement_id':'s','file_id':'f'}]
        self.assertEqual(assess(data)['score'],85)
        data['financials'].append({'year':'2025','equity':'0','statement_id':'s2','file_id':'f2'})
        self.assertEqual(assess(data)['score'],100)
        data['financials'].append({'year':'2025','equity':'-200','statement_id':'s3','file_id':'f3'})
        result=assess(data)
        self.assertEqual(result['score'],100)
        self.assertTrue(any('Multiple latest' in w for w in result['warnings']))

    def test_legal_protection_not_labelled_declared_insolvency(self):
        data=item();data['v_insolvency']=[{'proceeding_state':'ACTIVE','proceeding_form':'LEGAL_PROTECTION','record_key':'k'}]
        result=assess(data)
        self.assertEqual(result['score'],85)
        self.assertEqual(result['events'][0]['title'],'Active legal protection proceeding')

    def test_three_negative_years_one_thirty_point_event_with_all_evidence(self):
        data=item()
        data['financials']=[{'year':str(y),'equity':'-100','statement_id':str(y),'file_id':str(y)} for y in [2023,2025,2024,2022]]
        result=assess(data)
        self.assertEqual(result['score'],70)
        self.assertEqual(len(result['events']),1)
        event=result['events'][0]
        self.assertEqual(event['rule'],'persistent_negative_equity')
        self.assertEqual(len(event['evidence']),3)
        self.assertIn('2023–2025',event['title'])
        data['financials']=[f for f in data['financials'] if f['year']=='2025']
        single=assess(data)['events'][0]
        self.assertEqual(event['id'],single['id'])
        self.assertEqual(single['penalty'],15)

    def test_two_year_equity_and_large_loss_threshold(self):
        data=item()
        data['financials']=[{'year':str(y),'equity':('-10' if y>2023 else '10'),
            'net_income':'-50000','currency':'EUR','rounded_to_nearest':'ONES',
            'statement_id':str(y),'file_id':str(y)} for y in [2025,2024,2023]]
        self.assertEqual(assess(data)['score'],80)
        data['financials'][1]['net_income']='-50000.01'
        self.assertEqual(assess(data)['score'],30)
        data['financials'][0]['net_income']='-60000'
        result=assess(data)
        self.assertEqual(result['score'],30)
        self.assertEqual(len([e for e in result['events'] if e['rule']=='large_annual_loss']),1)
        for row in data['financials']:row['net_income']='0'
        data['financials'].append({**data['financials'][0],'year':'2022','net_income':'-90000'})
        self.assertEqual(assess(data)['score'],80)
        data['financials'][0].update(net_income='-51',rounded_to_nearest='THOUSANDS')
        self.assertEqual(assess(data)['score'],30)
        data['financials'][0]['currency']='LVL'
        self.assertEqual(assess(data)['score'],80)

    def test_equity_gaps_duplicates_invalid_values_and_recovery(self):
        base=[{'year':str(y),'equity':'-100','statement_id':str(y),'file_id':str(y)} for y in [2025,2024,2023]]
        for bad in [None,'','NaN','Infinity','-Infinity','invalid']:
            with self.subTest(equity=bad):
                data=item();data['financials']=copy.deepcopy(base);data['financials'][1]['equity']=bad
                self.assertEqual(assess(data)['score'],85)
        for year in ['2021','2025']:
            with self.subTest(year=year):
                data=item();data['financials']=copy.deepcopy(base);data['financials'][1]['year']=year
                self.assertEqual(assess(data)['score'],85 if year=='2021' else 100)
        for recovered in ['0','100']:
            data=item();data['financials']=copy.deepcopy(base);data['financials'][0]['equity']=recovered
            self.assertEqual(assess(data)['score'],100)
        data=item();data['financials']=copy.deepcopy(base);data['financials'][1]['equity']='0'
        self.assertEqual(assess(data)['score'],85)

    def test_explicit_monitoring_baseline_ignores_latest_report(self):
        data=item();companies=[{'registration_number':data['registration_number'],'name':'Example','role':'ROOT'}]
        with tempfile.TemporaryDirectory() as directory:
            folder=Path(directory)
            first,_=build_report(None,'r',companies,lambda *args:data,folder)
            data['financials']=[{'year':'2025','equity':'-1','statement_id':'s','file_id':'f'}]
            second,_=build_report(None,'r',companies,lambda *args:data,folder)
            third,_=build_report(None,'r',companies,lambda *args:data,folder,baseline=first['id'])
            self.assertEqual(third['previous_id'],first['id'])
            self.assertNotEqual(third['id'],second['id'])
            score_change=next(r for r in third['sheets']['Changes'] if r['Field']=='Reliability score')
            self.assertEqual(score_change['Previous value'],100)
            self.assertEqual(score_change['Current value'],85)
            with self.assertRaises(ValueError):build_report(None,'r',companies,lambda *args:data,folder,baseline='../latest')

    def test_official_event_retained_on_failure_and_removed_on_success(self):
        data=item();data['quality']=[{'source':'vid_debt','status':'FOUND'}]
        data['tax_debt']=[{'query_status':'PUBLISHED_DEBT','published_debt_amount':'200','effective_date':'2026-09-09','evidence_url':'https://example.org'}]
        companies=[{'registration_number':data['registration_number'],'name':'Example','role':'ROOT'}]
        with tempfile.TemporaryDirectory() as directory:
            build_report(None,'r',companies,lambda *args:data,Path(directory))
            data['quality'][0]['status']='ERROR';data['tax_debt']=[]
            second,_=build_report(None,'r',companies,lambda *args:data,Path(directory))
            self.assertEqual(second['sheets']['Overview'][0]['Reliability score'],85)
            data['quality'][0]['status']='FOUND'
            data['tax_debt']=[{'query_status':'NO_PUBLISHED_DEBT_ABOVE_THRESHOLD'}]
            third,_=build_report(None,'r',companies,lambda *args:data,Path(directory))
            self.assertEqual(third['sheets']['Overview'][0]['Reliability score'],100)
            self.assertTrue(any('no longer scored' in r['Field'] for r in third['sheets']['Changes']))


if __name__ == '__main__':unittest.main()
