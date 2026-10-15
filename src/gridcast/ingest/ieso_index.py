"""Parse IESO report folder listings and report file names.

File names follow ``PUB_<Report>[_<key>][_v<N>].<ext>`` where ``key`` is a period:
``YYYY`` (yearly CSV), ``YYYYMM`` (monthly CSV), ``YYYYMMDD`` (delivery date) or
``YYYYMMDDHH`` (delivery date and hour ending). A file without ``_vN`` is an alias of
the latest version (decisions.md V-05a). A file without a key is the folder-wide
"latest" alias.
"""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import date, datetime

from gridcast.timeutil.ieso import ieso_naive_to_utc

_LISTING_ROW = re.compile(
    r'<a href="(?P<name>PUB_[^"/]+)">[^<]*</a>\s+'
    r"(?P<modified>\d{2}-[A-Za-z]{3}-\d{4} \d{2}:\d{2})\s+(?P<size>\S+)"
)


@dataclass(frozen=True)
class ReportFile:
    report: str
    filename: str
    key: str | None
    """Period key from the file name, e.g. ``20261015``; ``None`` for the folder alias."""
    version: int | None
    """``N`` from ``_vN``; ``None`` for alias files."""

    @property
    def is_alias(self) -> bool:
        return self.version is None

    @property
    def period_start(self) -> date | None:
        if self.key is None:
            return None
        return date(int(self.key[:4]), int(self.key[4:6] or 1), int(self.key[6:8] or 1))

    @property
    def period_end(self) -> date | None:
        """Last calendar day covered by the key (used for look-back filtering)."""
        start = self.period_start
        if start is None or self.key is None:
            return None
        if len(self.key) == 4:
            return date(start.year, 12, 31)
        if len(self.key) == 6:
            return date(start.year, start.month, calendar.monthrange(start.year, start.month)[1])
        return start

    @property
    def hour_ending(self) -> int | None:
        return int(self.key[8:10]) if self.key is not None and len(self.key) == 10 else None

    @property
    def partition(self) -> str:
        """Bronze sub-directory: ISO date for daily/hourly keys, else the raw period."""
        if self.key is None:
            return "_latest"
        if len(self.key) >= 8:
            start = self.period_start
            assert start is not None
            return start.isoformat()
        if len(self.key) == 6:
            return f"{self.key[:4]}-{self.key[4:]}"
        return self.key


@dataclass(frozen=True)
class ListingEntry:
    file: ReportFile
    modified_utc: datetime
    size: str


def parse_filename(report: str, filename: str) -> ReportFile | None:
    """Return the parsed name, or ``None`` if it does not belong to ``report``."""
    pattern = (
        rf"^PUB_{re.escape(report)}"
        r"(?:_(?P<key>\d{4}(?:\d{2}){0,3}))?(?:_v(?P<version>\d+))?\.(?:xml|csv)$"
    )
    match = re.match(pattern, filename)
    if match is None:
        return None
    version = match["version"]
    return ReportFile(
        report=report,
        filename=filename,
        key=match["key"],
        version=int(version) if version is not None else None,
    )


def parse_listing(report: str, html: str) -> list[ListingEntry]:
    """Parse an Apache directory index. Listing times are EST (decisions.md V-03)."""
    entries = []
    for row in _LISTING_ROW.finditer(html):
        parsed = parse_filename(report, row["name"])
        if parsed is None:
            continue
        modified = datetime.strptime(row["modified"], "%d-%b-%Y %H:%M")  # noqa: DTZ007
        entries.append(
            ListingEntry(file=parsed, modified_utc=ieso_naive_to_utc(modified), size=row["size"])
        )
    return entries
