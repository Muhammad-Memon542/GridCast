"""Validation-only search for the neural tier (M3-T7). Run with the ``neural`` group:

    uv run --group neural python scripts/research/neural_validation.py

Every configuration is a full rolling-origin backtest on the validation window with rolling
CQR calibration; the test window stays sealed. Results go to
``data/gold/tuning/neural_validation.json`` (one record per run, appended).
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from gridcast.backtest.runner import run_validation
from gridcast.config import Settings
from gridcast.ingest.power import on_ac_power

SEARCH: list[tuple[str, dict[str, Any]]] = [
    ("nhits_s0", {"max_steps": 1000, "learning_rate": 1e-3, "scaler_type": "identity"}),
    (
        "nhits_s0",
        {"max_steps": 1000, "learning_rate": 1e-3, "scaler_type": "identity", "input_size": 336},
    ),
    ("nhits_s0", {"max_steps": 2000, "learning_rate": 5e-4, "scaler_type": "identity"}),
    (
        "patchtst_s0",
        {
            "max_steps": 500,
            "hidden_size": 64,
            "n_heads": 4,
            "encoder_layers": 2,
            "scaler_type": "identity",
        },
    ),
    ("tft_s0", {"max_steps": 500, "hidden_size": 32, "scaler_type": "identity"}),
]


class UnpluggedError(RuntimeError):
    pass


def run(name: str, params: dict[str, Any], out: Path) -> dict[str, Any]:
    if not on_ac_power():
        raise UnpluggedError("on battery: stopping (resumes where it left off when rerun)")
    started = time.perf_counter()
    result, scores = run_validation(name, params={name: params}, calibrate=True)
    record = {
        "model": name,
        "params": params,
        "seconds": round(time.perf_counter() - started),
        "fits": result.fits,
        **{k: round(v, 4) for k, v in scores.as_dict().items()},
    }
    with out.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record) + "\n")
    print("RESULT", json.dumps(record), flush=True)
    return record


def main() -> None:
    out = Settings().data_dir / "gold" / "tuning" / "neural_validation.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    done = (
        {
            json.dumps([r["model"], r["params"]], sort_keys=True)
            for r in map(json.loads, out.read_text().splitlines())
        }
        if out.exists()
        else set()
    )
    records = []
    for name, params in SEARCH:
        if json.dumps([name, params], sort_keys=True) not in done:
            records.append(run(name, params, out))
    # Seeds 1 and 2 for the best N-HiTS config and for the PatchTST / TFT configs.
    rows = [json.loads(line) for line in out.read_text().splitlines()]
    best = min((r for r in rows if r["model"] == "nhits_s0"), key=lambda r: r["crps"])
    for arch, params in [
        ("nhits", best["params"]),
        *[
            (r["model"].split("_")[0], r["params"])
            for r in rows
            if r["model"] in ("patchtst_s0", "tft_s0")
        ],
    ]:
        for seed in (1, 2):
            key = json.dumps([f"{arch}_s{seed}", params], sort_keys=True)
            if key not in done:
                run(f"{arch}_s{seed}", params, out)


if __name__ == "__main__":
    main()
