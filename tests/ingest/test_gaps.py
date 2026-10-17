from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from gridcast.ingest.collector import CollectorConfig, ReportSpec
from gridcast.ingest.gaps import gap_report, render
from gridcast.ingest.manifest import FileRecord, Manifest

START = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
NOW = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)  # complete IESO days: Oct 1-4


def _spec(name: str, versions: str = "all", check_gaps: bool = True) -> ReportSpec:
    return ReportSpec.model_validate(
        {
            "name": name,
            "dataset": "D",
            "every_hours": 1,
            "versions": versions,
            "lookback_days": 120,
            "check_gaps": check_gaps,
        }
    )


def _add(m: Manifest, report: str, key: str, version: int | None, status: int = 200) -> None:
    m.add_file(
        FileRecord(
            report=report,
            filename=f"PUB_{report}_{key}_v{version}.xml",
            period_key=key,
            version=version,
            listing_modified_utc=START,
            url="u",
            fetched_at_utc=START,
            http_status=status,
            run_id="r",
        )
    )


@pytest.fixture
def manifest(tmp_path: Path) -> Manifest:
    m = Manifest(tmp_path / "m.sqlite")
    m.add_listing("Daily", "r", START, 10, ok=True)
    m.add_listing("Daily", "r", NOW, 10, ok=True)
    for day in ("20261001", "20261002", "20261004"):  # Oct 3 missing
        for v in (1, 2):
            _add(m, "Daily", day, v)
    _add(m, "Daily", "20261004", 4)  # v3 missing on Oct 4
    for day in ("20261001", "20261002", "20261003", "20261004"):
        for hour in range(1, 25):
            if not (day == "20261002" and hour == 24):
                _add(m, "Hourly", f"{day}{hour:02d}", 12)
    _add(m, "Yearly", "2026", 300)
    _add(m, "Skipper", "20261001", 1)
    return m


def test_gap_report_finds_missing_days_hours_and_versions(manifest: Manifest) -> None:
    config = CollectorConfig(
        base_url="https://x.test/",
        reports=(
            _spec("Daily"),
            _spec("Hourly", "latest"),
            _spec("Yearly", "latest"),
            _spec("Skipper", check_gaps=False),
        ),
    )
    report = gap_report(manifest, config, days=7, now=NOW)
    assert report.window == [date(2026, 10, d) for d in (1, 2, 3, 4)]
    daily, hourly, yearly, skipper = report.reports
    assert daily.missing_days == [date(2026, 10, 3)]
    assert daily.version_holes == {"20261004": [3]}
    assert daily.clean_days == {date(2026, 10, 1), date(2026, 10, 2)}
    assert hourly.incomplete_hours == {date(2026, 10, 2): 23}
    assert (yearly.granularity, skipper.granularity) == ("period", "unchecked")
    assert report.gap_free_streak == 0  # Oct 4 has a version hole

    text = render(report)
    assert "| Daily | daily |" in text
    assert "2026-10-02: 23/24" in text
    assert "20261004: [3]" in text


def test_streak_counts_back_from_yesterday(manifest: Manifest) -> None:
    config = CollectorConfig(base_url="https://x.test/", reports=(_spec("Hourly", "latest"),))
    assert gap_report(manifest, config, days=7, now=NOW).gap_free_streak == 2  # Oct 3, 4


def test_window_waits_for_a_listing_after_the_day_ends(manifest: Manifest) -> None:
    # "Daily" was listed at NOW, but "Late" only before Oct 4 ended, so Oct 4 is not judged.
    manifest.add_listing("Late", "r", datetime(2026, 10, 4, 23, 0, tzinfo=UTC), 1, ok=True)
    config = CollectorConfig(
        base_url="https://x.test/", reports=(_spec("Hourly", "latest"), _spec("Late"))
    )
    report = gap_report(manifest, config, days=7, now=NOW)
    assert report.window[-1] == date(2026, 10, 3)


def test_empty_manifest(tmp_path: Path) -> None:
    config = CollectorConfig(base_url="https://x.test/", reports=(_spec("Daily"),))
    with Manifest(tmp_path / "m.sqlite") as m:
        report = gap_report(m, config, days=7, now=NOW)
        assert m.run_statuses() == {}
    assert report.gap_free_streak == 0
    assert "never run" in render(report)
