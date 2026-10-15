"""Live snapshot collector for IESO public reports (M1-T2, M1-T3).

Each run lists every report folder that is due, downloads files it has not seen yet
and writes them to bronze with a manifest row. Re-running immediately downloads
nothing. Because IESO keeps 30-90 days of files, a run after a gap (the Mac was
asleep or on battery) catches up on everything still listed.
"""

from __future__ import annotations

import fcntl
import gzip
import hashlib
import os
import re
import uuid
from collections import defaultdict
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Literal, Self

import structlog
from pydantic import BaseModel, ConfigDict, Field, model_validator

from gridcast.config import Settings, load_yaml
from gridcast.ingest.http import FetchError, PoliteClient
from gridcast.ingest.ieso_index import ListingEntry, parse_listing
from gridcast.ingest.manifest import FileRecord, Manifest
from gridcast.ingest.power import on_ac_power
from gridcast.timeutil.ieso import ieso_date, ieso_naive_to_utc

log = structlog.get_logger(__name__)

COLLECTOR_CONFIG_FILE = "collector.yaml"
DUE_SLACK = timedelta(minutes=10)
"""Tolerance so an hourly report listed at 10:07 is due again at 11:07 despite jitter."""
POWER_CHECK_EVERY = 50
"""Re-check the power source after this many downloads within one report."""

RunStatus = Literal["ok", "partial", "skipped_on_battery", "stopped_on_battery", "busy"]


