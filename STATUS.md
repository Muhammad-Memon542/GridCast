# Status — week 0 (2026-10-15)

Milestone: M0 (complete, awaiting owner review) | Overall: **Amber**, because the live collector (M1-T3) is not running yet and forward-report history is being lost daily (R2).

Work started on Thursday 2026-10-15, ahead of the plan's assumed start of Monday 2026-10-19.

**Done**
- [M0-T1] Repo scaffold: uv, Python 3.12.15, `src/gridcast` layout, ruff, mypy `--strict`, pytest, pre-commit, Makefile, GitHub Actions (05cb9a1). `make check` passes locally on a fresh clone. *CI green is pending a GitHub remote `[OWNER]`.*
- [M0-T2] Config system, pydantic-settings + typed YAML (ADR-008); `.env.example`; gitleaks pre-commit hook, shown to block a fake token (05cb9a1)
- [M0-T3] `docs/decisions.md`, `STATUS.md`, `docs/data_sources.md` (63a8089)
- [M0-T4] `PREREGISTRATION.md` (0696d27); hash recorded in README
- [M0-T5] `[VERIFY]` sweep: bid window, time convention, `CreatedAt` zone, price cap/floor, file formats. All verified; see `docs/decisions.md` V-01 to V-08 (63a8089)

**Next**
- [M1-T3] Live snapshot collector, **first and urgently** (D03 pre-dispatch vintages live only ~30 days; earlier DA versions can disappear, V-05b)
- [M1-T1] Data-availability audit, giving the ADR-001 recommendation
- [M1-T2] Generic `ReportCollector` with recorded fixtures

**Blocked / needs owner**
- Create a GitHub remote, push, and confirm CI is green (M0-T1 AC)
- ADR-009: how to restate O3 (operator pre-dispatch is published ~11 h after our issue time)
- Approve or amend `PREREGISTRATION.md` (two executor-defined operationalisations to confirm: RQ3 evaluation window and k\* definition; RQ1 ±12-month windows)
- ADR-002 (interim): where the live collector runs (GitHub Actions cron vs. a small VM vs. this Mac)
- `GRIDCAST_CONTACT` value for the User-Agent

**Risks changed**
- R2 (forward-report loss): **active**. Every day without a collector loses pre-dispatch vintages.
- R8 (time-zone errors): reduced. EST-year-round convention verified (V-02, V-03).
- New: O3 is not like-for-like as written (V-07, ADR-009).

**Data health:** no collector yet; nothing ingested.

**Metrics snapshot:** n/a.
