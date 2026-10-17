"""Backfill from the Grid Status hosted API (ADR-001).

Each pull is stored unmodified as Parquet under ``bronze/gridstatus/<dataset>/`` and
logged in ``bronze/_gridstatus.sqlite``. The free plan allows 500,000 rows and 250
requests per month. A pull is refused if its estimated size would take this month's usage
past ``max_quota_fraction`` of the limit (risk R6 triggers at 70%).

Grid Status terms forbid republishing this data; it never goes into the open dataset.
"""

from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

import pandas as pd
import structlog

log = structlog.get_logger(__name__)

PAGE_SIZE = 50_000  # the free plan's per-response maximum, so each page is one request

_SCHEMA = """
CREATE TABLE IF NOT EXISTS pulls (
    id INTEGER PRIMARY KEY,
    dataset TEXT NOT NULL,
    start_utc TEXT NOT NULL,
    end_utc TEXT NOT NULL,
    pulled_at_utc TEXT NOT NULL,
    rows INTEGER NOT NULL,
    estimated_rows INTEGER NOT NULL,
    rows_used_before INTEGER NOT NULL,
    path TEXT NOT NULL,
    sha256 TEXT NOT NULL
);
"""


class GridStatusAPI(Protocol):
    def get_dataset(
        self, dataset: str, *, start: str, end: str, page_size: int, verbose: bool
    ) -> Any: ...
    def get_dataset_metadata(self, dataset: str) -> Any: ...
    def get_api_usage(self) -> Any: ...


class QuotaError(RuntimeError):
    """Raised when a pull would push monthly usage past the allowed fraction."""


@dataclass(frozen=True)
class PullResult:
    dataset: str
    rows: int
    estimated_rows: int
    path: Path


def _as_utc(value: Any) -> datetime:
    ts = pd.Timestamp(value)
    return (ts.tz_localize(UTC) if ts.tzinfo is None else ts.tz_convert(UTC)).to_pydatetime()


def estimate_rows(metadata: dict[str, Any], start: datetime, end: datetime) -> int:
    """Approximate rows in ``[start, end)``, assuming rows are spread evenly over time."""
    total = int(metadata.get("number_of_rows_approximate") or 0)
    first = _as_utc(metadata["earliest_available_time_utc"])
    last = _as_utc(metadata["latest_available_time_utc"])
    span = (last - first).total_seconds()
    overlap = (min(end, last) - max(start, first)).total_seconds()
    if total == 0 or span <= 0 or overlap <= 0:
        return 0
    return int(total * min(overlap / span, 1.0)) + 1


def quota_status(api: GridStatusAPI) -> tuple[int, int]:
    """(rows used this period, row limit)."""
    usage = api.get_api_usage()
    used = int(usage["current_period_usage"]["total_api_rows_returned"])
    limit = int(usage["limits"]["api_rows_returned_limit"])
    return used, limit


def pull(
    api: GridStatusAPI,
    dataset: str,
    start: datetime,
    end: datetime,
    *,
    data_dir: Path,
    max_quota_fraction: float = 0.7,
    now: datetime | None = None,
) -> PullResult:
    if start.tzinfo is None or end.tzinfo is None:
        raise ValueError("start and end must be timezone-aware")
    if end <= start:
        raise ValueError("end must be after start")
    estimated = estimate_rows(dict(api.get_dataset_metadata(dataset)), start, end)
    used, limit = quota_status(api)
    if used + estimated > max_quota_fraction * limit:
        raise QuotaError(
            f"{dataset}: ~{estimated:,} rows would take usage from {used:,} past "
            f"{max_quota_fraction:.0%} of the {limit:,}-row monthly limit"
        )
    log.info("gridstatus_pull_start", dataset=dataset, estimated_rows=estimated, used=used)
    frame = api.get_dataset(
        dataset,
        start=start.isoformat(),
        end=end.isoformat(),
        page_size=PAGE_SIZE,
        verbose=False,
    )
    if not isinstance(frame, pd.DataFrame):
        raise TypeError(f"expected a DataFrame from Grid Status, got {type(frame).__name__}")

    pulled_at = now or datetime.now(UTC)
    stamp = pulled_at.strftime("%Y%m%dT%H%M%SZ")
    rel = Path(
        "bronze",
        "gridstatus",
        dataset,
        f"{start:%Y%m%dT%H%MZ}_{end:%Y%m%dT%H%MZ}__{stamp}.parquet",
    )
    path = data_dir / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    frame.to_parquet(tmp, index=False)
    tmp.replace(path)
    sha256 = hashlib.sha256(path.read_bytes()).hexdigest()

    db_path = data_dir / "bronze" / "_gridstatus.sqlite"
    with sqlite3.connect(db_path) as db:
        db.executescript(_SCHEMA)
        db.execute(
            "INSERT INTO pulls (dataset, start_utc, end_utc, pulled_at_utc, rows, estimated_rows,"
            " rows_used_before, path, sha256) VALUES (?,?,?,?,?,?,?,?,?)",
            (
                dataset,
                start.isoformat(),
                end.isoformat(),
                pulled_at.isoformat(timespec="seconds"),
                len(frame),
                estimated,
                used,
                rel.as_posix(),
                sha256,
            ),
        )
    log.info("gridstatus_pull_done", dataset=dataset, rows=len(frame), path=rel.as_posix())
    return PullResult(dataset=dataset, rows=len(frame), estimated_rows=estimated, path=path)
