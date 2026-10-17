# Decision log (ADRs) and verification log

Two logs live here:

1. **ADRs**: architecture and project decisions. Status is one of `Proposed`, `Accepted`, `Superseded by ADR-xxx`, `Rejected`. `[OWNER]` decisions are only `Accepted` once the owner says so.
2. **Verification log (V-xx)**: every `[VERIFY]` item from the plan, checked against a primary source before code depends on it (plan §0 rule 4).

## ADR template

```markdown
### ADR-NNN — Title
- Status: Proposed | Accepted | Superseded by ADR-xxx | Rejected
- Date: YYYY-MM-DD · Decider: owner | executor
- Context: what forces the decision
- Options: A / B / C with trade-offs
- Decision: …
- Consequences: what changes, what we give up, follow-up tasks
```

---

## ADRs

### ADR-000 — Pre-made decisions
- Status: Accepted · Date: 2026-10-15 · Decider: owner (plan §14)
- Decision: Python 3.12 + uv; Parquet + DuckDB; Dagster; MLflow; FastAPI; Streamlit; Pyomo/CVXPY + HiGHS; issue time 09:00 America/Toronto on D−1; 21-quantile grid; splits per plan §10; battery defaults per plan §11; TimesFM-3 research-only; Chronos-2 or in-house models for production.
- Consequences: these values are encoded in `configs/project.yaml` and covered by `PREREGISTRATION.md`.

### ADR-001 — Backfill route for post-May-2025 prices `[OWNER]`
- Status: **Accepted: Grid Status hosted API, free plan** (owner, 2026-10-15; the owner created the account and supplied the key, which is stored in git-ignored `.env`). Emailing IESO Customer Relations remains an optional parallel route for data we could republish.
- Coverage (catalog, 2026-10-15): DA Ontario zonal LMP `ieso_lmp_day_ahead_hourly_ontario_zonal` from 2025-05-03 (~12.3k rows); RT 5-min `ieso_lmp_real_time_5_min_ontario_zonal` from 2025-05-01 (~148k rows); **pre-dispatch with every vintage** `ieso_lmp_predispatch_hourly_ontario_zonal` (`publish_time_utc`) from 2025-05-01 (~333k rows). Also HOEP 2002–2025, operator load, adequacy and wind/solar forecasts with vintages (2–8M rows each, too big for the free plan).
- Free-plan limits (from the API): 500,000 rows and 250 requests per month, 50,000 rows per response, 1 request/s. Pull plan: DA + RT in October (~160k rows); pre-dispatch vintages after the 1 November reset (~333k rows, which enables O3 over the whole test year).
- Terms of Use (last updated 2026-04-02): licence for internal use and for "Licensee Materials" (derivative works that cannot be reverse-engineered back to the data), with attribution required ("Data provided by Grid Status", with a link and their logo on first use). Publishing a dataset that "substantially reproduces" the data is prohibited. **Consequences:** (1) no Grid Status data in the open dataset (as the plan already assumed); (2) the public dashboard and scorecard show actual prices from our own IESO collector, not Grid Status; (3) the paper, README and dashboard carry the attribution.

### ADR-002 — Cloud host and object storage `[OWNER]`
- Status: **Accepted: Azure for Students VM** (owner, 2026-10-15). It supersedes the interim Mac host below, which stays only as a fallback. Details are in `docs/runbook.md`: Canada Central, `Standard_B2pts_v2` (Arm, 1 GB RAM plus 2 GB swap; B1s is not offered to this subscription in Canada Central), Ubuntu 24.04, collector on a systemd timer. It is paid from the $100/year student credit, with no card attached. Object storage: the VM disk for now; Azure Blob (5 GB free) if backups or offsite copies are needed.
- Earlier interim decision, kept for history:
- Interim decision: the live collector runs on the owner's Mac as a LaunchAgent (`local.gridcast.collector`), hourly at :07, and **only while on AC power** (`--require-ac`, owner's request). launchd never wakes the Mac, and a run missed during sleep fires once on wake.
- Why this is safe for the data: every run re-lists each folder and fetches anything it has not seen. Being off or on battery only delays collection. Data is lost only if the Mac is never plugged in for longer than the shortest on-site retention (~30 days for intermediate pre-dispatch, VG-forecast and generator-output versions; real-time intermediate versions are not kept anyway, see ADR-010).
- Cost: the first run fetches ~13,000 files (~1.5 GB raw), about 3–4 h at 1 request/s. After that, ~25 MB/day raw, stored gzip-compressed.
- Follow-up: move to GitHub Actions or a small host with object storage when the production host is chosen.
- **Budget (owner, 2026-10-15):** a small monthly cost for the production host is approved. Recommendation: one small always-on Ubuntu VM (DigitalOcean Toronto, 1–2 GB) running the collector, the daily forecast job, the API and the dashboard in Docker. That also removes the collector's dependency on the Mac being plugged in. The owner creates the account and VM (payment is `[OWNER]`); access is by a dedicated deploy SSH key (`~/.ssh/gridcast_deploy`).

