"""Public scorecard: published forecasts scored against actual DA OZP (M3-T10, M6-T6 later)."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from gridcast.evaluate.metrics import crps, median_index


def load_log(log_dir: Path) -> pd.DataFrame:
    """Long frame of every published forecast: target_day, ts_utc, quantile, value."""
    rows = []
    for path in sorted((log_dir / "forecasts").rglob("*.json")):
        entry = json.loads(path.read_text(encoding="utf-8"))
        levels = entry["quantile_levels"]
        for hour in entry["hours"]:
            for q, v in zip(levels, hour["quantiles"], strict=True):
                rows.append(
                    (date.fromisoformat(entry["target_day"]), pd.Timestamp(hour["ts_utc"]), q, v)
                )
    return pd.DataFrame(rows, columns=["target_day", "ts_utc", "quantile", "value"])


def score_days(forecasts: pd.DataFrame, truth: pd.Series) -> pd.DataFrame:
    """Per published day with all 24 actuals: CRPS, MAE, 80%/90% coverage."""
    out = []
    for day, group in forecasts.groupby("target_day", sort=True):
        wide = group.pivot(index="ts_utc", columns="quantile", values="value").sort_index(axis=1)
        y = wide.index.to_series().map(truth)
        if y.isna().any() or len(wide) != 24:
            continue
        levels = [float(c) for c in wide.columns]
        q = wide.to_numpy()
        yv = y.to_numpy()
        med = q[:, median_index(levels)]
        out.append(
            {
                "target_day": day,
                "crps": float(np.mean(crps(yv, q, levels))),
                "mae": float(np.mean(np.abs(yv - med))),
                "coverage_80": float(
                    np.mean((yv >= q[:, levels.index(0.10)]) & (yv <= q[:, levels.index(0.90)]))
                ),
                "coverage_90": float(
                    np.mean((yv >= q[:, levels.index(0.05)]) & (yv <= q[:, levels.index(0.95)]))
                ),
            }
        )
    return pd.DataFrame(out, columns=["target_day", "crps", "mae", "coverage_80", "coverage_90"])


def summary(days: pd.DataFrame, published: int, now: datetime) -> dict[str, Any]:
    def window(n: int | None) -> dict[str, float | int]:
        part = days if n is None else days.tail(n)
        return (
            {
                "days": len(part),
                **{
                    k: round(float(part[k].mean()), 4)
                    for k in ("crps", "mae", "coverage_80", "coverage_90")
                },
            }
            if len(part)
            else {"days": 0}
        )

    return {
        "generated_at_utc": now.astimezone(UTC).isoformat(timespec="seconds"),
        "published_days": published,
        "scored_days": len(days),
        "last_7_days": window(7),
        "last_30_days": window(30),
        "since_start": window(None),
        "daily": [
            {**r, "target_day": str(r["target_day"])} for r in days.round(4).to_dict("records")
        ],
    }


def write_scorecard(log_dir: Path, truth: pd.Series, now: datetime | None = None) -> dict[str, Any]:
    forecasts = load_log(log_dir)
    published = forecasts["target_day"].nunique() if not forecasts.empty else 0
    days = (
        score_days(forecasts, truth)
        if not forecasts.empty
        else score_days(forecasts.iloc[0:0], truth)
    )
    result = summary(days, published, now or datetime.now(UTC))
    (log_dir / "scorecard.json").write_text(json.dumps(result, indent=1) + "\n", encoding="utf-8")
    lines = [
        "# GridCast live scorecard",
        "",
        f"Generated {result['generated_at_utc']}. Published days: {published};"
        f" scored: {len(days)}.",
        "Day-ahead Ontario Zonal Price forecasts issued 09:00 Toronto time the day before.",
        "",
        "| Window | Days | CRPS | MAE ($/MWh) | 80% coverage | 90% coverage |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for label, key in (
        ("Last 7 days", "last_7_days"),
        ("Last 30 days", "last_30_days"),
        ("Since start", "since_start"),
    ):
        w = result[key]
        if w["days"]:
            lines.append(
                f"| {label} | {w['days']} | {w['crps']:.3f} | {w['mae']:.2f}"
                f" | {w['coverage_80']:.3f} | {w['coverage_90']:.3f} |"
            )
        else:
            lines.append(f"| {label} | 0 | - | - | - | - |")
    (log_dir / "scorecard.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return result
