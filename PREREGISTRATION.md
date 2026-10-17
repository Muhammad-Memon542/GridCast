# GridCast — Pre-registration

| Field | Value |
| --- | --- |
| Registered | 2026-10-15 |
| Version | 1.0, approved by the owner on 2026-10-15 (git tag `prereg-v1.0`) |
| Machine-readable twin | `configs/project.yaml` (its `config_hash` is stamped on every result) |
| Integrity | Frozen at the git commit recorded in `README.md`. Later edits appear only in §11 (Amendments) or §12 (Deviations). |

**State of knowledge at registration.** No model has been fit, tuned or evaluated on any data. To check file formats during setup (M0-T5), a few raw IESO files were opened: day-ahead OZP for delivery 2026-09-24 and 2026-10-15, one real-time and one pre-dispatch OZP file for 2026-10-15, and the 2025–2026 Demand CSVs. No statistics, plots or models were computed from them. Details are in `docs/decisions.md` (V-02 to V-08).

---

## 1. Research questions and hypotheses

| # | Question | Hypothesis | Primary test |
| --- | --- | --- | --- |
| RQ1 | Did Market Renewal (go-live 2025-05-01) change how Ontario prices behave? | A structural break at go-live in level, volatility, spike frequency and daily shape | Chow test at 2025-05-01; Bai–Perron multiple breaks; KS tests; Hill tail index; monthly count of hours above the P99 |
| RQ2 | Do zero-shot time-series foundation models (TSFMs) match tuned specialists? | Zero-shot TSFMs beat naive/statistical baselines but lose to tuned LightGBM/LEAR with weather and load features. Fine-tuning closes most of the gap | Rolling-origin backtest; multivariate DM with Holm correction; GW; 90% model confidence set |
| RQ3 | After a redesign, which strategy recovers fastest? | TSFMs given a short post-redesign context reach baseline accuracy with fewer days of new data than models retrained from scratch | Learning curves over k ∈ {7, 14, 30, 60, 120, 240, 365} days |
| RQ4 | Does better uncertainty translate into money? | Probabilistic dispatch (mean-based, CVaR, 48-h rolling) captures more perfect-foresight revenue than median-based deterministic dispatch. Accuracy correlates only loosely with revenue | Backtested revenue, % of perfect foresight, block-bootstrap CIs, Spearman correlation of CRPS vs. revenue |

## 2. Forecasting task

- **Issue time:** 09:00 America/Toronto on D−1. The DAM submission window closes 10:00 EPT (Market Manual 4.2 §2.2).
- **Information set:** only data with `available_at_utc` ≤ issue time. Leakage tests (plan §7.5) must pass in CI.
- **Primary target:** day-ahead Ontario Zonal Price (`ZonalPrice` in `DAHourlyOntarioZonalPrice`) for the 24 hours of delivery day D. Hours are indexed hour-ending 1–24 on EST (UTC−5) all year.
- **Ground truth:** the highest published version (`_vN`) at evaluation time. Sensitivity check: first-published version.
- **Secondary targets:** real-time OZP averaged to hourly (mean of the 12 five-minute `LmpCap` values); DA − RT spread; Ontario demand (`Ontario Demand` in the Demand report).
- **Output:** 21 quantiles {0.01, 0.05, 0.10, …, 0.95, 0.99}, non-crossing, for each of the 24 hours.

## 3. Data splits (inclusive dates)

| Split | Range | Use |
| --- | --- | --- |
| Old market | 2019-01-01 → 2025-04-30 | RQ1; pretraining/transfer in RQ3 |
| Validation | 2025-05-01 → 2025-10-31 | Hyperparameter tuning, conformal calibration, ensemble weights, strategy parameters (λ, horizon) |
| Test | 2025-11-01 → latest available | All headline results. **Never used for tuning or selection** |
| Operator reference | Delivery days with collected pre-dispatch vintages | O3 only (§7) |
| Live out-of-sample | From the first public forecast | Scorecard. Never used for tuning |

In rolling-origin backtests, a model may *train* on earlier test days (that is what rolling origin means). It may not *tune or select* on them. Hyperparameters and model configs are frozen in `configs/frozen/` and committed before the first test-window run. That commit hash is recorded in `docs/results/rq2.md`.

