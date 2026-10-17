"""Freeze model configurations after validation (M3-T6) and run sealed test windows.

A frozen config (``configs/frozen/<model>.yaml``) records the parameters, whether CQR
calibration is applied, the validation scores, the code version and the data snapshot.
Only a committed, unchanged frozen config can open the test seal (ADR-011).
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from gridcast.backtest.dataset import snapshot_id
from gridcast.backtest.engine import git_sha
from gridcast.backtest.runner import FEATURE_TABLES, run_validation
from gridcast.config import REPO_ROOT, Settings, load_yaml

FROZEN_DIR = REPO_ROOT / "configs" / "frozen"
FIXED_PARAMS = {"lear": "configs/models/lear.yaml", "sarimax": "configs/models/sarimax.yaml"}


def frozen_path(model: str) -> Path:
    return FROZEN_DIR / f"{model}.yaml"


def fixed_params(model: str) -> dict[str, Any]:
    rel = FIXED_PARAMS.get(model)
    return load_yaml(REPO_ROOT / rel) if rel else {}


def freeze(
    model: str,
    *,
    params: dict[str, Any] | None = None,
    calibrate: bool,
    source: str,
    data_dir: Path | None = None,
) -> Path:
    """Run the validation backtest with ``params`` and write the frozen config."""
    data_dir = data_dir or Settings().data_dir
    params = params if params is not None else fixed_params(model)
    _, scores = run_validation(
        model, data_dir=data_dir, params={model: params}, calibrate=calibrate
    )
    record = {
        "model": model,
        "params": params,
        "calibrate_cqr": calibrate,
        "params_source": source,
        "validation_scores": {k: round(v, 6) for k, v in scores.as_dict().items()},
        "frozen_at_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "code_git_sha": git_sha(),
        "data_snapshot_id": snapshot_id(data_dir, FEATURE_TABLES),
    }
    path = frozen_path(model)
    path.parent.mkdir(parents=True, exist_ok=True)
    header = (
        f"# Frozen {model} config (ADR-011). Do not edit; once committed it can unseal the test.\n"
    )
    path.write_text(header + yaml.safe_dump(record, sort_keys=False), encoding="utf-8")
    return path


def load_frozen(model: str) -> dict[str, Any]:
    return load_yaml(frozen_path(model))
