# Media pipeline version 5

## Processing sequence

1. Build up to six name-only queries. Current and up to two historical names are
   interleaved across the separate Latvian topics `tiesa`, `kartelis` and
   `maksātnespēja`. The six-query bound can omit later topics when multiple names
   exist. Registration numbers are never search terms. Diacritics and legal prefixes
   are normalized for name matching; quoted trading names are used where available.
2. Tavily Search requests five results per query with `include_raw_content=false`.
   Save the original response and retain unique canonical URLs. A result without
   the company name in its title or snippet becomes FILTERED, with a stored reason.
   This lexical filter can miss abbreviations, spelling variants and indirect mentions.
3. Each retained news item uses two separate model calls, each with fresh system/user
   messages. The first receives company identity, available historical-name end dates,
   title, URL, publication date, criteria and up to 1,200 snippet characters. It writes
   an explicit English explanation of relevance, conflicts and missing evidence.
   The second receives the same inputs plus only that explanation, and must return
   exactly `yes` or `no`. No assistant history, hidden reasoning or encrypted reasoning
   metadata is forwarded. The verifier must check the source and may disagree.
   Invalid verdicts are errors, never silently interpreted as yes. Both calls consume
   the existing persistent request budget. This separation is not an independent
   factual guarantee or a demonstrated hallucination reduction.
4. Only a `yes` verdict permits full-text retrieval through Tavily Extract. No
   requests are made directly to arbitrary article hosts. A returned URL must match
   the requested canonical URL. Missing text is LIMITED_CONTENT, never no risk.
5. Select original lines containing a company name and neighboring lines. Remove
   obvious navigation/link lists and repeated lines. Retain at most 5,000 characters.
   If a suitable company-centered excerpt cannot be selected, do not analyze it.
   Raw responses remain available as evidence snapshots and in provider HTML logs;
   they are not forwarded wholesale to the model.
6. Validate the detailed structured model output against the submitted excerpt.
   Exact quotes, supported categories, valid dates and matched identity are required.
   Findings remain NEEDS_REVIEW. Selected/truncated context is EXCERPT_REVIEW and
   keeps coverage PARTIAL, even if findings were extracted successfully.
7. Skip detailed re-analysis of exact normalized excerpt copies, retaining a link
   to their original article. A duplicate does not fill coverage if the original is
   incomplete or failed. Existing exact-evidence event grouping remains available.
   Additionally, same-company/type/status findings with compatible dates and at least
   five overlapping summary terms (Jaccard >= 0.25) get candidate event links. These
   links require manual review, preserve all source findings, and do not merge events.

## Commands and scope

```sh
docker compose build collector
docker compose run --rm collector web --run RUN_ID --limit 2 --dry-run
docker compose run --rm collector pipeline --run RUN_ID --limit 2
docker compose run --rm collector web-status --job JOB_ID
docker compose run --rm collector pipeline --job JOB_ID
```

`pipeline` accepts exactly one of `--run`, `--job`, or `--input`. `--input --replay`
reuses official snapshots but starts a fresh media job. `--limit` applies only to new
jobs, selecting root companies in registration-number order. Resuming a job retains
its original company scope. Related companies are not automatically searched.

`web --mode search` and `web --mode analyze --job JOB_ID` remain separate entry points.
Analyze mode now needs both keys because selected snippets can require Tavily Extract.
`--dry-run` makes no provider requests or database writes; it previews queries, data
transfers, credential presence and limits. It does not validate provider access.

Version-2/3/4 jobs and official-data history remain readable. A changed prompt/pipeline
version cannot resume an old media job. Start a new job for version 5. The original
requested model and limit values are pinned in job configuration.

## Budgets and retries

Defaults per company/job:

| Bound | Default |
|---|---:|
| Successfully triaged articles | 10 |
| Prepared evidence articles | 5 |
| Tavily extraction HTTP attempts | 5 |
| Model HTTP attempts, including retries | 20 |
| Cumulative serialized model-input characters | 60,000 |
| Stop threshold for reported tokens | 40,000 |
| Snippet characters per triage | 1,200 |
| Excerpt characters per detailed analysis | 5,000 |

