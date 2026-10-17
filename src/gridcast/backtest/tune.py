"""Hyperparameter tuning on the validation window only (pre-registration §3/§4, M3-T6).

Each Optuna trial is a full rolling-origin backtest over ``validation_eval`` (the test
window is sealed); the objective is mean CRPS. The study lives in
``gold/tuning/<model>.db`` so it can be resumed.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import optuna

from gridcast.backtest.runner import run_validation

SEED = 0


def lgbm_space(trial: optuna.Trial) -> dict[str, Any]:
    return {
        "n_estimators": trial.suggest_int("n_estimators", 100, 800, step=50),
        "learning_rate": trial.suggest_float("learning_rate", 0.01, 0.2, log=True),
        "num_leaves": trial.suggest_int("num_leaves", 4, 63),
        "min_child_samples": trial.suggest_int("min_child_samples", 5, 100),
        "colsample_bytree": trial.suggest_float("colsample_bytree", 0.4, 1.0),
        "subsample": trial.suggest_float("subsample", 0.5, 1.0),
        "reg_lambda": trial.suggest_float("reg_lambda", 1e-3, 10.0, log=True),
    }


SPACES = {"lgbm": lgbm_space}


def tune(model: str, trials: int, data_dir: Path) -> optuna.Study:
    storage_dir = data_dir / "gold" / "tuning"
    storage_dir.mkdir(parents=True, exist_ok=True)
    study = optuna.create_study(
        study_name=model,
        storage=f"sqlite:///{storage_dir / f'{model}.db'}",
        direction="minimize",
        sampler=optuna.samplers.TPESampler(seed=SEED),
        load_if_exists=True,
    )

    def objective(trial: optuna.Trial) -> float:
        params = SPACES[model](trial)
        _, scores = run_validation(model, data_dir=data_dir, params={model: params})
        for key, value in scores.as_dict().items():
            trial.set_user_attr(key, value)
        return scores.crps

    remaining = max(trials - len([t for t in study.trials if t.state.is_finished()]), 0)
    study.optimize(objective, n_trials=remaining)
    return study
