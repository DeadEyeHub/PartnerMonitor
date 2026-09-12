import re
import sqlite3
from pathlib import Path

from .pipeline import SCHEMA


def quote(name):
    if not re.fullmatch('[A-Za-z_][A-Za-z_0-9]*', name):
        raise ValueError('Invalid SQL identifier')
    return '"' + name + '"'


def connect(data_dir: Path, sources):
    data_dir.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(data_dir / 'monitoring.db', timeout=30)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    db.executescript(SCHEMA)
    db.executescript('''
    CREATE TABLE IF NOT EXISTS schema_versions (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL);
    INSERT OR IGNORE INTO schema_versions VALUES (2, CURRENT_TIMESTAMP);
    CREATE TABLE IF NOT EXISTS run_companies (
      run_id TEXT NOT NULL REFERENCES monitoring_runs, registration_number TEXT NOT NULL REFERENCES companies,
      role TEXT NOT NULL, depth INTEGER NOT NULL, PRIMARY KEY(run_id,registration_number));
    CREATE TABLE IF NOT EXISTS source_checks (
      run_id TEXT NOT NULL REFERENCES monitoring_runs, source TEXT NOT NULL,
      status TEXT NOT NULL, snapshot_id TEXT REFERENCES source_snapshots,
      rows_scanned INTEGER NOT NULL DEFAULT 0, rows_imported INTEGER NOT NULL DEFAULT 0,
      error_type TEXT, detail TEXT, PRIMARY KEY(run_id,source));
    CREATE TABLE IF NOT EXISTS company_source_checks (
      run_id TEXT NOT NULL REFERENCES monitoring_runs, registration_number TEXT NOT NULL REFERENCES companies,
      source TEXT NOT NULL, status TEXT NOT NULL, row_count INTEGER NOT NULL,
      PRIMARY KEY(run_id,registration_number,source));
    CREATE TABLE IF NOT EXISTS financial_metrics (
      run_id TEXT NOT NULL REFERENCES monitoring_runs, registration_number TEXT NOT NULL REFERENCES companies,
      statement_id TEXT NOT NULL, file_id TEXT NOT NULL, metric TEXT NOT NULL,
      value TEXT, status TEXT NOT NULL, PRIMARY KEY(run_id,statement_id,file_id,metric));
    CREATE TABLE IF NOT EXISTS sanction_entities (
      run_id TEXT NOT NULL REFERENCES monitoring_runs, source TEXT NOT NULL, entity_id TEXT NOT NULL,
      entity_type TEXT, program TEXT, legal_url TEXT, raw_xml TEXT NOT NULL,
      snapshot_id TEXT NOT NULL REFERENCES source_snapshots,
      PRIMARY KEY(run_id,source,entity_id));
    CREATE TABLE IF NOT EXISTS sanction_names (
      run_id TEXT NOT NULL, source TEXT NOT NULL, entity_id TEXT NOT NULL, name TEXT NOT NULL,
      normalized_name TEXT NOT NULL,
      FOREIGN KEY(run_id,source,entity_id) REFERENCES sanction_entities,
      PRIMARY KEY(run_id,source,entity_id,name));
    CREATE TABLE IF NOT EXISTS sanction_attributes (
      run_id TEXT NOT NULL, source TEXT NOT NULL, entity_id TEXT NOT NULL,
      attribute_index INTEGER NOT NULL, attribute_type TEXT NOT NULL, value_json TEXT NOT NULL,
      FOREIGN KEY(run_id,source,entity_id) REFERENCES sanction_entities,
      PRIMARY KEY(run_id,source,entity_id,attribute_index));
    CREATE TABLE IF NOT EXISTS sanction_identifiers (
      run_id TEXT NOT NULL, source TEXT NOT NULL, entity_id TEXT NOT NULL,
      identifier_index INTEGER NOT NULL, identifier_type TEXT, number TEXT, country TEXT,
      FOREIGN KEY(run_id,source,entity_id) REFERENCES sanction_entities,
      PRIMARY KEY(run_id,source,entity_id,identifier_index));
    CREATE TABLE IF NOT EXISTS tax_debt (
      run_id TEXT NOT NULL REFERENCES monitoring_runs, registration_number TEXT NOT NULL REFERENCES companies,
      effective_date TEXT, published_debt_amount TEXT, publication_threshold TEXT NOT NULL,
      query_status TEXT NOT NULL, retrieved_at TEXT NOT NULL, evidence_url TEXT,
      snapshot_id TEXT REFERENCES source_snapshots, detail TEXT,
      PRIMARY KEY(run_id,registration_number));
    CREATE INDEX IF NOT EXISTS sanction_name_lookup ON sanction_names(normalized_name);
    CREATE VIEW IF NOT EXISTS v_source_status AS
      SELECT c.*, json_extract(s.metadata_json,'$.retrieved_at') retrieved_at,
        json_extract(s.metadata_json,'$.source_as_of') source_as_of,
        json_extract(s.metadata_json,'$.sha256') sha256,
        json_extract(s.metadata_json,'$.path') snapshot_path
      FROM source_checks c LEFT JOIN source_snapshots s USING(snapshot_id);
    ''')
    for source in sources:
        if source['format'] != 'csv':
            continue
        table = quote(source['table'])
        columns = ','.join(quote(c) + ' TEXT' for c in source['columns'])
        db.execute(f'''CREATE TABLE IF NOT EXISTS {table} (
          run_id TEXT NOT NULL REFERENCES monitoring_runs,
          registration_number TEXT NOT NULL REFERENCES companies,
          snapshot_id TEXT NOT NULL REFERENCES source_snapshots, source_row INTEGER NOT NULL,
          record_key TEXT NOT NULL, row_hash TEXT NOT NULL, raw_json TEXT NOT NULL,
          {columns}, PRIMARY KEY(run_id,record_key))''')
        db.execute(f'CREATE INDEX IF NOT EXISTS {quote("idx_"+source["table"]+"_company")} ON {table}(run_id,registration_number)')
    db.executescript('''
    CREATE VIEW IF NOT EXISTS v_company_overview AS
      SELECT r.run_id,r.registration_number,r.name,r.type,r.address,r.registered,r.terminated,
        CASE WHEN r.terminated IS NULL THEN 'REGISTERED' ELSE 'TERMINATED' END registry_status,
        rc.role,rc.depth,
        (SELECT COUNT(*) FROM company_source_checks c WHERE c.run_id=r.run_id
          AND c.registration_number=r.registration_number AND c.status IN ('FOUND','NO_RECORDS','LOADED')) sources_checked,
        (SELECT COUNT(*) FROM company_source_checks c WHERE c.run_id=r.run_id
          AND c.registration_number=r.registration_number) sources_total
      FROM registry r JOIN run_companies rc USING(run_id,registration_number);
    CREATE VIEW IF NOT EXISTS v_financials AS
      SELECT f.run_id,f.registration_number,f.id statement_id,f.file_id,f.year,f.year_started_on,f.year_ended_on,
        f.source_type,f.source_schema,f.currency,f.rounded_to_nearest,f.employees,
        b.total_assets,b.total_current_assets,b.current_liabilities,b.non_current_liabilities,b.equity,
        i.net_turnover,i.net_income
      FROM financial_statements f
      LEFT JOIN balance_sheets b ON b.run_id=f.run_id AND b.statement_id=f.id AND b.file_id=f.file_id
      LEFT JOIN income_statements i ON i.run_id=f.run_id AND i.statement_id=f.id AND i.file_id=f.file_id;
    CREATE VIEW IF NOT EXISTS v_vat AS
      SELECT *,CASE lower(Aktivs) WHEN 'ir' THEN 'ACTIVE' WHEN 'nav' THEN 'INACTIVE' ELSE 'UNKNOWN' END vat_state
      FROM vat_status;
    CREATE VIEW IF NOT EXISTS v_insolvency AS
      SELECT p.*,CASE
        WHEN p.proceeding_started_on IS NULL THEN 'UNKNOWN'
        WHEN date(p.proceeding_started_on)>date(r.started_at) THEN 'FUTURE'
        WHEN p.proceeding_ended_on IS NOT NULL AND date(p.proceeding_ended_on)<=date(r.started_at) THEN 'ENDED'
        ELSE 'ACTIVE' END proceeding_state
      FROM insolvency_proceedings p JOIN monitoring_runs r USING(run_id);
    CREATE VIEW IF NOT EXISTS v_vid_activity AS
      SELECT p.*,CASE
        WHEN p.Lemuma_par_atjaunosanu_datums IS NOT NULL
          AND date(p.Lemuma_par_atjaunosanu_datums)<=date(r.started_at)
          AND (p.Aizliegts_veikt_darijumus_no IS NULL OR date(p.Lemuma_par_atjaunosanu_datums)>=date(p.Aizliegts_veikt_darijumus_no)) THEN 'RESTORED'
        WHEN p.Aizliegts_veikt_darijumus_no IS NULL THEN 'UNKNOWN'
        WHEN date(p.Aizliegts_veikt_darijumus_no)>date(r.started_at) THEN 'FUTURE'
        WHEN p.Aizliegts_veikt_darijumus_lidz IS NOT NULL AND date(p.Aizliegts_veikt_darijumus_lidz)<date(r.started_at) THEN 'ENDED'
        ELSE 'SUSPENDED' END activity_state
      FROM vid_suspensions p JOIN monitoring_runs r USING(run_id);
    ''')
    db.commit()
    return db
