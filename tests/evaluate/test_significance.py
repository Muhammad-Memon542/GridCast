"""Significance tests on synthetic data with known answers (M3-T3)."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from gridcast.evaluate.significance import daily_loss, dm_test, gw_test, holm, model_confidence_set


def test_dm_by_hand() -> None:
    # d = a - b = [1, 2, 3, 4]: mean 2.5, var (ddof 0) 1.25, stat = 2.5 / sqrt(1.25 / 4).
    result = dm_test([2, 3, 4, 5], [1, 1, 1, 1])
    assert result.statistic == pytest.approx(2.5 / np.sqrt(1.25 / 4))
    assert result.p_value == pytest.approx(2 * (1 - 0.9999961), abs=1e-6)
    one_sided = dm_test([2, 3, 4, 5], [1, 1, 1, 1], alternative="b_better")
    assert one_sided.p_value == pytest.approx(result.p_value / 2)


def test_dm_symmetry_power_and_size() -> None:
    rng = np.random.default_rng(0)
    base = rng.gamma(2.0, 10.0, 400)
    better = base - 2.0 + rng.normal(0, 3, 400)
    assert dm_test(base, better).p_value < 1e-6
    assert dm_test(base, better).statistic == pytest.approx(-dm_test(better, base).statistic)
    # Same distribution: rejections at 5% happen about 5% of the time.
    rejections = np.mean(
        [dm_test(rng.normal(0, 1, 200), rng.normal(0, 1, 200)).p_value < 0.05 for _ in range(400)]
    )
    assert 0.02 < rejections < 0.09


def test_dm_degenerate_inputs() -> None:
    assert dm_test([1, 1, 1], [1, 1, 1]).p_value == 1.0
    assert dm_test([2, 2, 2], [1, 1, 1]).statistic == np.inf  # constant non-zero difference
    # NaN days are dropped pairwise: d = [1 - 0, 3 - 1] = [1, 2] -> 1.5 / sqrt(0.25 / 2).
    assert dm_test([1.0, np.nan, 3.0], [0.0, 5.0, 1.0]).statistic == pytest.approx(
        1.5 / np.sqrt(0.125)
    )
    with pytest.raises(ValueError, match="equal length"):
        dm_test([1, 2], [1])


def test_gw_matches_regression_form() -> None:
    rng = np.random.default_rng(1)
    a, b = rng.normal(5, 1, 300), rng.normal(5, 1, 300)
    d = a - b
    z = np.column_stack([np.ones(299), d[:-1]]) * d[1:, None]
    # n * uncentered R^2 of regressing a vector of ones on Z equals the GW statistic.
    ones = np.ones(299)
    beta, *_ = np.linalg.lstsq(z, ones, rcond=None)
    expected = float(ones @ z @ beta)
    result = gw_test(a, b)
    assert result.statistic == pytest.approx(expected)
    assert 0 <= result.p_value <= 1
    assert gw_test(a + 1.0, b).p_value < 1e-6  # a clearly, predictably worse
    assert np.isnan(gw_test([1, 2], [2, 1]).statistic)


def test_holm_textbook() -> None:
    # sorted 0.005, 0.01, 0.03, 0.04 times 4, 3, 2, 1 -> 0.02, 0.03, 0.06, 0.04 -> monotone.
    assert holm([0.01, 0.04, 0.03, 0.005]) == pytest.approx([0.03, 0.06, 0.06, 0.02])
    assert holm([0.9, 0.8]) == [1.0, 1.0]


def test_daily_loss_aggregation() -> None:
    hourly = pd.Series([1.0, 2.0, 3.0, 4.0])
    day = pd.Series(["d1", "d1", "d2", "d2"])
    assert daily_loss(hourly, day, "sum").tolist() == [3.0, 7.0]
    assert daily_loss(hourly, day, "mean").tolist() == [1.5, 3.5]


def test_mcs_excludes_a_clearly_worse_model() -> None:
    rng = np.random.default_rng(2)
    base = rng.gamma(2.0, 5.0, 250)
    losses = pd.DataFrame(
        {
            "good": base + rng.normal(0, 0.5, 250),
            "also_good": base + rng.normal(0, 0.5, 250),
            "bad": base + 3.0 + rng.normal(0, 0.5, 250),
        }
    )
    result = model_confidence_set(losses, reps=500)
    assert list(result.index) == ["good", "also_good", "bad"]
    assert result.loc["good", "in_mcs"]
    assert result.loc["also_good", "in_mcs"]
    assert not result.loc["bad", "in_mcs"]
