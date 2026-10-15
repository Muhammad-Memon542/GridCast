"""Command-line entry point: ``gridcast <command>``."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from datetime import UTC, datetime

from gridcast.config import Settings
from gridcast.ingest.collector import load_collector_config, run_collection
from gridcast.ingest.gaps import gap_report, render
from gridcast.ingest.manifest import Manifest
from gridcast.logs import configure_logging


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="gridcast")
    sub = parser.add_subparsers(dest="command", required=True)

    collect = sub.add_parser("collect", help="download new IESO report files into bronze")
    collect.add_argument(
        "--require-ac", action="store_true", help="do nothing unless the Mac is on AC power"
    )
    collect.add_argument("--force", action="store_true", help="ignore per-report cadence")
    collect.add_argument(
        "--report", action="append", dest="reports", metavar="NAME", help="limit to a report"
    )

    gaps = sub.add_parser("gaps", help="show the collector gap report")
    gaps.add_argument("--days", type=int, default=7)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    settings = Settings()
    configure_logging(settings.log_level)
    config = load_collector_config()

    if args.command == "collect":
        summary = run_collection(
            settings,
            config,
            only=set(args.reports) if args.reports else None,
            force=args.force,
            require_ac=args.require_ac,
        )
        return 1 if summary.status == "partial" else 0

    manifest_path = settings.data_dir / "bronze" / "_manifest.sqlite"
    if not manifest_path.exists():
        print("No manifest yet: the collector has never run.")
        return 1
    with Manifest(manifest_path) as manifest:
        report = gap_report(manifest, config, days=args.days, now=datetime.now(UTC))
    print(render(report))
    return 0
