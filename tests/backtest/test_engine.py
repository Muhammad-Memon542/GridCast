"""Backtester and model tests on synthetic data (M3-T1, M3-T4..T6)."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import ClassVar

import numpy as np
import pandas as pd
import pytest

from gridcast.backtest.dataset import day_range, targets_known_at
from gridcast.backtest.engine import BacktestConfig, run_backtest
from gridcast.backtest.seal import SealedWindowError, WindowSeal
from gridcast.models.base import QUANTILES, EmpiricalResiduals, Forecaster, to_long, to_wide
from gridcast.models.lear import LEAR
from gridcast.models.lgbm import LGBMQuantile
from gridcast.models.naive import NaiveLag
from gridcast.timeutil.ieso import TS_DTYPE, he_to_ts_utc, issue_time_utc

FIRST, LAST = date(2025, 5, 3), date(2025, 7, 20)
SEAL = WindowSeal(test_start=date(2025, 11, 1))


def synthetic(seed: int = 0) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Features with lags derived from a daily-periodic price, and a DA OZP table."""
    rng = np.random.default_rng(seed)
    days = day_range(FIRST - timedelta(days=7), LAST)
    frame = pd.DataFrame(
        [(d, h) for d in days for h in range(1, 25)], columns=["delivery_date", "hour_ending"]
    )
    frame["hour_ending"] = frame["hour_ending"].astype("int8")
    frame["ts_utc"] = he_to_ts_utc(frame["delivery_date"], frame["hour_ending"])
    shape = 40 + 20 * np.sin(np.pi * (frame["hour_ending"] - 6) / 12)
    frame["price"] = shape + rng.normal(0, 3, len(frame))
    price = frame.set_index("ts_utc")["price"]
    da = pd.DataFrame(
        {
            "ts_utc": price.index,
            "zonal_price": price.to_numpy(),
            "source": "gridstatus",
            "version": pd.array([None] * len(price), dtype="Int16"),
            # published 15:30 EPT the day before delivery (approximated as 20:30 UTC)
            "available_at_utc": (price.index.floor("D") - pd.Timedelta(hours=3, minutes=30)).astype(
                TS_DTYPE
            ),
        }
    )
    feats = frame[frame["delivery_date"] >= FIRST].copy()
    for lag in (1, 2, 3, 7):
        feats[f"da_lag{lag}"] = (feats["ts_utc"] - pd.Timedelta(days=lag)).map(price)
    for col in (
        "wx_temperature_2m",
        "wx_wind_speed_100m",
        "wx_shortwave_radiation",
        "demand_lag2",
        "rt_lag2",
    ):
        feats[col] = rng.normal(0, 1, len(feats))
    feats["dow"] = feats["delivery_date"].map(lambda d: d.weekday()).astype("int8")
    feats["issue_time_utc"] = (
        feats["delivery_date"]
        .map(lambda d: pd.Timestamp(issue_time_utc(d, time(9))))
        .astype(TS_DTYPE)
    )
    return feats.drop(columns="price").reset_index(drop=True), da


class Spy:
    """Records what the engine trains on; predicts the d-1 price."""

    point_only = False
    name = "spy"
    seen: ClassVar[list[tuple[datetime, pd.DataFrame, pd.Series]]] = []

    def fit(self, features: pd.DataFrame, target: pd.Series, *, as_of: datetime) -> None:
        Spy.seen.append((as_of, features, target))

    def predict_quantiles(self, features, *, as_of, target_day, quantiles=QUANTILES):  # type: ignore[no-untyped-def]
        point = features["da_lag1"].to_numpy()
        return to_long(
            features["ts_utc"],
            point[:, None] + np.linspace(-5, 5, len(quantiles))[None, :],
            quantiles,
        )


def _config(**kw: object) -> BacktestConfig:
    return BacktestConfig(eval_start=date(2025, 6, 10), eval_end=date(2025, 6, 30), **kw)  # type: ignore[arg-type]


def test_training_only_sees_published_targets_and_past_days() -> None:
    feats, da = synthetic()
    Spy.seen = []
    run_backtest(Spy, feats, da, _config(refit_every_days=7), seal=SEAL)
    assert len(Spy.seen) == 3  # 21 days, weekly refit
    for as_of, x, y in Spy.seen:
        avail = x["ts_utc"].map(da.set_index("ts_utc")["available_at_utc"])
        assert (avail <= pd.Timestamp(as_of)).all()
        assert (
            x["delivery_date"].max()
            < (pd.Timestamp(as_of).tz_convert("America/Toronto") + pd.Timedelta(days=1)).date()
        )
        assert y.notna().all()


def test_seal_blocks_test_window_and_opens_only_with_committed_config(tmp_path: Path) -> None:
    feats, da = synthetic()
    sealed = WindowSeal(test_start=date(2025, 6, 20))
    with pytest.raises(SealedWindowError, match="sealed"):
        run_backtest(Spy, feats, da, _config(), seal=sealed)
    with pytest.raises(SealedWindowError, match="committed"):
        WindowSeal.open_with(date(2025, 6, 20), tmp_path / "not_there.yaml")
    sealed.check(date(2025, 6, 19))  # before the seal: fine


