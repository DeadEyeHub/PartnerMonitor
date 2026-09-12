"""Read public CKAN metadata; keep discovery evidence outside Git."""
import json
from pathlib import Path
import requests

BASE = 'https://data.gov.lv/dati/api/3/action/'


def api(action, **params):
    response = requests.get(BASE + action, params=params, timeout=60)
    response.raise_for_status()
    result = response.json()
    if not result['success']:
        raise RuntimeError('CKAN request failed')
    return result['result']


if __name__ == '__main__':
    packages = {}
    for seed in ['uz', 'nodoklu-maksataju-reitings']:
        package = api('package_show', id=seed)
        organization = package['organization']['name']
        for item in api('package_search', fq='organization:' + organization, rows=1000)['results']:
            packages[item['name']] = item
    root = Path('data/discovery')
    root.mkdir(parents=True, exist_ok=True)
    (root / 'catalog.json').write_text(json.dumps(packages, ensure_ascii=False, indent=2), encoding='utf-8')
    for name, package in sorted(packages.items()):
        print(name, '|', package['title'])
        for resource in package['resources']:
            print(' ', resource.get('format'), resource.get('name'), resource['url'])
    response = requests.get('https://sankcijas.fid.gov.lv/lv/meklet-sankciju-sarakstos', timeout=60)
    response.raise_for_status()
    (root / 'fid.html').write_text(response.text, encoding='utf-8')
