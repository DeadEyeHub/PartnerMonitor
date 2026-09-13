# Partner Monitor

Collect official data about Latvian companies in SQLite. The first stage covers
fact ingestion, run history, source quality checks, and result inspection.
Sanctions name screening produces review candidates. Adverse-media search and LLM
extraction are implemented; the final risk engine remains a later stage.

## Run and inspect

Run commands from this directory. Rebuilding preserves the existing
`monitoring-data` volume. SQLite does not require a network port.

```sh
docker compose build collector
docker compose run --rm collector collect --input /input/companies.demo.csv
docker compose run --rm collector status
docker compose run --rm collector report
docker compose run --rm collector company REGISTRATION_NUMBER
```

Open `data/reports/latest.html` in a browser to inspect source statuses,
companies, and expandable data tables. Report labels are in English; official
names, source field identifiers, and source text retain their original language.

The local `data/input/companies.demo.csv` contains 25 real SIA companies whose
names contain `būv` and whose source registry records have no termination date.
This is a technical sample, not a verified construction-sector classification by NACE.
Place your own CSV/XLSX in `data/input/`.
Required column: `registration_number` (11 digits, preferably stored as text).
Optional columns: `name`, `partner_type`, `comment`, `business_unit`.
XLSX uses the first worksheet and its first row as headers. CSV uses UTF-8 with
commas, semicolons, or tabs. Duplicate and invalid numbers are rejected before
downloading. Input files and collected data are excluded from Git.

## Credentials

If `.env` is missing, copy `.env.example` to `.env`.
Store all usernames, passwords, and API keys only in the local `.env`, which is
excluded from Git and the Docker build context. `.env.example` contains empty
secret fields and public settings only. Current public downloads need no keys.
Override a source URL with `<SOURCE_ID>_URL`, for example `UR_MEMBERS_URL`.
Do not include credentials in URLs or command arguments.

## Sources and tables

Actual URLs, CSV headers, and keys were checked on 2026-09-12 and recorded in
`config/sources.json`. There are 22 file sources plus a separate tax debt evidence import.

| Source | Tables |
|---|---|
| UR: registry and historical names | `registry`, `company_names` |
| UR: SIA members and AS stockholders | `members`, `stockholders` |
| UR: beneficial owners and officers | `beneficial_owners`, `officers` |
| UR: proceedings and restrictions | `insolvency_proceedings`, `liquidations`, `activity_restrictions`, `securing_measures` |
| UR: sanctions-related information | `ur_sanctions` |
| UR: financial statements | `financial_statements`, `balance_sheets`, `income_statements`, `cash_flow_statements`, `financial_metrics` |
| VID | `vat_status`, `vid_suspensions`, `vid_ratings`, `tax_payments`, `tax_debt` |
| FID: EU, Latvian and UN lists | `sanction_entities`, `sanction_names`, `sanction_identifiers`, `sanction_attributes` |

