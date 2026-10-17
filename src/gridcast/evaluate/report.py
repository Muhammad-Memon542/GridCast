"""Comparison tables for a set of backtested models (validation now; RQ2 test after G1)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from itertools import combinations

import numpy as np
import pandas as pd

from gridcast.evaluate.metrics import Scores, crps, median_index
from gridcast.evaluate.significance import dm_test, holm, model_confidence_set
from gridcast.models.base import to_wide

EST = "Etc/GMT+5"


def _p(value: float) -> str:
    """p-values below double precision print as an upper bound, not 0."""
    return "<1e-15" if value < 1e-15 else f"{value:.2g}"


def daily_losses(forecasts: pd.DataFrame, truth: pd.Series) -> pd.DataFrame:
    """Per IESO day: mean hourly CRPS and the L1 norm (sum) of the 24 median errors."""
    wide = to_wide(forecasts.rename(columns={"target_ts_utc": "ts_utc"}))
    y = wide.index.to_series().map(truth)
    keep = y.notna()
    wide, y = wide[keep], y[keep]
    levels = [float(c) for c in wide.columns]
    hourly_crps = crps(y.to_numpy(), wide.to_numpy(), levels)
    abs_err = np.abs(y.to_numpy() - wide.to_numpy()[:, median_index(levels)])
    day = pd.DatetimeIndex(wide.index).tz_convert(EST).date
    frame = pd.DataFrame({"day": day, "crps": hourly_crps, "abs_err": abs_err})
    out = frame.groupby("day").agg(
        crps=("crps", "mean"), mae_l1=("abs_err", "sum"), hours=("crps", "size")
    )
    return out[out["hours"] == 24]


def comparison_markdown(
    scores: Mapping[str, Scores],
    losses: Mapping[str, pd.DataFrame],
    *,
    title: str,
    note: str,
    mcs_reps: int = 10_000,
) -> str:
    names = list(scores)
    lines = [f"# {title}", "", note, "", "## Scores", ""]
    lines += [
        "| Model | CRPS | MAE | RMSE | rMAE | 80% cov. | 90% cov. | 90% width | Spike MAE"
        " | Spike P/R |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for name in sorted(names, key=lambda n: scores[n].crps):
        s = scores[name]
        lines.append(
            f"| `{name}` | {s.crps:.3f} | {s.mae:.2f} | {s.rmse:.2f} | {s.rmae:.3f}"
            f" | {s.coverage_80:.3f} | {s.coverage_90:.3f} | {s.width_90:.1f} | {s.spike_mae:.1f}"
            f" | {s.spike_precision:.2f}/{s.spike_recall:.2f} |"
        )
    common = sorted(set.intersection(*(set(losses[n].index) for n in names))) if names else []
    lines += ["", f"Significance on {len(common)} common days (daily losses).", ""]
    for metric, label in (("crps", "CRPS (daily mean)"), ("mae_l1", "MAE (daily L1 norm)")):
        pairs = list(combinations(sorted(names, key=lambda n: scores[n].crps), 2))
        results = [
            dm_test(losses[a].loc[common, metric], losses[b].loc[common, metric]) for a, b in pairs
        ]
        adjusted = holm([r.p_value for r in results])
        lines += [
            f"### Diebold-Mariano, {label}",
            "",
            "| A | B | DM stat (A - B) | p | Holm p |",
            "| --- | --- | --- | --- | --- |",
        ]
        for (a, b), r, p_adj in zip(pairs, results, adjusted, strict=True):
            lines.append(f"| `{a}` | `{b}` | {r.statistic:.2f} | {_p(r.p_value)} | {_p(p_adj)} |")
        lines.append("")
    if len(names) >= 2:
        table = pd.DataFrame({n: losses[n].loc[common, "crps"] for n in names})
        mcs = model_confidence_set(table, reps=mcs_reps)
        lines += [
            "### 90% model confidence set (daily CRPS)",
            "",
            "| Model | MCS p-value | In set |",
            "| --- | --- | --- |",
        ]
        mcs_p = mcs["mcs_p_value"].astype("float64")
        for name in sorted(names, key=lambda n: -mcs_p[n]):
            in_set = "yes" if mcs.loc[name, "in_mcs"] else "no"
            lines.append(f"| `{name}` | {mcs.loc[name, 'mcs_p_value']:.3f} | {in_set} |")
        lines.append("")
    return "\n".join(lines)


def models_in(scores: Mapping[str, Scores]) -> Sequence[str]:
    return sorted(scores, key=lambda n: scores[n].crps)
