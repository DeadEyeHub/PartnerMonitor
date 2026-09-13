# Partner Monitor

Local research software for collecting Latvian company records, screening names
against sanctions lists, analyzing adverse media and comparing assessments over time.
Official observations, provider evidence and deterministic scoring remain separate.

**Noncommercial use only.** See [LICENSE.md](LICENSE.md). There is no startup
acceptance dialog; the license still applies. This software is not legal clearance
or a substitute for reviewing source evidence.

## Features

- Company input by registration number, a manually assembled list, or CSV/XLSX.
- UR company, ownership, officer, proceeding and financial records; VID data and tax debt.
- Name screening against EU, Latvian and UN lists supplied by FID.
- Tavily search/extraction and model analysis through OpenRouter.
- Versioned scoring with preserved evidence, HTML/CSV reports and optional CLI Excel export.
- Manual monitoring against a specifically selected saved assessment.
- Readable missing-data explanations and separate provider request/response logs.

## Requirements

- Docker Desktop with Linux containers and Docker Compose.
- Python 3.10+ on the host for the local launcher.
- Host `openpyxl` for XLSX upload validation: `python -m pip install openpyxl`.
- Tavily and OpenRouter keys for news analysis; official collection does not use these keys.
- For Excel: the configured Codex workspace Node runtime with `@oai/artifact-tool`.
  This runtime is external to the repository and is not installed by Docker.

The full UI workflow includes Excel export. If that runtime is unavailable, use the
CLI to generate HTML/CSV/JSON, or configure the runtime before using the UI.
A missing Excel runtime may mark the launcher job failed after HTML/CSV were generated.

## Setup

Run commands from this repository directory in PowerShell:

```powershell
# First setup only: do not overwrite an existing .env.
if (!(Test-Path .env)) { Copy-Item .env.example .env }
New-Item -ItemType Directory -Force data/input, data/reports | Out-Null
python -m pip install openpyxl
docker compose build collector
./scripts/Start-UI.ps1
```

Set `TAVILY_API_KEY`, `OPENROUTER_API_KEY` and the desired `OPENROUTER_MODEL` in
`.env` before starting a full assessment. The model identifier must be available
through your provider account. `.env.example` documents defaults and optional limits.
Never put real keys in tracked configuration, input files, URLs or CLI arguments.