**Fallback (plan §10):** if ADR-001 cannot secure post-redesign price history, the test window becomes the on-site retention window plus the live period, with wider CIs reported. That is a deviation (§12).

## 4. Models

- **Tier 0:** naive d−1 (p(d−1, h)); naive d−7 (p(d−7, h)); operator reference (§7).
- **Tier 1:** LEAR (epftoolbox; asinh transform; ensemble over calibration windows); SARIMAX.
- **Tier 2:** LightGBM quantile models (Optuna on validation only).
- **Tier 3:** epftoolbox DNN, N-HiTS, TFT, PatchTST. Three seeds each; mean ± s.d. reported.
- **Tier 4:** zero-shot Chronos-2, TimesFM-3 (research only, non-commercial licence), Moirai 2.0, TiRex. Chronos-2 is also fine-tuned on pre-test data only.
- **Wrappers:** CQR + adaptive conformal inference; quantile regression averaging of the best 3–5 models, with weights fit on validation only.
- **Transform:** asinh variance stabilisation for tiers 1–3.
- **Point forecast:** the 0.5 quantile. TimesFM-3 outputs 9 quantiles (0.1–0.9), so it is scored only on that shared subset. Tails are never extrapolated.

## 5. Metrics

- **Probabilistic (primary):** CRPS, approximated by the average pinball loss over the 21 quantiles.
- **Point:** MAE (primary), RMSE, rMAE against weekly naive p(d−7, h).
- **Calibration:** empirical coverage and mean width of the 80% and 90% central intervals; PIT histograms. Target (O4): 90% coverage in [87%, 93%].
- **Spikes:** MAE on the top 5% of hours by actual price; spike precision/recall, where a spike is an actual above the validation-window P95.
- **Not used:** MAPE and sMAPE (prices sit near zero and reach −$100/MWh).
- **Compute:** training and inference time per model per day.

## 6. Statistical procedures

- **Significance level:** α = 0.05, two-sided unless stated.
- **Diebold–Mariano:** multivariate DM on the daily L1 norm of the 24 hourly errors (MAE) and of the 24 hourly CRPS values, plus per-hour DM. Holm correction across all pairwise comparisons within a table.
- **Giacomini–White:** conditional predictive ability test (epftoolbox) on the same daily losses.
- **Model confidence set:** 90% MCS (`arch.bootstrap.MCS`, "max" statistic, stationary bootstrap, mean block length 7 days, 10,000 replications) on daily CRPS and, separately, daily MAE.
- **Bootstrap CIs:** 95% percentile intervals from a moving-block bootstrap with 7-day blocks, 10,000 replications.

## 7. Objectives tested and decision rules

| Objective / hypothesis | Supported if |
| --- | --- |
| **RQ1** break at go-live | Using the comparable series (§8), the Chow test at 2025-05-01 rejects (p < 0.05) for the daily mean **and** for the daily log standard deviation; Bai–Perron finds a break within ±30 days of go-live; KS rejects equality of hourly price distributions; the Hill tail-index bootstrap CIs do not overlap. Primary windows are 12 months on each side (2024-05-01 → 2025-04-30 vs. 2025-05-01 → 2026-04-30). Full history is a sensitivity check. |
| **RQ2a** zero-shot TSFMs beat simple baselines | The best zero-shot TSFM has lower test CRPS than naive d−7 and SARIMAX, with multivariate DM p < 0.05 after Holm. |
| **RQ2b** tuned specialists beat zero-shot TSFMs | The best of {LEAR, LightGBM} with exogenous features has lower test CRPS than the best zero-shot TSFM, with DM p < 0.05 after Holm. |
| **RQ2c** fine-tuning closes most of the gap | Fine-tuned Chronos-2 closes > 50% of the test-CRPS gap between zero-shot Chronos-2 and the best specialist. |
| **RQ3** TSFMs recover faster | Let k\* be the smallest k at which a model's CRPS is at or below the naive-d−7 CRPS on the same window ("baseline accuracy"). Supported if k\*(best zero-shot TSFM) < k\*(best retrained specialist). Each model at issue date t sees only the most recent k days before t. Evaluation window: **2026-05-01 → latest available**, so every k ≤ 365 can be met with post-go-live data only. Each k is run with and without old-market history added. |
| **RQ4a** probabilistic dispatch adds value | The 95% bootstrap CI of the daily revenue difference (best of strategies 4–6 minus strategy 3) excludes zero. "Best" is chosen on **validation**. |
| **RQ4b** accuracy correlates loosely with revenue | Across all models fed through strategy 4, Spearman \|ρ\| between test CRPS and revenue is < 0.5. |
| **O3** operator reference | ADR-009 option A. Our 09:00 D−1 forecast vs. the operator's first pre-dispatch vintage for D, issued about 20:00 EST on D−1, i.e. about 11 h later with DAM results known. Reported for DA OZP and RT OZP. Supported if MAE is lower with DM p < 0.05. |
| **O5** decision value | The 95% bootstrap CI of the revenue difference (best probabilistic strategy minus rule of thumb, strategy 2) excludes zero. |