### ADR-003 — Weather API: Previous Runs vs. Single Runs, and which model
- Status: **Accepted (executor, 2026-10-17)**, within the plan's pre-made constraints (§5.3); the owner may overrule. Detail in V-10.
- Decision: Open-Meteo **Previous Runs API**, model **`gfs_seamless`** (hourly; every variable complete from 2024-03-01), leads N ∈ {1, 2}, five variables at the 8 plan points (`configs/weather.yaml`).
- **Availability rule, stricter than plan §5.3:** a value `<var>_previous_dayN` at valid time v counts as published at `v − N·24h + 8h`. A feature for issue time t may use it only if that is ≤ t, and silver keeps a value only if that is ≤ its fetch time (this drops Open-Meteo's placeholders; see V-10). At a 09:00 Eastern issue time the rule picks N = 2 for almost every hour of D (N = 1 only for valid times up to ~01:00 Eastern on D). That is slightly staler than the plan's rule, but leak-proof whichever run Open-Meteo actually uses.
- Not chosen: the Single Runs API (exact runs but many more requests; possible later refinement); ECMWF IFS (possible later ablation); the Historical Forecast API and ERA5 (never for features).

### ADR-004 — Alert channel `[OWNER]`
- Status: **Open**, needed by week 12.

### ADR-005 — Public forecast log location `[OWNER]`
- Status: **Open**, needed by week 8.

### ADR-006 — Gas price source (optional) `[OWNER]`
- Status: **Open**, needed by week 5.

### ADR-007 — Paper venue / workshop `[OWNER]`
- Status: **Open**, needed by week 12.

### ADR-008 — Configuration system: pydantic-settings + typed YAML (not Hydra)
- Status: Accepted (executor choice within plan M0-T2; the owner may overrule) · Date: 2026-10-15
- Context: M0-T2 allows "Hydra or pydantic-settings". We need typed configs that pass `mypy --strict`, a stable `config_hash` for every result file, and secrets that never print.
- Options:
  - **A: Hydra.** Strong at CLI overrides and multirun sweeps. Configs are untyped `DictConfig` unless structured configs are used, mypy support is weak, and the last major release was 2023.
  - **B: pydantic-settings for environment/secrets plus pydantic models validated from YAML.** Typed, validated at load time, `SecretStr` for keys, and the hash is trivial (canonical JSON). No built-in sweep launcher.
- Decision: **B**. `gridcast.config.Settings` reads `GRIDCAST_*` env vars and `.env`. `ProjectConfig` validates `configs/project.yaml` (frozen, `extra="forbid"`). Backtest and model configs in M3 follow the same pattern. Sweeps are plain Python loops or Optuna.
- Consequences: no `hydra` CLI overrides. If sweeps get complicated in M3–M4, revisit with a superseding ADR.

### ADR-009 — Operator pre-dispatch baseline (O3) cannot be issued before our forecast `[OWNER]`
- Status: **Accepted: option A** (owner, 2026-10-15). See V-07.
- Context: O3 compares GridCast against "IESO pre-dispatch OZP projection". V-07 shows the first pre-dispatch OZP vintage for delivery day D is published about 20:19 EST on D−1. That is roughly 11 hours after our 09:00 issue time and after the day-ahead results are out. No operator price projection for D exists at issue time.
- Options:
  - **A (recommended): keep it as an information-advantaged reference.** Compare our 09:00 D−1 forecast with the operator's *first* pre-dispatch vintage for D, clearly labelled as issued about 11 h later with DAM results known. Report it for both the DA OZP target and the RT OZP secondary target. Pre-dispatch projects real-time prices, so RT OZP is the more natural yardstick. Restate O3 accordingly.
  - **B: drop O3** and make LEAR, the strongest published EPF baseline, the headline "beat" target.
  - **C: compare both at a common later issue time** (e.g. 21:00 EST D−1) as a separate experiment. This is fair but no longer actionable for DAM bidding.
- Consequences of A: the O3 claim becomes "beats/does not beat a later, better-informed operator projection". That is a stronger statement if it holds, and the expected result if it does not. `PREREGISTRATION.md` uses option A pending confirmation.

### ADR-010 — Collector storage: SQLite manifest, gzip bronze, version policy per report
- Status: Accepted (executor, within M1-T2/T3) · Date: 2026-10-15
- Context: plan §7.2 sketches a single `_manifest.parquet`. The collector appends one row per file every hour, and a run can stop half-way (unplugged, network).
- Decision:
  - **Manifest** in `data/bronze/_manifest.sqlite` (tables `files`, `listings`, `runs`). It is transactional, so a crash loses at most the file in flight. A Parquet export for DuckDB comes with M1-T4. Idempotency key: (report, filename, listing time). Content dedupe is by sha256 of the raw bytes.
  - **Bronze files gzip-compressed** (`<original>__<fetched_utc>.<ext>.gz`, `mtime=0`). This is lossless and deterministic, and about 10× smaller.
  - **Version policy** (`configs/collector.yaml`): `all` for daily reports, because their versions are forecast vintages or intraday updates. `latest` for the hourly real-time reports, whose `_vN` only adds 5-minute intervals within the hour. Un-versioned period files are skipped when versioned siblings exist (V-05a).
- Consequences: real-time partial-hour snapshots are not kept. That is no loss for research; it only matters for an intraday nowcast, which is out of scope.

### ADR-011 — Start M2/M3 work before G1 formally passes (process note)
- Status: **Accepted** (owner, 2026-10-17)
- Context: G1's only open criterion (7 gap-free days of live collection, ~2026-10-23) concerns the live collector, not the historical data that the backtests use. Plan §0 rule 3 says not to start the next milestone before a gate is approved.
- Decision: start M3 machinery and M2 code now, under a **test seal**:
  1. Allowed now: backtester, metrics and significance tests (on synthetic data), baselines, tuning on the **validation window only** (2025-05-01 → 2025-10-31), freezing configs in `configs/frozen/`, and M2 code run on old-market data.
  2. After G1 approval: anything that reads test-window outcomes (≥ 2025-11-01), meaning the RQ1 verdict and test-window backtests, run against frozen configs.
  3. The seal is enforced in code (`gridcast.backtest.seal`): scoring a test-window day needs `--unseal-test` and committed frozen configs.
- Disclosure: during M1 data engineering the executor saw aggregate data-quality summaries over the whole period (coverage, min/max, counts of negative prices) and a few dozen individual test-window values while checking parsers and the feature builder. No model, tuning or result has been computed on test-window outcomes. Also recorded in `PREREGISTRATION.md` §11.

### ADR-012 — RQ1 headline verdict is placebo-calibrated
- Status: **Accepted** (owner, 2026-10-17), recorded as a pre-registration amendment before any real RQ1 run.
- Context: `docs/results/rq1_placebo.md`. The registered rule fires at 1 of 5 old-market placebo dates, because year-on-year drift alone makes the Chow and KS tests reject.
- Decision: option B. Report the registered rule unchanged; the headline verdict compares go-live statistics with 48 monthly placebo dates (2020-05 → 2024-04). Full specification in `PREREGISTRATION.md` §11.
- Consequences: `gridcast rq1` computes the placebo distribution (old market only, runnable now) and, after G1, the go-live statistics and calibrated p-values.

---

## Verification log

Checked 2026-10-15 by the executor. "Verified" means read in a primary source (an IESO manual or rule text, or IESO's own files and servers), with the evidence listed.

### V-01 — Day-ahead bid window → **Verified**
- Claim (plan §5.1): the restricted window starts at 10:00 EPT on D−1.
- Source: IESO Market Manual 4.2 *Operation of the Day-Ahead Market*, Issue 5.0 (2026-06-16), §2.2 "Day-Ahead Market Process Timeline".
- Finding: the submission window opens 06:00 EPT and **closes 10:00 EPT**, which starts the restricted window. Results are typically issued **by 13:30 EPT**. Results or a failure declaration come by **15:30 EPT** at the latest. The next-day Adequacy report is issued at 05:30 EPT, updated at 09:00 EPT, and updated again after DAM results.
- Consequence: the issue time of **09:00 America/Toronto on D−1 stands**, with a 1-hour margin. The 09:00 EPT Adequacy update races our issue time, so the as-of join must use `available_at_utc` (normally we get the 05:30 version).

### V-02 — Time convention of IESO reports → **Verified** (final live confirmation on 2026-11-01)
- Claim (plan §5.1, §7.3): reports index the dispatch day as midnight-to-midnight EST with hour ending 1–24.
- Evidence:
  1. The XSD `DAHourlyOntarioZonalPrice_r1.xsd` allows at most 24 `HourlyPriceComponents` (`maxOccurs="24"`), with `PricingHour` of type `Hour1To24` documented as "hour ending at (1 to 24)". A 25-hour EPT day could not be represented.
  2. `PUB_Demand_2025.csv` and `PUB_Demand_2026.csv` have exactly 24 hourly rows on DST days 2025-03-09, **2025-11-02** and **2026-03-08**. The last two are after go-live.
  3. `CreatedAt` and directory-listing times line up with HTTP `Last-Modified` only at UTC−5 in October, when Toronto is on EDT (V-03).
  4. MM 4.2 keeps EST for dispatch-day items (e.g. "pre-dispatch calculation engine runs at 20:00 EST") and EPT for the DAM submission timeline.
- Consequence: `ts_utc = delivery_date 00:00 at fixed UTC−5 + (hour_ending − 1) h` (`market.report_utc_offset_hours: -5`). Every delivery day has 24 rows, including the plan's DST test dates. The live collector will confirm on delivery date 2026-11-01 (DST ends), the first DST day inside DA OZP retention.

### V-03 — Time zone of `CreatedAt` → **Verified: EST (UTC−5), no offset in the string**
- Evidence: day-ahead OZP files, comparing `CreatedAt` with HTTP `Last-Modified` (GMT):

  | File | CreatedAt | Last-Modified (GMT) | Implied offset |
  | --- | --- | --- | --- |
  | `…_20261015_v1.xml` | 2026-10-14T12:31:28 | 17:33:26 | UTC−5 (2 min lag) |
  | `…_20260924_v1.xml` | 2026-09-23T12:33:10 | 17:34:16 | UTC−5 |
  | `…_20260924_v2.xml` | 2026-09-23T14:26:29 | 19:28:27 | UTC−5 |

  The web server's directory listings are also in EST (listing "12:33" equals 17:33 GMT).
- Consequence: parse `CreatedAt` as naive local time at fixed UTC−5. Never use `America/Toronto` for it.

### V-04 — Price cap and floor → **Verified**
- Sources: IESO *Guide to IESO Market Calculation Engines* (training guide, Nov 2025) §3.5 "Settlement Price Floors and Caps": "Settlement price floor for energy is set at −$100/MWh and the cap is at $2,000/MWh". Offers may still go as low as −MMCP (−$2,000/MWh). The guide says "at the current time", and secondary summaries of Renewed Market Rules Chapter 7 (Issue 3.0) describe both values as **IESO Board-set parameters**, so they can change. We have not read the Chapter 7 text ourselves yet; do that in M1-T4.
- Consequence: bounds live in config (`market.price_floor: -100`, `market.price_cap: 2000`), not in code. M1-T4 starts the silver range check on `ZonalPrice` as a warning. It becomes a hard Pandera bound once 90 days of data confirm that published zonal prices are already settlement-bounded (field names such as `LossPriceCapped` and `LmpCap` suggest they are).

### V-05 — File formats, naming and versioning → **Verified, with corrections to plan §5.2 and §7.4**
- (a) **The un-suffixed daily file is an alias of the latest version, not "version 0".** SHA-256 of `PUB_DAHourlyOntarioZonalPrice_20260924.xml` equals `…_v3.xml`, and `…_20261015.xml` equals `…_v1.xml`. Versions start at `_v1`. **Change to §7.4:** `version` = N from `_vN`. Base files are stored in bronze, de-duplicated by sha256, and resolved to the matching `_vN`. If a base file matches no retained `_vN`, it gets `version = NULL` with `available_at = fetched_at`.
- (b) **Older versions can vanish from the site.** Delivery 2026-09-11 lists only the base file and `_v2`; there is no `_v1`. Only our own live collector preserves the full vintage history.
- (c) Day-ahead OZP (`DAHourlyOntarioZonalPrice`, XSD `_r1`): namespace `http://www.ieso.ca/schema`; `DocHeader/CreatedAt`; `DocBody/DeliveryDate`; up to 24 `HourlyPriceComponents{PricingHour, ZonalPrice, LossPriceCapped, CongestionPriceCapped, Flag}`. The XSD documents `Flag` as "administered or from the DSO-RD". It matches plan §5.2.
- (d) Real-time OZP (`RealtimeOntarioZonalPrice`, XSD `_r2`): **one file per delivery hour**, `PUB_RealtimeOntarioZonalPrice_<YYYYMMDDHH>.xml`. `_vN` grows as 5-minute intervals arrive (e.g. `_v12`). Body: `DeliveryDate`, `DeliveryHour`, 12 × `ZonalPrice{Interval, Flag, LmpCap, LossPriceCap, CongPriceCap}`. The field names differ from the DA report. About 2,760 files are listed.
- (e) Pre-dispatch OZP (`PredispHourlyOntarioZonalPrice`, XSD `_r1`): one file per delivery date with about 27 hourly versions. `_v1` comes out about 20:19 EST on D−1, then hourly through D. **Intermediate versions are kept about 30 days**; after that only the base file and final version remain, back to about 90 days. Body has no `Flag`.
- (f) Demand CSV: three `\\`-prefixed header lines including `\\Created at YYYY-MM-DD HH:MM:SS` (EST), then `Date,Hour,Market Demand,Ontario Demand`. Yearly files, with versions like `_v395`.
- (g) `/docrefs/helpfile/` returns 403 because directory browsing is off. Help PDFs must be fetched by exact name. `/docrefs/schema/<Report>_rN.xsd` is fetchable.
- (h) Across about 20 requests, the server reset one connection. The collector needs retries with exponential backoff (already in plan §0 rule 10).

### V-06 — Day-ahead results publication time → **Verified, with a correction to plan §7.3**
- Finding: `CreatedAt` for the first version of D is 12:31–12:33 EST on D−1, which is **13:31–13:33 EDT** in summer. Corrections arrive within hours (2026-09-24: `_v2` 14:26, `_v3` 14:27 EST) or days later (2026-09-11: `_v2` on 09-15).
- Consequence: the plan's example rule "day-ahead OZP for D available at 13:00 Eastern on D−1" is **too early during DST**. For backfilled data without vintages, use **15:30 EPT on D−1**, the market-rule deadline in V-01. This never binds at a 09:00 issue time, but it keeps the rule safe for later issue times.

### V-07 — Pre-dispatch OZP timing → **Verified; affects objective O3** (see ADR-009)
- Evidence: versions of `PUB_PredispHourlyOntarioZonalPrice_20261014`: `_v1` at 2026-10-13 20:19 EST, then hourly to `_v27` at 2026-10-14 22:23 EST.
- Consequence: **no operator price projection for D exists at 09:00 D−1.** O3 needs restating (ADR-009).

### V-08 — Data-quality note found in passing
- `PUB_Demand_2025.csv` has **23 rows for 2025-05-01**, the go-live day. Triage in M1-T9.

### V-09 — Survey of all collected report folders (2026-10-15)
- Every folder follows `PUB_<Report>[_<key>][_vN].<ext>`, with key = `YYYY`, `YYYYMM`, `YYYYMMDD` or `YYYYMMDDHH`.
- Largest folders: `RealtimeZonalEnergyPrices` (12,728 files, ~20 KB each), `Adequacy3` (5,552, ~170 KB), `RealtimeOntarioZonalPrice` (2,763). `GenOutputCapability` versions grow through the day from ~70 KB to ~900 KB.
- `DAConstrShadowPrices` is published with a **~6-day lag** (delivery 2026-10-09 created 2026-10-15 08:01 EST) and **skips days** (no files for 09-14, 09-15, 09-17, 10-03). It cannot be an issue-time feature for D, only for D−6 and earlier. It is excluded from the daily gap check.
- A live smoke test of the collector against this folder (28 files) stored `CreatedAt` within 1 minute of the listing time under the EST rule, consistent with V-03.

### V-10 — Open-Meteo Previous Runs semantics (2026-10-17)
- Docs: `<var>_previous_dayN` "returns the value predicted 24 hours before valid time", up to day 7. Global models update every 6 h. The docs give no definition of the run and no publication latency.
- Probe (Toronto, GFS, fetched 2026-10-17 20:19 UTC): for past valid times, day0, day1 and day2 differ as expected. For valid times whose N-day-earlier run had not been published yet, Open-Meteo **returns the latest run instead** (day1 = day0 from 2026-10-18 18:00 UTC; day2 = day0 from 2026-10-18 18:00 UTC). The pattern fits "the most recent run initialised at or before v − N·24h": at 20:19 UTC the 12z run was out and the 18z run was not.
- Coverage probe: every variable is complete from 2024-03 (`wind_speed_100m` is missing on 2024-02-01). Full fetch 2024-03-01 → 2026-10-19: 8 points × 23,112 hours, no nulls.
- Consequence: the availability rule in ADR-003. Bronze chunks newer than 3 days are marked incomplete and re-fetched.

### V-11 — Findings from the first silver build (2026-10-17)
- **A new version is not always a correction.** Day-ahead OZP 2026-09-24 `_v1`, `_v2` and `_v3` are identical apart from `CreatedAt`. `make dq` counts versions that actually changed values.
- **The in-progress real-time hour** lists all 12 intervals, with empty values for those not yet published. Silver drops the empty ones.
- **Real-time `<AveragePrice>`** in each hourly file equals the plain mean of its 12 intervals (2026-10-01 HE1: 30.39). `rt_ozp_hourly.lmp_mean` uses the same definition.
- **Grid Status day-ahead prices match IESO's own files exactly**, including the EST timestamp convention (checked 2026-09-24 latest version and 2026-10-15, all 24 hours).
- **IESO's Demand file has no HE1 for 2025-05-01** (go-live day), the 23 rows noted in V-08.
- **Availability of IESO files** = max(`CreatedAt`, listing time + 1 min), because `CreatedAt` precedes publication by 1–2 minutes (V-03). This is slightly more conservative than plan §7.3.

### V-12 — Data-quality triage (first `make dq`, 2026-10-17)

| Anomaly | Evidence | Disposition |
| --- | --- | --- |
| DA OZP has no prices for delivery 2025-05-22 (24 h) | `dq_report.md` coverage; no public IESO notice found | Possibly a DAM failure day. **Left missing, never imputed**; that day is excluded from DA scoring. Ask IESO if a data request is sent |
| RT OZP hourly: 3 missing hours (2025-10-24, 2025-11-10, 2026-01-28) and 19 hours built from fewer than 12 intervals | `dq_report.md` | Hourly means need all 12 intervals for scoring; features tolerate gaps (NaN). Keep as is |
| IESO vs Grid Status RT 5-min: 227 of 27,042 overlapping intervals differ (max 37.56 $/MWh) | `dq_report.md` | IESO's final file is authoritative; `latest` views prefer IESO. Grid Status-only history may carry a few superseded values (limitation) |
| IESO vs Grid Status DA: 0 of 2,251 differ | `dq_report.md` | Grid Status DA history accepted as-is |
| DA versions: of 94 collected delivery days, 1 has several versions on site, all identical | `dq_report.md`, V-11 | Earlier versions were purged before collection. Live collection captures them from now on |
| Demand 2025-05-01 HE1 absent | V-08 | IESO's own file. Left missing |
| DA OZP never negative (min 0.00); RT reached the −$100 floor 5 times, 512 negative intervals | `dq_report.md` | Real behaviour, not an error. Note for EDA (M2) |

### V-13 — Method notes from the first model runs (2026-10-17)
- **LEAR (re-implemented).** epftoolbox predates Python 3.12, so LEAR uses scikit-learn's `LassoLarsIC(criterion="aic")`. Since scikit-learn 1.1 that class refuses n < p without a noise-variance estimate. Passing `noise_variance = var(y)` reproduces the pre-1.1 behaviour that epftoolbox relied on. Calibration windows are (56, 84, 182, 364) days instead of Lago's (56, 84, 1092, 1456) because the renewed market only began 2025-05-01.
- **Point-model quantiles.** Naive, LEAR and SARIMAX produce a median; quantiles come from the model's own rolling out-of-sample residuals (last 56 days, published by the issue time), falling back to in-sample residuals for the first 14 days.
- **Rolling CQR (M3-T8)** must be fitted on *raw* (pre-calibration) past quantiles. Calibrating on already-calibrated history shrinks the correction and under-covers: LightGBM 90% coverage was 0.863 with the bug and 0.901 after the fix. The engine stores `value_raw` next to `value`.
- **LightGBM** needs the OpenMP runtime: `brew install libomp` on macOS; `libgomp1` on Ubuntu (in the bootstrap script).
- **RQ1 placebo:** see `docs/results/rq1_placebo.md`. The pre-registered rule fires at 1 of 5 old-market placebo dates.

### Deferred `[VERIFY]` items (checked in the milestone that needs them)

| Item | Plan ref | When |
| --- | --- | --- |
| Grid Status dataset IDs, free-tier limits, terms and redistribution | §5.2 | M1-T1 audit (feeds ADR-001) |
| IESO terms of use for redistributing report data | M7-T2 | M1-T1 audit, because it shapes what the bronze store may later publish |
| Open-Meteo Single Runs publication latency; Previous Runs coverage per model | §5.3 | M1-T6 / ADR-003 |
| Chronos-2 `predict_df` signature and fine-tuning API | §5.4 | M4-T1 |
| Moirai 2.0 / TiRex package names, APIs, licences | §5.4 | M4-T1 |
| DST behaviour of DA OZP on 2026-11-01 (live confirmation of V-02) | §7.3 | M1-T3, after 2026-10-31 13:30 EPT |

### Sources
- IESO public reports: https://reports-public.ieso.ca/public/ (folders `DAHourlyOntarioZonalPrice`, `RealtimeOntarioZonalPrice`, `PredispHourlyOntarioZonalPrice`, `Demand`), schemas at https://reports-public.ieso.ca/docrefs/schema/
- Market Manual 4.2, Issue 5.0: https://ieso.ca/-/media/Files/IESO/Document-Library/Renewed-Market-Rules-and-Manuals/market-manuals/market-operations/ieso-mo-operation-dam.pdf
- Guide to IESO Market Calculation Engines (Nov 2025): https://ieso.ca/-/media/Files/IESO/Document-Library/training/Guide-to-IESO-Market-Calculation-Engines.pdf
- Renewed Market Rules Chapter 7, Issue 3.0: https://www.ieso.ca/-/media/Files/IESO/Document-Library/Renewed-Market-Rules-and-Manuals/market-rules/ieso-mr-chapter0-7.pdf
