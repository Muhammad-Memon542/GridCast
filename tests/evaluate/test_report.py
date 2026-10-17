from __future__ import annotations

import numpy as np
import pandas as pd

from gridcast.evaluate.metrics import score
from gridcast.evaluate.report import comparison_markdown, daily_losses
from gridcast.models.base import QUANTILES, to_long


def _forecasts(y: pd.Series, noise: float, seed: int) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    point = y.to_numpy() + rng.normal(0, noise, len(y))
    spread = np.linspace(-2 * noise, 2 * noise, len(QUANTILES))
    long = to_long(y.index.to_series(), point[:, None] + spread[None, :], QUANTILES)
    return long.rename(columns={"ts_utc": "target_ts_utc"})


def test_daily_losses_and_markdown() -> None:
    ts = pd.date_range("2025-07-01 05:00", periods=24 * 30, freq="h", tz="UTC")
    y = pd.Series(50 + 10 * np.sin(np.arange(len(ts)) / 4), index=ts)
    fc = {"good": _forecasts(y, 1.0, 0), "bad": _forecasts(y, 8.0, 1)}
    losses = {k: daily_losses(v, y) for k, v in fc.items()}
    assert len(losses["good"]) == 30
    assert (losses["good"]["crps"] < losses["bad"]["crps"]).mean() > 0.9
    scores = {}
    for k, v in fc.items():
        wide = v.pivot(index="target_ts_utc", columns="quantile", values="value")
        scores[k] = score(
            y.to_numpy(),
            wide.to_numpy(),
            list(QUANTILES),
            naive_weekly=y.to_numpy() + 5,
            spike_threshold=1e9,
        )
    text = comparison_markdown(scores, losses, title="T", note="n", mcs_reps=200)
    assert text.index("`good`") < text.index("`bad`")  # sorted by CRPS
    assert "Diebold-Mariano, CRPS" in text
    assert "model confidence set" in text
    assert "| `good` | `bad` |" in text
