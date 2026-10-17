"""Golden-file tests: parsers against real IESO files recorded in tests/fixtures (M1-T4)."""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from gridcast.parse.ieso_csv import (
    parse_demand,
    parse_gen_month,
    parse_hoep,
    parse_intertie_year,
    read_ieso_csv,
)
from gridcast.parse.ieso_xml import parse_da_ozp, parse_predisp_ozp, parse_rt_ozp

FIX = Path(__file__).parents[1] / "fixtures" / "ieso"


def _read(name: str) -> bytes:
    return (FIX / name).read_bytes()


def test_da_ozp_golden() -> None:
    df = parse_da_ozp(_read("PUB_DAHourlyOntarioZonalPrice_20261015_v1.xml"))
    assert len(df) == 24
    assert list(df["hour_ending"]) == list(range(1, 25))
    first = df.iloc[0]
    assert (first.delivery_date, first.zonal_price, first.loss_price_capped) == (
        date(2026, 10, 15),
        45.15,
        0.31,
    )
    assert first.congestion_price_capped == 0.0
    assert first.flag == "DSO-RD"
    assert df.iloc[1].zonal_price == 46.8
    # CreatedAt 2026-10-14T12:31:28 is EST (V-03).
    assert first.doc_created_at == datetime(2026, 10, 14, 17, 31, 28, tzinfo=UTC)


def test_da_ozp_versions_can_be_pure_republications() -> None:
    # 2026-09-24 v1/v2/v3 differ only in CreatedAt (V-11): a new version is not always a correction.
    cols = ["hour_ending", "zonal_price", "loss_price_capped", "congestion_price_capped", "flag"]
    v1 = parse_da_ozp(_read("PUB_DAHourlyOntarioZonalPrice_20260924_v1.xml"))
    v3 = parse_da_ozp(_read("PUB_DAHourlyOntarioZonalPrice_20260924_v3.xml"))
    assert v1.doc_created_at.iloc[0] < v3.doc_created_at.iloc[0]
    assert v1[cols].equals(v3[cols])


def test_rt_ozp_golden() -> None:
    df = parse_rt_ozp(_read("PUB_RealtimeOntarioZonalPrice_2026100101_v12.xml"))
    assert len(df) == 12
    assert list(df["interval"]) == list(range(1, 13))
    assert set(df["hour_ending"]) == {1}
    assert list(df["lmp"].head(3)) == [33.69, 33.67, 33.67]
    assert df["lmp"].iloc[-1] == 27.70
    # The file's <AveragePrice> (30.39) is the plain mean of the 12 intervals.
    assert round(df["lmp"].mean(), 2) == 30.39
    assert df.doc_created_at.iloc[0] == datetime(2026, 10, 1, 5, 52, 21, tzinfo=UTC)


def test_predisp_ozp_golden() -> None:
    df = parse_predisp_ozp(_read("PUB_PredispHourlyOntarioZonalPrice_20261014_v1.xml"))
    assert len(df) == 24
    assert list(df["zonal_price"].head(3)) == [39.33, 37.43, 34.47]
    assert "flag" not in df
    # First vintage for 2026-10-14 was created 2026-10-13 20:16 EST, after the DAM (V-07).
    assert df.doc_created_at.iloc[0] == datetime(2026, 10, 14, 1, 16, 24, tzinfo=UTC)


def test_demand_golden_including_go_live_gap() -> None:
    df = parse_demand(_read("PUB_Demand_2025_excerpt.csv"))
    go_live = df[df.delivery_date == date(2025, 5, 1)]
    assert list(go_live.hour_ending) == list(range(2, 25))  # IESO's own file lacks HE1 (V-08)
    assert len(df[df.delivery_date == date(2025, 11, 2)]) == 24  # DST day: still 24 hours
    row = go_live.iloc[0]
    assert (row.market_demand, row.ontario_demand) == (13153.0, 12352.0)
    assert row.doc_created_at == datetime(2026, 1, 31, 12, 30, 13, tzinfo=UTC)


def test_hoep_golden() -> None:
    df = parse_hoep(_read("PUB_PriceHOEPPredispOR_2025_excerpt.csv"))
    assert len(df) == 24
    last = df.iloc[-1]
    assert (last.hour_ending, last.hoep, last.predispatch_1h, last.predispatch_3h) == (
        24,
        14.34,
        14.34,
        14.40,
    )


def test_csv_without_created_line() -> None:
    frame, created = read_ieso_csv(b"Date,Hour,X\n2025-01-01,1,2\n")
    assert created is None
    assert frame["Date"].iloc[0] == date(2025, 1, 1)


@pytest.mark.parametrize(
    "content",
    [
        b'<Document xmlns="http://example.com/other"><DocBody/></Document>',
        b'<Document xmlns="http://www.ieso.ca/schema"><DocBody/></Document>',
    ],
)
def test_malformed_xml_rejected(content: bytes) -> None:
    with pytest.raises(ValueError, match=r"namespace|DeliveryDate"):
        parse_da_ozp(content)


def test_external_entities_are_not_resolved() -> None:
    evil = (
        b'<?xml version="1.0"?><!DOCTYPE d [<!ENTITY x SYSTEM "file:///etc/passwd">]>'
        b'<Document xmlns="http://www.ieso.ca/schema"><DocHeader/><DocBody>'
        b"<DeliveryDate>2026-01-01</DeliveryDate><HourlyPriceComponents><PricingHour>1</PricingHour>"
        b"<ZonalPrice>1</ZonalPrice><LossPriceCapped>&x;</LossPriceCapped>"
        b"<CongestionPriceCapped></CongestionPriceCapped><Flag>A</Flag></HourlyPriceComponents>"
        b"</DocBody></Document>"
    )
    df = parse_da_ozp(evil)
    assert df.loss_price_capped.isna().all()  # entity left unexpanded, never read from disk
    assert df.congestion_price_capped.isna().all()  # empty_or_decimal -> NaN
    assert df.doc_created_at.isna().all()


def test_gen_month_sums_units_by_fuel() -> None:
    df = parse_gen_month(_read("PUB_GenOutputCapabilityMonth_202609_excerpt.csv"))
    he17 = df[df.hour_ending == 17].set_index("fuel")
    assert set(he17.index) == {"hydro", "nuclear", "wind"}
    # The excerpt keeps every nuclear unit, one hydro and one wind unit.
    assert (he17.loc["nuclear", "output_mw"], he17.loc["nuclear", "capability_mw"]) == (8466, 8575)
    assert he17.loc["wind", "available_capacity_mw"] == 60
    assert he17.loc["wind", "capability_mw"] != he17.loc["wind", "capability_mw"]  # NaN for wind
    assert len(df) == 3 * 24
    assert df.doc_created_at.iloc[0] == datetime(2026, 10, 1, 11, 0, 9, tzinfo=UTC)


def test_intertie_year_totals() -> None:
    df = parse_intertie_year(_read("PUB_IntertieScheduleFlowYear_2025_excerpt.csv"))
    assert len(df) == 24
    first = df.iloc[0]
    assert (first.imports_sched_mw, first.exports_sched_mw, first.total_flow_mw) == (94, 3502, 3843)
    assert first.doc_created_at == datetime(2026, 1, 31, 13, 2, 8, tzinfo=UTC)
