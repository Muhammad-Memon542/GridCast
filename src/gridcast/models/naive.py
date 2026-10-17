"""Tier 0: naive baselines (pre-registration §4)."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime

import numpy as np
import pandas as pd

from gridcast.models.base import QUANTILES, EmpiricalResiduals, to_long


class NaiveLag:
    """p(d - lag, h): the same hour ``lag`` days earlier (``da_lag{lag}`` feature).

    It has no parameters, so its in-sample residuals are genuine out-of-sample errors;
    the backtester still replaces them with rolling out-of-sample residuals.
    """

    point_only = True

    def __init__(self, lag_days: int) -> None:
        self.lag_days = lag_days
        self.name = f"naive_d{lag_days}"
        self.column = f"da_lag{lag_days}"
        self.in_sample_residuals: EmpiricalResiduals | None = None

    def _point(self, features: pd.DataFrame) -> np.ndarray:
        """``da_lag{lag}``, falling back to the other price lags where it is missing
        (e.g. the day after the 2025-05-22 gap)."""
        point = features[self.column].astype("float64")
        lags = [str(c) for c in features.columns if str(c).startswith("da_lag")]
        for other in sorted(c for c in lags if c != self.column):
            point = point.fillna(features[other].astype("float64"))
        return point.to_numpy()

    def fit(self, features: pd.DataFrame, target: pd.Series, *, as_of: datetime) -> None:
        self.in_sample_residuals = EmpiricalResiduals(target.to_numpy() - self._point(features))

    def predict_quantiles(
        self,
        features: pd.DataFrame,
        *,
        as_of: datetime,
        target_day: date,
        quantiles: Sequence[float] = QUANTILES,
    ) -> pd.DataFrame:
        point = self._point(features)
        residuals = self.in_sample_residuals or EmpiricalResiduals(point[:0])
        return to_long(features["ts_utc"], residuals.apply(point, quantiles), quantiles)
