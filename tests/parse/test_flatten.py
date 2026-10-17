"""Golden-file tests for the nested-report flattener (Adequacy3, VGForecastSummary, DATotals)."""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from gridcast.parse.ieso_flatten import (
    ADEQUACY_SERIES,
    DA_TOTALS_SERIES,
    VG_SERIES,
    flatten_hourly,
    forecast_timestamp,
    wide_series,
)

FIX = Path(__file__).parents[1] / "fixtures" / "ieso"


def _one(prefix: str) -> bytes:
    (path,) = FIX.glob(f"{prefix}_*.xml")
    return path.read_bytes()


def test_adequacy_golden() -> None:
    wide, created = wide_series(_one("PUB_Adequacy3_20261016"), ADEQUACY_SERIES)
    assert len(wide) == 24
    assert not [c for c in ADEQUACY_SERIES if wide[c].isna().all()]
    he1 = wide.iloc[0]
    assert (he1.delivery_date, he1.hour_ending) == (date(2026, 10, 16), 1)
    assert (he1.ont_demand_fcst, he1.total_requirement, he1.excess_capacity) == (14102, 15305, 8427)
    assert (he1.nuclear_capacity, he1.nuclear_outages) == (12188, 4689)
    assert he1.exports_scheduled == -3131  # signs are kept as published
    assert created is not None
    assert created.tzinfo is UTC


def test_adequacy_series_names_are_unique_and_qualified() -> None:
    long, _ = flatten_hourly(_one("PUB_Adequacy3_20261016"))
    names = set(long.series)
    assert len(names) == 99
    assert "ForecastSupply/InternalResources/Nuclear/Capacities/Capacity.EnergyMW" in names
    assert "ForecastDemand/ZonalExports/New York/Schedules/Schedule.EnergyMW" in names
    assert not long.duplicated(["delivery_date", "hour_ending", "series"]).any()


def test_vg_forecast_golden_spans_three_days() -> None:
    content = _one("PUB_VGForecastSummary_20261016")
    wide, created = wide_series(content, VG_SERIES)
    assert sorted(set(wide.delivery_date)) == [date(2026, 10, d) for d in (16, 17, 18)]
    first = wide.iloc[0]
    assert (first.delivery_date, first.hour_ending, first.wind_mp_fcst) == (
        date(2026, 10, 16),
        10,
        3025.9,
    )
    assert forecast_timestamp(content) == datetime(2026, 10, 16, 13, 33, 9, tzinfo=UTC)
    assert created == datetime(2026, 10, 16, 13, 55, 55, tzinfo=UTC)


def test_da_totals_golden() -> None:
    wide, _ = wide_series(_one("PUB_DATotals_20261016"), DA_TOTALS_SERIES)
    he1 = wide.iloc[0]
    assert (he1.da_total_energy, he1.da_total_load, he1.da_total_loss) == (16667.9, 16335.3, 332.7)


def test_report_without_body_rejected() -> None:
    with pytest.raises(ValueError, match="DocBody"):
        flatten_hourly(b'<Document xmlns="http://www.ieso.ca/schema"><DocHeader/></Document>')
    assert (
        forecast_timestamp(b'<Document xmlns="http://www.ieso.ca/schema"><DocBody/></Document>')
        is None
    )
