"""Forecast metrics (pre-registration §5, M3-T2).

Forecasts are wide arrays ``Q`` of shape (n, k) for k quantile levels ``taus`` (sorted),
and outcomes ``y`` of shape (n,). The point forecast is the 0.5 quantile.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict, dataclass

import numpy as np
from numpy.typing import NDArray

Array = NDArray[np.float64]


def _arrays(y: Sequence[float] | Array, q: Array) -> tuple[Array, Array]:
    y_arr = np.asarray(y, dtype="float64")
    q_arr = np.asarray(q, dtype="float64")
    if q_arr.ndim != 2 or q_arr.shape[0] != y_arr.shape[0]:
        raise ValueError(f"expected Q of shape (n, k) with n={y_arr.shape[0]}, got {q_arr.shape}")
    return y_arr, q_arr


def pinball(y: Array, q: Array, tau: float) -> Array:
    """Pinball (quantile) loss per observation for one level ``tau``."""
    diff = np.asarray(y, dtype="float64") - np.asarray(q, dtype="float64")
    loss: Array = np.maximum(tau * diff, (tau - 1.0) * diff)
    return loss


def pinball_matrix(y: Array, q: Array, taus: Sequence[float]) -> Array:
    """(n, k) pinball losses."""
    y_arr, q_arr = _arrays(y, q)
    return np.column_stack([pinball(y_arr, q_arr[:, j], t) for j, t in enumerate(taus)])


def crps(y: Array, q: Array, taus: Sequence[float]) -> Array:
    """CRPS per observation, approximated by the average pinball loss over the levels.

    This is the pre-registered approximation (§5). It omits the factor 2 and the uneven
    level spacing of the exact integral, which does not change model rankings on a
    common grid.
    """
    return pinball_matrix(y, q, taus).mean(axis=1)


def median_index(taus: Sequence[float]) -> int:
    for j, t in enumerate(taus):
        if abs(t - 0.5) < 1e-9:
            return j
    raise ValueError("quantile grid has no median")


def coverage(y: Array, lo: Array, hi: Array) -> float:
    y_arr = np.asarray(y, dtype="float64")
    return float(np.mean((y_arr >= lo) & (y_arr <= hi)))


def pit(y: Array, q: Array, taus: Sequence[float]) -> Array:
    """Probability integral transform via linear interpolation of the quantile function.

    Outcomes below the lowest (above the highest) quantile map to the lowest (highest)
    level, so the PIT lives in [taus[0], taus[-1]].
    """
    y_arr, q_arr = _arrays(y, q)
    levels = np.asarray(taus, dtype="float64")
    return np.array([np.interp(v, row, levels) for v, row in zip(y_arr, q_arr, strict=True)])


@dataclass(frozen=True)
class Scores:
    n: int
    crps: float
    mae: float
    rmse: float
    rmae: float
    coverage_80: float
    coverage_90: float
    width_80: float
    width_90: float
    spike_mae: float
    spike_precision: float
    spike_recall: float

    def as_dict(self) -> dict[str, float]:
        return {k: float(v) for k, v in asdict(self).items()}


def score(
    y: Array,
    q: Array,
    taus: Sequence[float],
    *,
    naive_weekly: Array,
    spike_threshold: float,
) -> Scores:
    """All pre-registered metrics on one evaluation set.

    ``naive_weekly`` is the p(d-7, h) forecast for the same rows (denominator of rMAE).
    ``spike_threshold`` is the validation-window P95 of actual prices (spike definition).
    Spike-hour MAE uses the top 5% of hours by actual price in this set.
    """
    y_arr, q_arr = _arrays(y, q)
    levels = list(taus)
    med = q_arr[:, median_index(levels)]
    err = y_arr - med
    mae = float(np.mean(np.abs(err)))
    naive_mae = float(np.mean(np.abs(y_arr - np.asarray(naive_weekly, dtype="float64"))))

    def band(lo_tau: float, hi_tau: float) -> tuple[float, float]:
        lo, hi = q_arr[:, levels.index(lo_tau)], q_arr[:, levels.index(hi_tau)]
        return coverage(y_arr, lo, hi), float(np.mean(hi - lo))

    cov80, width80 = band(0.10, 0.90)
    cov90, width90 = band(0.05, 0.95)
    top = y_arr >= np.quantile(y_arr, 0.95)
    actual_spike = y_arr > spike_threshold
    predicted_spike = med > spike_threshold
    tp = int(np.sum(actual_spike & predicted_spike))
    precision = tp / int(np.sum(predicted_spike)) if predicted_spike.any() else float("nan")
    recall = tp / int(np.sum(actual_spike)) if actual_spike.any() else float("nan")
    return Scores(
        n=int(y_arr.shape[0]),
        crps=float(np.mean(crps(y_arr, q_arr, levels))),
        mae=mae,
        rmse=float(np.sqrt(np.mean(err**2))),
        rmae=mae / naive_mae if naive_mae > 0 else float("nan"),
        coverage_80=cov80,
        coverage_90=cov90,
        width_80=width80,
        width_90=width90,
        spike_mae=float(np.mean(np.abs(err[top]))),
        spike_precision=precision,
        spike_recall=recall,
    )
