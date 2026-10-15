"""Bronze manifest: one row per file observation, plus listing and run history.

SQLite rather than the single Parquet file sketched in plan §7.2: the collector appends
one row per download, and a transactional store survives a run that dies half-way
(ADR-010). A Parquet export for DuckDB arrives with the silver parsers (M1-T4).
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
    id INTEGER PRIMARY KEY,
    report TEXT NOT NULL,
    filename TEXT NOT NULL,
    period_key TEXT,
    version INTEGER,
    listing_modified_utc TEXT NOT NULL,
    url TEXT NOT NULL,
    fetched_at_utc TEXT NOT NULL,
    http_status INTEGER NOT NULL,
    sha256 TEXT,
    bytes INTEGER,
    path TEXT,
    duplicate_of INTEGER REFERENCES files(id),
    doc_created_at_utc TEXT,
    run_id TEXT NOT NULL,
    UNIQUE (report, filename, listing_modified_utc)
);
CREATE INDEX IF NOT EXISTS files_report_sha ON files (report, sha256);
CREATE INDEX IF NOT EXISTS files_report_key ON files (report, period_key);
CREATE TABLE IF NOT EXISTS listings (
    report TEXT NOT NULL,
    run_id TEXT NOT NULL,
    listed_at_utc TEXT NOT NULL,
    entries INTEGER NOT NULL,
    ok INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    started_at_utc TEXT NOT NULL,
    finished_at_utc TEXT,
    status TEXT NOT NULL,
    on_ac_power INTEGER,
    files_downloaded INTEGER NOT NULL DEFAULT 0,
    bytes_downloaded INTEGER NOT NULL DEFAULT 0,
    errors INTEGER NOT NULL DEFAULT 0
);
"""


def _ts(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("manifest timestamps must be timezone-aware")
    return value.isoformat(timespec="seconds")


@dataclass(frozen=True)
class FileRecord:
    report: str
    filename: str
    period_key: str | None
    version: int | None
    listing_modified_utc: datetime
    url: str
    fetched_at_utc: datetime
    http_status: int
    run_id: str
    sha256: str | None = None
    bytes: int | None = None
    path: str | None = None
    duplicate_of: int | None = None
    doc_created_at_utc: datetime | None = None


@dataclass(frozen=True)
class StoredFile:
    report: str
    period_key: str | None
    version: int | None
    fetched_at_utc: datetime


class Manifest:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._db = sqlite3.connect(path, isolation_level=None)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.executescript(_SCHEMA)

    def close(self) -> None:
        self._db.close()

    def __enter__(self) -> Manifest:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ---------------------------------------------------------------- files
    def seen(self, report: str, filename: str, listing_modified_utc: datetime) -> bool:
        row = self._db.execute(
            "SELECT 1 FROM files WHERE report=? AND filename=? AND listing_modified_utc=?",
            (report, filename, _ts(listing_modified_utc)),
        ).fetchone()
        return row is not None

    def find_by_sha(self, report: str, sha256: str) -> tuple[int, str] | None:
        row = self._db.execute(
            "SELECT id, path FROM files WHERE report=? AND sha256=? AND duplicate_of IS NULL"
            " AND path IS NOT NULL ORDER BY id LIMIT 1",
            (report, sha256),
        ).fetchone()
        return (int(row[0]), str(row[1])) if row is not None else None

    def add_file(self, rec: FileRecord) -> int:
        cur = self._db.execute(
            "INSERT INTO files (report, filename, period_key, version, listing_modified_utc,"
            " url, fetched_at_utc, http_status, sha256, bytes, path, duplicate_of,"
            " doc_created_at_utc, run_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                rec.report,
                rec.filename,
                rec.period_key,
                rec.version,
                _ts(rec.listing_modified_utc),
                rec.url,
                _ts(rec.fetched_at_utc),
                rec.http_status,
                rec.sha256,
                rec.bytes,
                rec.path,
                rec.duplicate_of,
                _ts(rec.doc_created_at_utc) if rec.doc_created_at_utc is not None else None,
                rec.run_id,
            ),
        )
        assert cur.lastrowid is not None
        return cur.lastrowid

    def stored_files(self, report: str) -> list[StoredFile]:
        """Successfully fetched files of ``report`` that carry a period key."""
        rows = self._db.execute(
            "SELECT report, period_key, version, fetched_at_utc FROM files"
            " WHERE report=? AND http_status=200 AND period_key IS NOT NULL",
            (report,),
        ).fetchall()
        return [StoredFile(r[0], r[1], r[2], datetime.fromisoformat(r[3])) for r in rows]

    # -------------------------------------------------------------- listings
    def add_listing(
        self, report: str, run_id: str, listed_at_utc: datetime, entries: int, ok: bool
    ) -> None:
        self._db.execute(
            "INSERT INTO listings VALUES (?,?,?,?,?)",
            (report, run_id, _ts(listed_at_utc), entries, int(ok)),
        )

    def last_ok_listing(self, report: str) -> datetime | None:
        row = self._db.execute(
            "SELECT MAX(listed_at_utc) FROM listings WHERE report=? AND ok=1", (report,)
        ).fetchone()
        return datetime.fromisoformat(row[0]) if row and row[0] else None

    # ------------------------------------------------------------------ runs
    def start_run(self, run_id: str, started_at_utc: datetime, on_ac_power: bool | None) -> None:
        self._db.execute(
            "INSERT INTO runs (run_id, started_at_utc, status, on_ac_power) VALUES (?,?,?,?)",
            (run_id, _ts(started_at_utc), "running", on_ac_power),
        )

    def finish_run(
        self,
        run_id: str,
        finished_at_utc: datetime,
        status: str,
        *,
        files_downloaded: int,
        bytes_downloaded: int,
        errors: int,
    ) -> None:
        self._db.execute(
            "UPDATE runs SET finished_at_utc=?, status=?, files_downloaded=?,"
            " bytes_downloaded=?, errors=? WHERE run_id=?",
            (_ts(finished_at_utc), status, files_downloaded, bytes_downloaded, errors, run_id),
        )

    def first_collecting_run(self) -> datetime | None:
        """Start of the first run that actually fetched listings (collection start)."""
        row = self._db.execute("SELECT MIN(listed_at_utc) FROM listings WHERE ok=1").fetchone()
        return datetime.fromisoformat(row[0]) if row and row[0] else None

    def run_statuses(self) -> dict[str, int]:
        rows = self._db.execute("SELECT status, COUNT(*) FROM runs GROUP BY status").fetchall()
        return {str(status): int(n) for status, n in rows}
