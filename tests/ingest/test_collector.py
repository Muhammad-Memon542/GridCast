from __future__ import annotations

import gzip
import sqlite3
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import httpx
import pytest
import respx
from pydantic import ValidationError

from gridcast.config import REPO_ROOT, Settings
from gridcast.ingest.collector import (
    CollectorConfig,
    ReportSpec,
    _exclusive_lock,
    extract_created_at,
    is_due,
    load_collector_config,
    run_collection,
    select_downloads,
)
from gridcast.ingest.http import PoliteClient
from gridcast.ingest.ieso_index import parse_listing
from gridcast.ingest.manifest import Manifest

FIXTURES = Path(__file__).parents[1] / "fixtures" / "ieso"
DA = "DAHourlyOntarioZonalPrice"
BASE = "https://reports.example.test/public/"
NOW = datetime(2026, 10, 15, 18, 0, tzinfo=UTC)
INDEX = (FIXTURES / f"{DA}_index.html").read_text()
P = "PUB_DAHourlyOntarioZonalPrice"
FILES = {  # served name -> recorded fixture
    f"{P}_20260924_v1.xml": f"{P}_20260924_v1.xml",
    f"{P}_20260924_v2.xml": f"{P}_20260924_v2.xml",
    f"{P}_20260924_v3.xml": f"{P}_20260924_v3.xml",
    # no recording of 20260925_v1; any distinct real file will do
    f"{P}_20260925_v1.xml": f"{P}_20261015_v1.xml",
}


def _spec(name: str = DA, **overrides: object) -> ReportSpec:
    fields: dict[str, object] = {
        "name": name,
        "dataset": "D01",
        "every_hours": 1,
        "versions": "all",
        "lookback_days": 120,
    }
    fields.update(overrides)
    return ReportSpec.model_validate(fields)


def _config(*specs: ReportSpec) -> CollectorConfig:
    return CollectorConfig(base_url=BASE, reports=specs or (_spec(),))


@pytest.fixture
def settings(clean_env: pytest.MonkeyPatch, tmp_path: Path) -> Settings:
    clean_env.setenv("GRIDCAST_DATA_DIR", str(tmp_path / "data"))
    return Settings(_env_file=None)


@pytest.fixture
def client() -> Iterator[PoliteClient]:
    with PoliteClient("GridCast-test/0", sleep=lambda _: None) as c:
        yield c


@pytest.fixture
def ieso() -> Iterator[respx.MockRouter]:
    with respx.mock(base_url=BASE, assert_all_called=False) as router:
        router.get(f"{DA}/", name="listing").mock(return_value=httpx.Response(200, text=INDEX))
        for served, recorded in FILES.items():
            router.get(f"{DA}/{served}", name=served).mock(
                return_value=httpx.Response(200, content=(FIXTURES / recorded).read_bytes())
            )
        yield router


def _manifest_rows(settings: Settings, sql: str) -> list[tuple[object, ...]]:
    with sqlite3.connect(settings.data_dir / "bronze" / "_manifest.sqlite") as db:
        return db.execute(sql).fetchall()


# ----------------------------------------------------------------- selection


def test_select_downloads_keeps_versions_and_skips_aliases() -> None:
    entries = parse_listing(DA, INDEX)
    picked = [e.file.filename for e in select_downloads(entries, _spec(), date(2026, 10, 15))]
    assert picked == [
        "PUB_DAHourlyOntarioZonalPrice_20260924_v1.xml",
        "PUB_DAHourlyOntarioZonalPrice_20260924_v2.xml",
        "PUB_DAHourlyOntarioZonalPrice_20260924_v3.xml",
        "PUB_DAHourlyOntarioZonalPrice_20260925_v1.xml",
    ]


def test_select_downloads_latest_and_lookback() -> None:
    entries = parse_listing(DA, INDEX)
    latest = select_downloads(entries, _spec(versions="latest"), date(2026, 10, 15))
    assert [e.file.filename[-15:] for e in latest] == ["20260924_v3.xml", "20260925_v1.xml"]
    recent = select_downloads(entries, _spec(lookback_days=20), date(2026, 10, 15))
    assert [e.file.key for e in recent] == ["20260925"]


def test_select_downloads_keeps_unversioned_files_when_no_versions_exist() -> None:
    html = INDEX.replace("_v1.xml", "_x.xml").replace("_v2.xml", "_x.xml").replace("_v3", "_x")
    entries = parse_listing(DA, html)
    picked = select_downloads(entries, _spec(), date(2026, 10, 15))
    assert [e.file.filename for e in picked] == [
        "PUB_DAHourlyOntarioZonalPrice_20260924.xml",
        "PUB_DAHourlyOntarioZonalPrice_20260925.xml",
    ]


# ---------------------------------------------------------------- CreatedAt


