"""Weather forecasts as known in advance, from the Open-Meteo Previous Runs API (M1-T6).

Raw JSON responses go to ``bronze/weather/<model>/<point>/`` (gzip) and are logged in
``bronze/_weather.sqlite``. A chunk whose end is more than ``max(lead_days)`` + 1 days in
the past is complete (its previous-run values can no longer change) and is never fetched
again; newer chunks are re-fetched on each run.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Self

import structlog
from pydantic import BaseModel, ConfigDict, Field, model_validator

from gridcast.config import Settings, load_yaml
from gridcast.ingest.http import FetchError, PoliteClient

log = structlog.get_logger(__name__)

WEATHER_CONFIG_FILE = "weather.yaml"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS fetches (
    id INTEGER PRIMARY KEY,
    model TEXT NOT NULL,
    point TEXT NOT NULL,
    start_date TEXT NOT NULL,
    end_date TEXT NOT NULL,
    fetched_at_utc TEXT NOT NULL,
    complete INTEGER NOT NULL,
    hours INTEGER NOT NULL,
    null_values INTEGER NOT NULL,
    path TEXT NOT NULL,
    sha256 TEXT NOT NULL
);
"""


class WeatherPoint(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(pattern=r"^[a-z_]+$")
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)


class WeatherConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    base_url: str = Field(pattern=r"^https://")
    model: str
    start: date
    chunk_days: int = Field(gt=0, le=366)
    lead_days: tuple[int, ...]
    latency_hours: float = Field(ge=0)
    variables: tuple[str, ...]
    points: tuple[WeatherPoint, ...]

    @model_validator(mode="after")
    def _check(self) -> Self:
        if not self.lead_days or any(n < 1 or n > 7 for n in self.lead_days):
            raise ValueError("lead_days must be within 1..7 (day 0 is the current run: leaky)")
        if len({p.name for p in self.points}) != len(self.points):
            raise ValueError("point names must be unique")
        return self

    @property
    def hourly_fields(self) -> list[str]:
        return [f"{v}_previous_day{n}" for v in self.variables for n in self.lead_days]


def load_weather_config(path: Path | None = None) -> WeatherConfig:
    if path is None:
        path = Settings().configs_dir / WEATHER_CONFIG_FILE
    return WeatherConfig.model_validate(load_yaml(path))


def chunks(start: date, end: date, size_days: int) -> list[tuple[date, date]]:
    """Inclusive ``[start, end]`` split into consecutive inclusive chunks."""
    out = []
    cursor = start
    while cursor <= end:
        last = min(cursor + timedelta(days=size_days - 1), end)
        out.append((cursor, last))
        cursor = last + timedelta(days=1)
    return out


@dataclass(frozen=True)
class WeatherRunSummary:
    fetched: int
    skipped: int
    errors: int


class WeatherStore:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        path = data_dir / "bronze" / "_weather.sqlite"
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path, isolation_level=None)
        self._db.executescript(_SCHEMA)

    def close(self) -> None:
        self._db.close()

    def has_complete(self, model: str, point: str, start: date, end: date) -> bool:
        row = self._db.execute(
            "SELECT 1 FROM fetches WHERE model=? AND point=? AND start_date=? AND end_date=?"
            " AND complete=1",
            (model, point, start.isoformat(), end.isoformat()),
        ).fetchone()
        return row is not None

    def save(
        self,
        *,
        model: str,
        point: str,
        start: date,
        end: date,
        fetched_at: datetime,
        complete: bool,
        payload: bytes,
    ) -> Path:
        hourly = json.loads(payload)["hourly"]
        hours = len(hourly["time"])
        nulls = sum(v is None for k, col in hourly.items() if k != "time" for v in col)
        stamp = fetched_at.strftime("%Y%m%dT%H%M%SZ")
        rel = Path("bronze", "weather", model, point, f"{start}_{end}__{stamp}.json.gz")
        path = self.data_dir / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{path.name}.tmp")
        tmp.write_bytes(gzip.compress(payload, mtime=0))
        tmp.replace(path)
        self._db.execute(
            "INSERT INTO fetches (model, point, start_date, end_date, fetched_at_utc, complete,"
            " hours, null_values, path, sha256) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                model,
                point,
                start.isoformat(),
                end.isoformat(),
                fetched_at.isoformat(timespec="seconds"),
                int(complete),
                hours,
                nulls,
                rel.as_posix(),
                hashlib.sha256(payload).hexdigest(),
            ),
        )
        return path


def fetch_weather(
    config: WeatherConfig,
    *,
    data_dir: Path,
    client: PoliteClient,
    today: date,
    now: datetime | None = None,
) -> WeatherRunSummary:
    """Fetch every missing or still-changing chunk from ``config.start`` to ``today + 2``."""
    end = today + timedelta(days=2)
    settle = timedelta(days=max(config.lead_days) + 1)
    fetched = skipped = errors = 0
    store = WeatherStore(data_dir)
    try:
        for point in config.points:
            for start, last in chunks(config.start, end, config.chunk_days):
                if store.has_complete(config.model, point.name, start, last):
                    skipped += 1
                    continue
                url = (
                    f"{config.base_url}?latitude={point.lat}&longitude={point.lon}"
                    f"&hourly={','.join(config.hourly_fields)}&models={config.model}"
                    f"&start_date={start}&end_date={last}&timezone=GMT"
                )
                try:
                    response = client.get(url)
                except FetchError as exc:
                    log.error(
                        "weather_fetch_failed", point=point.name, start=str(start), error=str(exc)
                    )
                    errors += 1
                    continue
                if response.status_code != 200:
                    log.error("weather_fetch_failed", point=point.name, status=response.status_code)
                    errors += 1
                    continue
                store.save(
                    model=config.model,
                    point=point.name,
                    start=start,
                    end=last,
                    fetched_at=now or datetime.now(UTC),
                    complete=last + settle < today,
                    payload=response.content,
                )
                fetched += 1
    finally:
        store.close()
    log.info("weather_fetched", fetched=fetched, skipped=skipped, errors=errors)
    return WeatherRunSummary(fetched=fetched, skipped=skipped, errors=errors)
