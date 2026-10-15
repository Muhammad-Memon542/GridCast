# Status — week 0 (2026-10-15)

Milestone: M1 in progress (M0 complete; fast track) | Overall: **Green**. The collector runs 24/7 on the production server; its first full pass started 2026-10-16 00:00 UTC.

Work started on Thursday 2026-10-15, ahead of the plan's assumed start of Monday 2026-10-19.

**Done**
- [M0-T1] Repo scaffold: uv, Python 3.12.15, ruff, mypy `--strict`, pytest, pre-commit, Makefile, GitHub Actions. **CI green** on GitHub (run 38798872788, commit c0c9cf5)
- [M0-T2] Config system (ADR-008); `.env.example`; gitleaks hook shown to block a fake token
- [M0-T3] `docs/decisions.md`, `STATUS.md`, `docs/data_sources.md`
- [M0-T4] `PREREGISTRATION.md`, **approved by the owner 2026-10-15** (tag `prereg-v1.0`), with its hash in the README
- [M0-T5] `[VERIFY]` sweep, V-01 to V-08
- [M1-T2] Generic IESO collector: listing parser, polite client (≤1 req/s, backoff), sha256 dedupe, SQLite manifest, gzip bronze (ADR-010), recorded fixtures with no network in tests. AC met: a live rerun downloaded 0 files.
- [M1-T3, partial] Live collector on the Azure production server (systemd timer, hourly). The Mac LaunchAgent was removed and is kept as a fallback. Gap report: `make server-gaps`. *Still open: 7 consecutive gap-free days.*
- ADR-001 accepted (Grid Status free plan); ADR-002 accepted (Azure for Students VM, Canada Central). See `docs/runbook.md`.

**Next**
- Let the first full collector pass finish (~13k files, 3–4 h)
- [M1-T5] Backfill DA and RT OZP from Grid Status (~160k rows this month); pre-dispatch vintages after the 1 Nov quota reset
- [M1-T1] Data-availability audit, then the ADR-001 recommendation
- [M1-T4] Parsers to silver

**Blocked / needs owner**
- Nothing blocking

**Risks changed**
- R2 (forward-report loss): **low**. 24/7 server collector; no dependency on the Mac.
- New: DAConstrShadowPrices is published ~6 days late (V-09). It cannot feed issue-time features.

**Data health:** collection started 2026-10-15 (smoke test: DAConstrShadowPrices, 28 files). Gap report: no complete day yet.

**Metrics snapshot:** n/a.
