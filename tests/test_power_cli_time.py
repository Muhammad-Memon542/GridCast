from __future__ import annotations

import json
import subprocess
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
import structlog

from gridcast import cli
from gridcast.config import load_project_config
from gridcast.ingest import power
from gridcast.logs import configure_logging
from gridcast.timeutil.ieso import IESO_REPORT_TZ, ieso_date, ieso_naive_to_utc

PMSET_AC = "Now drawing from 'AC Power'\n -InternalBattery-0 (id=1)\t80%; charging;\n"
PMSET_BATTERY = "Now drawing from 'Battery Power'\n -InternalBattery-0 (id=1)\t48%; discharging;\n"


# --------------------------------------------------------------------- power


@pytest.mark.parametrize(
    ("output", "expected"), [(PMSET_AC, True), (PMSET_BATTERY, False), ("", False)]
)
def test_parse_pmset(output: str, expected: bool) -> None:
    assert power.parse_pmset(output) is expected


def test_non_mac_counts_as_mains(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(power.sys, "platform", "linux")
    assert power.on_ac_power() is True


def test_mac_reads_pmset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(power.sys, "platform", "darwin")
    monkeypatch.setattr(
        power.subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout=PMSET_BATTERY, stderr=""),
    )
    assert power.on_ac_power() is False


def test_mac_without_pmset_assumes_battery(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*args: object, **kwargs: object) -> None:
        raise FileNotFoundError("pmset")

    monkeypatch.setattr(power.sys, "platform", "darwin")
    monkeypatch.setattr(power.subprocess, "run", boom)
    assert power.on_ac_power() is False


# ------------------------------------------------------------------ timeutil


def test_ieso_clock_is_fixed_est_and_matches_config() -> None:
    assert IESO_REPORT_TZ.utcoffset(None) == timedelta(hours=-5)
    assert load_project_config().market.report_utc_offset_hours == -5
    # 12:31 EST in October (EDT season) is 17:31 UTC, not 16:31.
    assert ieso_naive_to_utc(datetime(2026, 10, 14, 12, 31)) == datetime(
        2026, 10, 14, 17, 31, tzinfo=UTC
    )
    assert ieso_date(datetime(2026, 10, 15, 4, 30, tzinfo=UTC)) == date(2026, 10, 14)


def test_ieso_clock_rejects_wrong_awareness() -> None:
    with pytest.raises(ValueError, match="naive"):
        ieso_naive_to_utc(datetime(2026, 1, 1, tzinfo=UTC))
    with pytest.raises(ValueError, match="aware"):
        ieso_date(datetime(2026, 1, 1))


# ------------------------------------------------------------------- logging


def test_log_lines_are_json_with_run_id(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging("INFO")
    structlog.contextvars.bind_contextvars(run_id="r-123")
    try:
        structlog.get_logger("t").info("hello", n=1)
        structlog.get_logger("t").debug("hidden")
    finally:
        structlog.contextvars.clear_contextvars()
        structlog.reset_defaults()
    lines = capsys.readouterr().out.strip().splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0]) | {"timestamp": None} == {
        "event": "hello",
        "n": 1,
        "run_id": "r-123",
        "level": "info",
        "timestamp": None,
    }


# ----------------------------------------------------------------------- cli


@pytest.fixture
def data_dir(clean_env: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    clean_env.setenv("GRIDCAST_DATA_DIR", str(tmp_path))
    yield tmp_path
    structlog.reset_defaults()


def test_cli_gaps_before_first_run(data_dir: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["gaps"]) == 1
    assert "never run" in capsys.readouterr().out


def test_cli_collect_on_battery_then_gaps(
    data_dir: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("gridcast.ingest.collector.on_ac_power", lambda: False)
    assert cli.main(["collect", "--require-ac", "--report", "Demand"]) == 0
    assert '"status": "skipped_on_battery"' in capsys.readouterr().out
    assert cli.main(["gaps", "--days", "3"]) == 0
    assert "never run" in capsys.readouterr().out


def test_cli_partial_run_exits_nonzero(data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from gridcast.ingest.collector import RunSummary

    monkeypatch.setattr(cli, "run_collection", lambda *a, **k: RunSummary("r", "partial"))
    assert cli.main(["collect"]) == 1
