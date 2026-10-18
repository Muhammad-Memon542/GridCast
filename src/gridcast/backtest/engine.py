"""Rolling-origin backtester (M3-T1).

For each target day D, in order:

1. The issue time is 09:00 America/Toronto on D-1 (from the feature matrix).
2. Training rows are days before D whose targets were published by the issue time,
   optionally limited to the last ``train_window_days``.
3. The model is refitted every ``refit_every_days`` (daily for cheap models, weekly for
   expensive ones) and predicts 21 non-crossing quantiles for D's 24 hours.
4. Point models (``point_only = True``) get quantiles from their own past out-of-sample
   residuals over the last ``residual_days`` (split-conformal style). Until enough exist,
   the model's in-sample residuals are used.

Forecasts are cached per day under ``gold/forecasts/<run>/`` with the forecast-record
columns of plan §7.6, so an interrupted run resumes where it stopped.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import time as clock
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import structlog

from gridcast.backtest.dataset import DAY, targets_known_at
from gridcast.backtest.seal import WindowSeal
from gridcast.config import REPO_ROOT
from gridcast.models.base import QUANTILES, EmpiricalResiduals, Forecaster, to_long, to_wide
from gridcast.models.conformal import RollingCQR

log = structlog.get_logger(__name__)


@dataclass(frozen=True)
class BacktestConfig:
    eval_start: date
    eval_end: date
    refit_every_days: int = 1
    train_window_days: int | None = None
    min_train_days: int = 28
    residual_days: int = 56
    min_residual_days: int = 14


@dataclass
class BacktestResult:
    forecasts: pd.DataFrame
    fit_seconds: float = 0.0
    predict_seconds: float = 0.0
    fits: int = 0
    days: list[date] = field(default_factory=list)


def git_sha() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def config_hash(payload: dict[str, object]) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()[
        :16
    ]


def run_backtest(
    factory: Callable[[], Forecaster],
    features: pd.DataFrame,
    da_ozp: pd.DataFrame,
    config: BacktestConfig,
    *,
    seal: WindowSeal,
    quantiles: Sequence[float] = QUANTILES,
    out_dir: Path | None = None,
    run_meta: dict[str, object] | None = None,
    calibrator: RollingCQR | None = None,
    bounds: tuple[float, float] | None = None,
) -> BacktestResult:
    days = sorted(d for d in features[DAY].unique() if config.eval_start <= d <= config.eval_end)
    for day in days:
        seal.check(day)
    meta = dict(run_meta or {})
    result = BacktestResult(forecasts=pd.DataFrame(), days=days)
    by_day = {d: g for d, g in features.groupby(DAY, sort=True)}
    cached = _load_cache(out_dir)
    model: Forecaster | None = None
    last_fit: date | None = None
    history: list[pd.DataFrame] = [cached] if not cached.empty else []
    in_sample: EmpiricalResiduals | None = None

    for day in days:
        rows = by_day[day]
        issue = pd.Timestamp(rows["issue_time_utc"].iloc[0])
        if out_dir is not None and (out_dir / f"day={day}.parquet").exists():
            continue
        if model is None or last_fit is None or (day - last_fit).days >= config.refit_every_days:
            train_x, train_y = _training_set(features, da_ozp, day, issue, config)
            if train_x[DAY].nunique() < config.min_train_days:
                log.info("backtest_skip_day", day=str(day), reason="not enough training days")
                continue
            model = factory()
            started = clock.perf_counter()
            model.fit(train_x, train_y, as_of=issue.to_pydatetime())
            result.fit_seconds += clock.perf_counter() - started
            result.fits += 1
            last_fit = day
            in_sample = getattr(model, "in_sample_residuals", None)

        observe = getattr(model, "observe", None)
        if observe is not None and last_fit != day:
            # Sequence models need the latest published prices, not just the training set.
            past_x, past_y = _training_set(features, da_ozp, day, issue, config)
            observe(past_x, past_y)
        started = clock.perf_counter()
        long = model.predict_quantiles(
            rows, as_of=issue.to_pydatetime(), target_day=day, quantiles=quantiles
        )
        result.predict_seconds += clock.perf_counter() - started
        if getattr(model, "point_only", False):
            long = _residual_quantiles(long, history, da_ozp, issue, config, quantiles, in_sample)
        raw = long
        if calibrator is not None and history:
            # Calibrate against the model's raw past quantiles, never already-calibrated ones.
            past = pd.concat(history, ignore_index=True)
            past = past.assign(value=past["value_raw"])
            long = calibrator.adjust(long, past, targets_known_at(da_ozp, issue), issue, quantiles)
        if bounds is not None:
            # DA OZP settles inside [floor, cap] (V-04): outside quantiles are impossible.
            long = long.assign(value=long["value"].clip(*bounds))
        _check_forecast(long, rows, quantiles)
        record = _record(long, issue, meta, model.name)
        record["value_raw"] = (
            to_wide(raw)
            .stack()
            .reindex(pd.MultiIndex.from_arrays([record["target_ts_utc"], record["quantile"]]))
            .to_numpy()
        )
        if out_dir is not None:
            out_dir.mkdir(parents=True, exist_ok=True)
            record.to_parquet(out_dir / f"day={day}.parquet", index=False)
        history.append(record)

    result.forecasts = pd.concat(history, ignore_index=True) if history else pd.DataFrame()
    if out_dir is not None:
        timing = {
            "fit_seconds": result.fit_seconds,
            "predict_seconds": result.predict_seconds,
            "fits": result.fits,
        }
        (out_dir / "run.json").write_text(
            json.dumps({**meta, **asdict(config), **timing}, default=str, indent=1)
        )
    log.info(
        "backtest_done",
        model=meta.get("model_name"),
        days=len(days),
        fits=result.fits,
        fit_s=round(result.fit_seconds, 2),
        predict_s=round(result.predict_seconds, 2),
    )
    return result


def _training_set(
    features: pd.DataFrame,
    da_ozp: pd.DataFrame,
    day: date,
    issue: pd.Timestamp,
    config: BacktestConfig,
) -> tuple[pd.DataFrame, pd.Series]:
    past = features[features[DAY] < day]
    if config.train_window_days is not None:
        past = past[past[DAY] >= day - timedelta(days=config.train_window_days)]
    known = targets_known_at(da_ozp, issue)
    y = past["ts_utc"].map(known)
    keep = y.notna()
    return past[keep].reset_index(drop=True), y[keep].reset_index(drop=True).astype("float64")


def _residual_quantiles(
    long: pd.DataFrame,
    history: list[pd.DataFrame],
    da_ozp: pd.DataFrame,
    issue: pd.Timestamp,
    config: BacktestConfig,
    quantiles: Sequence[float],
    in_sample: EmpiricalResiduals | None,
) -> pd.DataFrame:
    """Replace a point model's quantiles with point + quantiles of past OOS residuals."""
    point = long[long["quantile"] == 0.5].set_index("ts_utc")["value"]
    residuals = np.array([])
    if history:
        past = pd.concat(history, ignore_index=True)
        past = past[
            (past["quantile"] == 0.5)
            & (past["target_ts_utc"] >= issue - pd.Timedelta(days=config.residual_days))
        ]
        actual = past["target_ts_utc"].map(targets_known_at(da_ozp, issue))
        residuals = (actual - past["value"]).dropna().to_numpy()
    enough = residuals.size >= 24 * config.min_residual_days
    source = EmpiricalResiduals(residuals) if enough or in_sample is None else in_sample
    values = source.apply(point.to_numpy(), quantiles)
    return to_long(point.index.to_series(), values, quantiles)


