"""RQ1 runner: the pre-registered break tests (pre-registration §7, RQ1 row).

Supported if, on the comparable series: the Chow test at the break rejects (p < 0.05) for
the daily mean AND the daily log standard deviation; a detected break lies within +/-30
days of it; KS rejects equal hourly distributions; and the Hill tail-index bootstrap CIs
do not overlap. Windows: 12 months on each side.

The real run reads post-go-live prices (part of the test window) and needs ``after_g1``
(ADR-011). The placebo run applies the same rule at 2024-05-01 inside the old market.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from gridcast.evaluate.regime import (
    chow_test,
    comparable_series,
    daily_stats,
    detect_breaks,
    ks_test,
    monthly_spike_counts,
    shift_test_hac,
    tail_index,
)

ALPHA = 0.05
NEAR_DAYS = 30


def analyse(series: pd.DataFrame, break_at: date, months: int = 12) -> dict[str, Any]:
    start = (pd.Timestamp(break_at) - pd.DateOffset(months=months)).date()
    end = (pd.Timestamp(break_at) + pd.DateOffset(months=months)).date() - timedelta(days=1)
    day = series["ts_utc"].dt.tz_convert("Etc/GMT+5").dt.date
    window = series[(day >= start) & (day <= end)]
    wday = window["ts_utc"].dt.tz_convert("Etc/GMT+5").dt.date
    before, after = window[wday < break_at], window[wday >= break_at]
    daily = daily_stats(window)
    breaks = detect_breaks(daily["mean"])
    nearest = min(breaks, key=lambda d: abs((d - break_at).days)) if breaks else None
    tails = {"before": tail_index(before["price"]), "after": tail_index(after["price"])}
    threshold = float(np.quantile(window["price"], 0.99))
    result: dict[str, Any] = {
        "break_at": str(break_at),
        "window": [str(start), str(end)],
        "hours": {"before": len(before), "after": len(after)},
        "daily_mean": {
            "before": float(daily.loc[daily.index < break_at, "mean"].mean()),
            "after": float(daily.loc[daily.index >= break_at, "mean"].mean()),
        },
        "chow_mean": chow_test(daily["mean"], break_at).as_dict(),
        "chow_log_sd": chow_test(daily["log_sd"], break_at).as_dict(),
        "hac_shift_mean": shift_test_hac(daily["mean"], break_at).as_dict(),
        "hac_shift_log_sd": shift_test_hac(daily["log_sd"], break_at).as_dict(),
        "detected_breaks": [str(d) for d in breaks],
        "nearest_break_days": None if nearest is None else (nearest - break_at).days,
        "ks_hourly": ks_test(before["price"], after["price"]).as_dict(),
        "tail_index": {k: v.as_dict() for k, v in tails.items()},
        "p99_threshold": threshold,
        "monthly_hours_above_p99": monthly_spike_counts(window, threshold).to_dict(),
    }
    t_before, t_after = tails["before"], tails["after"]
    criteria = {
        "chow_mean": result["chow_mean"]["p_value"] < ALPHA,
        "chow_log_sd": result["chow_log_sd"]["p_value"] < ALPHA,
        "break_within_30_days": nearest is not None and abs((nearest - break_at).days) <= NEAR_DAYS,
        "ks": result["ks_hourly"]["p_value"] < ALPHA,
        "tail_cis_disjoint": t_before.ci_high < t_after.ci_low or t_after.ci_high < t_before.ci_low,
    }
    result["criteria"] = criteria
    result["supported"] = all(criteria.values())
    return result


def run(data_dir: Path, *, go_live: date, placebo: bool, after_g1: bool = False) -> dict[str, Any]:
    if not placebo and not after_g1:
        raise PermissionError("the RQ1 verdict reads test-window prices; run it after G1 (ADR-011)")

    def read(name: str) -> pd.DataFrame:
        return pd.read_parquet(data_dir / "silver" / name).drop(columns=["month"], errors="ignore")

    series = comparable_series(read("hoep"), read("rt_ozp_hourly"), go_live)
    if placebo:
        series = series[series["regime"] == "old"]
        return {
            "kind": "placebo",
            **analyse(series, date(go_live.year - 1, go_live.month, go_live.day)),
        }
    return {"kind": "rq1", **analyse(series, go_live)}


def write(result: dict[str, Any], out: Path) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=1, default=str) + "\n", encoding="utf-8")
    return out
