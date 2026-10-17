"""Daily live forecast (M3-T10 early publishing; M6-T3 later).

At 09:00 America/Toronto on D-1:

1. optionally refresh inputs (collector pass for the reports features need; weather);
2. rebuild the silver tables the features use;
3. build features as of the issue time (never later, even if the job runs late);
4. run the frozen champion over the last ``warmup_days`` plus D, so rolling CQR has its
   out-of-sample calibration history (forecasts for past days are computed, cached under
   ``gold/forecasts/live/`` and never reported);
5. write D's forecast to the append-only forecast log, refusing to overwrite a day or to
   publish after the bid window closes (10:00 EPT).
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
import structlog

from gridcast.backtest.dataset import snapshot_id
from gridcast.backtest.engine import BacktestConfig, config_hash, git_sha, run_backtest
from gridcast.backtest.freeze import frozen_path, load_frozen
from gridcast.backtest.runner import (
    FEATURE_TABLES,
    load_backtest_config,
    load_features,
    model_registry,
)
from gridcast.backtest.seal import WindowSeal
from gridcast.config import Settings, load_project_config
from gridcast.models.base import to_wide
from gridcast.models.conformal import RollingCQR
from gridcast.timeutil.ieso import EASTERN, issue_time_utc

log = structlog.get_logger(__name__)

CHAMPION = "lgbm"
BID_WINDOW_CLOSES = time(10, 0)
LIVE_TABLES = (
    "da_ozp",
    "rt_ozp_hourly",
    "rt_ozp_5min",
    "demand",
    "weather_fcst",
    "gen_by_fuel",
    "intertie",
)
REFRESH_REPORTS = (
    "DAHourlyOntarioZonalPrice",
    "RealtimeOntarioZonalPrice",
    "Demand",
    "GenOutputCapabilityMonth",
    "IntertieScheduleFlowYear",
)


class PublicationError(RuntimeError):
    pass


@dataclass(frozen=True)
class LiveForecast:
    target_day: date
    issued_at_utc: datetime
    model: str
    frozen_config_sha: str
    wide: pd.DataFrame  # index ts_utc, columns quantile levels

    def to_json(self, meta: dict[str, Any]) -> dict[str, Any]:
        stamps = pd.DatetimeIndex(self.wide.index)
        values = self.wide.to_numpy(dtype="float64")
        hours = [
            {
                "ts_utc": stamps[i].isoformat(),
                "hour_ending": i + 1,
                "quantiles": [round(float(v), 4) for v in values[i]],
            }
            for i in range(len(stamps))
        ]
        return {
            "target_day": self.target_day.isoformat(),
            "issued_at_utc": self.issued_at_utc.isoformat(),
            "model": self.model,
            "quantile_levels": [float(q) for q in self.wide.columns],
            "hours": hours,
            "units": "$/MWh, day-ahead Ontario Zonal Price, hour-ending EST",
            **meta,
        }


def default_target_day(now: datetime) -> date:
    return (now.astimezone(EASTERN) + timedelta(days=1)).date()


def refresh_inputs(settings: Settings) -> None:  # pragma: no cover - network
    from gridcast.ingest.collector import load_collector_config, run_collection
    from gridcast.ingest.http import PoliteClient
    from gridcast.ingest.weather import fetch_weather, load_weather_config

    run_collection(settings, load_collector_config(), only=set(REFRESH_REPORTS), force=True)
    with PoliteClient(settings.user_agent, min_interval_s=settings.http_min_interval_s) as client:
        fetch_weather(
            load_weather_config(),
            data_dir=settings.data_dir,
            client=client,
            today=datetime.now(UTC).date(),
        )


def forecast(target_day: date, *, data_dir: Path, warmup_days: int = 56) -> LiveForecast:
    project = load_project_config()
    cfg = load_backtest_config()
    issue = issue_time_utc(
        target_day, project.forecast.issue_time_local, project.forecast.issue_day_offset
    )
    frozen = load_frozen(CHAMPION)
    params = frozen.get("params") or {}
    seal = WindowSeal.open_with(project.splits.test.start, frozen_path(CHAMPION))
    features = load_features(data_dir, target_day, cfg)
    features = features[features["issue_time_utc"] <= pd.Timestamp(issue)]  # nothing issued later
    da_ozp = pd.read_parquet(data_dir / "silver" / "da_ozp").drop(
        columns=["month"], errors="ignore"
    )
    bt = BacktestConfig(
        eval_start=target_day - timedelta(days=warmup_days),
        eval_end=target_day,
        refit_every_days=cfg.models[CHAMPION].refit_every_days,
        min_train_days=cfg.min_train_days,
        residual_days=cfg.residual_days,
        min_residual_days=cfg.min_residual_days,
    )
    payload: dict[str, object] = {
        "model": CHAMPION,
        "params": params,
        "frozen": frozen.get("frozen_at_utc"),
    }
    out_dir = data_dir / "gold" / "forecasts" / "live" / f"{CHAMPION}_{config_hash(payload)}"
    meta: dict[str, object] = {
        "run_id": "live",
        "model_name": CHAMPION,
        "git_sha": git_sha(),
        "config_hash": config_hash(payload),
        "data_snapshot_id": snapshot_id(data_dir, FEATURE_TABLES),
    }
    calibrator = (
        RollingCQR(window_days=cfg.residual_days, min_days=cfg.min_residual_days)
        if frozen.get("calibrate_cqr")
        else None
    )
    result = run_backtest(
        model_registry({CHAMPION: params})[CHAMPION],
        features,
        da_ozp,
        bt,
        seal=seal,
        quantiles=project.forecast.quantiles,
        out_dir=out_dir,
        run_meta=meta,
        calibrator=calibrator,
    )
    day = result.forecasts[
        pd.to_datetime(result.forecasts["issued_at_utc"], utc=True) == pd.Timestamp(issue)
    ]
    if day.empty:
        raise PublicationError(f"no forecast produced for {target_day}")
    wide = to_wide(day.rename(columns={"target_ts_utc": "ts_utc"}))
    return LiveForecast(
        target_day=target_day,
        issued_at_utc=issue,
        model=f"{CHAMPION}+cqr",
        frozen_config_sha=str(frozen.get("code_git_sha")),
        wide=wide,
    )


def write_log_entry(entry: LiveForecast, log_dir: Path, *, now: datetime, data_dir: Path) -> Path:
    """Append-only: one file per target day; never rewritten; refused after 10:00 EPT on D-1."""
    deadline = datetime.combine(
        entry.target_day - timedelta(days=1), BID_WINDOW_CLOSES, tzinfo=EASTERN
    )
    if now > deadline:
        raise PublicationError(
            f"too late to publish {entry.target_day}: bid window closed at {deadline}"
        )
    path = (
        log_dir
        / "forecasts"
        / f"{entry.target_day:%Y}"
        / f"{entry.target_day:%m}"
        / f"{entry.target_day}.json"
    )
    if path.exists():
        raise PublicationError(f"{path.name} already published; the log is append-only")
    meta = {
        "published_at_utc": now.astimezone(UTC).isoformat(timespec="seconds"),
        "code_git_sha": git_sha(),
        "frozen_config_git_sha": entry.frozen_config_sha,
        "data_snapshot_id": snapshot_id(data_dir, FEATURE_TABLES),
        "attribution": "Price history before live collection: data provided by Grid Status "
        "(https://www.gridstatus.io/).",
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(entry.to_json(meta), indent=1) + "\n"
    path.write_text(payload, encoding="utf-8")
    (log_dir / "latest.json").write_text(payload, encoding="utf-8")
    return path


def publish(log_dir: Path, message: str) -> bool:  # pragma: no cover - git/network
    """Commit and push the log if it is a git checkout with a remote; True if pushed."""
    if not (log_dir / ".git").exists():
        log.warning("forecast_log_not_a_repo", path=str(log_dir))
        return False
    git = ["git", "-C", str(log_dir)]
    subprocess.run([*git, "add", "-A"], check=True)
    subprocess.run([*git, "commit", "-q", "-m", message], check=True)
    remotes = subprocess.run(
        [*git, "remote"], capture_output=True, text=True, check=True
    ).stdout.split()
    if not remotes:
        return False
    subprocess.run([*git, "push", "-q", remotes[0], "HEAD"], check=True)
    return True
