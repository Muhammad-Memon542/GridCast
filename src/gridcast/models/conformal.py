"""Conformal calibration of quantile forecasts (M3-T8).

Rolling conformalized quantile regression (CQR; Romano, Patterson and Candes, 2019). For
each symmetric pair of levels (tau, 1 - tau), with miscoverage alpha = 2 * tau, the
conformity score of a past forecast is E = max(q_tau - y, y - q_{1-tau}). Both quantiles
of the new forecast move outward (or inward, if E is negative) by the (1 - alpha)
empirical quantile of E over the last ``window_days`` of out-of-sample forecasts whose
outcomes were published by the issue time. The median is never changed.

Optionally the level used for each pair adapts online (adaptive conformal inference;
Gibbs and Candes, 2021): alpha_t+1 = alpha_t + gamma * (alpha - err_t).
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np
import pandas as pd

from gridcast.models.base import to_long, to_wide


class RollingCQR:
    def __init__(self, window_days: int = 56, min_days: int = 14, gamma: float = 0.0) -> None:
        self.window_days = window_days
        self.min_days = min_days
        self.gamma = gamma
        self._alpha: dict[float, float] = {}

    def adjust(
        self,
        forecast: pd.DataFrame,
        history: pd.DataFrame,
        truth: pd.Series,
        issue: pd.Timestamp,
        quantiles: Sequence[float],
    ) -> pd.DataFrame:
        """``forecast`` and ``history`` are long frames (ts_utc/target_ts_utc, quantile, value)."""
        levels = sorted(quantiles)
        wide = to_wide(forecast)
        if history.empty:
            return forecast
        past = history[history["target_ts_utc"] >= issue - pd.Timedelta(days=self.window_days)]
        past_wide = to_wide(past.rename(columns={"target_ts_utc": "ts_utc"}))
        y = past_wide.index.to_series().map(truth)
        keep = y.notna()
        past_wide, y = past_wide[keep], y[keep]
        if len(past_wide) < 24 * self.min_days:
            return forecast
        out = wide.copy()
        for tau in [t for t in levels if t < 0.5]:
            hi_tau = round(1.0 - tau, 10)
            if hi_tau not in wide.columns:
                continue
            nominal = 2.0 * tau
            alpha = self._alpha.get(tau, nominal)
            scores = np.maximum((past_wide[tau] - y).to_numpy(), (y - past_wide[hi_tau]).to_numpy())
            level = float(np.clip(1.0 - alpha, 0.0, 1.0))
            n = scores.size
            rank = min(np.ceil((n + 1) * level) / n, 1.0)  # finite-sample correction
            shift = float(np.quantile(scores, rank))
            out[tau] = wide[tau] - shift
            out[hi_tau] = wide[hi_tau] + shift
            if self.gamma > 0:
                last_day = past_wide.index.max().normalize()
                recent = past_wide.index >= last_day
                miss = float(
                    np.mean(
                        (y[recent] < past_wide.loc[recent, tau])
                        | (y[recent] > past_wide.loc[recent, hi_tau])
                    )
                )
                self._alpha[tau] = float(
                    np.clip(alpha + self.gamma * (nominal - miss), 1e-4, 0.999)
                )
        return to_long(out.index.to_series(), out[levels].to_numpy(), levels)
