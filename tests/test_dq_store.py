from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from gridcast import cli
from gridcast.quality.dq import build_report, missing_ranges
from gridcast.silver import build_all
from gridcast.store import connect, da_ozp_asof, tables
from test_silver import data_dir  # noqa: F401  (fixture)


def test_missing_ranges_finds_runs() -> None:
    ts = pd.Series(
        pd.to_datetime(
            ["2026-01-01 00:00", "2026-01-01 01:00", "2026-01-01 04:00", "2026-01-01 06:00"],
            utc=True,
        )
    )
    runs = missing_ranges(ts)
    assert [(a.hour, b.hour, n) for a, b, n in runs] == [(2, 3, 2), (5, 5, 1)]
    assert missing_ranges(ts.iloc[:2]) == []
    assert missing_ranges(ts.iloc[:1]) == []


def test_store_views_and_asof(data_dir: Path) -> None:  # noqa: F811
    build_all(data_dir)
    con = connect(data_dir)
    assert {"da_ozp", "da_ozp_latest", "demand", "weather_fcst"} <= tables(con)
    latest = con.execute(
        "SELECT version FROM da_ozp_latest WHERE ts_utc = TIMESTAMPTZ '2026-09-24 05:00:00+00'"
    ).fetchone()
    assert latest == (3,)
    # Before v3 (19:27 UTC) only v1 was known.
    early = da_ozp_asof(con, datetime(2026, 9, 23, 18, tzinfo=UTC))
    row = early[early.ts_utc == pd.Timestamp("2026-09-24 05:00", tz="UTC")]
    assert row.version.tolist() == [1]


def test_report_sections(data_dir: Path) -> None:  # noqa: F811
    build_all(data_dir)
    text = build_report(data_dir, now=datetime(2026, 10, 17, tzinfo=UTC))
    for heading in (
        "## Silver tables",
        "## Hourly coverage",
        "## Ranges",
        "## Versions",
        "## Cross-source",
    ):
        assert heading in text
    assert "| `da_ozp` | 96 |" in text
    assert "1 have more than one version; in 0 of those" in text  # 2026-09-24 republications
    assert "Demand (2019+)" in text


def test_cli_silver_and_dq(data_dir: Path, clean_env, tmp_path: Path, capsys) -> None:  # noqa: F811
    clean_env.setenv("GRIDCAST_DATA_DIR", str(data_dir))
    assert cli.main(["silver"]) == 0
    out = tmp_path / "dq.md"
    assert cli.main(["dq", "--out", str(out)]) == 0
    assert out.read_text().startswith("# Data-quality report")
    assert "da_ozp" in capsys.readouterr().out