**Primary endpoints** (no further multiplicity correction): (1) test CRPS of DA OZP for the RQ2 comparisons; (2) the O5 revenue difference. Everything else is secondary.

**Model feeding RQ4:** the RQ2 model with the lowest **validation** CRPS (the champion). All models also run through strategy 4 for RQ4b.

**Battery (plan §11):** 100 MW / 400 MWh, SoC 10–90%, round-trip efficiency 88% (η_c = η_d = √0.88), degradation $10/MWh of throughput. The day-ahead schedule is settled at actual DA OZP as a price-taker. SoC carries across days. Strategies: (1) perfect foresight; (2) rule of thumb, 4 cheapest / 4 dearest forecast hours; (3) deterministic on the median, 24 h, terminal SoC = initial; (4) deterministic on the mean; (5) 48-h rolling horizon on scenarios; (6) mean − λ·CVaR₀.₉₅, λ ∈ {0.25, 0.5, 1}, with λ chosen on validation. Scenarios come from a Gaussian copula on PIT residuals, 200–500 per day. Revenue is reported in $/MW-year and % of perfect foresight.

## 8. Known limitations, stated in advance

- **HOEP ≠ OZP.** HOEP (old) and OZP (new) are different quantities. RQ1 uses a comparable series: HOEP before go-live vs. real-time OZP averaged to hourly after. This is a stated limitation, not a like-for-like price.
- **Operator timing.** The operator's pre-dispatch projection for D does not exist at our issue time (V-07). Any O3 comparison favours the operator.
- **Small test set.** About 12 months of test data may give wide CIs (risk R7). The live period extends it.
- **Price-taker.** This is unrealistic at large battery sizes; the size sensitivity is reported (R11).
- **Vintages.** Real pre-publication vintages exist only from the start of live collection. Backfilled history uses conservative availability rules (V-06).

## 9. Robustness checks (secondary)

Three to five seeds for neural models. Ablations: weather off, operator features off, context length. Error breakdown by hour, season, temperature band and congestion events. Case studies of the three worst days. Sensitivity sweeps for RQ4: efficiency 85–90%, degradation $5–20/MWh, power 10–250 MW at 4 h.

## 10. Reporting commitments

All pre-registered analyses are reported whatever the result, including null and negative results (R12). Every number in the README, dashboard and paper is generated by code in this repository from a results file carrying `git_sha`, `config_hash` and `data_snapshot_id`.

## 11. Amendments (before the first test-window model run)

Each amendment is dated, gives its reason, and is committed before any model touches test data.

- **2026-10-15, O3.** The owner confirmed ADR-009 option A, so "(pending owner confirmation)" was removed from the O3 row. The definition is unchanged.
- **2026-10-17, exposure disclosure (no change to the design).** While building and checking the data pipeline (M1), the executor saw data-quality summaries over the whole period (coverage, min/max, counts of negative prices) and a few dozen individual test-window values (parser and feature-builder spot checks). No model has been fit, tuned or scored on test-window outcomes. From now on a code-level test seal blocks test-window scoring until G1 is approved and configs are frozen (ADR-011).
- **2026-10-15, approval.** The owner approved v1.0 as written, including three operationalisations the executor chose where the plan was silent: the RQ1 windows of 12 months on each side; the RQ3 evaluation window (2026-05-01 onward); and the RQ3 baseline definition (k\* = smallest k with CRPS ≤ naive d−7).

## 12. Deviations (after the first test-window model run)

_None yet._ Each deviation is dated, gives its reason and expected effect, and is repeated in the paper.
