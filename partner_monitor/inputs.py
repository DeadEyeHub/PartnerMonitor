import csv
import re
from pathlib import Path


def read_companies(path: Path) -> list[dict]:
    """Validate the whole input before downloading anything. Never guess IDs."""
    if path.suffix.lower() == '.csv':
        with path.open(encoding='utf-8-sig', newline='') as stream:
            sample = stream.read(8192)
            stream.seek(0)
            try:
                dialect = csv.Sniffer().sniff(sample, delimiters=',;\t')
            except csv.Error:
                dialect = csv.excel
            rows = list(csv.reader(stream, dialect))
    elif path.suffix.lower() == '.xlsx':
        from openpyxl import load_workbook
        workbook = load_workbook(path, read_only=True, data_only=False)
        try:
            rows = list(workbook.active.values)
        finally:
            workbook.close()
    else:
        raise ValueError('Input must be CSV or XLSX')
    if not rows:
        raise ValueError('Input is empty')
    headers = [str(value or '').strip() for value in rows[0]]
    if 'registration_number' not in headers or len(headers) != len(set(headers)):
        raise ValueError('Unique headers including registration_number are required')
    result, errors, seen = [], [], set()
    for line, values in enumerate(rows[1:], 2):
        if not any(value is not None and str(value).strip() for value in values):
            continue
        if len(values) > len(headers):
            errors.append(f'Row {line}: too many columns')
            continue
        row = dict(zip(headers, values))
        value = row.get('registration_number')
        number = str(value).strip() if value is not None else ''
        if not re.fullmatch(r'[0-9]{11}', number):
            errors.append(f'Row {line}: registration_number must contain 11 digits')
        elif number in seen:
            errors.append(f'Row {line}: duplicate registration_number {number}')
        else:
            seen.add(number)
            result.append({key: str(row.get(key) or '').strip() for key in
                           ('registration_number', 'name', 'partner_type', 'comment', 'business_unit')})
    if errors:
        raise ValueError('\n'.join(errors))
    if not result:
        raise ValueError('No companies in input')
    return result
