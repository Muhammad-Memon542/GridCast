from __future__ import annotations

import gzip
import json
import sqlite3
from datetime import UTC, date, datetime
from pathlib import Path

import httpx
import pytest
import respx
from pydantic import ValidationError

from gridcast.config import REPO_ROOT
from gridcast.ingest.http import PoliteClient
from gridcast.ingest.weather import WeatherConfig, chunks, fetch_weather, load_weather_config

BASE = "https://weather.example.test/v1/forecast"


def _config(**overrides: object) -> WeatherConfig:
    fields: dict[str, object] = {
        "base_url": BASE,
        "model": "gfs_seamless",
        "start": "2026-10-01",
        "chunk_days": 10,
        "lead_days": [1, 2],
        "latency_hours": 8,
        "variables": ["temperature_2m"],
        "points": [{"name": "toronto", "lat": 43.65, "lon": -79.38}],
    }
    fields.update(overrides)
    return WeatherConfig.model_validate(fields)


def _payload(request: httpx.Request) -> httpx.Response:
    start = request.url.params["start_date"]
    body = {
        "hourly": {
            "time": [f"{start}T00:00", f"{start}T01:00"],
            "temperature_2m_previous_day1": [1.0, None],
            "temperature_2m_previous_day2": [2.0, 3.0],
        }
    }
    return httpx.Response(200, json=body)


def test_chunks_cover_range_inclusively() -> None:
    assert chunks(date(2026, 1, 1), date(2026, 1, 25), 10) == [
        (date(2026, 1, 1), date(2026, 1, 10)),
        (date(2026, 1, 11), date(2026, 1, 20)),
        (date(2026, 1, 21), date(2026, 1, 25)),
    ]


def test_repo_weather_config() -> None:
    cfg = load_weather_config(REPO_ROOT / "configs" / "weather.yaml")
    assert len(cfg.points) == 8
    assert cfg.hourly_fields[:2] == ["temperature_2m_previous_day1", "temperature_2m_previous_day2"]
    assert load_weather_config() == cfg


@pytest.mark.parametrize(
    "overrides",
    [{"lead_days": [0, 1]}, {"lead_days": []}, {"points": [{"name": "a", "lat": 1, "lon": 1}] * 2}],
)
def test_invalid_weather_configs(overrides: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        _config(**overrides)


@respx.mock
def test_fetch_stores_chunks_and_skips_complete_ones(tmp_path: Path) -> None:
    route = respx.get(BASE).mock(side_effect=_payload)
    now = datetime(2026, 10, 17, 20, 0, tzinfo=UTC)
    with PoliteClient("t", sleep=lambda _: None) as client:
        first = fetch_weather(
            _config(), data_dir=tmp_path, client=client, today=date(2026, 10, 17), now=now
        )
        # Oct 1-10 is settled (ends > 3 days ago); Oct 11-19 is still changing.
        assert (first.fetched, first.skipped, first.errors) == (2, 0, 0)
        second = fetch_weather(
            _config(), data_dir=tmp_path, client=client, today=date(2026, 10, 17), now=now
        )
        assert (second.fetched, second.skipped) == (1, 1)
    assert route.call_count == 3
    params = route.calls[0].request.url.params
    assert params["hourly"] == "temperature_2m_previous_day1,temperature_2m_previous_day2"
    assert params["timezone"] == "GMT"
    with sqlite3.connect(tmp_path / "bronze" / "_weather.sqlite") as db:
        rows = db.execute(
            "SELECT start_date, complete, hours, null_values, path FROM fetches ORDER BY id"
        ).fetchall()
    assert [r[:4] for r in rows] == [
        ("2026-10-01", 1, 2, 1),
        ("2026-10-11", 0, 2, 1),
        ("2026-10-11", 0, 2, 1),
    ]
    stored = json.loads(gzip.decompress((tmp_path / rows[0][4]).read_bytes()))
    assert stored["hourly"]["temperature_2m_previous_day2"] == [2.0, 3.0]


@respx.mock
def test_fetch_errors_are_counted_not_raised(tmp_path: Path) -> None:
    respx.get(BASE).mock(
        side_effect=[httpx.Response(400), httpx.Response(503)] + [httpx.Response(503)] * 10
    )
    with PoliteClient("t", sleep=lambda _: None, max_retries=0) as client:
        summary = fetch_weather(
            _config(), data_dir=tmp_path, client=client, today=date(2026, 10, 17)
        )
    assert (summary.fetched, summary.errors) == (0, 2)
