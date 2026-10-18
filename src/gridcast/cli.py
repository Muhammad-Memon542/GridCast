"""Command-line entry point: ``gridcast <command>``."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

from gridcast.config import Settings
from gridcast.ingest.collector import load_collector_config, run_collection
from gridcast.ingest.gaps import gap_report, render
from gridcast.ingest.manifest import Manifest
from gridcast.logs import configure_logging


def _utc_day(value: str) -> datetime:
    return datetime.combine(date.fromisoformat(value), time(0), tzinfo=UTC)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="gridcast")
    sub = parser.add_subparsers(dest="command", required=True)

    collect = sub.add_parser("collect", help="download new IESO report files into bronze")
    collect.add_argument(
        "--require-ac", action="store_true", help="do nothing unless the Mac is on AC power"
    )
    collect.add_argument("--force", action="store_true", help="ignore per-report cadence")
    collect.add_argument(
        "--report", action="append", dest="reports", metavar="NAME", help="limit to a report"
    )
    collect.add_argument(
        "--config", type=Path, help="collector config (default configs/collector.yaml)"
    )

    gaps = sub.add_parser("gaps", help="show the collector gap report")
    gaps.add_argument("--days", type=int, default=7)

    gs = sub.add_parser("gs-pull", help="backfill one Grid Status dataset into bronze (ADR-001)")
    gs.add_argument("dataset")
    gs.add_argument("--start", required=True, type=_utc_day, help="UTC date, inclusive")
    gs.add_argument("--end", type=_utc_day, help="UTC date, exclusive (default: today + 2 days)")
    gs.add_argument("--max-quota", type=float, default=0.7, help="max fraction of monthly rows")

    sub.add_parser("weather", help="fetch Open-Meteo previous-run forecasts into bronze (M1-T6)")
    sub.add_parser("silver", help="rebuild all silver tables from bronze (M1-T4)")
    bt = sub.add_parser("backtest", help="rolling-origin backtest on the validation window (M3)")
    bt.add_argument("model")
    tune = sub.add_parser("tune", help="Optuna tuning on the validation window (M3-T6)")
    tune.add_argument("model")
    tune.add_argument("--trials", type=int, default=25)
    fz = sub.add_parser(
        "freeze", help="validate a model config and write configs/frozen/<model>.yaml"
    )
    fz.add_argument("model")
    fz.add_argument("--from-study", action="store_true", help="use the best Optuna trial's params")
    fz.add_argument("--calibrate", action="store_true", help="apply rolling CQR (M3-T8)")
    sub.add_parser("validation", help="compare all frozen models on the validation window")
    live = sub.add_parser(
        "live", help="issue tomorrow's forecast with the frozen champion (M3-T10)"
    )
    live.add_argument("--target-day", type=date.fromisoformat, help="default: tomorrow (Toronto)")
    live.add_argument(
        "--refresh", action="store_true", help="collect inputs and rebuild silver first"
    )
    live.add_argument("--publish", action="store_true", help="commit and push the forecast log")
    live.add_argument(
        "--log-dir", type=Path, help="forecast log checkout (default data/forecast_log)"
    )
    sub.add_parser("site-data", help="build site/data/site.json from results files")
    sc = sub.add_parser("scorecard", help="score published forecasts against actual prices")
    sc.add_argument("--log-dir", type=Path)
    sc.add_argument("--publish", action="store_true")
    rq1 = sub.add_parser("rq1", help="RQ1 break tests (placebo now; the real run after G1)")
    rq1.add_argument("--after-g1", action="store_true", help="read post-go-live prices (ADR-011)")
    dq = sub.add_parser("dq", help="write the data-quality report (M1-T9)")
    dq.add_argument("--out", type=Path, default=Path("docs/results/dq_report.md"))
    return parser


def _collect(args: argparse.Namespace, settings: Settings) -> int:
    summary = run_collection(
        settings,
        load_collector_config(args.config),
        only=set(args.reports) if args.reports else None,
        force=args.force,
        require_ac=args.require_ac,
    )
    return 1 if summary.status == "partial" else 0


def _gaps(args: argparse.Namespace, settings: Settings) -> int:
    manifest_path = settings.data_dir / "bronze" / "_manifest.sqlite"
    if not manifest_path.exists():
        print("No manifest yet: the collector has never run.")
        return 1
    with Manifest(manifest_path) as manifest:
        report = gap_report(
            manifest, load_collector_config(), days=args.days, now=datetime.now(UTC)
        )
    print(render(report))
    return 0


def _gs_pull(args: argparse.Namespace, settings: Settings) -> int:  # pragma: no cover - network
    from gridstatusio.gs_client import GridStatusClient

    from gridcast.ingest.gridstatus import pull

    if settings.gridstatus_api_key is None:
        print("GRIDSTATUS_API_KEY is not set (see .env.example).")
        return 1
    api = GridStatusClient(api_key=settings.gridstatus_api_key.get_secret_value())
    end = args.end or _utc_day((datetime.now(UTC) + timedelta(days=2)).date().isoformat())
    result = pull(
        api,
        args.dataset,
        args.start,
        end,
        data_dir=settings.data_dir,
        max_quota_fraction=args.max_quota,
    )
    print(f"{result.dataset}: {result.rows:,} rows (estimated {result.estimated_rows:,})")
    print(result.path)
    return 0


def _weather(args: argparse.Namespace, settings: Settings) -> int:
    from gridcast.ingest.http import PoliteClient
    from gridcast.ingest.weather import fetch_weather, load_weather_config

    with PoliteClient(settings.user_agent, min_interval_s=settings.http_min_interval_s) as client:
        summary = fetch_weather(
            load_weather_config(),
            data_dir=settings.data_dir,
            client=client,
            today=datetime.now(UTC).date(),
        )
    return 1 if summary.errors else 0


def _silver(args: argparse.Namespace, settings: Settings) -> int:
    from gridcast.silver import build_all

    for name, rows in build_all(settings.data_dir).items():
        print(f"{name:>15}: {rows:>9,} rows")
    return 0


def _backtest(args: argparse.Namespace, settings: Settings) -> int:  # pragma: no cover - long run
    from gridcast.backtest.runner import run_validation

    out = settings.data_dir / "gold" / "forecasts" / "validation"
    result, scores = run_validation(args.model, data_dir=settings.data_dir, out_root=out)
    print(f"{args.model}: {len(result.days)} days, {result.fits} fits")
    for key, value in scores.as_dict().items():
        print(f"  {key:>16}: {value:.4f}")
    return 0


def _tune(args: argparse.Namespace, settings: Settings) -> int:  # pragma: no cover - long run
    from gridcast.backtest.tune import tune

    study = tune(args.model, args.trials, settings.data_dir)
    print(f"best CRPS {study.best_value:.4f} with {study.best_params}")
    return 0


def _freeze(args: argparse.Namespace, settings: Settings) -> int:  # pragma: no cover - long run
    from gridcast.backtest.freeze import freeze

    params = None
    source = "configs/models (fixed, not tuned)"
    if args.from_study:
        import optuna

        db = settings.data_dir / "gold" / "tuning" / f"{args.model}.db"
        study = optuna.load_study(study_name=args.model, storage=f"sqlite:///{db}")
        params = dict(study.best_params)
        done = len([t for t in study.trials if t.state.is_finished()])
        source = (
            f"optuna study '{args.model}': best of {done} trials,"
            f" validation CRPS {study.best_value:.4f}"
        )
    print(
        freeze(
            args.model,
            params=params,
            calibrate=args.calibrate,
            source=source,
            data_dir=settings.data_dir,
        )
    )
    return 0


def _validation(args: argparse.Namespace, settings: Settings) -> int:  # pragma: no cover - long run
    from gridcast.backtest.validation_report import build

    print(build(settings.data_dir))
    return 0


def _live(args: argparse.Namespace, settings: Settings) -> int:  # pragma: no cover - live run
    from gridcast.pipelines.live import (
        LIVE_TABLES,
        default_target_day,
        forecast,
        publish,
        refresh_inputs,
        write_log_entry,
    )
    from gridcast.silver import build_all

    now = datetime.now(UTC)
    target = args.target_day or default_target_day(now)
    log_dir = args.log_dir or settings.data_dir / "forecast_log"
    if args.refresh:
        refresh_inputs(settings)
        build_all(settings.data_dir, only=LIVE_TABLES)
    entry = forecast(target, data_dir=settings.data_dir)
    path = write_log_entry(entry, log_dir, now=datetime.now(UTC), data_dir=settings.data_dir)
    print(path)
    if args.publish:
        pushed = publish(
            log_dir, f"forecast for {target} issued {entry.issued_at_utc:%Y-%m-%dT%H:%MZ}"
        )
        print("pushed" if pushed else "committed locally (no remote)")
    return 0


def _scorecard(args: argparse.Namespace, settings: Settings) -> int:  # pragma: no cover - live run
    import pandas as pd

    from gridcast.backtest.dataset import truth
    from gridcast.pipelines.live import publish
    from gridcast.pipelines.scorecard import write_scorecard

    log_dir = args.log_dir or settings.data_dir / "forecast_log"
    da = pd.read_parquet(settings.data_dir / "silver" / "da_ozp").drop(
        columns=["month"], errors="ignore"
    )
    result = write_scorecard(log_dir, truth(da))
    print({k: result[k] for k in ("published_days", "scored_days", "last_7_days")})
    if args.publish:
        publish(log_dir, f"scorecard {result['generated_at_utc']}")
    return 0


def _site_data(args: argparse.Namespace, settings: Settings) -> int:
    from gridcast.pipelines.site_data import build

    print(build())
    return 0


def _rq1(args: argparse.Namespace, settings: Settings) -> int:  # pragma: no cover - data run
    from gridcast.config import load_project_config
    from gridcast.evaluate.rq1 import run, write

    go_live = load_project_config().market.go_live
    placebo = not args.after_g1
    result = run(settings.data_dir, go_live=go_live, placebo=placebo, after_g1=args.after_g1)
    name = "rq1_placebo.json" if placebo else "rq1.json"
    print(write(result, Path("docs/results") / name))
    if placebo:
        print("placebo (registered rule):", result["criteria"], "supported:", result["supported"])
        print(
            "placebo reference 50/90/95%:",
            result["placebo_reference"]["placebo_quantiles_50_90_95"],
        )
    else:
        print("registered rule supported:", result["registered_rule"]["supported"])
        print("calibrated:", result["calibrated"])
    return 0


def _dq(args: argparse.Namespace, settings: Settings) -> int:
    from gridcast.quality.dq import write_report

    print(write_report(settings.data_dir, args.out))
    return 0


COMMANDS = {
    "backtest": _backtest,
    "tune": _tune,
    "dq": _dq,
    "rq1": _rq1,
    "freeze": _freeze,
    "validation": _validation,
    "live": _live,
    "scorecard": _scorecard,
    "site-data": _site_data,
    "collect": _collect,
    "gaps": _gaps,
    "gs-pull": _gs_pull,
    "weather": _weather,
    "silver": _silver,
}


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    settings = Settings()
    configure_logging(settings.log_level)
    return COMMANDS[args.command](args, settings)
