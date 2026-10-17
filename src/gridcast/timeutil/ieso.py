"""IESO clock conventions and the canonical time model (plan §7.3, M1-T7).

IESO reports, ``CreatedAt`` stamps and the report server's directory listings all use
Eastern Standard Time (UTC-5) all year, with no daylight saving (docs/decisions.md,
V-02 and V-03). Never use ``America/Toronto`` for these values. Every delivery day has
exactly 24 hours, hour-ending 1-24, and ``ts_utc`` is the UTC start of the interval.

The forecast issue time, by contrast, is a wall-clock time in ``America/Toronto``.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import pandas as pd

IESO_REPORT_TZ = timezone(timedelta(hours=-5), name="EST")
EASTERN = ZoneInfo("America/Toronto")
TS_DTYPE = pd.DatetimeTZDtype(unit="us", tz="UTC")


def ieso_naive_to_utc(value: datetime) -> datetime:
    """Interpret a naive IESO timestamp as EST and return it in UTC."""
    if value.tzinfo is not None:
        raise ValueError(f"expected a naive IESO timestamp, got {value.isoformat()}")
    return value.replace(tzinfo=IESO_REPORT_TZ).astimezone(UTC)


def ieso_date(at: datetime) -> date:
    """Calendar date on the IESO (EST) clock for an aware instant."""
    if at.tzinfo is None:
        raise ValueError("expected an aware datetime")
    return at.astimezone(IESO_REPORT_TZ).date()


def he_start_utc(delivery_date: date, hour_ending: int, interval: int | None = None) -> datetime:
    """UTC start of hour-ending ``hour_ending`` (1-24), or of 5-minute ``interval`` (1-12)."""
    if not 1 <= hour_ending <= 24:
        raise ValueError(f"hour_ending must be 1..24, got {hour_ending}")
    if interval is not None and not 1 <= interval <= 12:
        raise ValueError(f"interval must be 1..12, got {interval}")
    start = datetime.combine(delivery_date, time(0), tzinfo=IESO_REPORT_TZ)
    start += timedelta(hours=hour_ending - 1, minutes=5 * ((interval or 1) - 1))
    return start.astimezone(UTC)


def he_to_ts_utc(
    delivery_date: pd.Series, hour_ending: pd.Series, interval: pd.Series | None = None
) -> pd.Series:
    """Vectorised :func:`he_start_utc`; returns ``datetime64[us, UTC]``."""
    if ((hour_ending < 1) | (hour_ending > 24)).any():
        raise ValueError("hour_ending must be 1..24")
    base = pd.to_datetime(delivery_date).dt.tz_localize(None).astype("datetime64[us]")
    offset = pd.to_timedelta(hour_ending.astype("int64") - 1, unit="h")
    if interval is not None:
        if ((interval < 1) | (interval > 12)).any():
            raise ValueError("interval must be 1..12")
        offset = offset + pd.to_timedelta(5 * (interval.astype("int64") - 1), unit="min")
    # EST is UTC-5 all year: add 5 hours to get UTC.
    return (base + offset + pd.Timedelta(hours=5)).dt.tz_localize("UTC").astype(TS_DTYPE)


def ts_to_delivery(ts_utc: pd.Series) -> tuple[pd.Series, pd.Series]:
    """Inverse of :func:`he_to_ts_utc` for hourly stamps: (delivery_date, hour_ending)."""
    est = ts_utc.dt.tz_convert(None) - pd.Timedelta(hours=5)
    return est.dt.date, (est.dt.hour + 1).astype("int8")


def issue_time_utc(target_day: date, local_time: time, day_offset: int = -1) -> datetime:
    """Forecast issue time: ``local_time`` in America/Toronto on ``target_day + day_offset``."""
    local = datetime.combine(target_day + timedelta(days=day_offset), local_time, tzinfo=EASTERN)
    return local.astimezone(UTC)


def local_time_on(day: date, local_time: time, *, days_after: int = 0) -> datetime:
    """``local_time`` in America/Toronto on ``day + days_after``, as UTC."""
    local = datetime.combine(day + timedelta(days=days_after), local_time, tzinfo=EASTERN)
    return local.astimezone(UTC)