def test_created_at_from_recorded_xml_is_est() -> None:
    content = (FIXTURES / "PUB_DAHourlyOntarioZonalPrice_20260924_v1.xml").read_bytes()
    # CreatedAt 2026-09-23T12:33:10 (EST); server Last-Modified 17:34:16 GMT.
    assert extract_created_at(content) == datetime(2026, 9, 23, 17, 33, 10, tzinfo=UTC)


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        (
            b"\\\\Hourly Demand Report,,,\n\\\\Created at 2026-10-15 07:30:13,,,\n",
            datetime(2026, 10, 15, 12, 30, 13, tzinfo=UTC),
        ),
        (
            b"<CreatedAt>2026-10-14T12:31:28-04:00</CreatedAt>",
            datetime(2026, 10, 14, 16, 31, 28, tzinfo=UTC),
        ),
        (b"<CreatedAt>not a time</CreatedAt>", None),
        (b"no header at all", None),
    ],
)
def test_created_at_variants(content: bytes, expected: datetime | None) -> None:
    assert extract_created_at(content) == expected


# --------------------------------------------------------------- end to end


def test_collect_stores_bronze_and_manifest(
    settings: Settings, client: PoliteClient, ieso: respx.MockRouter
) -> None:
    summary = run_collection(settings, _config(), client=client, now=lambda: NOW)
    assert summary.status == "ok"
    assert summary.downloaded == 4

    rows = _manifest_rows(
        settings,
        "SELECT filename, version, sha256, path, doc_created_at_utc FROM files ORDER BY filename",
    )
    assert [r[1] for r in rows] == [1, 2, 3, 1]
    v1 = rows[0]
    stored = settings.data_dir / str(v1[3])
    assert stored.parent == settings.data_dir / "bronze" / "ieso" / DA / "2026-09-24"
    assert stored.name == "PUB_DAHourlyOntarioZonalPrice_20260924_v1.xml__20261015T180000Z.xml.gz"
    original = (FIXTURES / "PUB_DAHourlyOntarioZonalPrice_20260924_v1.xml").read_bytes()
    assert gzip.decompress(stored.read_bytes()) == original
    assert v1[4] == "2026-09-23T17:33:10+00:00"


def test_second_run_downloads_nothing(
    settings: Settings, client: PoliteClient, ieso: respx.MockRouter
) -> None:
    run_collection(settings, _config(), client=client, now=lambda: NOW)
    calls_after_first = ieso.calls.call_count

    again = run_collection(settings, _config(), client=client, force=True, now=lambda: NOW)
    assert again.downloaded == 0
    assert ieso.calls.call_count == calls_after_first + 1  # just the listing

    not_due = run_collection(settings, _config(), client=client, now=lambda: NOW)
    assert not_due.reports == []
    assert ieso.calls.call_count == calls_after_first + 1


def test_relisted_identical_file_is_recorded_not_stored_twice(
    settings: Settings, client: PoliteClient, ieso: respx.MockRouter
) -> None:
    run_collection(settings, _config(), client=client, now=lambda: NOW)
    ieso.routes["listing"].mock(
        return_value=httpx.Response(
            200, text=INDEX.replace("23-Sep-2026 14:29", "23-Sep-2026 15:00")
        )
    )
    summary = run_collection(settings, _config(), client=client, force=True, now=lambda: NOW)
    assert (summary.downloaded, summary.reports[0].duplicates) == (0, 1)
    dup = _manifest_rows(settings, "SELECT duplicate_of FROM files WHERE duplicate_of IS NOT NULL")
    assert len(dup) == 1
    assert len(list((settings.data_dir / "bronze" / "ieso").rglob("*.gz"))) == 4


def test_purged_file_is_recorded_once_and_not_an_error(
    settings: Settings, client: PoliteClient, ieso: respx.MockRouter
) -> None:
    ieso.routes["PUB_DAHourlyOntarioZonalPrice_20260924_v2.xml"].mock(
        return_value=httpx.Response(404)
    )
    summary = run_collection(settings, _config(), client=client, now=lambda: NOW)
    assert (summary.status, summary.downloaded, summary.errors) == ("ok", 3, 0)
    rerun = run_collection(settings, _config(), client=client, force=True, now=lambda: NOW)
    assert rerun.reports[0].selected == 0


def test_failed_download_leaves_report_due(
    settings: Settings, client: PoliteClient, ieso: respx.MockRouter
) -> None:
    ieso.routes["PUB_DAHourlyOntarioZonalPrice_20260925_v1.xml"].mock(
        return_value=httpx.Response(500)
    )
    summary = run_collection(settings, _config(), client=client, now=lambda: NOW)
    assert (summary.status, summary.downloaded, summary.errors) == ("partial", 3, 1)
    with Manifest(settings.data_dir / "bronze" / "_manifest.sqlite") as m:
        assert is_due(m, _spec(), NOW)


