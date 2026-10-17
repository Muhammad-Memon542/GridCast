"""Canonical time model (plan §7.3, M1-T7), including the mandatory DST dates."""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta

import pandas as pd
import pytest

from gridcast.timeutil.ieso import (
    he_start_utc,
    he_to_ts_utc,
    issue_time_utc,
    local_time_on,
    ts_to_delivery,
)

DST_DAYS = [date(2025, 11, 2), date(2026, 3, 8), date(2026, 11, 1)]


@pytest.mark.parametrize("day", DST_DAYS)
def test_dst_days_have_exactly_24_contiguous_hours(day: date) -> None:
    # Three days around the DST change: 72 rows, strictly hourly, no duplicates.
    days = [day - timedelta(days=1), day, day + timedelta(days=1)]
    frame = pd.DataFrame(
        [(d, h) for d in days for h in range(1, 25)], columns=["delivery_date", "hour_ending"]
    )
    ts = he_to_ts_utc(frame["delivery_date"], frame["hour_ending"])
    assert len(ts) == 72
    assert ts.is_unique
    assert (ts.diff().dropna() == pd.Timedelta(hours=1)).all()
    assert (frame[frame.delivery_date == day].shape[0]) == 24
    # HE1 always starts at 05:00 UTC, whatever the wall clock in Toronto does.
    assert ts.iloc[24] == pd.Timestamp(datetime.combine(day, time(5)), tz="UTC")


def test_scalar_and_vector_agree_including_intervals() -> None:
    d = date(2026, 10, 15)
    assert he_start_utc(d, 1) == datetime(2026, 10, 15, 5, tzinfo=UTC)
    assert he_start_utc(d, 24) == datetime(2026, 10, 16, 4, tzinfo=UTC)
    assert he_start_utc(d, 12, interval=12) == datetime(2026, 10, 15, 16, 55, tzinfo=UTC)
    vec = he_to_ts_utc(pd.Series([d]), pd.Series([12]), pd.Series([12]))
    assert vec.iloc[0] == pd.Timestamp("2026-10-15 16:55", tz="UTC")
    assert str(vec.dtype) == "datetime64[us, UTC]"


def test_round_trip_to_delivery_hours() -> None:
    frame = pd.DataFrame({"d": [date(2026, 3, 8)] * 24, "h": range(1, 25)})
    ts = he_to_ts_utc(frame["d"], frame["h"])
    days, hours = ts_to_delivery(ts)
    assert list(days) == [date(2026, 3, 8)] * 24
    assert list(hours) == list(range(1, 25))


@pytest.mark.parametrize(
    ("args", "kwargs"),
    [
        ((date(2026, 1, 1), 0), {}),
        ((date(2026, 1, 1), 25), {}),
        ((date(2026, 1, 1), 1), {"interval": 13}),
    ],
)
def test_out_of_range_hours_rejected(args: tuple[date, int], kwargs: dict[str, int]) -> None:
    with pytest.raises(ValueError, match=r"1\.\.(24|12)"):
        he_start_utc(*args, **kwargs)


def test_vector_rejects_bad_hours_and_intervals() -> None:
    with pytest.raises(ValueError, match="hour_ending"):
        he_to_ts_utc(pd.Series([date(2026, 1, 1)]), pd.Series([0]))
    with pytest.raises(ValueError, match="interval"):
        he_to_ts_utc(pd.Series([date(2026, 1, 1)]), pd.Series([1]), pd.Series([0]))


def test_issue_time_follows_toronto_wall_clock() -> None:
    nine = time(9, 0)
    # D = 2026-11-01: issued 2026-10-31 09:00 EDT (UTC-4).
    assert issue_time_utc(date(2026, 11, 1), nine) == datetime(2026, 10, 31, 13, tzinfo=UTC)
    # D = 2026-11-02: issued 2026-11-01 09:00 EST (after the 02:00 change).
    assert issue_time_utc(date(2026, 11, 2), nine) == datetime(2026, 11, 1, 14, tzinfo=UTC)
    assert local_time_on(date(2026, 1, 5), time(15, 30), days_after=-1) == datetime(
        2026, 1, 4, 20, 30, tzinfo=UTC
    )
