# Data-quality report

Generated 2026-10-17 21:00 UTC by `make dq` at commit `dd9f0e9`. Counts only; no raw rows.

## Silver tables

| Table | Rows |
| --- | --- |
| `adequacy_fcst` | 135,912 |
| `da_ozp` | 15,091 |
| `da_ozp_latest` | 12,792 |
| `da_totals` | 2,256 |
| `demand` | 214,440 |
| `gen_by_fuel` | 406,056 |
| `hoep` | 201,624 |
| `intertie` | 77,064 |
| `predisp_ozp` | 23,064 |
| `rt_ozp_5min` | 180,913 |
| `rt_ozp_hourly` | 15,084 |
| `vg_fcst` | 38,592 |
| `weather_fcst` | 369,424 |

## Hourly coverage

Missing hours are counted between each series' first and last timestamp (UTC).

| Series | First | Last | Hours | Missing | Duplicate keys | Longest gaps |
| --- | --- | --- | --- | --- | --- | --- |
| DA OZP (latest) | 2025-05-03 05:00 | 2026-10-19 04:00 | 12,792 | 24 | 0 | 2025-05-22 05:00 to 2025-05-23 04:00 (24 h) |
| RT OZP hourly (any source) | 2025-05-01 08:00 | 2026-10-17 20:00 | 12,826 | 3 | 0 | 2025-10-24 21:00 to 2025-10-24 21:00 (1 h); 2025-11-10 16:00 to 2025-11-10 16:00 (1 h); 2026-01-28 20:00 to 2026-01-28 20:00 (1 h) |
| Demand (2019+) | 2019-01-01 05:00 | 2026-10-17 05:00 | 68,304 | 1 | 0 | 2025-05-01 05:00 to 2025-05-01 05:00 (1 h) |
| HOEP (2019+) | 2019-01-01 05:00 | 2025-05-01 04:00 | 55,488 | 0 | 0 | none |
| Generator output, nuclear (2019-05+) | 2019-05-01 05:00 | 2026-10-17 04:00 | 65,424 | 0 | 0 | none |
| Intertie totals (2019+) | 2019-01-01 05:00 | 2026-10-17 04:00 | 68,304 | 0 | 0 | none |
| Pre-dispatch OZP (targets with a vintage) | 2026-07-16 05:00 | 2026-10-18 04:00 | 2,256 | 0 | 0 | none |
| Weather chatham_kent lead 1 | 2024-03-01 00:00 | 2026-10-18 12:00 | 23,077 | 0 | 0 | none |
| Weather chatham_kent lead 2 | 2024-03-01 00:00 | 2026-10-19 12:00 | 23,101 | 0 | 0 | none |
| Weather hamilton lead 1 | 2024-03-01 00:00 | 2026-10-18 12:00 | 23,077 | 0 | 0 | none |
| Weather hamilton lead 2 | 2024-03-01 00:00 | 2026-10-19 12:00 | 23,101 | 0 | 0 | none |
| Weather kitchener lead 1 | 2024-03-01 00:00 | 2026-10-18 12:00 | 23,077 | 0 | 0 | none |
| Weather kitchener lead 2 | 2024-03-01 00:00 | 2026-10-19 12:00 | 23,101 | 0 | 0 | none |
| Weather london lead 1 | 2024-03-01 00:00 | 2026-10-18 12:00 | 23,077 | 0 | 0 | none |
| Weather london lead 2 | 2024-03-01 00:00 | 2026-10-19 12:00 | 23,101 | 0 | 0 | none |
| Weather ottawa lead 1 | 2024-03-01 00:00 | 2026-10-18 12:00 | 23,077 | 0 | 0 | none |
| Weather ottawa lead 2 | 2024-03-01 00:00 | 2026-10-19 12:00 | 23,101 | 0 | 0 | none |
| Weather sudbury lead 1 | 2024-03-01 00:00 | 2026-10-18 12:00 | 23,077 | 0 | 0 | none |
| Weather sudbury lead 2 | 2024-03-01 00:00 | 2026-10-19 12:00 | 23,101 | 0 | 0 | none |
| Weather toronto lead 1 | 2024-03-01 00:00 | 2026-10-18 12:00 | 23,077 | 0 | 0 | none |
| Weather toronto lead 2 | 2024-03-01 00:00 | 2026-10-19 12:00 | 23,101 | 0 | 0 | none |
| Weather windsor lead 1 | 2024-03-01 00:00 | 2026-10-18 12:00 | 23,077 | 0 | 0 | none |
| Weather windsor lead 2 | 2024-03-01 00:00 | 2026-10-19 12:00 | 23,101 | 0 | 0 | none |

## Ranges

- **DA OZP:** min 0.00, max 644.02 $/MWh; 0 hours outside the settlement bounds [-100, 2000]; 0 negative; 0 at the floor.
- **RT OZP 5-min:** min -100.00, max 1998.16 $/MWh; 0 intervals outside the settlement bounds [-100, 2000]; 512 negative; 5 at the floor.
- **RT OZP hourly:** 19 hours built from fewer than 12 intervals.

## Versions (IESO day-ahead OZP)

- 94 delivery days collected; 1 have more than one version; in 0 of those a later version changed a price (largest change 0.00 $/MWh). Other re-publications are identical (V-11).

## Cross-source agreement (IESO vs Grid Status)

- **DA OZP:** 2,251 overlapping hours; 0 differ by more than 0.005 $/MWh (max 0.00).
- **RT OZP 5-min:** 27,042 overlapping intervals; 227 differ by more than 0.005 $/MWh (max 37.56).
