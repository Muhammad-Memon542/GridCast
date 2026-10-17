"""End-to-end silver build on a miniature bronze store made from recorded fixtures."""

from __future__ import annotations

import gzip
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
import pytest

from gridcast.features.availability import load_availability_config
from gridcast.ingest.manifest import FileRecord, Manifest
from gridcast.ingest.weather import WeatherStore, load_weather_config
from gridcast.silver import (
    build_all,
    build_da_ozp,
    build_rt_ozp_5min,
    build_rt_ozp_hourly,
    build_weather,
    write_table,
)

FIX = Path(__file__).parent / "fixtures" / "ieso"
SHA = "a" * 64

# (report, fixture, period_key, version, listing time UTC)
IESO_FILES = [
    (
        "DAHourlyOntarioZonalPrice",
        "PUB_DAHourlyOntarioZonalPrice_20260924_v1.xml",
        "20260924",
        1,
        "2026-09-23T17:34:00+00:00",
    ),
    (
        "DAHourlyOntarioZonalPrice",
        "PUB_DAHourlyOntarioZonalPrice_20260924_v3.xml",
        "20260924",
        3,
        "2026-09-23T19:29:00+00:00",
    ),
    (
        "DAHourlyOntarioZonalPrice",
        "PUB_DAHourlyOntarioZonalPrice_20261015_v1.xml",
        "20261015",
        1,
        "2026-10-14T17:33:00+00:00",
    ),
    (
        "RealtimeOntarioZonalPrice",
        "PUB_RealtimeOntarioZonalPrice_2026100101_v12.xml",
        "2026100101",
        12,
        "2026-10-01T05:53:00+00:00",
    ),
    (
        "PredispHourlyOntarioZonalPrice",
        "PUB_PredispHourlyOntarioZonalPrice_20261014_v1.xml",
        "20261014",
        1,
        "2026-10-14T01:19:00+00:00",
    ),
    ("Demand", "PUB_Demand_2025_excerpt.csv", "2025", 396, "2026-01-31T12:31:00+00:00"),
    (
        "Adequacy3",
        "PUB_Adequacy3_20261016_v99.xml",
        "20261016",
        99,
        "2026-10-16T21:00:00+00:00",
    ),
    (
        "VGForecastSummary",
        "PUB_VGForecastSummary_20261016_v9.xml",
        "20261016",
        9,
        "2026-10-16T21:00:00+00:00",
    ),
    (
        "DATotals",
        "PUB_DATotals_20261016_v1.xml",
        "20261016",
        1,
        "2026-10-16T21:00:00+00:00",
    ),
    (
        "PriceHOEPPredispOR",
        "PUB_PriceHOEPPredispOR_2025_excerpt.csv",
        "2025",
        119,
        "2025-05-01T13:01:00+00:00",
    ),
]


