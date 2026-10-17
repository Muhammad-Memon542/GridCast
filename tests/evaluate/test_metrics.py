"""Metrics against hand-computed values (M3-T2)."""

from __future__ import annotations

import numpy as np
import pytest

from gridcast.evaluate.metrics import coverage, crps, median_index, pinball, pit, score

QUANTILES = (0.01, *[round(i / 20, 2) for i in range(1, 20)], 0.99)


def test_pinball_by_hand() -> None:
    assert pinball(np.array([10.0]), np.array([8.0]), 0.9)[0] == pytest.approx(1.8)
    assert pinball(np.array([10.0]), np.array([12.0]), 0.9)[0] == pytest.approx(0.2)
    assert pinball(np.array([10.0]), np.array([10.0]), 0.5)[0] == 0.0


def test_crps_is_mean_pinball() -> None:
    # tau 0.1: diff 2 -> 0.2; tau 0.5: 0; tau 0.9: diff -2 -> 0.2. Mean = 0.4 / 3.
    value = crps(np.array([10.0]), np.array([[8.0, 10.0, 12.0]]), [0.1, 0.5, 0.9])
    assert value[0] == pytest.approx(0.4 / 3)


def test_coverage_and_pit_by_hand() -> None:
    assert coverage(
        np.array([1.0, 5.0, 9.0]), np.array([0.0] * 3), np.array([6.0] * 3)
    ) == pytest.approx(2 / 3)
    q = np.array([[8.0, 10.0, 12.0]] * 3)
    values = pit(np.array([11.0, 5.0, 20.0]), q, [0.1, 0.5, 0.9])
    assert values.tolist() == pytest.approx([0.7, 0.1, 0.9])  # interpolated; clipped at the ends


def test_score_on_a_constructed_case() -> None:
    y = np.array([50.0, 60.0, 70.0, 300.0])
    med = np.array([52.0, 58.0, 70.0, 200.0])
    offsets = np.linspace(-20, 20, len(QUANTILES))
    q = med[:, None] + offsets[None, :]
    s = score(
        y, q, QUANTILES, naive_weekly=np.array([40.0, 70.0, 90.0, 100.0]), spike_threshold=150.0
    )
    assert s.n == 4
    assert s.mae == pytest.approx((2 + 2 + 0 + 100) / 4)
    assert s.rmse == pytest.approx(np.sqrt((4 + 4 + 0 + 10_000) / 4))
    assert s.rmae == pytest.approx(26 / ((10 + 10 + 20 + 200) / 4))
    # 90% band is med +/- 18 (levels 0.05/0.95 sit at offsets -18/+18): rows 1-3 covered.
    assert s.coverage_90 == pytest.approx(0.75)
    assert s.width_90 == pytest.approx(36.0)
    assert s.width_80 == pytest.approx(32.0)
    assert s.spike_mae == pytest.approx(100.0)  # top 5% of 4 rows = the 300 $/MWh hour
    assert (s.spike_precision, s.spike_recall) == (1.0, 1.0)
    assert set(s.as_dict()) >= {"crps", "mae", "coverage_90"}


def test_shape_and_grid_errors() -> None:
    with pytest.raises(ValueError, match="shape"):
        crps(np.array([1.0, 2.0]), np.array([[1.0, 2.0]]), [0.4, 0.6])
    with pytest.raises(ValueError, match="median"):
        median_index([0.1, 0.9])


def test_spike_metrics_undefined_without_spikes() -> None:
    y = np.array([10.0, 20.0])
    q = np.repeat(y[:, None], len(QUANTILES), axis=1)
    s = score(y, q, QUANTILES, naive_weekly=y + 1, spike_threshold=1000.0)
    assert np.isnan(s.spike_precision)
    assert np.isnan(s.spike_recall)