Lower bounds can be configured through the commented `WEB_MAX_*` settings in
`.env.example`. They are read before collecting new official data. Invalid values
fail before provider calls. Resuming cannot reset the budget by changing `.env`.

Each actual model/extract HTTP attempt reserves budget in SQLite before transport.
Failures and process interruptions retain their reservations. Transient HTTP/network
errors may retry up to three times, subject to the remaining budget. Returned token
usage and cost are stored per response. Missing usage is not a confirmed zero charge.
The token threshold is checked before subsequent calls; an in-flight response can
overshoot it. Character/request bounds apply independently. No exact currency cap is
claimed, and reported cost does not include Tavily fees unless separately provided.

`pipeline` can run up to three processing passes to drain remaining PENDING rows.
Automatic continuation does not retry errors. Explicit resume can retry ERROR rows
within the original budget; BUDGET_LIMIT rows remain visible and are not retried.
Concurrent execution of the same job is unsupported. Unexpected errors leave the
job resumable; ordinary provider errors are recorded and the report still exports.


## Historical-name dates and recovery

The imported UR name history supplies `date_to` only. `company_context` includes these
source end dates for searched historical names; no start dates are invented. The code
compares a valid supplied publication date against each matched historical end date.
After-end matches are flagged because the article may be retrospective. Earlier dates
still have START_DATE_UNKNOWN, not a confirmed valid interval. Missing/invalid publication
or historical end dates are explicit review flags. Publication dates are not inferred
from URLs and are never substituted for event dates.

The date check and both model outputs are persisted in `web_article_judgment` and shown
under Web judgments in HTML. A successful explanation is committed before verification;
if verification fails, resume retries verification without regenerating the explanation.
Both request/response pairs remain in the standalone model HTML and journal. If a `no`
verdict has unresolved date metadata, the item remains TRIAGE_UNCERTAIN/PARTIAL. A `yes`
verdict allows detailed analysis, but date uncertainties still preserve review status.
The verifier never turns a date mismatch directly into a claim of a different company.

No budget was increased to pay for the extra call. With the same request/text limits,
fewer articles may reach evidence analysis. Budget gaps remain visible.

## Reporting and audit

The main HTML report includes `web_quality`, `web_selection`, findings and candidate
`web_event_links`. Quality distinguishes total retrieved result occurrences, retained
URLs, name candidates, filters, triage, available excerpts, analyzed articles,
duplicates, budget-limited rows, model requests, input characters and reported usage.
Selection shows the stored snippet and the code/model selection reasons.

FILTERED/TRIAGE_REJECTED mean the bounded selection method rejected a result; they
are not proof that a company has no adverse history. COMPLETED describes execution
within the stated selection scope. Missing company names or failed queries cannot
produce NO_RESULTS. Uncertain identity, excerpt-only context, unavailable text and
budget gaps retain PARTIAL. Official collection status is never overwritten.

The live event journal is `raw/web_logs/JOB_ID.jsonl` in the data volume. Its HTML
index is `REPORT_DIR/web-logs/JOB_ID/index.html`; refresh during execution. Every
completed pass/report also creates `tavily.html` and `model.html` in that folder,
containing complete request/response pairs without the event timeline. Tavily output
separates search and extract; model output separates explanation, verification and evidence analysis.
The main report links to the journal, and the journal links to these two files.

Logs preserve prompts, snippets, selected text, provider responses, retries, timing,
validation outcomes, extraction decisions and configured limits. Secrets and sensitive
structured fields are masked. Headers, cookies and raw HTTP error bodies are not
recorded. HTML escapes all source/provider text. Full returned reasoning fields may
be present, including encrypted provider metadata; internal model execution is not
observable. Runtime evidence and reports are ignored by Git.

Compact CSV exports eight Overview fields. Scores and changes are calculated by
the versioned assessment engine and shared with HTML and workbook exports.

Provider usage is recorded per job. It is not a reconciled invoice and does not
include costs that the provider does not return. Validate changes with synthetic
fixtures; production evidence and run-specific results belong outside the repository.
