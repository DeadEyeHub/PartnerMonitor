"""Readable report view models, separate from database inspection."""
def missing_data_rows(items, payload):
    missing_rows = []
    labels = {'UR':'Company registration details', 'VID':'VID taxpayer rating', 'VAT':'VAT registration check',
              'Financials':'Annual financial statements', 'Tax debt':'Tax debt amount or official no-published-debt result',
              'Sanctions':'Sanctions name screening', 'Web':'Completed news search and model analysis'}
    actions = {'UR':'Retry the company register import and verify the registration number.',
               'VID':'Retry VID taxpayer rating collection.', 'VAT':'Retry the VAT register check.',
               'Financials':'Check whether annual statements were filed and retry financial collection.',
               'Tax debt':'Retry the VID debt form or verify it manually.',
               'Sanctions':'Refresh sanctions sources and rerun name screening.',
               'Web':'Run news search and analysis for this company; review unresolved articles.'}
    from .assessment import coverage
    for reg, item in items.items():
        name = (item.get('registry') or {}).get('name') if isinstance(item.get('registry'), dict) else None
        name = name or payload['companies'][reg].get('name') or reg
        for area, available in coverage(item).items():
            if available: continue
            reason = 'No usable result was saved for this check.'
            if area == 'Web':
                checks = item.get('web_checks') or []
                reason = ('Search and analysis were not run for this collection.' if not checks else
                    'News checking is unfinished: search ' + str(checks[0].get('search_status', 'not started')).lower().replace('_',' ') +
                    ', analysis ' + str(checks[0].get('analysis_status', 'not started')).lower().replace('_',' ') + '.')
            if area == 'Tax debt' and item.get('tax_debt'):
                detail = item['tax_debt'][0].get('detail')
                reason = {'Company name required by VID':'The VID request could not start because the legal name was missing.',
                          'VID PDF download unavailable':'The VID certificate could not be downloaded.'}.get(detail, 'VID did not return a usable debt result; inspect the company evidence.')
            missing_rows.append({'Company':name,'Registration number':reg,'Missing data or unfinished check':labels[area],
                'What happened':reason,'Next step':actions[area]})
        source_labels = {'ur_names':'Historical company names', 'ur_members':'Company owners',
            'ur_stockholders':'Shareholders', 'ur_beneficial_owners':'Beneficial owners', 'ur_officers':'Company officers',
            'ur_insolvency':'Insolvency and legal protection', 'ur_liquidations':'Liquidation records',
            'ur_suspensions':'Register activity restrictions', 'ur_measures':'Registered restrictive measures',
            'ur_sanctions':'Register sanctions records', 'ur_income':'Income statements', 'ur_balance':'Balance sheets',
            'ur_cashflow':'Cash flow statements', 'vid_taxes':'Annual tax payments', 'vid_suspensions':'VID activity restrictions'}
        for check in item.get('quality', []):
            source = check.get('source')
            if source not in source_labels or check.get('status') in {'FOUND','NO_RECORDS'}: continue
            missing_rows.append({'Company':name,'Registration number':reg,'Missing data or unfinished check':source_labels[source],
                'What happened':'This source check did not finish with a usable result.',
                'Next step':'Retry official collection; if unavailable, check the original source manually.'})
        for f in item.get('financials', [])[:3]:
            absent = [label for key,label in [('net_income','profit after tax'),('equity','equity'),('net_turnover','revenue'),('total_assets','assets')] if f.get(key) is None]
            if absent:
                missing_rows.append({'Company':name,'Registration number':reg,'Missing data or unfinished check':'Financial fields ('+str(f.get('year'))+'): '+', '.join(absent),
                    'What happened':'The imported statement does not contain these values.', 'Next step':'Verify the original annual statement.'})
    return missing_rows

