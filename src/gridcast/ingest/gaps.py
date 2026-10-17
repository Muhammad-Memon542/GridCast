"""Gap report for the live collector (M1-T3 acceptance: 7 consecutive gap-free days).

For each daily or hourly report it checks every IESO-calendar day in the window:
the day's file exists (daily), all 24 hour-ending files exist (hourly), and for
reports that keep every version there are no holes in the ``_vN`` sequence.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from gridcast.ingest.collector import CollectorConfig, ReportSpec
from gridcast.ingest.manifest import Manifest, StoredFile
from gridcast.timeutil.ieso import ieso_date

PUBLICATION_GRACE = timedelta(hours=2)


@dataclass
class ReportGaps:
    report: str
    granularity: str
    last_ok_listing: datetime | None
    missing_days: list[date] = field(default_factory=list)
    incomplete_hours: dict[date, int] = field(default_factory=dict)
    version_holes: dict[str, list[int]] = field(default_factory=dict)
    clean_days: set[date] = field(default_factory=set)


@dataclass
class GapReport:
    window: list[date]
    reports: list[ReportGaps]

    @property
    def gap_free_streak(self) -> int:
        """Consecutive gap-free days ending with the last day of the window."""
        checked = [r for r in self.reports if r.granularity in ("daily", "hourly")]
        streak = 0
        for day in reversed(self.window):
            if checked and all(day in r.clean_days for r in checked):
                streak += 1
            else:
                break
        return streak


def _granularity(files: list[StoredFile]) -> str:
    lengths = {len(f.period_key) for f in files if f.period_key}
    if lengths == {8}:
        return "daily"
    if lengths == {10}:
        return "hourly"
    return "period" if lengths else "none"


def _key_day(key: str) -> date:
    return date(int(key[:4]), int(key[4:6]), int(key[6:8]))


def _holes(versions: set[int]) -> list[int]:
    return sorted(set(range(1, max(versions) + 1)) - versions) if versions else []


def check_report(
    manifest: Manifest, spec: ReportSpec, window: list[date], holes_from: date
) -> ReportGaps:
    files = manifest.stored_files(spec.name)
    gaps = ReportGaps(
        report=spec.name,
        granularity=_granularity(files) if spec.check_gaps else "unchecked",
        last_ok_listing=manifest.last_ok_listing(spec.name),
    )
    if gaps.granularity not in ("daily", "hourly"):
        return gaps

    versions: dict[str, set[int]] = defaultdict(set)
    for f in files:
        assert f.period_key is not None
        versions[f.period_key].update({f.version} if f.version is not None else set())
    by_day: dict[date, list[str]] = defaultdict(list)
    for key in versions:
        by_day[_key_day(key)].append(key)

    for day in window:
        keys = by_day.get(day, [])
        ok = True
        if not keys:
            gaps.missing_days.append(day)
            ok = False
        elif gaps.granularity == "hourly" and len(keys) < 24:
            gaps.incomplete_hours[day] = len(keys)
            ok = False
        if spec.versions == "all" and day >= holes_from:
            for key in keys:
                if missing := _holes(versions[key]):
                    gaps.version_holes[key] = missing
                    ok = False
        if ok:
            gaps.clean_days.add(day)
    return gaps


def gap_report(
    manifest: Manifest, config: CollectorConfig, *, days: int, now: datetime
) -> GapReport:
    """Check the last ``days`` complete IESO days, starting no earlier than collection start."""
    started = manifest.first_collecting_run()
    if started is None:
        return GapReport(window=[], reports=[])
    # A day can only be judged once every checked report has been listed after that day
    # ended (plus a grace period for the last hour's files). Daily-cadence reports therefore
    # hold the window back by up to a day instead of showing false gaps.
    last = ieso_date(now) - timedelta(days=1)
    for spec in config.reports:
        listed = manifest.last_ok_listing(spec.name) if spec.check_gaps else None
        if listed is not None:
            last = min(last, ieso_date(listed - PUBLICATION_GRACE) - timedelta(days=1))
    first = max(last - timedelta(days=days - 1), ieso_date(started))
    window = [first + timedelta(days=i) for i in range((last - first).days + 1)]
    holes_from = ieso_date(started)
    return GapReport(
        window=window,
        reports=[check_report(manifest, spec, window, holes_from) for spec in config.reports],
    )


def render(report: GapReport) -> str:
    if not report.window:
        return "No complete collection day yet (or the collector has never run)."
    lines = [
        f"Window: {report.window[0]} to {report.window[-1]} (IESO/EST days)",
        f"Consecutive gap-free days ending {report.window[-1]}: {report.gap_free_streak}",
        "",
        "| Report | Kind | Last listing (UTC) | Missing days | Incomplete hours | Version holes |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for r in report.reports:
        listed = r.last_ok_listing.strftime("%Y-%m-%d %H:%M") if r.last_ok_listing else "never"
        missing = ", ".join(d.isoformat() for d in r.missing_days) or "-"
        hours = ", ".join(f"{d}: {n}/24" for d, n in sorted(r.incomplete_hours.items())) or "-"
        holes = ", ".join(f"{k}: {v}" for k, v in sorted(r.version_holes.items())) or "-"
        lines.append(f"| {r.report} | {r.granularity} | {listed} | {missing} | {hours} | {holes} |")
    return "\n".join(lines)
