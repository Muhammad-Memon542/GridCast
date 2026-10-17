"""Backtest inputs: the point-in-time feature matrix and the target table."""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from datetime import date, time, timedelta
from pathlib import Path

import pandas as pd

from gridcast.features.builder import (
    FeatureConfig,
    FeatureInputs,
    build_features,
    known_at,
    latest_per_ts,
)
from gridcast.timeutil.ieso import TS_DTYPE, issue_time_utc

DAY = "delivery_date"


def day_range(first: date, last: date) -> list[date]:
    return [first + timedelta(days=i) for i in range((last - first).days + 1)]


def feature_matrix(
    inputs: FeatureInputs,
    days: Iterable[date],
    config: FeatureConfig,
    issue_local: time,
    issue_day_offset: int = -1,
) -> pd.DataFrame:
    """Features for every target day, each built as of its own issue time."""
    parts = []
    for day in days:
        issue = issue_time_utc(day, issue_local, issue_day_offset)
        frame = build_features(inputs, day, issue, config)
        frame["issue_time_utc"] = pd.Timestamp(issue)
        parts.append(frame)
    out = pd.concat(parts, ignore_index=True)
    out["issue_time_utc"] = out["issue_time_utc"].astype(TS_DTYPE)
    return out


def targets_known_at(da_ozp: pd.DataFrame, issue: pd.Timestamp) -> pd.Series:
    """DA OZP by ts_utc as it was known at ``issue`` (preferred source, highest version)."""
    known = latest_per_ts(known_at(da_ozp, issue.to_pydatetime()))
    return known.set_index("ts_utc")["zonal_price"]


def truth(da_ozp: pd.DataFrame) -> pd.Series:
    """Evaluation truth: the highest version available now (pre-registration §2)."""
    return latest_per_ts(da_ozp).set_index("ts_utc")["zonal_price"]


def snapshot_id(data_dir: Path, tables: Iterable[str]) -> str:
    """Hash of the silver files a run reads (``data_snapshot_id`` on every result)."""
    digest = hashlib.sha256()
    for table in sorted(tables):
        for path in sorted((data_dir / "silver" / table).rglob("*.parquet")):
            digest.update(path.relative_to(data_dir).as_posix().encode())
            digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()[:16]
