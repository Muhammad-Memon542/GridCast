"""Tier 2: LightGBM quantile regression, one booster per quantile level.

Target: asinh-normalised DA OZP (pre-registration §4); quantiles are equivariant under
that monotone transform, so they are mapped back exactly. Features: every builder column
plus daily summaries of the lagged price curves. Quantiles are sorted per row to remove
crossing.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime
from typing import Any

import lightgbm as lgb
import numpy as np
import pandas as pd

from gridcast.backtest.dataset import DAY
from gridcast.models.base import QUANTILES, to_long
from gridcast.models.lear import _Normaliser

NON_FEATURES = {"ts_utc", DAY, "issue_time_utc", "_max_available_at", "weather_lead_days"}

DEFAULT_PARAMS: dict[str, Any] = {
    "n_estimators": 300,
    "learning_rate": 0.05,
    "num_leaves": 15,
    "min_child_samples": 20,
    "colsample_bytree": 0.8,
    "subsample": 0.8,
    "reg_lambda": 1.0,
}


def design(features: pd.DataFrame) -> pd.DataFrame:
    """Model inputs: builder columns plus per-day summaries of the price lags."""
    x = features.drop(columns=[c for c in features if c in NON_FEATURES]).copy()
    for col in [str(c) for c in features.columns if str(c).startswith(("da_lag", "rt_lag"))]:
        by_day = features.groupby(DAY)[col]
        x[f"{col}_daymean"] = by_day.transform("mean")
        x[f"{col}_daymax"] = by_day.transform("max")
        x[f"{col}_daymin"] = by_day.transform("min")
    for col in ("is_holiday", "is_weekend"):
        if col in x:
            x[col] = x[col].astype("int8")
    return x.astype("float64")


class LGBMQuantile:
    point_only = False

    def __init__(self, seed: int = 0, num_threads: int = 4, **params: Any) -> None:
        self.name = "lgbm"
        self.seed = seed
        self.num_threads = num_threads
        self.params = {**DEFAULT_PARAMS, **params}
        self._boosters: dict[float, lgb.LGBMRegressor] = {}
        self._norm: _Normaliser | None = None
        self._columns: list[str] = []

    def fit(self, features: pd.DataFrame, target: pd.Series, *, as_of: datetime) -> None:
        x = design(features)
        self._columns = list(x.columns)
        y = target.to_numpy(dtype="float64")
        self._norm = _Normaliser.fit(y[:, None])
        z = self._norm.forward(y[:, None])[:, 0]
        self._boosters = {}
        for tau in QUANTILES:
            booster = lgb.LGBMRegressor(
                objective="quantile",
                alpha=tau,
                random_state=self.seed,
                deterministic=True,
                force_row_wise=True,
                num_threads=self.num_threads,
                subsample_freq=1,
                verbose=-1,
                **self.params,
            )
            booster.fit(x, z)
            self._boosters[tau] = booster

    def predict_quantiles(
        self,
        features: pd.DataFrame,
        *,
        as_of: datetime,
        target_day: date,
        quantiles: Sequence[float] = QUANTILES,
    ) -> pd.DataFrame:
        if self._norm is None:
            raise RuntimeError("fit() first")
        x = design(features).reindex(columns=self._columns)
        z = np.column_stack([self._boosters[tau].predict(x) for tau in quantiles])
        values = self._norm.inverse(z.reshape(-1, 1)).reshape(z.shape)
        return to_long(features["ts_utc"], values, quantiles)