def test_non_200_non_404_file_counts_as_error(
    settings: Settings, client: PoliteClient, ieso: respx.MockRouter
) -> None:
    ieso.routes["PUB_DAHourlyOntarioZonalPrice_20260924_v1.xml"].mock(
        return_value=httpx.Response(403)
    )
    summary = run_collection(settings, _config(), client=client, now=lambda: NOW)
    assert (summary.status, summary.errors) == ("partial", 1)


@pytest.mark.parametrize("listing", [httpx.Response(403), httpx.Response(503)])
def test_failed_listing_is_recorded(
    settings: Settings, client: PoliteClient, ieso: respx.MockRouter, listing: httpx.Response
) -> None:
    ieso.routes["listing"].mock(return_value=listing)
    summary = run_collection(settings, _config(), client=client, now=lambda: NOW)
    assert (summary.status, summary.downloaded) == ("partial", 0)
    ok = _manifest_rows(settings, "SELECT ok FROM listings")
    assert ok == [(0,)]


def test_on_battery_nothing_is_fetched(
    settings: Settings, client: PoliteClient, ieso: respx.MockRouter
) -> None:
    summary = run_collection(
        settings,
        _config(),
        client=client,
        require_ac=True,
        power_check=lambda: False,
        now=lambda: NOW,
    )
    assert summary.status == "skipped_on_battery"
    assert ieso.calls.call_count == 0
    assert _manifest_rows(settings, "SELECT status, on_ac_power FROM runs") == [
        ("skipped_on_battery", 0)
    ]


def test_unplugging_stops_before_the_next_report(
    settings: Settings, client: PoliteClient, ieso: respx.MockRouter
) -> None:
    readings = iter([True, False])  # plugged in at start, unplugged after report 1
    ieso.get("Demand/", name="demand").mock(return_value=httpx.Response(200, text=""))
    config = _config(_spec(), _spec("Demand"))
    summary = run_collection(
        settings,
        config,
        client=client,
        require_ac=True,
        power_check=lambda: next(readings),
        now=lambda: NOW,
    )
    assert summary.status == "stopped_on_battery"
    assert [r.report for r in summary.reports] == [DA]
    assert not ieso.routes["demand"].called


def test_unplugging_mid_report_stops_and_resumes_later(
    settings: Settings,
    client: PoliteClient,
    ieso: respx.MockRouter,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("gridcast.ingest.collector.POWER_CHECK_EVERY", 2)
    readings = iter([True, False])
    summary = run_collection(
        settings,
        _config(),
        client=client,
        require_ac=True,
        power_check=lambda: next(readings),
        now=lambda: NOW,
    )
    assert (summary.status, summary.downloaded) == ("stopped_on_battery", 2)
    resumed = run_collection(
        settings,
        _config(),
        client=client,
        require_ac=True,
        power_check=lambda: True,
        now=lambda: NOW + timedelta(minutes=5),
    )
    assert (resumed.status, resumed.downloaded) == ("ok", 2)


def test_only_and_cadence(settings: Settings, client: PoliteClient, ieso: respx.MockRouter) -> None:
    config = _config(_spec(), _spec("Demand"))
    summary = run_collection(settings, config, client=client, only={DA}, now=lambda: NOW)
    assert [r.report for r in summary.reports] == [DA]
    with Manifest(settings.data_dir / "bronze" / "_manifest.sqlite") as m:
        # hourly report with 10 minutes of slack: due again from 50 minutes on
        assert not is_due(m, _spec(), NOW + timedelta(minutes=45))
        assert is_due(m, _spec(), NOW + timedelta(minutes=50))


def test_concurrent_run_reports_busy(settings: Settings, client: PoliteClient) -> None:
    lock = settings.data_dir / "bronze" / ".collector.lock"
    with _exclusive_lock(lock) as held:
        assert held
        summary = run_collection(settings, _config(), client=client, now=lambda: NOW)
    assert summary.status == "busy"


def test_default_client_is_created_and_closed(
    settings: Settings, ieso: respx.MockRouter, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("gridcast.ingest.http.time.sleep", lambda _: None)
    summary = run_collection(settings, _config(), now=lambda: NOW)
    assert summary.downloaded == 4
    assert ieso.routes["listing"].calls[0].request.headers["User-Agent"].startswith("GridCast/")


# ------------------------------------------------------------------- config


def test_repo_collector_config_is_valid() -> None:
    config = load_collector_config(REPO_ROOT / "configs" / "collector.yaml")
    names = [r.name for r in config.reports]
    assert names[0] == "PredispHourlyOntarioZonalPrice"  # scarcest history first
    assert {"DAHourlyOntarioZonalPrice", "RealtimeOntarioZonalPrice", "Adequacy3"} <= set(names)


def test_default_collector_config_path(clean_env: pytest.MonkeyPatch) -> None:
    assert load_collector_config() == load_collector_config(
        REPO_ROOT / "configs" / "collector.yaml"
    )


def test_duplicate_report_names_rejected() -> None:
    with pytest.raises(ValidationError, match="unique"):
        _config(_spec(), _spec())