class ReportSpec(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(pattern=r"^[A-Za-z0-9]+$")
    dataset: str
    every_hours: float = Field(gt=0)
    versions: Literal["all", "latest"]
    """``all`` keeps every ``_vN``; ``latest`` keeps only the newest version per period."""
    lookback_days: int = Field(gt=0)
    """Ignore files whose period ended more than this many days ago."""
    check_gaps: bool = True
    """Include in the gap report (off for reports that do not publish every day)."""


class CollectorConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    base_url: str = Field(pattern=r"^https://.+/$")
    reports: tuple[ReportSpec, ...]

    @model_validator(mode="after")
    def _unique_names(self) -> Self:
        names = [r.name for r in self.reports]
        if len(names) != len(set(names)):
            raise ValueError("report names must be unique")
        return self


def load_collector_config(path: Path | None = None) -> CollectorConfig:
    if path is None:
        path = Settings().configs_dir / COLLECTOR_CONFIG_FILE
    return CollectorConfig.model_validate(load_yaml(path))


def select_downloads(
    entries: list[ListingEntry], spec: ReportSpec, today: date
) -> list[ListingEntry]:
    """Pick the files worth having from one folder listing.

    * the folder-wide alias (no period key) is skipped: it duplicates a dated file;
    * a period's un-versioned file is skipped when versioned siblings exist, because it
      is an alias of the latest version (decisions.md V-05a);
    * ``latest`` keeps only the highest version per period.
    """
    cutoff = today - timedelta(days=spec.lookback_days)
    by_key: dict[str, list[ListingEntry]] = defaultdict(list)
    for entry in entries:
        end = entry.file.period_end
        if entry.file.key is None or end is None or end < cutoff:
            continue
        by_key[entry.file.key].append(entry)

    selected: list[ListingEntry] = []
    for key in sorted(by_key):
        group = by_key[key]
        versioned = sorted(
            (e for e in group if e.file.version is not None),
            key=lambda e: e.file.version or 0,
        )
        if not versioned:
            selected.extend(group)
        elif spec.versions == "latest":
            selected.append(versioned[-1])
        else:
            selected.extend(versioned)
    return selected


_XML_CREATED = re.compile(rb"<CreatedAt>\s*([^<]+?)\s*</CreatedAt>")
_CSV_CREATED = re.compile(rb"Created at (\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2})")


def extract_created_at(content: bytes) -> datetime | None:
    """``CreatedAt`` (XML) or ``Created at`` (CSV header) as UTC; IESO stamps are EST."""
    head = content[:4096]
    match = _XML_CREATED.search(head) or _CSV_CREATED.search(head)
    if match is None:
        return None
    try:
        value = datetime.fromisoformat(match.group(1).decode("ascii"))
    except (UnicodeDecodeError, ValueError):
        return None
    if value.tzinfo is not None:
        return value.astimezone(UTC)
    return ieso_naive_to_utc(value)


def _atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_bytes(content)
    os.replace(tmp, path)


@dataclass
class ReportResult:
    report: str
    listed: int = 0
    selected: int = 0
    downloaded: int = 0
    duplicates: int = 0
    bytes: int = 0
    errors: int = 0
    stopped_on_battery: bool = False


@dataclass
class RunSummary:
    run_id: str
    status: RunStatus
    reports: list[ReportResult] = field(default_factory=list)

    @property
    def downloaded(self) -> int:
        return sum(r.downloaded for r in self.reports)

    @property
    def errors(self) -> int:
        return sum(r.errors for r in self.reports)


class ReportCollector:
    def __init__(
        self,
        *,
        base_url: str,
        data_dir: Path,
        client: PoliteClient,
        manifest: Manifest,
        run_id: str,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
        keep_going: Callable[[], bool] = lambda: True,
    ) -> None:
        self._base_url = base_url
        self._data_dir = data_dir
        self._client = client
        self._manifest = manifest
        self._run_id = run_id
        self._now = now
        self.keep_going = keep_going

    def collect(self, spec: ReportSpec) -> ReportResult:
        result = ReportResult(report=spec.name)
        folder_url = f"{self._base_url}{spec.name}/"
        listed_at = self._now()
        try:
            response = self._client.get(folder_url)
        except FetchError as exc:
            log.error("listing_failed", report=spec.name, error=str(exc))
            self._manifest.add_listing(spec.name, self._run_id, listed_at, 0, ok=False)
            result.errors += 1
            return result
        if response.status_code != 200:
            log.error("listing_failed", report=spec.name, status=response.status_code)
            self._manifest.add_listing(spec.name, self._run_id, listed_at, 0, ok=False)
            result.errors += 1
            return result

        entries = parse_listing(spec.name, response.text)
        result.listed = len(entries)
        todo = [
            e
            for e in select_downloads(entries, spec, ieso_date(listed_at))
            if not self._manifest.seen(spec.name, e.file.filename, e.modified_utc)
        ]
        result.selected = len(todo)
        for i, entry in enumerate(todo):
            if i and i % POWER_CHECK_EVERY == 0 and not self.keep_going():
                result.stopped_on_battery = True
                break
            self._download(folder_url, entry, result)

        # A listing counts as done only if every selected file was handled, so a
        # partial pass keeps the report due and the next run resumes it.
        complete = not result.stopped_on_battery and result.errors == 0
        self._manifest.add_listing(spec.name, self._run_id, listed_at, len(entries), complete)
        log.info("report_collected", **vars(result))
        return result

    def _download(self, folder_url: str, entry: ListingEntry, result: ReportResult) -> None:
        f = entry.file
        url = f"{folder_url}{f.filename}"
        try:
            response = self._client.get(url)
        except FetchError as exc:
            log.error("download_failed", url=url, error=str(exc))
            result.errors += 1
            return
        record = FileRecord(
            report=f.report,
            filename=f.filename,
            period_key=f.key,
            version=f.version,
            listing_modified_utc=entry.modified_utc,
            url=url,
            fetched_at_utc=self._now(),
            http_status=response.status_code,
            run_id=self._run_id,
        )
        if response.status_code != 200:
            # 404 = purged between listing and download; recorded so we do not retry it.
            log.warning("download_not_ok", url=url, status=response.status_code)
            self._manifest.add_file(record)
            if response.status_code != 404:
                result.errors += 1
            return

        content = response.content
        sha256 = hashlib.sha256(content).hexdigest()
        existing = self._manifest.find_by_sha(f.report, sha256)
        if existing is not None:
            duplicate_of, path = existing
            result.duplicates += 1
        else:
            stamp = record.fetched_at_utc.strftime("%Y%m%dT%H%M%SZ")
            suffix = Path(f.filename).suffix
            name = f"{f.filename}__{stamp}{suffix}.gz"
            rel = Path("bronze", "ieso", f.report, f.partition, name)
            # gzip with mtime=0 is lossless and deterministic; sha256 is of the raw bytes.
            _atomic_write(self._data_dir / rel, gzip.compress(content, mtime=0))
            duplicate_of, path = None, rel.as_posix()
            result.downloaded += 1
            result.bytes += len(content)
        self._manifest.add_file(
            replace(
                record,
                sha256=sha256,
                bytes=len(content),
                path=path,
                duplicate_of=duplicate_of,
                doc_created_at_utc=extract_created_at(content),
            )
        )


@contextmanager
def _exclusive_lock(path: Path) -> Iterator[bool]:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as fh:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def is_due(manifest: Manifest, spec: ReportSpec, now: datetime) -> bool:
    last = manifest.last_ok_listing(spec.name)
    return last is None or now - last >= timedelta(hours=spec.every_hours) - DUE_SLACK


def run_collection(
    settings: Settings,
    config: CollectorConfig,
    *,
    only: set[str] | None = None,
    force: bool = False,
    require_ac: bool = False,
    power_check: Callable[[], bool] | None = None,
    client: PoliteClient | None = None,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> RunSummary:
    """One collector pass over every due report, in config (priority) order."""
    power_check = power_check or on_ac_power
    run_id = f"{now().strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:6]}"
    structlog.contextvars.bind_contextvars(run_id=run_id)
    bronze = settings.data_dir / "bronze"
    with _exclusive_lock(bronze / ".collector.lock") as acquired:
        if not acquired:
            log.warning("collector_busy")
            return RunSummary(run_id=run_id, status="busy")
        with Manifest(bronze / "_manifest.sqlite") as manifest:
            on_ac = power_check() if require_ac else None
            manifest.start_run(run_id, now(), on_ac)
            summary = RunSummary(run_id=run_id, status="ok")
            if require_ac and not on_ac:
                summary.status = "skipped_on_battery"
            else:
                owns_client = client is None
                client = client or PoliteClient(
                    settings.user_agent, min_interval_s=settings.http_min_interval_s
                )
                collector = ReportCollector(
                    base_url=config.base_url,
                    data_dir=settings.data_dir,
                    client=client,
                    manifest=manifest,
                    run_id=run_id,
                    now=now,
                    keep_going=power_check if require_ac else lambda: True,
                )
                try:
                    _collect_due(summary, collector, manifest, config, only, force, now)
                finally:
                    if owns_client:
                        client.close()
            manifest.finish_run(
                run_id,
                now(),
                summary.status,
                files_downloaded=summary.downloaded,
                bytes_downloaded=sum(r.bytes for r in summary.reports),
                errors=summary.errors,
            )
    log.info("run_finished", status=summary.status, downloaded=summary.downloaded)
    return summary


def _collect_due(
    summary: RunSummary,
    collector: ReportCollector,
    manifest: Manifest,
    config: CollectorConfig,
    only: set[str] | None,
    force: bool,
    now: Callable[[], datetime],
) -> None:
    for spec in config.reports:
        if only is not None and spec.name not in only:
            continue
        if not force and not is_due(manifest, spec, now()):
            continue
        if summary.reports and not collector.keep_going():
            summary.status = "stopped_on_battery"
            return
        result = collector.collect(spec)
        summary.reports.append(result)
        if result.stopped_on_battery:
            summary.status = "stopped_on_battery"
            return
        if result.errors:
            summary.status = "partial"
