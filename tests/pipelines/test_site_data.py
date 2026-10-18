"""Project-site research data (site/data/site.json)."""

from __future__ import annotations

import json
from pathlib import Path

from gridcast.pipelines.site_data import LABELS, build, preregistration, validation


def test_preregistration_hash_matches_the_registered_text() -> None:
    record = preregistration()
    assert record["sha256"] == "36b7789496407ae40c170cbb7bdebb6c6d5ec0425707dac058fd0124d2c0adda"


def test_validation_rows_come_from_frozen_configs_best_first() -> None:
    rows = validation()
    assert {r["model"] for r in rows} <= set(LABELS)
    crps = [r["crps"] for r in rows]
    assert crps == sorted(crps)
    assert all(0 <= r["coverage_90"] <= 1 for r in rows)


def test_build_writes_site_json(tmp_path: Path) -> None:
    out = build(tmp_path / "site.json")
    data = json.loads(out.read_text())
    assert set(data) >= {"project", "preregistration", "validation", "rq1_placebo", "status"}
    assert data["project"]["battery"] == {"power_mw": 100.0, "energy_mwh": 400.0}
