"""Tier 1: LEAR — LASSO-estimated autoregressive model (Lago et al., 2021).

For each hour h of the target day, a LassoLarsIC (AIC) regression on asinh-normalised
inputs: the 24 hourly prices of each lag day, the 24 hourly values of each exogenous
forecast or lag, and day-of-week dummies. The point forecast averages the models fitted on
each calibration window. Inputs that are missing at prediction time are set to the
training median (0 after normalisation).
"""

from __future__ import annotations

import warnings
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import cast

import numpy as np
import pandas as pd
from sklearn.exceptions import ConvergenceWarning
from sklearn.linear_model import LassoLarsIC

from gridcast.backtest.dataset import DAY
from gridcast.models.base import QUANTILES, EmpiricalResiduals, to_long

MAD_SCALE = 1.4826  # MAD -> standard deviation under normality


@dataclass(frozen=True)
class _Normaliser:
    """asinh((x - median) / (1.4826 * MAD)), per column, fitted on the training window."""

    median: np.ndarray
    scale: np.ndarray

    @classmethod
    def fit(cls, x: np.ndarray) -> _Normaliser:
        median = np.nanmedian(x, axis=0)
        mad = np.nanmedian(np.abs(x - median), axis=0) * MAD_SCALE
        return cls(
            median=np.nan_to_num(median), scale=np.where(np.isfinite(mad) & (mad > 1e-9), mad, 1.0)
        )

    def forward(self, x: np.ndarray) -> np.ndarray:
        z: np.ndarray = np.arcsinh((x - self.median) / self.scale)
        return z

    def inverse(self, z: np.ndarray) -> np.ndarray:
        x: np.ndarray = np.sinh(z) * self.scale + self.median
        return x


def daily_design(features: pd.DataFrame, columns: Sequence[str]) -> pd.DataFrame:
    """One row per delivery day: ``<col>_h01 .. <col>_h24`` for each column, plus dow dummies."""
    wide = features.pivot(index=DAY, columns="hour_ending", values=list(columns))
    pairs = cast(list[tuple[str, int]], list(wide.columns))
    wide.columns = [f"{col}_h{int(h):02d}" for col, h in pairs]
    dow = pd.get_dummies(
        pd.Series([d.weekday() for d in wide.index], index=wide.index), prefix="dow"
    )
    dow = dow.reindex(columns=[f"dow_{i}" for i in range(7)], fill_value=False).astype("float64")
    return pd.concat([wide.astype("float64"), dow], axis=1)


class LEAR:
    point_only = True

    def __init__(
        self,
        calibration_windows_days: Sequence[int] = (56, 84, 182, 364),
        price_lags_days: Sequence[int] = (1, 2, 3, 7),
        exogenous: Sequence[str] = (
            "wx_temperature_2m",
            "wx_wind_speed_100m",
            "wx_shortwave_radiation",
            "demand_lag2",
            "rt_lag2",
        ),
    ) -> None:
        self.name = "lear"
        self.windows = tuple(sorted(calibration_windows_days))
        self.columns = [f"da_lag{lag}" for lag in price_lags_days] + list(exogenous)
        self._models: list[tuple[_Normaliser, _Normaliser, list[LassoLarsIC | float]]] = []
        self._design_columns: list[str] = []
        self.in_sample_residuals: EmpiricalResiduals | None = None

    # ------------------------------------------------------------------ fit
    def fit(self, features: pd.DataFrame, target: pd.Series, *, as_of: datetime) -> None:
        design = daily_design(features, self.columns)
        self._design_columns = list(design.columns)
        y = features.assign(_y=target.to_numpy()).pivot(
            index=DAY, columns="hour_ending", values="_y"
        )
        y = y.reindex(index=design.index, columns=range(1, 25))
        complete = design.notna().all(axis=1) & y.notna().all(axis=1)
        design, y = design[complete], y[complete]
        last_day = max(design.index)
        self._models = []
        for window in self.windows:
            keep = [d >= last_day - timedelta(days=window - 1) for d in design.index]
            x_w, y_w = design[keep].to_numpy(), y[keep].to_numpy()
            if len(x_w) < 14:
                continue
            x_norm, y_norm = _Normaliser.fit(x_w), _Normaliser.fit(y_w)
            xz, yz = x_norm.forward(x_w), y_norm.forward(y_w)
            hourly: list[LassoLarsIC | float] = []
            for h in range(24):
                if np.allclose(yz[:, h], yz[0, h]):
                    hourly.append(float(yz[0, h]))  # constant target: nothing to learn
                    continue
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore", ConvergenceWarning)
                    # noise_variance = var(y) reproduces the scikit-learn < 1.1 behaviour that
                    # epftoolbox's LEAR relied on; newer versions refuse n < p without it.
                    lasso = LassoLarsIC(
                        criterion="aic", max_iter=500, noise_variance=float(np.var(yz[:, h]))
                    )
                    hourly.append(lasso.fit(xz, yz[:, h]))
            self._models.append((x_norm, y_norm, hourly))
        if not self._models:
            raise ValueError("LEAR needs at least 14 complete training days")
        recent = design.index >= last_day - timedelta(days=55)
        fitted = self._predict_matrix(design[recent])
        self.in_sample_residuals = EmpiricalResiduals((y[recent].to_numpy() - fitted).ravel())

    def _predict_matrix(self, design: pd.DataFrame) -> np.ndarray:
        x = design.reindex(columns=self._design_columns).to_numpy(dtype="float64")
        preds = []
        for x_norm, y_norm, hourly in self._models:
            xz = np.nan_to_num(x_norm.forward(x), nan=0.0)
            zhat = np.column_stack(
                [np.full(len(xz), m) if isinstance(m, float) else m.predict(xz) for m in hourly]
            )
            preds.append(y_norm.inverse(zhat))
        return np.mean(preds, axis=0)

    # -------------------------------------------------------------- predict
    def predict_quantiles(
        self,
        features: pd.DataFrame,
        *,
        as_of: datetime,
        target_day: date,
        quantiles: Sequence[float] = QUANTILES,
    ) -> pd.DataFrame:
        design = daily_design(features, self.columns)
        point = self._predict_matrix(design)[0]
        ordered = features.sort_values("hour_ending")
        residuals = self.in_sample_residuals or EmpiricalResiduals(point[:0])
        return to_long(ordered["ts_utc"], residuals.apply(point, quantiles), quantiles)
