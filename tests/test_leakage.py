"""Leakage tests (plan §7.5). These must stay green on every PR.

1. Property: for random inputs and issue times, no feature uses a row published after
   the issue time, and every lag equals a brute-force as-of lookup.
2. Canary: a sentinel row published one second after issue never appears.
3. Weather lead offsets follow ADR-003.
4. Determinism: same inputs give identical features, whatever the row order.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta

import numpy as np
import pandas as pd
import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from gridcast.config import REPO_ROOT
from gridcast.features.builder import (
    FeatureConfig,
    FeatureInputs,
    build_features,
    choose_lead,
    latest_per_ts,
    load_feature_config,
)
from gridcast.quality.schemas import WEATHER_VARS
from gridcast.timeutil.ieso import TS_DTYPE, he_to_ts_utc, issue_time_utc

pytestmark = pytest.mark.leakage

TARGET = date(2026, 3, 9)  # day after a DST change, to keep the clocks honest
LATENCY = timedelta(hours=8)
CONFIG = FeatureConfig(
    price_lags_days=(1, 2, 7),
    rt_lags_days=(2,),
    demand_lags_days=(2,),
    weather_weights={"a": 1.0, "b": 3.0},
)


def _hours(first: date, days: int) -> pd.Series:
    dates = [first + timedelta(days=i) for i in range(days) for _ in range(24)]
    hes = [h for _ in range(days) for h in range(1, 25)]
    return he_to_ts_utc(pd.Series(dates), pd.Series(hes))


def synthetic_inputs(rng: np.random.Generator, issue: datetime) -> FeatureInputs:
    """Hourly history around TARGET with availability scattered on both sides of ``issue``."""
    ts = _hours(TARGET - timedelta(days=8), 9)
    n = len(ts)

    def offsets(size: int) -> pd.Series:
        hours = rng.uniform(-72, 72, size)
        return (pd.Timestamp(issue) + pd.to_timedelta(hours, unit="h")).astype(TS_DTYPE)

    da = pd.concat(
        [
            pd.DataFrame(
                {
                    "ts_utc": ts,
                    "zonal_price": rng.normal(50, 20, n),
                    "source": source,
                    "version": pd.array([version] * n, dtype="Int16"),
                    "available_at_utc": offsets(n),
                }
            )
            for source, version in [("ieso", 1), ("ieso", 2), ("gridstatus", None)]
        ],
        ignore_index=True,
    )
    rt = pd.DataFrame(
        {
            "ts_utc": ts,
            "lmp_mean": rng.normal(50, 20, n),
            "source": "ieso",
            "available_at_utc": offsets(n),
        }
    )
    demand = pd.DataFrame(
        {"ts_utc": ts, "ontario_demand": rng.uniform(1e4, 2e4, n), "available_at_utc": offsets(n)}
    )
    valid = _hours(TARGET, 1)
    weather = pd.concat(
        [
            pd.DataFrame(
                {
                    "point": point,
                    "valid_ts_utc": valid,
                    "lead_days": np.int8(lead),
                    **{v: rng.normal(10, 5, 24) for v in WEATHER_VARS},
                    "available_at_utc": (valid - pd.Timedelta(days=lead) + LATENCY).astype(
                        TS_DTYPE
                    ),
                }
            )
            for point in ("a", "b", "unweighted")
            for lead in (1, 2, 3)
        ],
        ignore_index=True,
    )
    return FeatureInputs(da_ozp=da, rt_hourly=rt, demand=demand, weather=weather)


def reference_lag(table: pd.DataFrame, value: str, ts: pd.Timestamp, issue: datetime) -> float:
    """Brute force: the preferred published row at ``ts``, or NaN."""
    rows = table[(table.ts_utc == ts) & (table.available_at_utc <= pd.Timestamp(issue))]
    if rows.empty:
        return float("nan")
    if "source" in rows:
        rows = rows.assign(
            r=rows.source.map({"ieso": 0, "gridstatus": 1}), v=rows.get("version", 0)
        )
        rows = rows.sort_values(["r", "v"], ascending=[True, False], na_position="last")
    return float(rows[value].iloc[0])


# 1. Property test -----------------------------------------------------------------


@settings(max_examples=40, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(seed=st.integers(0, 2**32 - 1), minutes_before=st.integers(0, 3 * 24 * 60))
def test_no_feature_uses_data_published_after_issue(seed: int, minutes_before: int) -> None:
    issue = datetime.combine(TARGET, time(5), tzinfo=UTC) - timedelta(minutes=minutes_before)
    inputs = synthetic_inputs(np.random.default_rng(seed), issue)
    features = build_features(inputs, TARGET, issue, CONFIG)

    used = features["_max_available_at"].dropna()
    assert (used <= pd.Timestamp(issue)).all()
    for _, row in features.iterrows():
        for lag in CONFIG.price_lags_days:
            expected = reference_lag(
                inputs.da_ozp, "zonal_price", row.ts_utc - pd.Timedelta(days=lag), issue
            )
            assert row[f"da_lag{lag}"] == pytest.approx(expected, nan_ok=True)
        expected = reference_lag(
            inputs.rt_hourly, "lmp_mean", row.ts_utc - pd.Timedelta(days=2), issue
        )
        assert row["rt_lag2"] == pytest.approx(expected, nan_ok=True)
        expected = reference_lag(
            inputs.demand, "ontario_demand", row.ts_utc - pd.Timedelta(days=2), issue
        )
        assert row["demand_lag2"] == pytest.approx(expected, nan_ok=True)


# 2. Canary test --------------------------------------------------------------------


def test_sentinel_published_one_second_late_never_appears() -> None:
    issue = issue_time_utc(TARGET, time(9))
    inputs = synthetic_inputs(np.random.default_rng(7), issue - timedelta(days=30))  # all published
    target_ts = he_to_ts_utc(pd.Series([TARGET]), pd.Series([5])).iloc[0]
    sentinel = 99_999.0
    late = pd.Timestamp(issue) + pd.Timedelta(seconds=1)
    canaries = FeatureInputs(
        da_ozp=pd.concat(
            [
                inputs.da_ozp,
                pd.DataFrame(
                    {
                        "ts_utc": [target_ts - pd.Timedelta(days=1)],
                        "zonal_price": [sentinel],
                        "source": ["ieso"],
                        "version": pd.array([9], dtype="Int16"),
                        "available_at_utc": [late],
                    }
                ),
            ],
            ignore_index=True,
        ),
        rt_hourly=pd.concat(
            [
                inputs.rt_hourly,
                pd.DataFrame(
                    {
                        "ts_utc": [target_ts - pd.Timedelta(days=2)],
                        "lmp_mean": [sentinel],
                        "source": ["ieso"],
                        "available_at_utc": [late],
                    }
                ),
            ],
            ignore_index=True,
        ).pipe(lambda f: f.drop_duplicates("ts_utc", keep="last")),
        demand=inputs.demand,
        weather=pd.concat(
            [
                inputs.weather.assign(available_at_utc=pd.Timestamp(issue) - pd.Timedelta(days=1)),
                inputs.weather.head(1).assign(
                    lead_days=np.int8(0), temperature_2m=sentinel, available_at_utc=late
                ),
            ],
            ignore_index=True,
        ),
    )
    features = build_features(canaries, TARGET, issue, CONFIG)
    assert not (features.select_dtypes("number") == sentinel).any().any()
    # The value that was published before the sentinel is still used.
    row = features[features.hour_ending == 5].iloc[0]
    assert row.da_lag1 == pytest.approx(
        reference_lag(inputs.da_ozp, "zonal_price", target_ts - pd.Timedelta(days=1), issue)
    )
    assert np.isnan(row.rt_lag2)  # the only RT row for that hour is the late sentinel


# 3. Weather lead-offset test ---------------------------------------------------------


@pytest.mark.parametrize(
    ("valid", "expected"),
    [
        (datetime(2026, 3, 9, 5, tzinfo=UTC), 1),  # 05:00 - 24h + 8h = 13:00 issue: allowed (<=)
        (datetime(2026, 3, 9, 6, tzinfo=UTC), 2),  # one hour later needs the 2-day-old run
        (datetime(2026, 3, 10, 4, tzinfo=UTC), 2),  # HE24 of D
        (datetime(2026, 3, 10, 6, tzinfo=UTC), None),  # beyond lead 2
    ],
)
def test_weather_lead_offset_rule(valid: datetime, expected: int | None) -> None:
    issue = issue_time_utc(TARGET, time(9))  # 2026-03-08 09:00 EDT = 13:00 UTC
    assert issue == datetime(2026, 3, 8, 13, tzinfo=UTC)
    assert choose_lead(valid, issue, (1, 2), LATENCY) == expected


def test_builder_weather_leads_match_the_rule_and_weights() -> None:
    issue = issue_time_utc(TARGET, time(9))
    inputs = synthetic_inputs(np.random.default_rng(3), issue)
    features = build_features(inputs, TARGET, issue, CONFIG)
    for _, row in features.iterrows():
        lead = choose_lead(row.ts_utc.to_pydatetime(), issue, (1, 2, 3), LATENCY)
        assert row.weather_lead_days == lead
        w = inputs.weather[
            (inputs.weather.valid_ts_utc == row.ts_utc) & (inputs.weather.lead_days == lead)
        ]
        a, b = (w[w.point == p].temperature_2m.iloc[0] for p in ("a", "b"))
        assert row.wx_temperature_2m == pytest.approx((a + 3 * b) / 4)  # "unweighted" point ignored


# 4. Determinism test ----------------------------------------------------------------


def test_features_are_deterministic_and_order_independent() -> None:
    issue = issue_time_utc(TARGET, time(9))
    inputs = synthetic_inputs(np.random.default_rng(11), issue)
    first = build_features(inputs, TARGET, issue, CONFIG)
    shuffled = FeatureInputs(
        **{
            name: frame.sample(frac=1, random_state=5).reset_index(drop=True)
            for name, frame in vars(inputs).items()
        }
    )
    again = build_features(shuffled, TARGET, issue, CONFIG)
    pd.testing.assert_frame_equal(first, again)
    assert (
        pd.util.hash_pandas_object(first, index=False).sum()
        == pd.util.hash_pandas_object(again, index=False).sum()
    )


# helpers ---------------------------------------------------------------------------


def test_latest_per_ts_prefers_ieso_then_highest_version() -> None:
    ts = pd.Timestamp("2026-01-01 05:00", tz="UTC")
    frame = pd.DataFrame(
        {
            "ts_utc": [ts] * 3,
            "source": ["gridstatus", "ieso", "ieso"],
            "version": pd.array([None, 1, 2], dtype="Int16"),
            "x": [1, 2, 3],
        }
    )
    assert latest_per_ts(frame).x.tolist() == [3]
    assert latest_per_ts(frame.iloc[0:0]).empty


def test_repo_feature_config_and_holidays() -> None:
    cfg = load_feature_config(REPO_ROOT / "configs" / "features.yaml")
    assert cfg.price_lags_days == (1, 2, 7)
    assert load_feature_config() == cfg
    empty = FeatureInputs(
        da_ozp=pd.DataFrame(
            columns=["ts_utc", "zonal_price", "source", "version", "available_at_utc"]
        ).astype({"ts_utc": TS_DTYPE, "available_at_utc": TS_DTYPE}),
        rt_hourly=pd.DataFrame(columns=["ts_utc", "lmp_mean", "source", "available_at_utc"]).astype(
            {"ts_utc": TS_DTYPE, "available_at_utc": TS_DTYPE}
        ),
        demand=pd.DataFrame(columns=["ts_utc", "ontario_demand", "available_at_utc"]).astype(
            {"ts_utc": TS_DTYPE, "available_at_utc": TS_DTYPE}
        ),
        weather=pd.DataFrame(
            columns=["point", "valid_ts_utc", "lead_days", *WEATHER_VARS, "available_at_utc"]
        ).astype({"valid_ts_utc": TS_DTYPE, "available_at_utc": TS_DTYPE}),
    )
    canada_day = date(2026, 7, 1)
    features = build_features(empty, canada_day, issue_time_utc(canada_day, time(9)), cfg)
    assert features.is_holiday.all()
    assert features.da_lag1.isna().all()
    assert features["_max_available_at"].isna().all()
