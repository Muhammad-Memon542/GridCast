"""Model interface (plan §7.6) and shared helpers."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime
from typing import Protocol, runtime_checkable

import numpy as np
import pandas as pd

QUANTILES: tuple[float, ...] = (0.01, *[round(i / 20, 2) for i in range(1, 20)], 0.99)


@runtime_checkable
class Forecaster(Protocol):
    name: str

    def fit(self, features: pd.DataFrame, target: pd.Series, *, as_of: datetime) -> None: ...

    def predict_quantiles(
        self,
        features: pd.DataFrame,
        *,
        as_of: datetime,
        target_day: date,
        quantiles: Sequence[float] = QUANTILES,
    ) -> pd.DataFrame:
        """Long frame ``ts_utc, quantile, value``: non-crossing, 24 hours of ``target_day``."""
        ...


def to_long(ts_utc: pd.Series, values: np.ndarray, quantiles: Sequence[float]) -> pd.DataFrame:
    """(n, k) quantile matrix -> long frame; rows are sorted so quantiles never cross."""
    q = np.sort(np.asarray(values, dtype="float64"), axis=1)
    if q.shape != (len(ts_utc), len(quantiles)):
        raise ValueError(f"expected shape {(len(ts_utc), len(quantiles))}, got {q.shape}")
    return pd.DataFrame(
        {
            "ts_utc": np.repeat(ts_utc.to_numpy(), len(quantiles)),
            "quantile": np.tile(np.asarray(quantiles, dtype="float64"), len(ts_utc)),
            "value": q.ravel(),
        }
    )


def to_wide(long: pd.DataFrame) -> pd.DataFrame:
    """Long forecasts -> one row per ts_utc, one column per quantile (sorted)."""
    wide = long.pivot(index="ts_utc", columns="quantile", values="value")
    return wide.sort_index(axis=1)


class EmpiricalResiduals:
    """Quantiles of past residuals (actual - point), added to a point forecast."""

    def __init__(self, residuals: np.ndarray) -> None:
        r = np.asarray(residuals, dtype="float64")
        self.residuals = r[~np.isnan(r)]

    def apply(self, point: np.ndarray, quantiles: Sequence[float]) -> np.ndarray:
        if self.residuals.size == 0:
            return np.repeat(np.asarray(point, dtype="float64")[:, None], len(quantiles), axis=1)
        offsets = np.quantile(self.residuals, np.asarray(quantiles, dtype="float64"))
        values: np.ndarray = np.asarray(point, dtype="float64")[:, None] + offsets[None, :]
        return values
