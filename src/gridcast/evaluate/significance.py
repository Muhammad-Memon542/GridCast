"""Significance tests (pre-registration §6, M3-T3).

Losses are per forecast day. For the multivariate Diebold-Mariano test of Lago et al.
(2021), a day's loss is the norm of its 24 hourly errors (L1 for MAE) or the mean of its
24 hourly CRPS values. Lower loss is better.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

import numpy as np
import pandas as pd
from numpy.typing import ArrayLike
from scipy import stats


@dataclass(frozen=True)
class TestResult:
    statistic: float
    p_value: float


def _diff(loss_a: ArrayLike, loss_b: ArrayLike) -> np.ndarray:
    a = np.asarray(loss_a, dtype="float64")
    b = np.asarray(loss_b, dtype="float64")
    if a.shape != b.shape or a.ndim != 1:
        raise ValueError("losses must be 1-D arrays of equal length")
    mask = ~(np.isnan(a) | np.isnan(b))
    diff: np.ndarray = a[mask] - b[mask]
    return diff


def dm_test(
    loss_a: ArrayLike,
    loss_b: ArrayLike,
    alternative: Literal["two-sided", "b_better"] = "two-sided",
) -> TestResult:
    """Diebold-Mariano test on daily losses (one-step-ahead, no HAC term, as in epftoolbox).

    ``b_better`` tests H1: model B has lower expected loss than model A.
    """
    d = _diff(loss_a, loss_b)
    n = d.size
    var = float(np.var(d))  # ddof=0, as in Lago et al.'s implementation
    if n < 2 or var == 0.0:
        return TestResult(statistic=0.0 if np.allclose(d, 0) else float("inf"), p_value=1.0)
    stat = float(np.mean(d) / np.sqrt(var / n))
    if alternative == "b_better":
        p = float(1.0 - stats.norm.cdf(stat))
    else:
        p = float(2.0 * (1.0 - stats.norm.cdf(abs(stat))))
    return TestResult(statistic=stat, p_value=p)


def daily_loss(hourly_loss: pd.Series, day: pd.Series, how: Literal["sum", "mean"]) -> pd.Series:
    """Aggregate hourly losses into one loss per day (L1 norm = sum of absolute errors)."""
    grouped = hourly_loss.groupby(day.to_numpy())
    return grouped.sum() if how == "sum" else grouped.mean()


def gw_test(loss_a: ArrayLike, loss_b: ArrayLike) -> TestResult:
    """Giacomini-White conditional predictive ability test, one step ahead.

    Instruments h_{t-1} = (1, d_{t-1}); statistic n * Zbar' Omega^-1 Zbar ~ chi2(2) with
    Z_t = h_{t-1} d_t and Omega = mean(Z_t Z_t'). H0: equal conditional predictive ability.
    """
    d = _diff(loss_a, loss_b)
    if d.size < 3:
        return TestResult(statistic=float("nan"), p_value=float("nan"))
    instruments = np.column_stack([np.ones(d.size - 1), d[:-1]])
    z = instruments * d[1:, None]
    n = z.shape[0]
    zbar = z.mean(axis=0)
    omega = (z.T @ z) / n
    try:
        stat = float(n * zbar @ np.linalg.solve(omega, zbar))
    except np.linalg.LinAlgError:
        return TestResult(statistic=float("nan"), p_value=float("nan"))
    return TestResult(statistic=stat, p_value=float(1.0 - stats.chi2.cdf(stat, df=2)))


def holm(p_values: Sequence[float]) -> list[float]:
    """Holm step-down adjusted p-values (monotone, capped at 1)."""
    p = np.asarray(p_values, dtype="float64")
    order = np.argsort(p)
    m = p.size
    adjusted = np.empty(m)
    running = 0.0
    for rank, idx in enumerate(order):
        running = max(running, (m - rank) * p[idx])
        adjusted[idx] = min(running, 1.0)
    return adjusted.tolist()


def model_confidence_set(
    losses: pd.DataFrame,
    *,
    size: float = 0.10,
    reps: int = 10_000,
    block_size: int = 7,
    seed: int = 0,
) -> pd.DataFrame:
    """Hansen's MCS (pre-registered: "max" statistic, stationary bootstrap, 7-day blocks).

    ``losses`` has one column per model and one row per day. Returns each model's MCS
    p-value and whether it is in the (1 - size) confidence set.
    """
    from arch.bootstrap import MCS

    mcs = MCS(
        losses.dropna(),
        size=size,
        reps=reps,
        block_size=block_size,
        method="max",
        bootstrap="stationary",
        seed=seed,
    )
    mcs.compute()
    pvalues = mcs.pvalues.iloc[:, 0].rename("mcs_p_value")
    out = pvalues.to_frame()
    out["in_mcs"] = out.index.isin(mcs.included)
    return out.reindex(losses.columns)
