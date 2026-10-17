# Data availability audit (M1-T1)

Audited 2026-10-15 to 2026-10-17. For each dataset in plan §6: where history can come from, what we hold now, and whether it can feed **backtests over the test window** (2025-11-01 →) or only the **live period**. Coverage numbers are from `make dq` (`docs/results/dq_report.md`).

## Summary

- **Every core input for the RQ2 backtests is available over the whole test window:** DA and RT OZP, demand, generator output by fuel, intertie flows, weather forecasts, calendar.
- **Operator forecasts** (Adequacy3, VG forecasts, DA totals) have only 30–90 days of history on the IESO site. In backtests they are live-period features or an ablation over the collected window. Grid Status has them back to 2024–25, but at 1.8–8.4 M rows each they far exceed the free plan.
- **Pre-dispatch OZP vintages for the full test year** come from Grid Status after the 1 November quota reset (~333k rows). This makes O3 (ADR-009) possible over the whole test window, not just the collected days.

## Per dataset

| ID | Dataset | IESO site | Other route | Held now (silver) | Backtest use |
| --- | --- | --- | --- | --- | --- |
| D01 | DA OZP | ~90 days (from delivery 2026-07-16); intermediate versions vanish (V-05b) | **Grid Status** `ieso_lmp_day_ahead_hourly_ontario_zonal` from 2025-05-03 (ADR-001); matches IESO exactly (V-11) | 2025-05-03 → D+1; 24 h missing (2025-05-22, see triage) | **Yes**, target and lags |
| D02 | RT OZP | ~90 days | Grid Status 5-min from 2025-05-01 | 2025-05-01 →; 3 h missing | **Yes**, secondary target and lags |
| D03 | Pre-dispatch OZP | all vintages ~30 days, then base + final to ~90 days | Grid Status, **every vintage** from 2025-05-01 (~333k rows) | collected vintages from 2026-07-16 | O3 only; **pull after 2026-11-01** |
| D04 | HOEP | yearly CSV 2002-05 → 2025-04-30 | — | 2002 → 2025-04-30; complete since 2019 | RQ1 / RQ3 (old market) |
| D05 | Demand | yearly CSV 2002 → today | — | complete since 2019 except 2025-05-01 HE1 (absent in IESO's own file) | **Yes**, lags (target: secondary) |
| D06 | Adequacy3 | ~90 days, many vintages per day | Grid Status `ieso_adequacy_report_forecast` from 2024-07-25 (~2.0 M rows; free plan too small) | 135,912 vintage-hours from ~2026-07-16 | Live period / ablation |
| D06b | DA totals | ~90 days | — | 94 delivery days | Live period |
| D07 | Generator output & capability | hourly XML ~90 days; **monthly CSV 2019-05 →** | Grid Status `ieso_generator_report_hourly` from 2023-10 | `gen_by_fuel` 2019-05 →, complete | **Yes**, lags by fuel |
| D08 | Wind/solar forecasts | ~30 days | Grid Status wind/solar MP + embedded from 2025-04-12 (~1.8 M rows each) | 38,592 vintage-hours from ~2026-09-14 | Live period; weather proxies in backtests (plan) |
| D09 | Intertie flows | hourly XML ~90 days; **yearly CSV 2018 →** | — | `intertie` 2018 →, complete | **Yes**, lag 3 |
| D10 | Virtual zonal prices | ~90 days | Grid Status virtual-zonal DA/RT from 2025-05 | bronze only (not parsed) | Stretch (zones) |
| D11 | DA shadow prices | ~40 days, published ~6 days late (V-09) | Grid Status from 2025-05-09 | bronze only (not parsed) | Stretch; never an issue-time feature for D |
| D12 | Weather forecasts | — | Open-Meteo Previous Runs, GFS, complete from 2024-03 (ADR-003) | 8 points × leads 1–2, 2024-03 → D+2 | **Yes** |
| D13 | Weather actuals | — | Open-Meteo ERA5 from 1940 | not fetched | Analysis only (M2) |
| D14 | Holidays | — | `holidays` package, Ontario | in the feature builder | **Yes** |
| D15 | Gas price | — | `[OWNER]` ADR-006 (optional) | — | Optional |

## Quota plan (Grid Status free plan: 500k rows, 250 requests per month)

| Month | Pulls | Rows |
| --- | --- | --- |
| October 2026 (done) | DA OZP, RT OZP 5-min | 167,694 (34%) |
| November 2026 | Pre-dispatch OZP, every vintage, 2025-05-01 → | ~333k (67%) |
| Later, optional | Adequacy / VG forecast history for chosen windows, if an ablation needs them | ≤ 300k per month |

## IESO data request (optional, owner)

Emailing IESO Customer Relations for historical DA/RT OZP and Adequacy vintages would give a republishable copy for the open dataset (M7-T2) and could explain the 2025-05-22 gap. It is not on the critical path.
