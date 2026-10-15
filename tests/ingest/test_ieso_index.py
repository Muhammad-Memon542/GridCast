from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from gridcast.ingest.ieso_index import parse_filename, parse_listing

FIXTURES = Path(__file__).parents[1] / "fixtures" / "ieso"
DA = "DAHourlyOntarioZonalPrice"


@pytest.mark.parametrize(
    ("filename", "key", "version", "partition", "period_end"),
    [
        (
            "PUB_DAHourlyOntarioZonalPrice_20260924_v3.xml",
            "20260924",
            3,
            "2026-09-24",
            date(2026, 9, 24),
        ),
        (
            "PUB_DAHourlyOntarioZonalPrice_20260924.xml",
            "20260924",
            None,
            "2026-09-24",
            date(2026, 9, 24),
        ),
        ("PUB_DAHourlyOntarioZonalPrice.xml", None, None, "_latest", None),
        (
            "PUB_DAHourlyOntarioZonalPrice_2026101515_v10.xml",
            "2026101515",
            10,
            "2026-10-15",
            date(2026, 10, 15),
        ),
        (
            "PUB_DAHourlyOntarioZonalPrice_202602_v14.csv",
            "202602",
            14,
            "2026-02",
            date(2026, 2, 28),
        ),
        ("PUB_DAHourlyOntarioZonalPrice_2025_v395.csv", "2025", 395, "2025", date(2025, 12, 31)),
    ],
)
def test_parse_filename(
    filename: str, key: str | None, version: int | None, partition: str, period_end: date | None
) -> None:
    parsed = parse_filename(DA, filename)
    assert parsed is not None
    assert (parsed.key, parsed.version, parsed.partition, parsed.period_end) == (
        key,
        version,
        partition,
        period_end,
    )
    assert parsed.is_alias is (version is None)


def test_hour_ending_only_for_hourly_keys() -> None:
    hourly = parse_filename(DA, "PUB_DAHourlyOntarioZonalPrice_2026101507_v1.xml")
    daily = parse_filename(DA, "PUB_DAHourlyOntarioZonalPrice_20261015_v1.xml")
    assert hourly is not None
    assert hourly.hour_ending == 7
    assert daily is not None
    assert daily.hour_ending is None


@pytest.mark.parametrize(
    "filename",
    [
        "PUB_DAHourlyOntarioZonalPriceX_20261015.xml",  # a different report
        "PUB_DAHourlyZonal_20261015.xml",
        "PUB_DAHourlyOntarioZonalPrice_20261015.pdf",
        "PUB_DAHourlyOntarioZonalPrice_2026101.xml",  # malformed key
    ],
)
def test_parse_filename_rejects_other_files(filename: str) -> None:
    assert parse_filename(DA, filename) is None


def test_parse_listing_from_recorded_index() -> None:
    html = (FIXTURES / "DAHourlyOntarioZonalPrice_index.html").read_text()
    entries = parse_listing(DA, html)
    names = [e.file.filename for e in entries]
    assert len(entries) == 7
    assert names[0] == "PUB_DAHourlyOntarioZonalPrice.xml"
    v1 = next(e for e in entries if e.file.filename.endswith("20260924_v1.xml"))
    # Listing shows 12:34 EST; the server's HTTP Last-Modified for this file is 17:34 GMT.
    assert v1.modified_utc == datetime(2026, 9, 23, 17, 34, tzinfo=UTC)
    assert v1.size == "5.9K"
    assert parse_listing("Adequacy3", html) == []
