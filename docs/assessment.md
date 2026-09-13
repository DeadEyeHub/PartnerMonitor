# Assessment methodology

The source/model data are immutable. Scoring is deterministic and separate from model
relevance verdicts. `config/risk_rules.json` owns the weights and thresholds. Change
its version when changing methodology; different versions start a new comparison baseline.

| Rule | Deduction | Evidence |
|---|---:|---|
| sanctions | 100 | A list candidate with a current-run review confirming identity **and applicability to the assessed company** |
| cartel | 30 | A reported cartel event, with procedural status preserved |
| court_dispute | 5 | Court dispute or insolvency petition, without implying proven misconduct |
| other_negative | 15 | Other validated adverse media event, published VID debt, active UR insolvency/legal protection, VID suspension, VID rating C, or negative equity in the latest unambiguous annual statement |
| persistent_negative_equity | 30 total | Negative equity in each of the three latest consecutive reporting years; replaces the single-year deduction |

Start at 100, floor at 0. Below 70 is High / Not recommended; 70–99 is Moderate /
Cooperate with caution; 100 is Low / Eligible for cooperation. These are business
policy labels, not probabilities. Missing checks never add points or subtract points.
They mark the recommendation provisional and identify the missing checks explicitly.
No expiry period has been agreed: historical media events remain scored until an
explicit evidence review excludes them. An ongoing petition is not proven nonpayment.
Financial losses, revenue decline and low ratios remain visible evidence, without
additional arbitrary thresholds. Negative equity uses only one latest-year statement;
ambiguous multiple filings are flagged rather than silently choosing one.
For the three-year rule, the window ends at the latest imported reporting year and
requires exactly one statement with a finite negative equity value in each year.
Missing years are never bridged using older statements. Missing/invalid equity or
ambiguous filings prevent the 30-point deduction; a verified negative latest year
still incurs 15 points. Zero or positive latest equity removes the equity deduction;
earlier negative years remain visible in Financials. The same equity event ID is used
for both durations, so the penalties never stack.

## One event, multiple publications

Identical quoted evidence is scored once. Cross-publication links use a reviewed
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
