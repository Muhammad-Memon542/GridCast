# GridCast

Probabilistic day-ahead price forecasting and battery dispatch for Ontario's renewed electricity market.

GridCast forecasts the next day's 24 hourly **day-ahead Ontario Zonal Prices** with 21 calibrated quantiles. It turns those forecasts into charge/discharge schedules for a simulated 100 MW / 400 MWh battery and reports their value in $/MW-year. The project has two halves:

- **Research:** a pre-registered benchmark, from naive baselines through LEAR, LightGBM and deep nets to time-series foundation models, on the market redesigned on 1 May 2025.
- **Production:** a monitored daily pipeline that publishes a timestamped forecast before the 10:00 EPT day-ahead bid window closes.

> **Status:** Milestone M1 (data foundation); live collector running. No results yet. Every number this README will ever show is generated from a results file in this repo.

## Pre-registration

The research questions, splits, metrics and decision rules were registered **before any model touched data**:

| | |
| --- | --- |
| File | [`PREREGISTRATION.md`](PREREGISTRATION.md) |
| Registered | 2026-10-15, commit `47d695503c10cd6aac072287f75e5dceff35f8d9` |
| SHA-256 of file at that commit | `36b7789496407ae40c170cbb7bdebb6c6d5ec0425707dac058fd0124d2c0adda` |
| Approved by the owner | 2026-10-15, git tag `prereg-v1.0`. Later changes appear only in its §11 (Amendments) and §12 (Deviations). |

Check it yourself:

```bash
git show 47d6955:PREREGISTRATION.md | shasum -a 256
git diff 47d6955 prereg-v1.0 -- PREREGISTRATION.md
```

## Quick start

Requires [uv](https://docs.astral.sh/uv/). uv installs Python 3.12 itself.

```bash
make setup   # create .venv from uv.lock and install git hooks
make check   # ruff, mypy --strict, pytest (network disabled, coverage gate)
make hooks   # all pre-commit hooks, including gitleaks secret scanning
make help    # every target; data/backtest/paper targets arrive in later milestones
```

Copy `.env.example` to `.env` for local settings. `.env` is git-ignored.

### Live data collector

Most IESO reports stay online for only 30–90 days, so a collector snapshots them every hour and keeps every published version:

```bash
make collector-install   # macOS LaunchAgent: hourly, only while on AC power
make gaps                # gap report for the last 7 days
make collect             # one pass right now
```

## Repository map

| Path | What |
| --- | --- |
| [`GRIDCAST_PROJECT_PLAN.md`](GRIDCAST_PROJECT_PLAN.md) | Project plan and execution brief |
| [`STATUS.md`](STATUS.md) | Progress log, updated every session |
| [`docs/decisions.md`](docs/decisions.md) | ADRs and the `[VERIFY]` log |
| [`docs/data_sources.md`](docs/data_sources.md) | Dataset register and collection etiquette |
| `configs/project.yaml` | Pre-registered constants: issue time, quantiles, splits, battery |
| `src/gridcast/` | Library code (ingest → parse → features → models → backtest → dispatch → serve) |
| `tests/` | Unit, leakage and golden-file tests (no network) |

## Licences and data terms

- **TimesFM-3** weights are under a non-commercial licence. They are used for the research benchmark only, never in production serving.
- **Open-Meteo** is free for non-commercial use.
- Data from the hosted backfill API (ADR-001) is likely not redistributable and is excluded from any published dataset.
- The licence for this repository's code is still to be chosen `[OWNER]`.