def _check_forecast(long: pd.DataFrame, rows: pd.DataFrame, quantiles: Sequence[float]) -> None:
    wide = to_wide(long)
    if len(wide) != len(rows) or set(wide.index) != set(rows["ts_utc"]):
        raise ValueError("forecast must cover exactly the target day's hours")
    if [float(c) for c in wide.columns] != sorted(quantiles):
        raise ValueError("forecast is missing quantile levels")
    if (np.diff(wide.to_numpy(), axis=1) < -1e-9).any():
        raise ValueError("quantiles cross")
    if not np.isfinite(wide.to_numpy()).all():
        raise ValueError("forecast has non-finite values")


def _record(
    long: pd.DataFrame, issue: pd.Timestamp, meta: dict[str, object], name: str
) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "run_id": str(meta.get("run_id", "")),
            "model_name": name,
            "model_version": str(meta.get("model_version", "")),
            "git_sha": str(meta.get("git_sha", "")),
            "config_hash": str(meta.get("config_hash", "")),
            "data_snapshot_id": str(meta.get("data_snapshot_id", "")),
            "issued_at_utc": issue,
            "target_ts_utc": long["ts_utc"].to_numpy(),
            "quantile": long["quantile"].to_numpy(),
            "value": long["value"].to_numpy(),
            "created_at_utc": pd.Timestamp(datetime.now(UTC)),
        }
    )


def _load_cache(out_dir: Path | None) -> pd.DataFrame:
    if out_dir is None or not out_dir.exists():
        return pd.DataFrame()
    files = sorted(out_dir.glob("day=*.parquet"))
    return (
        pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)
        if files
        else pd.DataFrame()
    )
