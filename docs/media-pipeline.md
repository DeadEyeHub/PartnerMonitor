# Media pipeline operation

## What runs

`pipeline` orchestrates official data, Tavily search, OpenRouter extraction, SQLite
persistence and the existing HTML/CSV report. It accepts exactly one starting point:

- `--run RUN_ID`: reuse an existing official-data run without downloading its sources.
- `--job JOB_ID`: resume the original web job and its company scope.
- `--input /input/companies.csv`: collect official sources first, then process media.
  Add `--replay RUN_ID` to replay saved official snapshots instead of downloading them.
  Replay applies to official data only; a new media job still calls the providers.

Credentials are checked before collection. A partial official run can still be
enriched, but the combined pipeline status remains PARTIAL. The official run's stored
status is never overwritten by media processing. Scoring is still NOT_ASSESSED.

## Preview and run

Run these commands from the repository directory:

```sh
docker compose build collector
docker compose run --rm collector web --run RUN_ID --limit 3 --dry-run
docker compose run --rm collector pipeline --run RUN_ID --limit 3
docker compose run --rm collector web-status --job JOB_ID
docker compose run --rm collector pipeline --job JOB_ID
```

The dry run reads SQLite and lists company identities, search queries, destinations,
request bounds and whether credentials are configured. It does not write to the
database, contact providers, or display keys. It does not verify key validity, credit
balance, model availability or structured-output support.

After verifying a small job, `--limit 25` creates a new job covering up to 25 root
companies, in registration-number order. Previously checked companies will be searched
again in a new job; use `--job` to resume instead. Related companies are not part of
this media scope. Reports contain all official-run companies, including unchecked ones.

The job ID is printed before the first provider request. An interrupted process can
therefore be resumed using saved progress. Its database status stays RUNNING if the
process was killed; inspect it with `web-status` and resume explicitly. Concurrent
execution of the same job is not supported. Resuming uses the job's original requested
model. A changed pipeline/prompt version requires a new job.

## Search and extraction

1. Build at most five queries: registration number with Latvian adverse keywords,
   current name with Latvian and English keywords, and up to two historical names
   with Latvian keywords. Use a quoted trading name when present in the legal name.
   Full official names remain in the identity context.
2. Tavily returns up to five results per query with raw text requested. Save its
   response as a SHA-256-addressed JSON object under `raw/web`.
3. Validate public HTTP(S) URLs, remove tracking parameters and deduplicate URLs and
   identical text. Upgrade snippet-only records when full text becomes available.
   An identical snippet must not suppress its full-text replacement.
4. Send raw article text and company identity to OpenRouter using a strict JSON
   schema. Snippets are not analyzed. Text longer than 18,000 characters is explicitly
   marked truncated and retains PARTIAL coverage.
5. Validate the model result locally. Findings require matched identity and an exact
   evidence quote in the submitted text. Validate categories, dates and confidence.
   Store invalid model responses for inspection, but publish no unsupported finding.
6. Save valid findings as NEEDS_REVIEW. Keep allegations, investigations, reported
   decisions and resolved matters distinct. The LLM does not assign a reliability score.

Tavily receives company names and registration numbers in queries. OpenRouter receives
the current name, number, address, up to two historical names, and article URL, title,
publication date and text. Owner records and VID PDFs are not submitted. Keys come from
the ignored `.env`; authorization headers and provider error bodies are not persisted.
Requests can incur provider charges.

## Bounds, errors and resuming

`web` attempts at most ten articles per company per invocation. `pipeline` runs up to
three analysis passes by default (`--analysis-passes 1..3`), allowing the maximum 25
search results per company to be processed. Automatic continuation processes only
remaining PENDING articles, without retrying previous errors. Explicit resume retries
failed queries and article analyses and reuses successful work. Pending articles have
priority over failed ones. Transient transport errors get up to three attempts per
request; these are not an exact monetary spending cap.

Errors are persisted per query/article as safe local codes, including HTTP status
codes when available. `web-status` lists company checks, article status counts, error
counts, model configuration and total findings without issuing provider requests.

- COMPLETED: the selected job scope finished its tracked checks.
- SEARCHED: search-only mode finished; analysis is pending.
- PARTIAL: failed search/analysis, pending articles, truncated text, snippets or
  uncertain identity remain. Search-only mode also returns PARTIAL on search failure.
- NO_RESULTS (company analysis status): a successfully completed bounded search
  returned no articles. Failed search never receives this status.

HTML and CSV are exported even for ordinary persisted provider errors. The CLI exits
with code 2 for PARTIAL and 1 for configuration or unexpected execution errors.
Unexpected storage/report failures still fail the command; successful provider work
already committed to SQLite remains available for resume.

## Outputs and remaining scope

### Live execution logs

Every `web` and `pipeline` invocation appends to the job's durable event journal:
`/data/raw/web_logs/JOB_ID.jsonl`. Events are flushed to disk before transport starts
and after each response. Resume appends events instead of replacing the earlier log.
An interrupted final JSONL line is ignored when reading; earlier complete events remain.

The HTML log is updated after every event at
`REPORT_DIR/web-logs/JOB_ID/index.html` (normally `data/reports/web-logs` on the host).
Refresh the index during execution. Event numbers open separate HTML detail pages in
the same folder, keeping the timeline small even when article text is large. Copy the
whole job folder when sharing a log. `pipeline --output` places the log alongside that
report. The main HTML report links to available job logs and rebuilds them from JSONL.

The journal records:

- UTC time, company, job, stage, mode, requested model and prompt version;
- exact application request bodies, including queries, prompts and article text;
- every HTTP attempt, status, duration, network error and retry delay;
- successful provider responses, including actual model, finish reason, usage,
  token counts and cost fields when supplied by the provider;
- parsing and evidence-validation outcomes, validation reasons, snapshot paths;
- duplicate decisions, snippet upgrades, reused searches and final article statuses;
- company/pass completion and caught interruptions.

Token usage and cost are provider-reported values, not a separately verified billing
total. Non-streaming requests reveal the sent input and returned output, not internal
model execution or token-by-token progress. A force-killed process may leave a request
without a response event, which remains visible in the timeline.

Authorization headers, cookies and raw HTTP error bodies are not collected. Sensitive
structured fields and configured secret values are masked before writing journals,
HTML and new provider snapshots. Exception messages from transport are not recorded.
Article text and provider output are HTML-escaped, so scripts in source content cannot
execute in a log page. Logs include the company context and article text and remain
under ignored data directories; no credentials or runtime logs belong in Git.

HTML includes job history, per-company queries and errors, article identity assessment
and its reason, source URLs and quoted findings. Each company card uses its latest job
for that official run; this can combine different job dates in one report. CSV retains
the eight task Overview columns. Score and New findings remain blank; risk class is
NOT_ASSESSED. Failed or incomplete media checks do not count toward web coverage.

This implementation does not add risk scoring, finding-level change tracking or the
six-sheet Excel workbook. It does not establish legal clearance or exhaustive media
coverage. Exact evidence grouping is implemented; semantic event deduplication remains
a review task.

## Validation record: 2026-09-12

Offline provider fixtures exercise persistence, separate search/analysis, evidence
validation, same-text snippet upgrades, pinned model resume, pending-article continuation,
partial reports, read-only preview and credential preflight. Fixtures use temporary
databases and never populate the production report.

The production database dry run confirmed both keys are configured, without exposing
them. A live provider request was blocked by the environment's automatic approval
review pending explicit authorization of the data transfer. No successful live search
or model result is claimed by this validation record.
