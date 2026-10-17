"""Tier 1: SARIMAX, one model per hour on the daily series of that hour (pre-registration §4).

Each hour's prices form a daily series (asinh-normalised with the training median/MAD,
pooled over hours). SARIMAX(order) x (seasonal_order, period 7) with a constant and the
hour's exogenous inputs; refitted daily by the backtester. Point model: the backtester
adds quantiles from rolling out-of-sample residuals.
"""

from __future__ import annotations

import warnings
from collections.abc import Sequence
from datetime import date, datetime

import numpy as np
import pandas as pd
from statsmodels.tools.sm_exceptions import ConvergenceWarning
from statsmodels.tsa.statespace.sarimax import SARIMAX as _SARIMAX

from gridcast.backtest.dataset import DAY
from gridcast.models.base import QUANTILES, EmpiricalResiduals, to_long
from gridcast.models.lear import _Normaliser


class SARIMAX24:
    point_only = True

    def __init__(
        self,
        order: tuple[int, int, int] = (1, 0, 1),
        seasonal_order: tuple[int, int, int] = (1, 0, 1),
        exogenous: Sequence[str] = ("wx_temperature_2m", "demand_lag2"),
        maxiter: int = 50,
    ) -> None:
        self.name = "sarimax"
        self.order = tuple(order)
        self.seasonal_order = (*tuple(seasonal_order), 7)
        self.exogenous = list(exogenous)
        self.maxiter = maxiter
        self._fits: dict[int, object] = {}
        self._y_norm: _Normaliser | None = None
        self._x_norm: dict[int, _Normaliser] = {}
        self.in_sample_residuals: EmpiricalResiduals | None = None

    def _exog(self, rows: pd.DataFrame, hour: int, fit: bool) -> np.ndarray:
        x = rows[self.exogenous].to_numpy(dtype="float64")
        if fit:
            self._x_norm[hour] = _Normaliser.fit(x)
        z: np.ndarray = np.nan_to_num(self._x_norm[hour].forward(x), nan=0.0)
        return z

    def fit(self, features: pd.DataFrame, target: pd.Series, *, as_of: datetime) -> None:
        frame = features.assign(_y=target.to_numpy()).sort_values([DAY, "hour_ending"])
        self._y_norm = _Normaliser.fit(frame["_y"].to_numpy()[:, None])
        residuals = []
        self._fits = {}
        for hour in range(1, 25):
            rows = frame[frame["hour_ending"] == hour]
            y = self._y_norm.forward(rows["_y"].to_numpy()[:, None])[:, 0]
            exog = self._exog(rows, hour, fit=True)
            model = _SARIMAX(
                y, exog=exog, order=self.order, seasonal_order=self.seasonal_order, trend="c"
            )
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", ConvergenceWarning)
                warnings.simplefilter("ignore", UserWarning)
                warnings.simplefilter("ignore", RuntimeWarning)
                result = model.fit(disp=False, maxiter=self.maxiter)
            self._fits[hour] = result
            fitted = self._y_norm.inverse(np.asarray(result.fittedvalues)[:, None])[:, 0]
            residuals.append(rows["_y"].to_numpy()[-56:] - fitted[-56:])
        self.in_sample_residuals = EmpiricalResiduals(np.concatenate(residuals))

    def predict_quantiles(
        self,
        features: pd.DataFrame,
        *,
        as_of: datetime,
        target_day: date,
        quantiles: Sequence[float] = QUANTILES,
    ) -> pd.DataFrame:
        if self._y_norm is None:
            raise RuntimeError("fit() first")
        rows = features.sort_values("hour_ending")
        point = np.empty(len(rows))
        for i, (_, row) in enumerate(rows.iterrows()):
            hour = int(row["hour_ending"])
            exog = self._exog(rows.iloc[[i]], hour, fit=False)
            z = float(np.asarray(self._fits[hour].forecast(steps=1, exog=exog))[0])  # type: ignore[attr-defined]
            point[i] = self._y_norm.inverse(np.array([[z]]))[0, 0]
        residuals = self.in_sample_residuals or EmpiricalResiduals(point[:0])
        return to_long(rows["ts_utc"], residuals.apply(point, quantiles), quantiles)
