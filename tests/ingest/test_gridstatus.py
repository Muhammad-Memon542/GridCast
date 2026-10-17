from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from gridcast.ingest.gridstatus import QuotaError, estimate_rows, pull

START = datetime(2025, 5, 1, tzinfo=UTC)
END = datetime(2025, 5, 2, tzinfo=UTC)
META = {
    "number_of_rows_approximate": 1000,
    "earliest_available_time_utc": "2025-05-01 00:00:00+00:00",
    "latest_available_time_utc": "2025-05-11 00:00:00+00:00",
}


class FakeAPI:
    def __init__(self, used: int = 0, limit: int = 500_000) -> None:
        self.used, self.limit = used, limit
        self.calls: list[dict[str, Any]] = []

    def get_dataset_metadata(self, dataset: str) -> dict[str, Any]:
        return META

    def get_api_usage(self) -> dict[str, Any]:
        return {
            "limits": {"api_rows_returned_limit": self.limit},
            "current_period_usage": {"total_api_rows_returned": self.used},
        }

    def get_dataset(self, dataset: str, **kwargs: Any) -> pd.DataFrame:
        self.calls.append({"dataset": dataset, **kwargs})
        return pd.DataFrame(
            {
                "interval_start_utc": pd.date_range(START, periods=24, freq="h"),
                "location": "ONZP",
                "lmp": range(24),
            }
        )


def test_estimate_rows_scales_with_overlap() -> None:
    assert estimate_rows(META, START, END) == 101  # 1 of 10 days, rounded up
    assert (
        estimate_rows(META, datetime(2024, 1, 1, tzinfo=UTC), datetime(2030, 1, 1, tzinfo=UTC))
        == 1001
    )
    assert (
        estimate_rows(META, datetime(2020, 1, 1, tzinfo=UTC), datetime(2020, 1, 2, tzinfo=UTC)) == 0
    )
    assert estimate_rows({**META, "number_of_rows_approximate": None}, START, END) == 0


def test_pull_stores_parquet_and_logs(tmp_path: Path) -> None:
    api = FakeAPI(used=10)
    now = datetime(2026, 10, 17, 20, 0, tzinfo=UTC)
    result = pull(api, "ieso_lmp_x", START, END, data_dir=tmp_path, now=now)
    assert result.rows == 24
    assert result.path.name == "20250501T0000Z_20250502T0000Z__20261017T200000Z.parquet"
    assert pd.read_parquet(result.path)["lmp"].tolist() == list(range(24))
    assert api.calls[0]["page_size"] == 50_000
    with sqlite3.connect(tmp_path / "bronze" / "_gridstatus.sqlite") as db:
        rows = db.execute(
            "SELECT dataset, rows, estimated_rows, rows_used_before FROM pulls"
        ).fetchall()
    assert rows == [("ieso_lmp_x", 24, 101, 10)]


def test_pull_refuses_when_quota_would_pass_limit(tmp_path: Path) -> None:
    api = FakeAPI(used=349_950)  # +101 estimated > 70% of 500k
    with pytest.raises(QuotaError, match="70%"):
        pull(api, "ieso_lmp_x", START, END, data_dir=tmp_path)
    assert api.calls == []


@pytest.mark.parametrize(
    ("start", "end"),
    [(datetime(2025, 5, 1), END), (END, START)],  # naive; reversed
)
def test_pull_validates_range(tmp_path: Path, start: datetime, end: datetime) -> None:
    with pytest.raises(ValueError, match=r"aware|after"):
        pull(FakeAPI(), "x", start, end, data_dir=tmp_path)


def test_pull_rejects_non_frame(tmp_path: Path) -> None:
    api = FakeAPI()
    api.get_dataset = lambda dataset, **kw: [1, 2]  # type: ignore[method-assign]
    with pytest.raises(TypeError, match="DataFrame"):
        pull(api, "x", START, END, data_dir=tmp_path)
