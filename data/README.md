# data/

Local data lake, ignored by git. Layout (plan §7.2):

- `bronze/` raw files exactly as downloaded, plus `_manifest.parquet`
- `silver/` typed UTC Parquet tables, every published version kept
- `gold/` features, forecasts, dispatch schedules

Rebuild with `make data` (milestone M1).
