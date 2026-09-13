# Cleanup review

Reviewed after adding manual monitoring. These are proposals, not deletions.

1. **Remove obsolete launcher UI state.** `ui/style.css` still contains license-dialog
   styles. `launcher.py` retains the old acceptance flag and license hash. `app.js`
   retains descriptions for processing modes no longer offered in the UI. Keep the
   license file and the session/origin checks.
2. **Separate server responsibilities.** `launcher.py` combines HTTP routing, Docker
   process management, saved-report rendering and history lookup. Move these into
   small modules with one shared validated job request. Keep CLI diagnostic modes.
3. **Separate report rendering from data inspection.** `inspection.py` combines SQL,
   evidence export, missing-data explanations and HTML string generation. Introduce
   an explicit report view model and templates; avoid rendering every list in a
   company record automatically.
4. **Make methodology text derive from rules.** Weights live in `config/risk_rules.json`,
   but descriptions are also maintained in HTML and documentation. Generate the
   visible rule table from configuration to prevent drift.
5. **Reduce report repetition.** The overview, scored events, coverage table and
   company cards repeat reasons and warnings. Keep a concise summary, changes and
   missing-data table; put raw source rows and provider traces in technical details.
6. **Improve change labels.** Current changes include internal event IDs and source
   field names. Map them to readable business labels; group field changes belonging
   to one record. Preserve raw identifiers only in expandable evidence.
7. **Define artifact retention.** Keep assessment snapshots, source evidence and
   archived reports. Workbook previews, temporary inspection files and duplicate
   convenience exports can have a documented cleanup policy. Do not delete history
   merely because it is old: monitoring comparisons and evidence links depend on it.
8. **Review event deduplication separately.** Exact quotations and reviewed case links
   are currently used to join evidence. Different articles can still describe the
   same case. Improve event linking with tests before changing scoring; this is a
   correctness task, not a cosmetic cleanup.

Suggested order: obsolete UI state, readable changes/report layout, shared methodology,
then module separation and an explicit artifact-retention policy.
