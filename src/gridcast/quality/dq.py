"""Data-quality report (M1-T9): coverage, gaps, duplicates, ranges, versions, cross-source checks.

``make dq`` writes ``docs/results/dq_report.md``. Only counts and summary statistics are
written, never raw rows (Grid Status data must not be republished, ADR-001).
"""

from __future__ import annotations

import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd

from gridcast.config import REPO_ROOT, load_project_config
from gridcast.store import connect, tables

SINCE_2019 = "ts_utc >= TIMESTAMPTZ '2019-01-01 05:00:00+00'"

# label -> (table, timestamp column, extra WHERE, duplicate-key SQL)
COVERAGE = {
    "DA OZP (latest)": ("da_ozp_latest", "ts_utc", "", "SELECT 0"),
    "RT OZP hourly (any source)": (
        "rt_ozp_hourly",
        "ts_utc",
        "",
        "SELECT count(*) - count(DISTINCT (ts_utc, source)) FROM rt_ozp_hourly",
    ),
    "Demand (2019+)": (
        "demand",
        "ts_utc",
        SINCE_2019,
        "SELECT count(*) - count(DISTINCT ts_utc) FROM demand",
    ),
    "HOEP (2019+)": (
        "hoep",
        "ts_utc",
        SINCE_2019,
        "SELECT count(*) - count(DISTINCT ts_utc) FROM hoep",
    ),
    "Pre-dispatch OZP (targets with a vintage)": (
        "predisp_ozp",
        "target_ts_utc",
        "",
        "SELECT count(*) - count(DISTINCT (target_ts_utc, issued_at_utc, version))"
        " FROM predisp_ozp",
    ),
}

WEATHER_SERIES = """
SELECT point, lead_days, count(*) - count(DISTINCT valid_ts_utc) AS dupes
FROM weather_fcst GROUP BY point, lead_days ORDER BY point, lead_days
"""

PRICE_RANGE = """
SELECT min({col}) AS lo, max({col}) AS hi,
       count(*) FILTER (WHERE {col} < {floor} OR {col} > {cap}) AS n_out,
       count(*) FILTER (WHERE {col} < 0) AS neg,
       count(*) FILTER (WHERE {col} = {floor}) AS at_floor
FROM {table}
"""

VERSION_CHANGES = """
WITH ieso AS (SELECT * FROM da_ozp WHERE source = 'ieso'),
per_day AS (
    SELECT delivery_date, count(DISTINCT version) AS versions FROM ieso GROUP BY 1
),
pairs AS (
    SELECT a.delivery_date, abs(a.zonal_price - b.zonal_price) AS change
    FROM ieso a JOIN ieso b
      ON a.ts_utc = b.ts_utc
     AND b.version = (
        SELECT max(version) FROM ieso c WHERE c.ts_utc = a.ts_utc AND c.version < a.version
     )
)
SELECT (SELECT count(*) FROM per_day) AS days,
       (SELECT count(*) FROM per_day WHERE versions > 1) AS multi,
       (SELECT count(DISTINCT delivery_date) FROM pairs WHERE change > 0) AS changed,
       (SELECT coalesce(max(change), 0) FROM pairs) AS max_change
"""

CROSS_SOURCE = """
WITH i AS (
    SELECT * FROM {table} WHERE source = 'ieso'
    QUALIFY row_number() OVER (PARTITION BY ts_utc ORDER BY version DESC NULLS LAST) = 1
)
SELECT count(*) AS n,
       count(*) FILTER (WHERE abs(i.{col} - g.{col}) > 0.005) AS n_diff,
       coalesce(max(abs(i.{col} - g.{col})), 0) AS mx
FROM i JOIN {table} g ON g.ts_utc = i.ts_utc AND g.source = 'gridstatus'
"""


HOUR = pd.Timedelta(hours=1)


def missing_ranges(
    ts: pd.Series, step: pd.Timedelta = HOUR
) -> list[tuple[pd.Timestamp, pd.Timestamp, int]]:
    """Runs of missing timestamps between the first and last present one: (start, end, count)."""
    present = pd.DatetimeIndex(ts.dropna().unique()).sort_values()
    if len(present) < 2:
        return []
    missing = pd.date_range(present[0], present[-1], freq=step).difference(present)
    runs: list[tuple[pd.Timestamp, pd.Timestamp, int]] = []
    for t in missing:
        if runs and t - runs[-1][1] == step:
            start, _, count = runs[-1]
            runs[-1] = (start, t, count + 1)
        else:
            runs.append((t, t, 1))
    return runs


def _fmt_ranges(runs: list[tuple[pd.Timestamp, pd.Timestamp, int]], limit: int = 5) -> str:
    if not runs:
        return "none"
    longest = sorted(runs, key=lambda r: -r[2])[:limit]
    text = "; ".join(f"{a:%Y-%m-%d %H:%M} to {b:%Y-%m-%d %H:%M} ({n} h)" for a, b, n in longest)
    return text + (f"; and {len(runs) - limit} more" if len(runs) > limit else "")


