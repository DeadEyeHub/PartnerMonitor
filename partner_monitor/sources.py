import json
import os
from pathlib import Path


def load_sources():
    path = Path(__file__).resolve().parent.parent / 'config' / 'sources.json'
    sources = json.loads(path.read_text(encoding='utf-8'))
    for source in sources:
        source['url'] = os.getenv(source['id'].upper() + '_URL', source['url'])
    return sources
