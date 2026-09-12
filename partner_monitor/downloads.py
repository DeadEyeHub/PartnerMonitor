"""Immutable, content-addressed public snapshots. Never log credentials."""
import hashlib
import json
import re
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit

import requests

from .ur import digest, utc_now


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(path)


def download(source: dict, data_dir: Path) -> dict:
    url = source['url']
    parts = urlsplit(url)
    if parts.scheme != 'https' or parts.username or parts.password or parts.query or parts.fragment:
        raise ValueError('Source URLs must be public HTTPS URLs without credentials/query/fragment')
    cache_file = data_dir / 'raw' / 'cache' / (source['id'] + '.json')
    cached = json.loads(cache_file.read_text(encoding='utf-8')) if cache_file.exists() else None
    if cached and (cached['download_url'] != url or not (data_dir / cached['path']).exists()):
        cached = None
    headers = {}
    if cached:
        if cached.get('etag'):
            headers['If-None-Match'] = cached['etag']
        if cached.get('http_last_modified'):
            headers['If-Modified-Since'] = cached['http_last_modified']
    raw = data_dir / 'raw' / 'objects'
    raw.mkdir(parents=True, exist_ok=True)
    partial = raw / (uuid.uuid4().hex + '.part')
    for attempt in range(3):
        try:
            with requests.Session() as session:
                if source['id'].startswith('fid_'):
                    page = session.get('https://sankcijas.fid.gov.lv/lv/meklet-sankciju-sarakstos', timeout=(10,60))
                    page.raise_for_status()
                    form = re.search(r'id="fullFileDownloadForm"(.*?)</form>', page.text, re.S)
                    token = re.search(r'name="csrf" value="([^"]+)', form.group(1)) if form else None
                    if not token:
                        raise ValueError('FID download form changed')
                    response = session.post(url, data={'csrf': token.group(1), 'fileType': 'xml'}, stream=True, timeout=(10,120))
                else:
                    response = session.get(url, headers=headers, stream=True, timeout=(10,120))
                with response:
                    if response.status_code == 304 and cached:
                        if digest(data_dir / cached['path']) != cached['sha256']:
                            headers = {}
                            cached = None
                            continue
                        return {**cached, 'checked_at': utc_now(), 'origin': 'http_304'}
                    response.raise_for_status()
                    sha, size = hashlib.sha256(), 0
                    with partial.open('wb') as stream:
                        for chunk in response.iter_content(1024*1024):
                            size += len(chunk)
                            if size > 2 * 1024**3:
                                raise ValueError('Source exceeds 2 GiB limit')
                            stream.write(chunk)
                            sha.update(chunk)
                    if not size:
                        raise ValueError('Empty source response')
                    expected = response.headers.get('Content-Length')
                    if expected and not response.headers.get('Content-Encoding') and size != int(expected):
                        raise requests.exceptions.ChunkedEncodingError('Incomplete download')
                    target = raw / (sha.hexdigest() + '.' + source['format'])
                    if target.exists() and digest(target) == sha.hexdigest():
                        partial.unlink()
                    else:
                        partial.replace(target)
                    meta = dict(source=source['id'], download_url=url, retrieved_at=utc_now(), checked_at=utc_now(),
                                source_as_of=None, sha256=sha.hexdigest(), size=size,
                                path=target.relative_to(data_dir).as_posix(), origin='download',
                                etag=response.headers.get('ETag'), http_last_modified=response.headers.get('Last-Modified'))
                    write_json(cache_file, meta)
                    return meta
        except requests.RequestException:
            partial.unlink(missing_ok=True)
            if attempt == 2:
                raise RuntimeError('Download failed after 3 attempts') from None
            time.sleep(2**attempt)
        except Exception:
            partial.unlink(missing_ok=True)
            raise
    raise RuntimeError('Could not obtain verified snapshot')


def replay(source, data_dir, manifest):
    entry = manifest.get(source['id'])
    if not entry or 'sha256' not in entry:
        raise FileNotFoundError('Source was unavailable in replay run')
    path = (data_dir / entry['path']).resolve()
    if not path.is_relative_to(data_dir.resolve()) or digest(path) != entry['sha256']:
        raise ValueError('Replay snapshot hash/path validation failed')
    return {**entry, 'origin': 'replay', 'checked_at': utc_now()}