Official catalogs: [UR](https://data.gov.lv/dati/dataset/uz),
[financial statements](https://data.gov.lv/dati/dataset/gada-parskatu-finansu-dati),
[VID](https://www.vid.gov.lv/lv/atvertie-dati-nodoklu-muitas-un-akcizes-precu-aprites-joma),
[FID](https://sankcijas.fid.gov.lv/lv/meklet-sankciju-sarakstos).

## Storage and normalization

- `/data/raw/objects/<sha256>.<format>`: immutable source files; identical content is stored once.
- `/data/raw/runs/<run_id>.json`: input list, source definitions, snapshots, and ownership traversal limits.
- `/data/raw/cache/`: pointers used for conditional ETag/Last-Modified requests.
- `/data/monitoring.db`: records grouped by run; earlier observations are preserved.

Each domain row has `run_id`, `registration_number`, `snapshot_id`, `source_row`,
`row_hash`, and `raw_json`. Normalized source fields are separate SQL columns.
Identifiers remain text, dates use ISO format, and amounts use exact decimal strings.
Missing values are NULL; actual zero values are preserved. Initial prototype tables
remain in the database.

Financial records join on both `statement_id` and `file_id`. Year, reporting period,
statement type, currency, and `rounded_to_nearest` are retained; different statements
for the same year are not merged. Amounts retain source units, so monetary comparisons
must account for rounding units. VID tax payments use EUR thousands as specified by
the source field names. The `current_ratio`, `debt_to_assets`, and `net_margin` metrics
use values from a single statement. Missing inputs or nonpositive denominators produce
NULL with a reason. Annual growth and selection of a single latest statement are not
implemented yet.

Ownership traversal follows only owner numbers present in the Latvian registry.
Default depth is 2; `--ownership-depth` supports up to 5. Cycles are not repeated,
and reaching the limit generates a warning. ROOT/RELATED roles and depth are stored
in `run_companies`. These are ownership links, not conclusions about sanctions control.

## SQL inspection

```sh
docker compose run --rm sqlite
```

```sql
.headers on
.mode box
SELECT run_id, started_at, status FROM monitoring_runs ORDER BY started_at DESC;
SELECT source, status, rows_imported, detail FROM v_source_status WHERE run_id = 'RUN_ID';
SELECT * FROM v_company_overview WHERE run_id = 'RUN_ID';
SELECT * FROM v_financials WHERE run_id = 'RUN_ID' AND registration_number = 'REGISTRATION_NUMBER';
SELECT * FROM v_vat WHERE run_id = 'RUN_ID';
SELECT * FROM v_insolvency WHERE run_id = 'RUN_ID';
SELECT * FROM v_vid_activity WHERE run_id = 'RUN_ID';
SELECT * FROM tax_debt WHERE run_id = 'RUN_ID';
PRAGMA integrity_check;
PRAGMA foreign_key_check;
```

`v_insolvency` distinguishes ACTIVE/ENDED/FUTURE/UNKNOWN as of the run date.
`v_vid_activity` describes individual restriction records, not an aggregate company
status. An absent record does not automatically mean ACTIVE. UR events without end
dates retain their source information; missing dates are not inferred.

## Statuses and limitations

Run statuses: COMPLETED/PARTIAL/FAILED. Source statuses: COMPLETED/ERROR/MANUAL_REQUIRED.
Company source statuses: FOUND/NO_RECORDS/ERROR/NOT_CHECKED/LOADED.
NO_RECORDS only means no matching row in a successfully processed file. Missing
beneficial owners or financial statements do not imply high risk. LOADED for sanctions
means a list was imported; screening outcomes are stored separately in `sanctions_screening` and `sanctions_candidates`.

Retrieval time, HTTP Last-Modified, and dates within sources are stored separately.
FID is the agreed source for EU, UN and Latvian lists at this stage. XML dates older
than seven days are informational and do not make a run PARTIAL. Missing or future
dates and failed/missing inputs still require attention. A fresh download does not reset the XML date.
All three FID lists are downloaded through the website's POST form with a temporary
CSRF token; tokens are not persisted. The previous collector already used POST;
a diagnostic GET failure did not represent a collector failure.

### Sanctions screening

Each run screens imported company names, historical names, owners, shareholders,
beneficial owners and officers against EU/LV/UN names and aliases. Related companies
are screened in their own rows. Rules normalize case, diacritics and punctuation,
compare reordered name tokens, and propose similar names at a 0.92 string-similarity
threshold when there is a shared token. Scores are string similarity, not a probability
of identity. Names shorter than four characters are not matched automatically.

Every candidate retains the UR record key and snapshot, list entity ID and snapshot,
matched alias, rule, score, legal reference and date-of-birth comparison where available.
All candidates remain NEEDS_REVIEW, including exact names or conflicting birth dates.
No automatic sanction designation, identity clearance, indirect ownership/control
attribution, cross-script transliteration or sectoral-sanctions evaluation is performed.
Corporate legal forms are not stripped. Identifier-only matching is not implemented.
These limitations must be considered when interpreting an absence of candidates.

Company screening states:
- CANDIDATES_REQUIRE_REVIEW: inspect candidates and the coverage limitations.
- INCOMPLETE: no candidates, but a required source/company name is absent or required date metadata is missing/invalid.
- NO_CANDIDATES: no candidates under these rules and no tracked source/date gaps; not legal clearance.
- Historical runs with no screening rows are shown as NOT_PERFORMED.

```sh
docker compose build collector
docker compose run --rm collector collect --input /input/companies.demo.csv --replay RUN_ID --refresh-sanctions
docker compose run --rm collector report
```

This refresh downloads EU/LV/UN while retaining the other snapshots and verified VID
PDFs from the replayed run. A plain replay remains offline. Candidate results and
source-date warnings appear in the HTML report and company inspection command.

The 2026-09-12 source audit found EU generationDate 2026-08-05 (6,234 entities),
LV PublishDate 2018-03-29 (3 entities), and UN dateGenerated 2026-09-04 (736 people,
275 entities). [The UN official page](https://main.un.org/securitycouncil/en/content/un-sc-consolidated-list)
reports the same update date and counts; this is metadata corroboration, not a byte-level
comparison. Direct programmatic access to that page returned HTTP 202 with an empty
body in this environment. [The EU primary service](https://webgate.ec.europa.eu/fsd/fsf)
returned HTTP 401 without authentication. EU and LV currentness is therefore not
independently verified. FID lists cover targeted financial sanctions; the report does
not cover all possible trade/service restrictions or OFAC/UK lists.

### VID tax debt

[VID publishes debt exceeding EUR 150](https://www.vid.gov.lv/lv/nodoklu-paradnieki)
through a separate interactive form. The collector opens Chromium, selects a legal
person, fills the company name, registration number and an available date, and
submits the form. It saves the current HTML response and the official downloaded
PDF as SHA-256-addressed objects. Both documents must agree on the company, date,
status and amount before the result is imported. Unknown wording, missing PDFs,
service errors and CAPTCHA leave the company NOT_CHECKED; CAPTCHA is not solved.

The default date is the third completed working day before today in Europe/Riga,
excluding weekends and Latvian public holidays. Before 07:00 it uses an additional
working-day buffer. Set `VID_DEBT_QUERY_DATE=2026-09-09` in `.env` to request a
specific historical date (ISO format). A date unavailable at VID is never treated
as no debt. The Docker image includes Chromium and ChromeDriver; Chromium runs as
the non-root monitor user, with Docker isolation and without its nested namespace
sandbox because Docker's default seccomp blocks it. Local execution requires
Chrome/Chromium and the Python requirements.

To refresh debt while reusing a previous run's other source snapshots:

```sh
docker compose build collector
docker compose run --rm collector collect --input /input/companies.demo.csv --replay RUN_ID --refresh-debt
docker compose run --rm collector report
```

A plain replay makes no browser/network requests and verifies hashes of saved
evidence, including HTML/PDF artifacts. Browser cookies stay in a temporary profile
and are removed when the browser exits. No session credentials enter Git.

For manually verified evidence, create a UTF-8 CSV with this header:

```text
registration_number,effective_date,published_debt_amount,publication_threshold,query_status,evidence_url
```

Allowed statuses: PUBLISHED_DEBT or NO_PUBLISHED_DEBT_ABOVE_THRESHOLD. The latter
requires an empty amount, not zero. Date and threshold are required. Use a public
VID evidence URL without credentials, and retain the supporting document separately
for verification of manual input. A header template is in `examples/tax_debt.example.csv`.

```sh
docker compose run --rm collector collect --input /input/companies.demo.csv --tax-debt-file /input/tax_debt.csv
```

Alternatively, set `TAX_DEBT_FILE=/input/tax_debt.csv` in `.env`. Companies without
evidence remain NOT_CHECKED. A run with any unverified company debt check is PARTIAL.
The CLI returns exit code 2 for PARTIAL and exit code 1 for errors.

## Replay and comparison

```sh
docker compose run --rm collector collect --input /input/companies.demo.csv --replay RUN_ID
docker compose run --rm collector compare --run NEW_RUN_ID --previous OLD_RUN_ID
docker compose run --rm collector sources
docker compose run --rm collector collect --input /input/companies.demo.csv --sources ur_register,ur_names
```

Replay performs no source downloads and verifies SHA-256 hashes. It creates a new run.
Comparison reports NEW/CHANGED/REMOVED_FROM_SOURCE/UNCHANGED for companies shared by
both runs and successfully imported sources. Removing a row does not mean a risk was
resolved. Unavailable sources produce NOT_COMPARABLE. Changing the input list does
not count as company removal. Sanctions list snapshots can be compared by hash;
row-level XML and risk-event comparisons are planned for a later stage.

## Development

Python 3.10+:

```sh
python -m pip install -r requirements.txt
python -m partner_monitor collect --input data/input/companies.demo.csv
python -m unittest discover -s tests -v
```

Local execution uses `DATA_DIR` from `.env` and a separate database on disk.
Docker uses the shared SQLite volume. `docker compose down -v` deletes that volume
and its data; removing a temporary container does not delete the data.


## Adverse media: name search, triage and evidence

Put `TAVILY_API_KEY`, `OPENROUTER_API_KEY` and the desired `OPENROUTER_MODEL` in the
ignored `.env`. The two-stage pipeline requires strict structured-output support.
It does not set temperature because the tested model endpoints do not support it.

```sh
docker compose build collector
docker compose run --rm collector web --run RUN_ID --limit 2 --dry-run
docker compose run --rm collector pipeline --run RUN_ID --limit 2
docker compose run --rm collector web-status --job JOB_ID
docker compose run --rm collector pipeline --job JOB_ID
```

Search uses current/historical names and separate Latvian topics, never registration
numbers. It requests snippets, not full raw pages. Code first checks for a company
name in the title/snippet. An analyst receives at most 1,200 snippet characters and explains relevance. A second,
fresh request receives the same news and explicit explanation and returns only yes/no.
Both receive source historical-name end dates and code-generated date review flags. Only selected publications proceed to Tavily Extract and evidence analysis.
The evidence model receives at most 5,000 characters of company-centered paragraphs,
not an entire raw page. Snippet triage never creates findings.

Default limits per company/job: ten triaged articles, five evidence articles, five
extraction requests, twenty model HTTP attempts including retries and 60,000 cumulative
model-input characters. Reaching 40,000 provider-reported tokens stops subsequent
model requests; an in-flight response may exceed that threshold. These are workload
bounds, not a guaranteed currency spending cap. Optional lower `WEB_MAX_*` values are
listed in `.env.example`. Limits and the model are pinned when the job starts and
remain in force across resumes. Version-2/3/4 jobs remain readable but cannot be resumed
with the version-5 selection logic; create a new job for the new method.

`web_quality` in the HTML company card shows retrieved results, retained URLs,
name candidates, filters, triage, available evidence, analyses, duplicates, budget gaps
and reported usage. `web_selection` shows snippets and selection reasons. `web_judgments` shows the
analyst explanation, verifier verdict and historical-name date checks. No start dates
are invented when UR only supplies an end date. Findings
remain NEEDS_REVIEW. Excerpt-only results remain EXCERPT_REVIEW and keep coverage
PARTIAL. Similar-event links are review suggestions; they do not merge findings or
confirm an event. Exact cleaned evidence copies skip repeated detailed analysis.

`web` supports separate `--mode search` and `--mode analyze --job JOB_ID` operations.
Analyze mode can call Tavily Extract and therefore also needs the Tavily key. New jobs
perform fresh paid searches. Resume reuses successful queries/triage/analysis and
preserves request-budget reservations, including failed requests. The `pipeline`
command also accepts `--input` to collect official data first, or `--input --replay`
to replay official snapshots. Official replay does not replay paid media searches.

Every job writes a live HTML execution log under `data/reports/web-logs/JOB_ID/index.html`.
After a pass/report, that folder also contains separate `tavily.html` and `model.html`
files containing only provider requests and responses. The durable journal is stored
under `raw/web_logs` in the data volume. Credentials are redacted; request headers
and raw HTTP error bodies are excluded. Provider-returned reasoning fields remain in
the full response. Logs do not reveal internal provider execution.

See [the operation guide](docs/media-pipeline.md) for validation, limitations and the
live comparison. No adverse-media result establishes absence of risk or legal clearance.


## Compact report export

`pipeline` also writes `model-final-JOB_ID.html`: a standalone company summary with
saved finding summaries, source links and final relevance verdicts, without prompts,
analyst explanations or provider metadata. A relevance yes is not a finding of
involvement; missing findings are not a clean bill of health. Only the selected web
job is included, so earlier runs cannot silently populate the new result.

The report command writes both `latest.html` and `latest.csv`. CSV includes only root
companies and exactly the eight Overview fields from task section 26: Company,
Registration number, Reliability score, Risk class, Coverage, Main reason,
New findings, Recommended action. UTF-8 BOM preserves Latvian text in Excel.
Import registration numbers as text. Formula-leading text is escaped for CSV safety.

Reliability score and New findings stay blank until scoring and finding-difference
logic exist; Risk class is NOT_ASSESSED. Main reason lists selected recorded facts
and pending work, not a comprehensive risk result. Coverage is a percentage of seven
equally weighted areas: UR identity, VID rating, VAT lookup, financial data, tax debt,
sanctions name screening and web analysis. No web result is a zero-risk conclusion.
Source dates remain visible in the HTML; raw JSON fields are hidden from its tables.
Old run history is retained. Replay creates a new run with the current FID date policy.
