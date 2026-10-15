# Status — week 0 (2026-10-15)

Milestone: M1 in progress (M0 complete) | Overall: **Amber**. The collector is installed but has not done its first full pass yet; it waits for AC power.

Work started on Thursday 2026-10-15, ahead of the plan's assumed start of Monday 2026-10-19.

**Done**
- [M0-T1] Repo scaffold: uv, Python 3.12.15, ruff, mypy `--strict`, pytest, pre-commit, Makefile, GitHub Actions. *CI green is pending the first push.*
- [M0-T2] Config system (ADR-008); `.env.example`; gitleaks hook shown to block a fake token
- [M0-T3] `docs/decisions.md`, `STATUS.md`, `docs/data_sources.md`
- [M0-T4] `PREREGISTRATION.md`, **approved by the owner 2026-10-15** (tag `prereg-v1.0`), with its hash in the README
- [M0-T5] `[VERIFY]` sweep, V-01 to V-08
- [M1-T2] Generic IESO collector: listing parser, polite client (≤1 req/s, backoff), sha256 dedupe, SQLite manifest, gzip bronze (ADR-010), recorded fixtures with no network in tests. AC met: a live rerun downloaded 0 files.
- [M1-T3, partial] Live collector installed as a LaunchAgent: hourly, AC power only (ADR-002 interim). Gap report: `make gaps`. *Still open: 7 consecutive gap-free days.*

**Next**
- First full collector pass (~13k files, 3–4 h) the next time the Mac is plugged in
- [M1-T1] Data-availability audit, then the ADR-001 recommendation
- [M1-T4] Parsers to silver

**Blocked / needs owner**
- Plug the Mac in regularly (at least every ~2 weeks) so the collector catches up

**Risks changed**
- R2 (forward-report loss): mitigation in place. It becomes low once the first full pass completes.
- New: DAConstrShadowPrices is published ~6 days late (V-09). It cannot feed issue-time features.

**Data health:** collection started 2026-10-15 (smoke test: DAConstrShadowPrices, 28 files). Gap report: no complete day yet.

**Metrics snapshot:** n/a.
