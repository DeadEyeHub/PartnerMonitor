import concurrent.futures
import json
from pathlib import Path
import requests

FILES = '''register.csv register_name_history.csv members.csv stockholders.csv beneficial_owners.csv officers.csv insolvency_legal_person_proceeding.csv liquidations.csv suspensions_prohibitions.csv securing_measures.csv sanctions.csv financial_statements.csv balance_sheets.csv income_statements.csv cash_flow_statements.csv pdb_pvnmaksataji_odata.csv pdb_saimndarbibaaptureta_odata.csv reitings_uznemumi.csv pdb_nm_komersantu_samaksato_nodoklu_kopsumas_odata.csv'''.split()


def probe(resource):
    with requests.get(resource['url'], stream=True, timeout=60) as response:
        response.raise_for_status()
        lines = response.iter_lines()
        header = next(lines).decode('utf-8-sig')
        # Save a sample for schema work, never print personal data.
        sample = next(lines, b'').decode('utf-8-sig')
        return {'url': resource['url'], 'resource_id': resource['id'], 'size': response.headers.get('Content-Length'),
                'header': header, 'sample': sample}


if __name__ == '__main__':
    root = Path('data/discovery')
    catalog = json.loads((root / 'catalog.json').read_text(encoding='utf-8'))
    selected = {r['url'].split('/')[-1]: r for p in catalog.values() for r in p['resources'] if r['url'].split('/')[-1] in FILES}
    output = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        tasks = {pool.submit(probe, resource): name for name, resource in selected.items()}
        for task in concurrent.futures.as_completed(tasks):
            name = tasks[task]
            try:
                output[name] = task.result()
                print(name, output[name]['size'], output[name]['header'], flush=True)
            except Exception as exc:
                print(name, type(exc).__name__, flush=True)
    (root / 'headers.json').write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding='utf-8')
