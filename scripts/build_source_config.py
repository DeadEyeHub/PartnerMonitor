"""Build the reviewed source registry from captured public CKAN metadata."""
import csv
import json
from pathlib import Path

SPECS = [
 ('ur_register','register.csv','registry','regcode'),
 ('ur_names','register_name_history.csv','company_names','regcode'),
 ('ur_members','members.csv','members','at_legal_entity_registration_number'),
 ('ur_stockholders','stockholders.csv','stockholders','at_legal_entity_registration_number'),
 ('ur_beneficial_owners','beneficial_owners.csv','beneficial_owners','legal_entity_registration_number'),
 ('ur_officers','officers.csv','officers','at_legal_entity_registration_number'),
 ('ur_insolvency','insolvency_legal_person_proceeding.csv','insolvency_proceedings','debtor_registration_number'),
 ('ur_liquidations','liquidations.csv','liquidations','legal_entity_registration_number'),
 ('ur_suspensions','suspensions_prohibitions.csv','activity_restrictions','legal_entity_registration_number'),
 ('ur_measures','securing_measures.csv','securing_measures','legal_entity_registration_number'),
 ('ur_sanctions','sanctions.csv','ur_sanctions','legal_entity_registration_number'),
 ('ur_financials','financial_statements.csv','financial_statements','legal_entity_registration_number'),
 ('ur_balance','balance_sheets.csv','balance_sheets','statement_id'),
 ('ur_income','income_statements.csv','income_statements','statement_id'),
 ('ur_cashflow','cash_flow_statements.csv','cash_flow_statements','statement_id'),
 ('vid_vat','pdb_pvnmaksataji_odata.csv','vat_status','Numurs'),
 ('vid_suspensions','pdb_saimndarbibaaptureta_odata.csv','vid_suspensions','Registracijas_kods'),
 ('vid_rating','reitings_uznemumi.csv','vid_ratings','Registracijas_kods'),
 ('vid_taxes','pdb_nm_komersantu_samaksato_nodoklu_kopsumas_odata.csv','tax_payments','Registracijas_kods'),
]

if __name__ == '__main__':
    headers = json.loads(Path('data/discovery/headers.json').read_text(encoding='utf-8'))
    config = []
    for source, filename, table, key in SPECS:
        entry = headers[filename]
        delimiter = ',' if source.startswith('vid_') else ';'
        config.append(dict(id=source, filename=filename, table=table, key=key, delimiter=delimiter,
                           url=entry['url'], resource_id=entry['resource_id'],
                           columns=next(csv.reader([entry['header']], delimiter=delimiter)),
                           verified_on='2026-09-12', format='csv'))
    for kind in ['eu','lv']:
        config.append(dict(id='fid_'+kind, filename=kind+'.xml', format='xml',
                           url='https://sankcijas.fid.gov.lv/lejupieladet-sarakstu/'+kind,
                           verified_on='2026-09-12'))
    Path('config').mkdir(exist_ok=True)
    Path('config/sources.json').write_text(json.dumps(config, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
