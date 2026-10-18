# Status — week 1 (2026-10-17)

Milestone: **M1 nearly complete** (G1 pending the 7-day collector streak, ~2026-10-23); **M2/M3 machinery under the test seal** (ADR-011) | Overall: **Green** (fast track)

**Done this session**
- [M1-T1] Availability audit: `docs/data_availability.md`
- [M1-T5] Backfill: Grid Status DA (12,787 h) and RT 5-min (153,827 intervals) from May 2025, which match IESO's own files; IESO history (HOEP 2002–25, Demand 2002–, generator output 2019–, interties 2018–)
- [M1-T6] Weather: Open-Meteo GFS previous runs, 8 points, 2024-03 →; ADR-003 availability rule; placeholder runs removed (V-10)
- [M1-T7] Canonical time model with DST tests
- [M1-T4] 12 validated silver tables (`make data`, ~60 s): da_ozp, rt_ozp_5min/hourly, predisp_ozp, demand, hoep, gen_by_fuel, intertie, weather_fcst, adequacy_fcst, vg_fcst, da_totals
- [M1-T8] `configs/availability.yaml`, point-in-time feature builder (29 columns), four leakage tests
- [M1-T9] `make dq` report plus triage (V-12)
- Collector moved to the Azure VM (ADR-002); `make data-sync` mirrors server bronze locally

**Done since (M2/M3 under the seal, ADR-011)**
- [M3-T1] Rolling-origin backtester (resumable, deterministic, per-model refit cadence); [M3-T2] metrics; [M3-T3] DM/GW/Holm/MCS
- [M3-T4..T6] naive d-1/d-7, LEAR, SARIMAX, LightGBM (Optuna, 25 trials on validation only); [M3-T8] rolling CQR
- Frozen configs for all five in `configs/frozen/`; `docs/results/validation.md`: on validation, LightGBM+CQR is best (CRPS 3.82 vs SARIMAX 4.53, LEAR 4.70, naive d-1 5.15; DM p < 1e-7) and alone in the 90% MCS; 90% coverage 0.898
- [M2-T2/T3] RQ1 break tests; placebo check, then owner-approved placebo-calibrated verdict (ADR-012)

**Next**
- Let the collector streak reach 7 gap-free days; then finalise `docs/gates/G1.md`
- 2026-11-01: pull pre-dispatch vintages from Grid Status (~333k rows) for O3
- After G1 approval: unseal and run the frozen models on the test window (RQ2); `gridcast rq1 --after-g1`; G2 assessment
- M3-T7 neural tier: validation search running (N-HiTS x3 configs, PatchTST, TFT; then 3 seeds each)
- M3-T10: daily live job runs at 09:00 on the server from 2026-10-18 (local log only); public once ADR-005 is decided

**Blocked / needs owner**
- G1 approval (~2026-10-23)
- ADR-005 / ADR-013: owner said "yes, both" (public GridCast-forecasts repo + project site). Owner still to: create the repo and add the deploy key, confirm the address (both free; recommended gridcast.muhammadmemon.ca), approve building a preview. Saved in `docs/proposals/site.md`. **Nothing public starts without approval.**

**Risks changed**
- R1 (post-redesign history): **resolved** for prices; operator-forecast history is live-only
- R6 (Grid Status quota): 34% used in October; the November pull is planned at ~67%

**Data health:** server collector 0 errors since 2026-10-16; gap-free streak 1 day (2026-10-15). Anomalies in V-12 (2025-05-22 DA day missing).

**Metrics snapshot (validation, 2025-07-01 → 10-31; not test):** best LightGBM+CQR CRPS 3.819, MAE 10.64 $/MWh, 90% coverage 0.898. Test window sealed.
