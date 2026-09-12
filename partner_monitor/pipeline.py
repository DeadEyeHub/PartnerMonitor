import json
import sqlite3
import uuid
from pathlib import Path

from .inputs import read_companies
from .ur import extract, same_name, snapshot, utc_now

SCHEMA = '''
CREATE TABLE IF NOT EXISTS monitoring_runs (
 run_id TEXT PRIMARY KEY, started_at TEXT NOT NULL, finished_at TEXT,
 status TEXT NOT NULL, error_type TEXT);
CREATE TABLE IF NOT EXISTS source_snapshots (
 snapshot_id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES monitoring_runs,
 source TEXT NOT NULL, metadata_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS companies (registration_number TEXT PRIMARY KEY);
CREATE TABLE IF NOT EXISTS company_observations (
 run_id TEXT NOT NULL REFERENCES monitoring_runs,
 registration_number TEXT NOT NULL REFERENCES companies,
 snapshot_id TEXT REFERENCES source_snapshots,
 query_status TEXT NOT NULL, input_json TEXT NOT NULL,
 official_json TEXT, name_matches INTEGER,
 PRIMARY KEY (run_id, registration_number));
'''


def run(input_path: Path, data_dir: Path, url: str, local_snapshot: Path | None = None) -> dict:
    companies = read_companies(input_path)
    data_dir.mkdir(parents=True, exist_ok=True)
    run_id = uuid.uuid4().hex
    connection = sqlite3.connect(data_dir / 'monitoring.db', timeout=30)
    try:
        connection.execute('PRAGMA foreign_keys=ON')
        connection.executescript(SCHEMA)
        with connection:
            connection.execute('INSERT INTO monitoring_runs VALUES (?, ?, NULL, ?, NULL)',
                               (run_id, utc_now(), 'RUNNING'))
            for company in companies:
                connection.execute('INSERT OR IGNORE INTO companies VALUES (?)',
                                   (company['registration_number'],))
                connection.execute('INSERT INTO company_observations VALUES (?, ?, NULL, ?, ?, NULL, NULL)',
                                   (run_id, company['registration_number'], 'NOT_CHECKED', json.dumps(company, ensure_ascii=False)))
        try:
            meta = snapshot(data_dir / 'raw' / run_id, url, local_snapshot)
            snapshot_id = uuid.uuid4().hex
            with connection:
                connection.execute('INSERT INTO source_snapshots VALUES (?, ?, ?, ?)',
                                   (snapshot_id, run_id, 'ur_register', json.dumps(meta)))
                connection.execute('UPDATE company_observations SET snapshot_id=? WHERE run_id=?',
                                   (snapshot_id, run_id))
            records = extract(Path(meta['path']), {item['registration_number'] for item in companies})
            with connection:
                for company in companies:
                    record = records.get(company['registration_number'])
                    matches = same_name(company['name'], record['name']) if record and company['name'] else None
                    connection.execute('''UPDATE company_observations
                        SET query_status=?, official_json=?, name_matches=?
                        WHERE run_id=? AND registration_number=?''',
                        ('FOUND' if record else 'NOT_FOUND', json.dumps(record, ensure_ascii=False) if record else None,
                         matches, run_id, company['registration_number']))
                connection.execute('UPDATE monitoring_runs SET finished_at=?, status=? WHERE run_id=?',
                                   (utc_now(), 'COMPLETED', run_id))
            return {'run_id': run_id, 'status': 'COMPLETED', 'companies': len(companies),
                    'found': len(records), 'not_found': len(companies) - len(records),
                    'snapshot_sha256': meta['sha256'], 'database': str(data_dir / 'monitoring.db')}
        except Exception as exc:
            connection.rollback()
            with connection:
                connection.execute('UPDATE monitoring_runs SET finished_at=?, status=?, error_type=? WHERE run_id=?',
                                   (utc_now(), 'FAILED', type(exc).__name__, run_id))
                connection.execute("UPDATE company_observations SET query_status='ERROR' WHERE run_id=?", (run_id,))
            raise
    finally:
        connection.close()
