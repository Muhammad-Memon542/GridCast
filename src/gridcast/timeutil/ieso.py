"""IESO clock conventions.

IESO reports, ``CreatedAt`` stamps and the report server's directory listings all use
Eastern Standard Time (UTC-5) all year, with no daylight saving (docs/decisions.md,
V-02 and V-03). Never use ``America/Toronto`` for these values.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta, timezone

IESO_REPORT_TZ = timezone(timedelta(hours=-5), name="EST")


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
