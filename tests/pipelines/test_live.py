"""Forecast log and scorecard (M3-T10)."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from gridcast.models.base import QUANTILES
from gridcast.pipelines.live import (
    LiveForecast,
    PublicationError,
    default_target_day,
    write_log_entry,
)
from gridcast.pipelines.scorecard import load_log, recent_hours, score_days, write_scorecard
from gridcast.timeutil.ieso import he_to_ts_utc

DAY = date(2026, 11, 2)  # issued 2026-11-01 09:00 EST, the first day after DST ends


def _entry(day: date = DAY, centre: float = 50.0) -> LiveForecast:
    ts = he_to_ts_utc(pd.Series([day] * 24), pd.Series(range(1, 25)))
    wide = pd.DataFrame(
        centre + np.linspace(-20, 20, len(QUANTILES))[None, :].repeat(24, 0),
        index=pd.DatetimeIndex(ts),
        columns=list(QUANTILES),
    )
    return LiveForecast(
        target_day=day,
        issued_at_utc=datetime(2026, 11, 1, 14, tzinfo=UTC),
        model="lgbm+cqr",
        frozen_config_sha="abc1234",
        wide=wide,
    )


def test_log_entry_is_append_only_and_respects_the_bid_window(tmp_path: Path) -> None:
    on_time = datetime(2026, 11, 1, 14, 20, tzinfo=UTC)  # 09:20 EST
    path = write_log_entry(_entry(), tmp_path, now=on_time, data_dir=tmp_path)
    assert path == tmp_path / "forecasts" / "2026" / "11" / "2026-11-02.json"
    record = json.loads(path.read_text())
    assert record["issued_at_utc"] == "2026-11-01T14:00:00+00:00"
    assert len(record["hours"]) == 24
    assert record["hours"][0]["ts_utc"] == "2026-11-02T05:00:00+00:00"
    assert record["quantile_levels"] == list(QUANTILES)
    assert "Grid Status" in record["attribution"]
    assert json.loads((tmp_path / "latest.json").read_text()) == record
    with pytest.raises(PublicationError, match="append-only"):
        write_log_entry(_entry(), tmp_path, now=on_time, data_dir=tmp_path)
    late = datetime(2026, 11, 1, 15, 1, tzinfo=UTC)  # 10:01 EST
    with pytest.raises(PublicationError, match="bid window"):
        write_log_entry(_entry(date(2026, 11, 2)), tmp_path / "other", now=late, data_dir=tmp_path)


def test_default_target_day_is_tomorrow_in_toronto() -> None:
    assert default_target_day(datetime(2026, 11, 1, 3, 30, tzinfo=UTC)) == date(
        2026, 11, 1
    )  # 22:30 Oct 31
    assert default_target_day(datetime(2026, 11, 1, 14, 0, tzinfo=UTC)) == date(2026, 11, 2)


def test_scorecard(tmp_path: Path) -> None:
    on_time = datetime(2026, 11, 1, 14, 20, tzinfo=UTC)
    write_log_entry(_entry(), tmp_path, now=on_time, data_dir=tmp_path)
    write_log_entry(
        _entry(date(2026, 11, 3)),
        tmp_path,
        now=datetime(2026, 11, 2, 14, tzinfo=UTC),
        data_dir=tmp_path,
    )
    log = load_log(tmp_path)
    assert log["target_day"].nunique() == 2
    ts = he_to_ts_utc(pd.Series([DAY] * 24), pd.Series(range(1, 25)))
    truth = pd.Series(55.0, index=pd.DatetimeIndex(ts))  # only the first day has actuals
    days = score_days(log, truth)
    assert list(days["target_day"]) == [DAY]
    assert days["mae"].iloc[0] == pytest.approx(5.0)
    assert days["coverage_90"].iloc[0] == 1.0
    result = write_scorecard(tmp_path, truth, now=datetime(2026, 11, 3, tzinfo=UTC))
    assert (result["published_days"], result["scored_days"]) == (2, 1)
    assert result["since_start"]["mae"] == pytest.approx(5.0)
    assert "| Since start | 1 |" in (tmp_path / "scorecard.md").read_text()
    hours = result["recent_hours"]
    assert len(hours) == 48  # both published days, in time order
    assert hours[0] == {
        "ts_utc": ts.iloc[0].isoformat(),
        "q05": 32.0,
        "q50": 50.0,
        "q95": 68.0,
        "actual": 55.0,
    }
    assert hours[-1]["actual"] is None  # not yet published by IESO
    assert "Independent Electricity System Operator" in result["notice"]
    assert len(recent_hours(log, truth, days=1)) == 24
    (tmp_path / "none").mkdir()
    empty = write_scorecard(tmp_path / "none", truth)
    assert empty["published_days"] == 0
    assert empty["recent_hours"] == []
