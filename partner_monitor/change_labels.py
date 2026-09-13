"""Business labels for grouped record changes; raw keys remain in snapshot facts."""
import re
TABLES={'registry':'Company registration','members':'Owners','stockholders':'Shareholders',
 'beneficial_owners':'Beneficial owners','officers':'Officers','names':'Company names',
 'balance_sheets':'Balance sheet','income_statements':'Income statement','financial_statements':'Annual statement',
 'tax_payments':'Tax payments','vat_status':'VAT registration'}
FIELDS={'net_income':'Profit after tax','net_turnover':'Revenue','equity':'Equity','total_assets':'Assets',
 'registered':'Registration date','terminated':'Termination date','name':'Name','year':'Year',
 'registration_number':'Registration number','position':'Position','amount':'Amount'}
def label(value):
 return FIELDS.get(value,re.sub(r'[_\s]+',' ',value).strip().capitalize())
def record_label(table, old, current):
 row=current or old
 identity=next((str(row[k]) for k in ('name','year','registration_number') if row.get(k)), '')
 return TABLES.get(table,label(table))+(' — '+identity if identity else '')
def values(row, fields):
 return '\n'.join(label(f)+': '+str(row.get(f) if row.get(f) is not None else 'Not recorded') for f in fields)
