"""RQ1 tests on synthetic series with known breaks (M2-T3)."""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from gridcast.evaluate.regime import (
    chow_test,
    comparable_series,
    daily_stats,
    detect_breaks,
    hill,
    ks_test,
    monthly_spike_counts,
    rolling_volatility,
    shift_test_hac,
    tail_index,
)
from gridcast.timeutil.ieso import TS_DTYPE, he_to_ts_utc

BREAK = date(2025, 5, 1)


def daily_series(shift: float, seed: int = 0, n: int = 400) -> pd.Series:
    rng = np.random.default_rng(seed)
    days = [BREAK - timedelta(days=n // 2) + timedelta(days=i) for i in range(n)]
    values = rng.normal(40, 5, n) + np.array([shift if d >= BREAK else 0.0 for d in days])
    return pd.Series(values, index=days)


def test_chow_and_hac_detect_a_real_shift_and_not_a_null() -> None:
    shifted, flat = daily_series(10.0), daily_series(0.0, seed=1)
    assert chow_test(shifted, BREAK).p_value < 1e-10
    assert shift_test_hac(shifted, BREAK).p_value < 1e-10
    assert chow_test(flat, BREAK).p_value > 0.01
    assert shift_test_hac(flat, BREAK).p_value > 0.01
    # F for one restriction is the square of the classic t-statistic.
    assert chow_test(shifted, BREAK).statistic > 100
    with pytest.raises(ValueError, match="inside"):
        chow_test(shifted, date(2030, 1, 1))


def test_detect_breaks_finds_the_shift_date() -> None:
    found = detect_breaks(daily_series(10.0))
    assert len(found) == 1
    assert abs((found[0] - BREAK).days) <= 3
    assert detect_breaks(daily_series(0.0, seed=2)) == []


def test_ks_test() -> None:
    rng = np.random.default_rng(3)
    a, b = pd.Series(rng.normal(0, 1, 500)), pd.Series(rng.normal(1, 1, 500))
    assert ks_test(a, b).p_value < 1e-10
    assert ks_test(a, pd.Series(rng.normal(0, 1, 500))).p_value > 0.01


def test_hill_recovers_a_pareto_tail() -> None:
    rng = np.random.default_rng(4)
    pareto = (rng.pareto(3.0, 20_000) + 1.0) * 10  # tail index alpha = 3
    assert hill(pareto, 1000) == pytest.approx(3.0, rel=0.1)
    ti = tail_index(pd.Series(pareto), top_fraction=0.05, reps=200)
    assert ti.ci_low < 3.0 < ti.ci_high
    assert ti.k == 1000
    with pytest.raises(ValueError, match="k"):
        hill(np.array([1.0, 2.0]), 5)


def test_comparable_series_switches_at_go_live() -> None:
    days = [date(2025, 4, 30), date(2025, 5, 1)]
    hours = pd.DataFrame(
        [(d, h) for d in days for h in range(1, 25)], columns=["delivery_date", "hour_ending"]
    )
    ts = he_to_ts_utc(hours["delivery_date"], hours["hour_ending"])
    hoep = hours.assign(ts_utc=ts, hoep=30.0)
    rt = hours.assign(ts_utc=ts, lmp_mean=60.0, n_intervals=np.int8(12), source="gridstatus")
    rt.loc[rt.index[-1], "n_intervals"] = np.int8(11)  # incomplete hour is dropped
    series = comparable_series(hoep, rt, BREAK)
    assert series.groupby("regime").size().to_dict() == {"new": 23, "old": 24}
    assert series[series.regime == "old"].price.eq(30.0).all()
    assert str(series.ts_utc.dtype) == str(TS_DTYPE)
    stats_ = daily_stats(series)
    assert list(stats_["hours"]) == [24, 23]
    assert stats_.loc[date(2025, 5, 1), "mean"] == 60.0


def test_spike_counts_and_volatility() -> None:
    ts = pd.date_range("2025-01-01 05:00", periods=24 * 40, freq="h", tz="UTC")
    hourly = pd.DataFrame(
        {"ts_utc": ts, "price": np.where(np.arange(len(ts)) % 100 == 0, 500.0, 40.0)}
    )
    counts = monthly_spike_counts(hourly, threshold=100.0)
    assert counts.sum() == 10
    vol = rolling_volatility(daily_stats(hourly)["mean"], window=5)
    assert vol.isna().sum() == 4