def test_naive_with_rolling_residual_quantiles() -> None:
    feats, da = synthetic()
    result = run_backtest(lambda: NaiveLag(1), feats, da, _config(min_residual_days=5), seal=SEAL)
    wide = to_wide(result.forecasts.rename(columns={"target_ts_utc": "ts_utc"}))
    assert wide.shape == (21 * 24, 21)
    assert (np.diff(wide.to_numpy(), axis=1) >= 0).all()
    assert set(result.forecasts.columns) >= {
        "run_id",
        "model_name",
        "issued_at_utc",
        "target_ts_utc",
        "quantile",
        "value",
        "config_hash",
        "data_snapshot_id",
        "git_sha",
    }


def test_deterministic_and_resumable(tmp_path: Path) -> None:
    feats, da = synthetic()
    first = run_backtest(
        lambda: LGBMQuantile(n_estimators=20, num_threads=1),
        feats,
        da,
        _config(),
        seal=SEAL,
        out_dir=tmp_path / "run",
    )
    again = run_backtest(
        lambda: LGBMQuantile(n_estimators=20, num_threads=1), feats, da, _config(), seal=SEAL
    )
    cols = ["target_ts_utc", "quantile", "value"]
    pd.testing.assert_frame_equal(first.forecasts[cols], again.forecasts[cols])
    (tmp_path / "run" / "day=2025-06-30.parquet").unlink()
    resumed = run_backtest(
        lambda: LGBMQuantile(n_estimators=20, num_threads=1),
        feats,
        da,
        _config(),
        seal=SEAL,
        out_dir=tmp_path / "run",
    )
    assert resumed.fits == 1  # only the deleted day was recomputed
    assert len(list((tmp_path / "run").glob("day=*.parquet"))) == 21
    assert (tmp_path / "run" / "run.json").exists()


def test_lear_learns_the_daily_shape() -> None:
    feats, da = synthetic()
    result = run_backtest(LEAR, feats, da, _config(refit_every_days=7), seal=SEAL)
    wide = to_wide(result.forecasts.rename(columns={"target_ts_utc": "ts_utc"}))
    actual = wide.index.to_series().map(da.set_index("ts_utc")["zonal_price"])
    mae = float(np.mean(np.abs(actual.to_numpy() - wide[0.5].to_numpy())))
    assert mae < 4.0  # noise sd is 3: close to the floor


def test_engine_rejects_bad_forecasts() -> None:
    feats, da = synthetic()

    class Crossing(Spy):
        def predict_quantiles(self, features, *, as_of, target_day, quantiles=QUANTILES):  # type: ignore[no-untyped-def]
            long = super().predict_quantiles(
                features, as_of=as_of, target_day=target_day, quantiles=quantiles
            )
            return long.iloc[:-1]  # drop one value

    with pytest.raises(ValueError, match=r"hours|quantile|non-finite"):
        run_backtest(Crossing, feats, da, _config(), seal=SEAL)


def test_helpers() -> None:
    assert isinstance(NaiveLag(1), Forecaster)
    with pytest.raises(ValueError, match="shape"):
        to_long(pd.Series([1, 2]), np.zeros((2, 3)), QUANTILES)
    assert EmpiricalResiduals(np.array([np.nan])).apply(np.array([1.0]), [0.5]).tolist() == [[1.0]]
    feats, da = synthetic()
    issue = pd.Timestamp(feats["issue_time_utc"].iloc[0])
    known = targets_known_at(da, issue)
    assert (da.set_index("ts_utc").loc[known.index, "available_at_utc"] <= issue).all()


def test_rolling_cqr_repairs_an_overconfident_model() -> None:
    from gridcast.models.conformal import RollingCQR

    feats, da = synthetic(seed=5)

    class Overconfident(Spy):
        def predict_quantiles(self, features, *, as_of, target_day, quantiles=QUANTILES):  # type: ignore[no-untyped-def]
            point = features["da_lag1"].to_numpy()  # error sd ~4.2, intervals far too narrow
            spread = np.linspace(-0.5, 0.5, len(quantiles))
            return to_long(features["ts_utc"], point[:, None] + spread[None, :], quantiles)

    cfg = BacktestConfig(eval_start=date(2025, 6, 1), eval_end=date(2025, 7, 20))
    raw = run_backtest(Overconfident, feats, da, cfg, seal=SEAL)
    calibrated = run_backtest(
        Overconfident, feats, da, cfg, seal=SEAL, calibrator=RollingCQR(window_days=28, min_days=7)
    )

    def coverage90(result) -> float:  # type: ignore[no-untyped-def]
        wide = to_wide(result.forecasts.rename(columns={"target_ts_utc": "ts_utc"}))
        wide = wide[
            wide.index >= pd.Timestamp("2025-06-15", tz="UTC")
        ]  # after the calibration warm-up
        y = wide.index.to_series().map(da.set_index("ts_utc")["zonal_price"])
        return float(((y >= wide[0.05]) & (y <= wide[0.95])).mean())

    assert coverage90(raw) < 0.3
    assert 0.85 <= coverage90(calibrated) <= 0.96
    medians = calibrated.forecasts[calibrated.forecasts["quantile"] == 0.5]
    assert np.allclose(medians["value"], medians["value_raw"])  # CQR never moves the median
