# Artifact retention

Keep assessment history, archived reports, latest exports, source snapshots, VID
certificates and provider logs. They are evidence or are referenced by reports.
No automatic deletion is enabled.

Reproducible workbook PNG previews and temporary workbook/inspection artifacts
older than seven days can be removed explicitly:

```powershell
python -m partner_monitor.artifact_cleanup
python -m partner_monitor.artifact_cleanup --apply
```

The first command previews exact paths. Only allowlisted regular files under the
reports folder are eligible. There is no recursive deletion. Recent temporary
files are retained so an ongoing export is not interrupted. `--days` must be at
least one. Back up evidence/history separately; never apply this policy to them.
