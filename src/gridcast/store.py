"""DuckDB views over silver Parquet (plan §7.4)."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import duckdb
import pandas as pd

from gridcast.silver import TS_COLUMN

LATEST_ORDER = "CASE source WHEN 'ieso' THEN 0 ELSE 1 END, version DESC NULLS LAST"


def connect(data_dir: Path) -> duckdb.DuckDBPyConnection:
    """In-memory DuckDB with one view per silver table, plus ``da_ozp_latest``."""
    con = duckdb.connect()
    con.execute("SET TimeZone = 'UTC'")
    for table in TS_COLUMN:
        root = data_dir / "silver" / table
        if any(root.glob("month=*/*.parquet")):
            glob = str(root / "month=*" / "*.parquet").replace("'", "''")
            con.execute(
                f"CREATE VIEW {table} AS SELECT * EXCLUDE (month) "
                f"FROM read_parquet('{glob}', hive_partitioning = true)"
            )
    if "da_ozp" in tables(con):
        con.execute(
            "CREATE VIEW da_ozp_latest AS SELECT * FROM da_ozp "
            f"QUALIFY row_number() OVER (PARTITION BY ts_utc ORDER BY {LATEST_ORDER}) = 1"
        )
    return con


def tables(con: duckdb.DuckDBPyConnection) -> set[str]:
    return {
        row[0]
        for row in con.execute("SELECT view_name FROM duckdb_views() WHERE NOT internal").fetchall()
    }


def da_ozp_asof(con: duckdb.DuckDBPyConnection, t: datetime) -> pd.DataFrame:
    """Per ``ts_utc``, the preferred version with ``available_at_utc <= t``."""
    sql = (
        "SELECT * FROM da_ozp WHERE available_at_utc <= ? QUALIFY row_number() OVER"
        f" (PARTITION BY ts_utc ORDER BY {LATEST_ORDER}) = 1 ORDER BY ts_utc"
    )
    return con.execute(sql, [t]).df()
