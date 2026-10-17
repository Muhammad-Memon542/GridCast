"""Glue: configs -> cached features -> backtest -> scores (``gridcast backtest``)."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from pydantic import BaseModel, ConfigDict

from gridcast.backtest.dataset import day_range, feature_matrix, snapshot_id, truth
from gridcast.backtest.engine import (
    BacktestConfig,
    BacktestResult,
    config_hash,
    git_sha,
    run_backtest,
)
from gridcast.backtest.seal import WindowSeal
from gridcast.config import Settings, load_project_config, load_yaml
from gridcast.evaluate.metrics import Scores, score
from gridcast.features.builder import FeatureInputs, load_feature_config
from gridcast.models.base import Forecaster, to_wide
from gridcast.models.naive import NaiveLag

FEATURE_TABLES = ("da_ozp", "rt_ozp_hourly", "demand", "weather_fcst", "gen_by_fuel", "intertie")


class Window(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    start: date
    end: date


class ModelRun(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    refit_every_days: int = 1


class BacktestFile(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    feature_start: date
    validation_eval: Window
    min_train_days: int
    residual_days: int
    min_residual_days: int
    models: dict[str, ModelRun]


def load_backtest_config(path: Path | None = None) -> BacktestFile:
    return BacktestFile.model_validate(load_yaml(path or Settings().configs_dir / "backtest.yaml"))


def model_registry(
    params: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Callable[[], Forecaster]]:
    """Model name -> factory. Optional per-model keyword overrides (used by tuning)."""
    params = params or {}
    registry: dict[str, Callable[[], Forecaster]] = {
        "naive_d1": lambda: NaiveLag(1),
        "naive_d7": lambda: NaiveLag(7),
    }
    try:
        from gridcast.models.lear import LEAR

        registry["lear"] = lambda: LEAR(**params.get("lear", {}))
    except ImportError:  # pragma: no cover
        pass
    try:
        from gridcast.models.lgbm import LGBMQuantile

        registry["lgbm"] = lambda: LGBMQuantile(**params.get("lgbm", {}))
    except ImportError:  # pragma: no cover
        pass
    return registry


def load_features(data_dir: Path, last_day: date, cfg: BacktestFile) -> pd.DataFrame:
    """Feature matrix from ``feature_start`` to ``last_day``, cached by data snapshot and config."""
    project = load_project_config()
    fcfg = load_feature_config()
    key = config_hash(
        {
            "snapshot": snapshot_id(data_dir, FEATURE_TABLES),
            "features": fcfg.model_dump(),
            "issue": str(project.forecast.issue_time_local),
            "start": cfg.feature_start,
            "end": last_day,
        }
    )
    path = data_dir / "gold" / "features" / f"features_{key}.parquet"
    if path.exists():
        return pd.read_parquet(path)
    inputs = FeatureInputs.from_silver(data_dir)
    frame = feature_matrix(
        inputs,
        day_range(cfg.feature_start, last_day),
        fcfg,
        project.forecast.issue_time_local,
        project.forecast.issue_day_offset,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(path, index=False)
    return frame


def evaluate(
    result: BacktestResult, features: pd.DataFrame, da_ozp: pd.DataFrame, spike_threshold: float
) -> Scores:
    wide = to_wide(result.forecasts.rename(columns={"target_ts_utc": "ts_utc"}))
    actual = wide.index.to_series().map(truth(da_ozp))
    naive = wide.index.to_series().map(features.set_index("ts_utc")["da_lag7"])
    keep = actual.notna() & naive.notna()
    return score(
        actual[keep].to_numpy(),
        wide[keep].to_numpy(),
        [float(c) for c in wide.columns],
        naive_weekly=naive[keep].to_numpy(),
        spike_threshold=spike_threshold,
    )


def validation_spike_threshold(da_ozp: pd.DataFrame, cfg: BacktestFile) -> float:
    """Pre-registered spike threshold: P95 of actual DA OZP over the validation split."""
    split = load_project_config().splits.validation
    y = truth(da_ozp)
    days = pd.Series(y.index).dt.tz_convert("Etc/GMT+5").dt.date.to_numpy()
    mask = (days >= split.start) & (days <= (split.end or split.start))
    return float(np.quantile(y.to_numpy()[mask], 0.95))


def run_validation(
    model_name: str,
    *,
    data_dir: Path | None = None,
    params: dict[str, dict[str, Any]] | None = None,
    out_root: Path | None = None,
) -> tuple[BacktestResult, Scores]:
    """Backtest one model over the validation evaluation window (the test window stays sealed)."""
    settings = Settings()
    data_dir = data_dir or settings.data_dir
    cfg = load_backtest_config()
    project = load_project_config()
    seal = WindowSeal(test_start=project.splits.test.start)
    features = load_features(data_dir, cfg.validation_eval.end, cfg)
    da_ozp = pd.read_parquet(data_dir / "silver" / "da_ozp").drop(
        columns=["month"], errors="ignore"
    )
    bt = BacktestConfig(
        eval_start=cfg.validation_eval.start,
        eval_end=cfg.validation_eval.end,
        refit_every_days=cfg.models[model_name].refit_every_days,
        min_train_days=cfg.min_train_days,
        residual_days=cfg.residual_days,
        min_residual_days=cfg.min_residual_days,
    )
    run_payload: dict[str, object] = {
        "model": model_name,
        "params": (params or {}).get(model_name, {}),
        "backtest": bt.__dict__,
    }
    meta: dict[str, object] = {
        "run_id": uuid.uuid4().hex[:12],
        "model_name": model_name,
        "git_sha": git_sha(),
        "config_hash": config_hash(run_payload),
        "data_snapshot_id": snapshot_id(data_dir, FEATURE_TABLES),
        "window": "validation",
    }
    out_dir = None if out_root is None else out_root / f"{model_name}_{meta['config_hash']}"
    factory = model_registry(params)[model_name]
    result = run_backtest(
        factory,
        features,
        da_ozp,
        bt,
        seal=seal,
        quantiles=project.forecast.quantiles,
        out_dir=out_dir,
        run_meta=meta,
    )
    return result, evaluate(result, features, da_ozp, validation_spike_threshold(da_ozp, cfg))
