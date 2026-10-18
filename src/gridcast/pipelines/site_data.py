"""``gridcast site-data``: research data for the project site, built from results files.

Writes ``site/data/site.json``. Every number shown on the site's research page comes from
here (plan §0 rule 6): frozen configs (validation scores), the RQ1 placebo results, the
project config and the pre-registration record. Counts and aggregates only; no raw
Grid Status data (ADR-001).
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from gridcast.backtest.freeze import FROZEN_DIR
from gridcast.config import REPO_ROOT, load_project_config, load_yaml

PREREG_COMMIT = "47d695503c10cd6aac072287f75e5dceff35f8d9"
LABELS = {
    "lgbm": "LightGBM + CQR",
    "sarimax": "SARIMAX",
    "lear": "LEAR",
    "naive_d1": "Yesterday's price",
    "naive_d7": "Last week's price",
}


def _git(*args: str) -> str:
    out = subprocess.run(["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, check=True)
    return out.stdout


def preregistration() -> dict[str, Any]:
    text = _git("show", f"{PREREG_COMMIT}:PREREGISTRATION.md")
    return {
        "commit": PREREG_COMMIT,
        "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "registered": "2026-10-15",
        "tag": "prereg-v1.0",
        "url": "https://github.com/Muhammad-Memon542/GridCast/blob/main/PREREGISTRATION.md",
    }


def validation() -> list[dict[str, Any]]:
    rows = []
    for path in sorted(FROZEN_DIR.glob("*.yaml")):
        frozen = load_yaml(path)
        if not str(path.stem).startswith(tuple(LABELS)):
            continue
        scores = frozen["validation_scores"]
        rows.append(
            {
                "model": path.stem,
                "label": LABELS.get(path.stem, path.stem),
                "calibrated": bool(frozen.get("calibrate_cqr")),
                **{
                    k: round(float(scores[k]), 4)
                    for k in (
                        "crps",
                        "mae",
                        "rmse",
                        "rmae",
                        "coverage_80",
                        "coverage_90",
                        "width_90",
                    )
                },
            }
        )
    return sorted(rows, key=lambda r: r["crps"])


def placebo() -> dict[str, Any]:
    path = REPO_ROOT / "docs" / "results" / "rq1_placebo.json"
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return {
        "statistics": data.get("placebo_statistics", []),
        "quantiles_50_90_95": data.get("placebo_reference", {}).get(
            "placebo_quantiles_50_90_95", {}
        ),
    }


def build(out: Path | None = None) -> Path:
    project = load_project_config()
    payload = {
        "generated_at_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "code_git_sha": _git("rev-parse", "--short", "HEAD").strip(),
        "project": {
            "issue_time_local": str(project.forecast.issue_time_local)[:5],
            "quantiles": list(project.forecast.quantiles),
            "go_live": str(project.market.go_live),
            "splits": {
                "validation": {
                    "start": str(project.splits.validation.start),
                    "end": str(project.splits.validation.end),
                },
                "test": {"start": str(project.splits.test.start), "end": None},
            },
            "battery": {
                "power_mw": project.battery.power_mw,
                "energy_mwh": project.battery.energy_mwh,
            },
        },
        "preregistration": preregistration(),
        "validation": {"window": "2025-07-01 to 2025-10-31", "models": validation()},
        "rq1_placebo": placebo(),
        "status": {
            "rq1": "Placebo reference ready; go-live verdict after the data gate (G1)",
            "rq2": "Specialist models tuned and validated; foundation models not started",
            "rq3": "Not started; needs the foundation models",
            "rq4": "Not started",
        },
    }
    out = out or REPO_ROOT / "site" / "data" / "site.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=1) + "\n", encoding="utf-8")
    return out