The UI opens at [127.0.0.1:18764](http://127.0.0.1:18764/). Choose another port with
`./scripts/Start-UI.ps1 -Port 18765`. SQLite has no network port. Keep Docker and
the launcher running until the job finishes; closing a browser tab does not cancel it.

## Create a report

Choose **Create report**, then an input method:

| Method | Behavior |
|---|---|
| Single company | Enter an 11-digit registration number. |
| Build a company list | Add numbers using **Add company** or Enter; review/remove with **Show list**. |
| Company file | Upload or select a CSV/XLSX file. |

Manual lists accept 1–100 unique numbers. Names already present in the local database
are shown beside numbers; lookup requires Docker but makes no external provider calls.
The manual list is page state and is lost on reload until submitted.

**Create report starts paid provider requests without a separate checkbox.** The UI
analyzes all selected root companies. Per-company budgets still apply, but are not
a guaranteed currency spending cap. Related official records follow the ownership
traversal rules and do not automatically receive root-company news analysis.

The pipeline collects official data, searches and analyzes news, generates reports,
then exports Excel. Only one job can run through a launcher instance at a time;
independent CLI processes are not covered by that lock.

### File format

```csv
registration_number,name
40103485560,
```

`registration_number` is required: exactly 11 ASCII digits, stored as text to preserve
leading zeros. Optional columns: `name`, `partner_type`, `comment`, `business_unit`.
CSV accepts UTF-8 (including BOM), comma/semicolon/tab separators. XLSX reads the
first worksheet with headers in row one. Invalid or duplicate numbers are rejected.
UI upload size is limited to 5 MB. No sample input is required or guaranteed to exist
in a fresh clone; `data/` is ignored by Git.

## Monitoring

Choose **Monitoring**, select a previous report, optionally open it using
**Open saved report**, then press **Check for changes**.

The launcher uses the selected report's root-company list, collects fresh official
observations and performs new paid media analysis. The result compares with that
specific assessment, not whichever report happened to run last. The summary shows
old/new scores, score differences and recorded changes; detailed changes follow.

Only baselines with the current scoring version are offered. When methodology changes,
create a fresh baseline. Earlier reports remain archived. Source failures are not
interpreted as removal of an earlier official problem. Monitoring is manual; there
is no automatic schedule, notification service or background watcher.

## Reports and interpretation

| Artifact | Purpose |
|---|---|
| `data/reports/latest.html` | Overview, events, changes, missing checks and expandable evidence |
| `data/reports/latest.csv` | Eight-field root-company overview, UTF-8 BOM |
| `data/reports/latest.xlsx` | Overview, Findings, Financials, Sanctions, Changes, Data Quality |
| `data/reports/latest.workbook.json` | Shared report payload for workbook generation |
| `data/reports/report-<assessment_id>.html/.csv` | Archived generated assessment |
| `data/reports/assessments/<assessment_id>.json` | Preserved assessment state and comparison facts |
| `data/reports/web-logs/<job_id>/` | Execution log plus `tavily.html` and `model.html` |
| `data/reports/model-final-<job_id>.html` | Saved company-level model results |

CSV fields: Company, Registration number, Reliability score, Risk class, Coverage,
Main reason, New findings, Recommended action. Excel export is a separate host step;
Docker alone generates HTML/CSV/JSON. Earlier snapshots without archived HTML can be
viewed through the UI's saved-report renderer.

`COMPLETED`, `PARTIAL` and `FAILED` describe execution. `PROVISIONAL` describes an
assessment with incomplete checks. A completed collection need not mean completed
news analysis. Coverage measures seven areas and is not a probability of correctness.
Missing data is not replaced by zero and does not itself deduct points.

Weights and thresholds are defined in [config/risk_rules.json](config/risk_rules.json)
and rendered into the report. See [assessment methodology](docs/assessment.md).
Financial amounts account for currency and scale. Negative-equity duration penalties
do not stack; the large annual-loss penalty is separate. Event grouping preserves
all evidence and uses the largest applicable penalty within one grouped event.

## Evidence limitations

- FID supplies all three sanctions lists. Direct EU/UN integration is not implemented.
  A name candidate requires identity/applicability review; similarity is not identity.
  No automatic ownership/control attribution, sectoral restrictions or legal clearance.
- VID uses its legal-person form and an available working date. It verifies company/date
  and saves HTML/PDF evidence. Valid HTML can be imported if the PDF download is unavailable;
  the missing PDF is recorded. Conflicting documents or unrecognized results are rejected.
  No published debt above the threshold is not proof of zero debt. See source dates.
- News analysis works on bounded evidence excerpts. An explanation and separate yes/no
  relevance verdict do not prove misconduct. Ambiguity and incomplete work remain visible.
- Exact quotes (whitespace normalized), reviewed case links and explicit Latvian civil-case
  identifiers can group events. Fuzzy similarity alone does not automatically merge cases.

## CLI

Replace `RUN_ID`, `JOB_ID` and `ASSESSMENT_ID` with actual identifiers. Paths under
`/input` refer to local `data/input`; `/reports` refers to local `data/reports`.

```powershell
# Official collection only; no Tavily/model calls.
docker compose run --rm collector collect --input /input/companies.csv
# Full workflow, all root companies. Paid calls.
docker compose run --rm collector pipeline --input /input/companies.csv --limit 0
# Inspect saved data and regenerate reports without paid calls.
docker compose run --rm collector status
docker compose run --rm collector company 40103485560 --run RUN_ID
docker compose run --rm collector report --run RUN_ID
# Preview the scope of a new web job without running it.
docker compose run --rm collector web --run RUN_ID --limit 0 --dry-run
# Inspect/resume an existing compatible media job; resume can incur charges.
docker compose run --rm collector web-status --job JOB_ID
docker compose run --rm collector pipeline --job JOB_ID
# Replay official snapshots but refresh VID debt.
docker compose run --rm collector collect --input /input/companies.csv --replay RUN_ID --refresh-debt
```

CLI `pipeline --limit` defaults to 3; use `0` for all roots. A plain `collect --replay`
reuses official snapshots. `pipeline --input ... --replay ...` still performs paid
news work. CLI `--baseline ASSESSMENT_ID` compares against that report, but does not
replace the explicitly supplied input list; use the UI for automatic scope reuse.

```powershell
# Build Excel from saved data; no provider calls.
./scripts/Finish-Report.ps1 -Run RUN_ID
# Export an existing workbook payload only.
./scripts/Finish-Report.ps1 -SkipReport
# Override the external workspace dependency root when necessary.
./scripts/Finish-Report.ps1 -Run RUN_ID -RuntimeRoot "C:/path/to/dependencies"
```

## Storage and maintenance

The Compose `monitoring-data` volume holds `/data/monitoring.db`, source objects,
run manifests and durable provider journals. Local `data/input` and `data/reports`
are bind-mounted separately. Back up both the volume and local reports/inputs;
reports alone are not a database backup. Avoid deleting the volume during upgrades.

After source/configuration changes, rebuild the collector. After launcher changes,
restart the launcher when idle. Changing `.env` does not require rebuilding the image;
new containers read it, while existing jobs retain their model/budget settings.

```powershell
python -m pip install -r requirements.txt
python -B -m unittest discover -s tests -q
# Preview allowed temporary-artifact cleanup; --apply performs it.
python -m partner_monitor.artifact_cleanup
```

See [artifact retention](docs/artifact-retention.md),
[media pipeline operations](docs/media-pipeline.md) and
[completed cleanup](docs/cleanup-review.md). Cleanup does not delete evidence or history.

## Code map

| Modules | Responsibility |
|---|---|
| `launcher`, `launcher_http`, `launcher_jobs`, `launcher_history` | Local UI server, jobs and report history |
| `collection`, `downloads`, `normalize`, `database` | Official ingestion, snapshots and normalization |
| `debt_browser`, `debt` | VID form evidence and import |
| `web_media`, `media_selection`, `web_logging` | Paid provider workflow, evidence selection and logs |
| `assessment`, `event_identity`, `methodology` | Versioned deterministic scoring and event grouping |
| `report_data`, `report_view`, `report_html`, `report_renderer` | Shared payload, readable sections and HTML |
| `scripts/Finish-Report.ps1`, `scripts/build-workbook.mjs` | Host Excel export |

Secrets, database files, source snapshots and personal input/report data are excluded
from Git. The UI is loopback-only and is not designed as a publicly hosted service.