def _write_gs(data_dir: Path, dataset: str, frame: pd.DataFrame) -> None:
    rel = Path("bronze", "gridstatus", dataset, "pull.parquet")
    (data_dir / rel).parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(data_dir / rel, index=False)
    with sqlite3.connect(data_dir / "bronze" / "_gridstatus.sqlite") as db:
        db.execute(
            "CREATE TABLE IF NOT EXISTS pulls"
            " (dataset TEXT, pulled_at_utc TEXT, path TEXT, sha256 TEXT)"
        )
        db.execute(
            "INSERT INTO pulls VALUES (?,?,?,?)",
            (dataset, "2026-10-17T20:00:00+00:00", rel.as_posix(), SHA),
        )


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    with Manifest(tmp_path / "bronze" / "_manifest.sqlite") as m:
        for report, fixture, key, version, listed in IESO_FILES:
            rel = Path("bronze", "ieso", report, key, f"{fixture}.gz")
            (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
            (tmp_path / rel).write_bytes(gzip.compress((FIX / fixture).read_bytes()))
            m.add_file(
                FileRecord(
                    report=report,
                    filename=fixture,
                    period_key=key,
                    version=version,
                    listing_modified_utc=datetime.fromisoformat(listed),
                    url="u",
                    fetched_at_utc=datetime(2026, 10, 16, tzinfo=UTC),
                    http_status=200,
                    run_id="r",
                    sha256=SHA,
                    bytes=1,
                    path=rel.as_posix(),
                )
            )
    hours = pd.date_range("2025-11-02 05:00", periods=24, freq="h", tz="UTC")
    _write_gs(
        tmp_path,
        "ieso_lmp_day_ahead_hourly_ontario_zonal",
        pd.DataFrame(
            {
                "interval_start_utc": hours,
                "interval_end_utc": hours + pd.Timedelta(hours=1),
                "location": "ONZP",
                "lmp": 50.0,
                "energy": 49.0,
                "congestion": 0.5,
                "loss": 0.5,
            }
        ),
    )
    fives = pd.date_range("2025-11-02 05:00", periods=12, freq="5min", tz="UTC")
    _write_gs(
        tmp_path,
        "ieso_lmp_real_time_5_min_ontario_zonal",
        pd.DataFrame(
            {
                "interval_start_utc": fives,
                "interval_end_utc": fives + pd.Timedelta(minutes=5),
                "location": "ONZP",
                "lmp": range(12),
                "energy": 0.0,
                "congestion": 0.0,
                "loss": 0.0,
            }
        ),
    )
    payload = {
        "hourly": {
            "time": ["2026-10-15T00:00", "2026-10-17T00:00"],
            **{
                f"{v}_previous_day{n}": [1.0, 2.0]
                for v in [
                    "temperature_2m",
                    "dew_point_2m",
                    "wind_speed_100m",
                    "cloud_cover",
                    "shortwave_radiation",
                ]
                for n in (1, 2)
            },
        }
    }
    store = WeatherStore(tmp_path)
    store.save(
        model="gfs_seamless",
        point="toronto",
        start=datetime(2026, 10, 15).date(),
        end=datetime(2026, 10, 17).date(),
        fetched_at=datetime(2026, 10, 16, 12, tzinfo=UTC),
        complete=False,
        payload=json.dumps(payload).encode(),
    )
    store.close()
    return tmp_path


def test_da_ozp_merges_sources_with_availability(data_dir: Path) -> None:
    df = build_da_ozp(data_dir, load_availability_config())
    assert df.groupby("source").size().to_dict() == {"gridstatus": 24, "ieso": 72}
    ieso = df[
        (df.source == "ieso") & (df.version == 1) & (df.delivery_date.astype(str) == "2026-10-15")
    ]
    # CreatedAt 17:31:28 vs listing 17:33 + 1 min: the later wins.
    assert ieso.available_at_utc.iloc[0] == pd.Timestamp("2026-10-14 17:34", tz="UTC")
    assert ieso.ts_utc.iloc[0] == pd.Timestamp("2026-10-15 05:00", tz="UTC")
    gs = df[df.source == "gridstatus"]
    # Delivery 2025-11-02 -> 15:30 EDT on 2025-11-01 = 19:30 UTC.
    assert (gs.available_at_utc == pd.Timestamp("2025-11-01 19:30", tz="UTC")).all()
    assert gs.version.isna().all()


def test_rt_hourly_mean_matches_ieso_average(data_dir: Path) -> None:
    rt5 = build_rt_ozp_5min(data_dir, load_availability_config())
    hourly = build_rt_ozp_hourly(rt5)
    ieso = hourly[hourly.source == "ieso"].iloc[0]
    assert (ieso.n_intervals, round(ieso.lmp_mean, 2)) == (12, 30.39)
    gs5 = rt5[rt5.source == "gridstatus"]
    assert list(gs5.interval) == list(range(1, 13))
    assert gs5.available_at_utc.iloc[0] == pd.Timestamp("2025-11-02 06:05", tz="UTC")


def test_weather_drops_placeholder_previous_runs(data_dir: Path) -> None:
    df = build_weather(data_dir, load_weather_config())
    # Fetched 2026-10-16 12:00 UTC. Valid 2026-10-17 00:00 with lead 1 would be published
    # 2026-10-16 08:00 -> genuine; lead 2 -> 2026-10-15 08:00 -> genuine; valid 10-15 too.
    assert len(df) == 4
    assert (df.available_at_utc <= df.fetched_at_utc).all()


def test_build_all_writes_partitioned_tables(data_dir: Path) -> None:
    counts = build_all(data_dir)
    assert counts["da_ozp"] == 96
    assert counts["demand"] == 47
    assert counts["hoep"] == 24
    assert counts["predisp_ozp"] == 24
    assert (counts["adequacy_fcst"], counts["da_totals"], counts["vg_fcst"]) == (24, 24, 48)
    parts = sorted(p.parent.name for p in (data_dir / "silver" / "da_ozp").rglob("*.parquet"))
    assert parts == ["month=2025-11", "month=2026-09", "month=2026-10"]
    back = pd.read_parquet(data_dir / "silver" / "demand")
    assert len(back) == 47


def test_empty_bronze_builds_empty_tables(tmp_path: Path) -> None:
    counts = build_all(tmp_path)
    assert set(counts.values()) == {0}
    assert write_table(pd.DataFrame(), tmp_path, "da_ozp") == 0
