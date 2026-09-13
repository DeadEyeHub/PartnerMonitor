# Completed cleanup

Each item was implemented in a separate commit:

1. Removed obsolete license-dialog CSS, acceptance/hash state and unused mode descriptions.
2. Split launcher HTTP routing, job execution and saved-report history into modules.
3. Split database inspection, report view models, HTML primitives and report composition.
   Evidence sections use an explicit allowlist.
4. Generate visible methodology from scoring configuration; removed stale duplicate tables.
5. Consolidated provider traces and source rows in technical details; removed repeated root-company summaries.
6. Grouped source-record changes and replaced internal IDs with business labels. Raw identities remain in snapshots.
7. Added opt-in retention for old reproducible workbook artifacts; history and evidence are protected.
8. Added conservative deduplication using whitespace-normalized quotations and explicit Latvian civil-case numbers.
   Reviewed case links take priority. Different or ambiguous case numbers remain separate.

The event-linking change uses methodology risk-v1.3.0. Existing reports remain archived;
new monitoring baselines use the current methodology. Fuzzy semantic matches still need
review and are never automatically merged. No evidence or historical report was deleted.

See `artifact-retention.md` and `assessment.md` for operation and scope.
