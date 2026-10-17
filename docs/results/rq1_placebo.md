# RQ1 placebo check (old market only)

Generated from `docs/results/rq1_placebo.json` and `docs/results/rq1_placebo_dates.json`. It uses only HOEP data before 2025-05-01, so no test-window prices are involved (ADR-011).

**Question.** Does the pre-registered RQ1 rule (pre-registration §7) fire at dates when nothing changed in the market design?

**Method.** Apply the exact rule (12-month windows on each side; Chow on daily mean and log s.d.; a detected break within ±30 days; KS on hourly prices; disjoint Hill tail-index CIs) at five placebo dates, each 1 May from 2020 to 2024.

| Placebo date | Chow mean | Chow log s.d. | Break ±30 d | KS | Tail CIs disjoint | **Rule says "break"** | Mean before → after ($/MWh) |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 2020-05-01 | yes | no | no | yes | no | no | 12.8 → 14.9 |
| 2021-05-01 | yes | yes | no | yes | no | no | 14.9 → 32.8 |
| 2022-05-01 | yes | yes | yes | yes | yes | **yes** | 32.8 → 41.2 |
| 2023-05-01 | yes | yes | no | yes | yes | no | 41.2 → 30.7 |
| 2024-05-01 | yes | yes | no | yes | no | no | 30.7 → 40.5 |
| **Share firing** | 100% | 80% | 20% | 100% | 40% | **20%** | |

**Finding.** Year-on-year price drift alone makes the Chow-on-mean and KS tests reject at every placebo date. The composite rule declared a break at 1 of 5 placebo dates (2022, during the gas-price surge): a ~20% false-positive rate against a nominal 5%. The rule cannot by itself separate the redesign from ordinary year-to-year change.

**Decision (owner, 2026-10-17): option B.** The headline RQ1 verdict is placebo-calibrated (ADR-012; specification in `PREREGISTRATION.md` §11). The registered rule is still reported unchanged.

## Placebo reference distribution (48 monthly dates, 2020-05 → 2024-04)

Computed by `gridcast rq1` from old-market data only; details in `docs/results/rq1_placebo.json`. The go-live statistic must exceed all but at most one placebo (calibrated p < 0.05) for both the level and the volatility statistics.

| Statistic | Median | 90th pct | 95th pct |
| --- | --- | --- | --- |
| Chow F, daily mean | 61.2 | 275.8 | 322.2 |
| Chow F, daily log s.d. | 59.1 | 131.3 | 162.7 |
| KS distance (hourly) | 0.289 | 0.485 | 0.521 |
| abs(log tail-index ratio) | 0.224 | 0.449 | 0.493 |

The real RQ1 run (`gridcast rq1 --after-g1`) waits for G1 approval (ADR-011).
