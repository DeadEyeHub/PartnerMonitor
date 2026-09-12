import csv
import hashlib
import json
import re
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

import requests

REQUIRED = {'regcode', 'name', 'registered', 'terminated', 'type', 'type_text', 'address'}


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(chunk)
    return value.hexdigest()


def snapshot(directory: Path, url: str, local: Path | None = None) -> dict:
    directory.mkdir(parents=True, exist_ok=False)
    target = directory / 'register.csv'
    partial = directory / 'register.csv.part'
    modified = None
    if local:
        shutil.copyfile(local, partial)
    else:
        parsed = urlsplit(url)
        if parsed.scheme != 'https' or parsed.username or parsed.password or parsed.query:
            raise ValueError('UR URL must be HTTPS without credentials or query parameters')
        for attempt in range(3):
            try:
                with requests.get(url, stream=True, timeout=(10, 90)) as response:
                    response.raise_for_status()
                    modified = response.headers.get('Last-Modified')
                    with partial.open('wb') as stream:
                        for chunk in response.iter_content(1024 * 1024):
                            stream.write(chunk)
                break
            except requests.RequestException:
                partial.unlink(missing_ok=True)
                if attempt == 2:
                    # Do not persist HTTP exceptions: these can contain credentials.
                    raise RuntimeError('UR download failed after 3 attempts') from None
                time.sleep(2 ** attempt)
    if not partial.stat().st_size:
        partial.unlink()
        raise ValueError('Empty UR download')
    partial.rename(target)
    metadata = {
        'source': 'ur_register', 'download_url': None if local else url,
        'retrieved_at': utc_now(), 'source_as_of': None,
        'http_last_modified': modified, 'sha256': digest(target),
        'size': target.stat().st_size, 'path': str(target),
        'origin': 'local_replay' if local else 'download',
    }
    (directory / 'metadata.json').write_text(json.dumps(metadata, indent=2), encoding='utf-8')
    return metadata


def normalized_date(value: str) -> str | None:
    if not value:
        return None
    # UR exports ISO dates, sometimes with a midnight time suffix.
    try:
        return datetime.fromisoformat(value.replace('Z', '+00:00')).date().isoformat()
    except ValueError:
        raise ValueError('Invalid UR date') from None


def extract(path: Path, numbers: set[str]) -> dict[str, dict]:
    found = {}
    with path.open(encoding='utf-8-sig', newline='') as stream:
        reader = csv.DictReader(stream, delimiter=';', strict=True)
        if not REQUIRED.issubset(reader.fieldnames or []):
            raise ValueError('UR schema changed: required columns are missing')
        total = 0
        for row in reader:
            total += 1
            if None in row or any(value is None for value in row.values()):
                raise ValueError(f'Malformed UR CSV row {reader.line_num}')
            number = row['regcode'].strip()
            if number not in numbers:
                continue
            if number in found:
                raise ValueError(f'Duplicate registration number in UR: {number}')
            record = {key: row[key].strip() or None for key in REQUIRED}
            if not record['name']:
                raise ValueError(f'Missing official name for {number}')
            record['registered'] = normalized_date(record['registered'])
            record['terminated'] = normalized_date(record['terminated'])
            record['source_row'] = reader.line_num
            # This is a registry status, not a claim about economic activity.
            record['registry_status'] = 'TERMINATED' if record['terminated'] else 'REGISTERED'
            found[number] = record
    if not total:
        raise ValueError('UR file contains no records')
    return found


def same_name(left: str, right: str) -> bool:
    def normalize(value):
        return re.sub(r'\s+', ' ', value).strip().casefold()
    return normalize(left) == normalize(right)