def _coverage_row(name: str, ts: pd.Series, dupes: int) -> str:
    runs = missing_ranges(ts)
    n_missing = sum(r[2] for r in runs)
    return (
        f"| {name} | {ts.min():%Y-%m-%d %H:%M} | {ts.max():%Y-%m-%d %H:%M} | {ts.nunique():,}"
        f" | {n_missing:,} | {dupes:,} | {_fmt_ranges(runs)} |"
    )


def _one(con: duckdb.DuckDBPyConnection, sql: str, params: list[Any] | None = None) -> Any:
    row = con.execute(sql, params or []).fetchone()
    assert row is not None
    return row[0]


def _row(con: duckdb.DuckDBPyConnection, sql: str) -> pd.Series:
    return con.execute(sql).df().iloc[0]


def _git_sha() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return "unknown"
    return out.stdout.strip()


def _coverage(con: duckdb.DuckDBPyConnection, present: set[str]) -> list[str]:
    lines = [
        "## Hourly coverage",
        "",
        "Missing hours are counted between each series' first and last timestamp (UTC).",
        "",
        "| Series | First | Last | Hours | Missing | Duplicate keys | Longest gaps |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for label, (table, col, where, dupes_sql) in COVERAGE.items():
        if table in present:
            clause = f" WHERE {where}" if where else ""
            ts = con.execute(f"SELECT DISTINCT {col} AS ts FROM {table}{clause}").df()["ts"]
            lines.append(_coverage_row(label, ts, int(_one(con, dupes_sql))))
    if "weather_fcst" in present:
        for point, lead, dupes in con.execute(WEATHER_SERIES).fetchall():
            ts = con.execute(
                "SELECT valid_ts_utc AS ts FROM weather_fcst WHERE point = ? AND lead_days = ?",
                [point, int(lead)],
            ).df()["ts"]
            lines.append(_coverage_row(f"Weather {point} lead {lead}", ts, int(dupes)))
    return lines


def _ranges(con: duckdb.DuckDBPyConnection, present: set[str]) -> list[str]:
    market = load_project_config().market
    bounds = f"[{market.price_floor:g}, {market.price_cap:g}]"
    lines = ["## Ranges", ""]
    for label, table, col, unit in [
        ("DA OZP", "da_ozp_latest", "zonal_price", "hours"),
        ("RT OZP 5-min", "rt_ozp_5min", "lmp", "intervals"),
    ]:
        if table not in present:
            continue
        sql = PRICE_RANGE.format(
            col=col, table=table, floor=market.price_floor, cap=market.price_cap
        )
        r = _row(con, sql)
        lines.append(
            f"- **{label}:** min {r['lo']:.2f}, max {r['hi']:.2f} $/MWh;"
            f" {int(r['n_out'])} {unit} outside the settlement bounds {bounds};"
            f" {int(r['neg'])} negative; {int(r['at_floor'])} at the floor."
        )
    if "rt_ozp_hourly" in present:
        partial = int(_one(con, "SELECT count(*) FROM rt_ozp_hourly WHERE n_intervals < 12"))
        lines.append(f"- **RT OZP hourly:** {partial} hours built from fewer than 12 intervals.")
    return lines


def _versions(con: duckdb.DuckDBPyConnection) -> list[str]:
    v = _row(con, VERSION_CHANGES)
    return [
        "## Versions (IESO day-ahead OZP)",
        "",
        f"- {int(v['days'])} delivery days collected; {int(v['multi'])} have more than one"
        f" version; in {int(v['changed'])} of those a later version changed a price"
        f" (largest change {v['max_change']:.2f} $/MWh). Other re-publications are identical"
        " (V-11).",
    ]


def _cross_source(con: duckdb.DuckDBPyConnection, present: set[str]) -> list[str]:
    lines = ["## Cross-source agreement (IESO vs Grid Status)", ""]
    for label, table, col, unit in [
        ("DA OZP", "da_ozp", "zonal_price", "hours"),
        ("RT OZP 5-min", "rt_ozp_5min", "lmp", "intervals"),
    ]:
        if table in present:
            r = _row(con, CROSS_SOURCE.format(table=table, col=col))
            lines.append(
                f"- **{label}:** {int(r['n']):,} overlapping {unit}; {int(r['n_diff']):,} differ"
                f" by more than 0.005 $/MWh (max {r['mx']:.2f})."
            )
    return lines


def build_report(data_dir: Path, now: datetime | None = None) -> str:
    con = connect(data_dir)
    present = tables(con)
    stamp = (now or datetime.now(UTC)).strftime("%Y-%m-%d %H:%M")
    lines = [
        "# Data-quality report",
        "",
        f"Generated {stamp} UTC by `make dq` at commit `{_git_sha()}`. Counts only; no raw rows.",
        "",
        "## Silver tables",
        "",
        "| Table | Rows |",
        "| --- | --- |",
        *[f"| `{t}` | {int(_one(con, f'SELECT count(*) FROM {t}')):,} |" for t in sorted(present)],
        "",
        *_coverage(con, present),
        "",
        *_ranges(con, present),
    ]
    if "da_ozp" in present:
        lines += ["", *_versions(con)]
    lines += ["", *_cross_source(con, present), ""]
    return "\n".join(lines)


def write_report(data_dir: Path, out: Path) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(build_report(data_dir), encoding="utf-8")
    return out
