# Assessment methodology

The source/model data are immutable. Scoring is deterministic and separate from model
relevance verdicts. `config/risk_rules.json` owns the weights and thresholds. Change
its version when changing methodology; different versions start a new comparison baseline.

The report generates its methodology table directly from the configured weights,
financial threshold/window, starting score, floor and recommendation boundary.
See `config/risk_rules.json` for current values instead of maintaining a second table.

Negative equity uses the consecutive period ending in the latest imported year.
One, two and three negative years select a single duration penalty; missing years
are never bridged. Zero or positive latest equity removes that penalty. Ambiguous
filings are flagged. A qualifying annual loss receives one additional penalty,
regardless of how many years within the configured window qualify. Currency and
scale must permit a EUR comparison. Other financial ratios remain evidence.

Missing checks do not change the score. Historical adverse events are not erased
merely because a later search did not return them. Labels are business policy
categories, not probabilities.

## One event, multiple publications

Identical quoted evidence (ignoring whitespace) is scored once. A single explicit
Latvian civil-case number in both evidence quotations also links publications; multiple
case numbers, IDs mentioned only in summaries and fuzzy text are not auto-linked.
Reviewed case IDs override automatic grouping. Cross-publication links use a reviewed
`case_id`; all linked evidence is retained and the largest applicable penalty is charged
once. This prevents a cartel fine and its appeal from adding separate penalties.
The two accepted SKONTO cartel findings are linked in `config/assessment_reviews.json`.
Other fuzzy event links remain suggestions: automatic semantic deduplication is not
claimed. Review distinct quotations about a potentially identical case before treating
their sum as final. Separate case IDs allow separate proven matters to be counted.

The HTML retains official source tables, relationships, annual statements, metrics,
tax evidence/PDFs, sanctions candidates and media processing evidence. Related companies
have separate assessments; their events do not automatically transfer to a root company.
Overview/CSV contains only roots. The five supporting workbook tabs also include related
companies. Financial amounts are converted to EUR units only for known EUR scales;
original statement/file IDs and units remain in the financial rows and HTML.

## Review file

Create `data/reports/assessment-reviews.json` (ignored by Git). Review keys for media
are in each assessment event's `review_keys` in the saved JSON; sanctions keys appear
in the Sanctions worksheet. Each review needs a reviewer and a reason. Examples:

```json
{
  "findings": {
    "MEDIA_REVIEW_KEY": {
      "case_id": "company-court-case-identifier",
      "rule": "cartel",
      "reviewer": "Reviewer name",
      "reason": "Same decision and appeal, identified by court case reference"
    },
    "ANOTHER_MEDIA_KEY": {
      "exclude": true,
      "reviewer": "Reviewer name",
      "reason": "Evidence establishes that the finding was incorrectly attributed"
    }
  },
  "sanctions": {
    "CANDIDATE_REVIEW_KEY": {
      "status": "CONFIRMED_APPLICABLE",
      "run_id": "EXACT_OFFICIAL_RUN_ID",
      "reviewer": "Reviewer name",
      "reason": "Verified identifiers and current applicability to this company"
    }
  }
}
```

Use `FALSE_POSITIVE` for a rejected sanctions candidate. Reviews do not modify the
original name-match result. Confirmation expires for a new official run; a name or
related-person match by itself is insufficient for a 100-point penalty. FID remains
the agreed EU/LV/UN source. Direct EU/UN integration and legal-act monitoring are deferred.

## History and regeneration

The assessment hash pins source data, selected latest media job per company, rules and
review inputs. Results are saved in SQLite and the report history. Changes compare the
last generated assessment with the same methodology, not an arbitrary older collection.
First-run New findings is blank. An unchanged regeneration reuses its immutable snapshot.
Source-field comparisons use natural record keys and compare only available sources.
Disappearance from a failed download is never reported as deletion. Earlier media
events remain scored when a later search does not rediscover them; explicit exclusion
is required. Earlier official negatives survive an unavailable source and are marked
as not revalidated. A successful current official check can supersede an older current
state. Regenerating a historical input does not rewind the latest baseline pointer.

The separate `model-final-JOB.html` remains tied to that job's media findings, with
official facts from its collection run. The main report may additionally retain earlier
scored events, and labels them explicitly.

## Current validation scope

The saved full collection covers 25 root and 3 related companies. Live media evidence
exists for the original two test companies. No paid search was repeated merely to
generate the final exports; other companies have explicit missing-web warnings.
The two earlier test jobs have budget/uncertainty gaps, preserved in coverage and logs.
All 25 scores are therefore provisional. Excel is a fixed assessment snapshot, not
an editable replacement for the scoring engine; regenerate it after changing reviews.
