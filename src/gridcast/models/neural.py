"""Tier 3: neural forecasters via neuralforecast (N-HiTS, PatchTST, TFT) (M3-T7).

The DA OZP is one hourly series; DA prices for D-1 are known at the 09:00 issue time on
D-1, so the forecast horizon is exactly the 24 hours of D. Target: asinh-normalised price
(pre-registration §4). Exogenous inputs are builder features attached to each target hour
(all known at issue). Probabilistic output: MQLoss on the 21 levels.

Optional dependency group ``neural``: ``uv run --group neural ...``.
"""

from __future__ import annotations

import logging
import warnings
from collections.abc import Sequence
from datetime import date, datetime
from typing import Any

import numpy as np
import pandas as pd

from gridcast.config import load_project_config
from gridcast.models.base import QUANTILES, to_long
from gridcast.models.lear import _Normaliser

EXOGENOUS = (
    "wx_temperature_2m",
    "wx_wind_speed_100m",
    "wx_shortwave_radiation",
    "wx_cloud_cover",
    "demand_lag2",
    "rt_lag2",
    "nuclear_capability_lag2",
    "hour_ending",
    "dow",
    "is_holiday",
)
INPUT_HOURS = 7 * 24


def _column(model: str, q: float) -> str:
    """neuralforecast names MQLoss outputs by central-interval level."""
    if abs(q - 0.5) < 1e-9:
        return f"{model}-median"
    level = round(abs(1 - 2 * q) * 100, 6)
    return f"{model}-{'lo' if q < 0.5 else 'hi'}-{level}"


class NeuralQuantile:
    point_only = False

    def __init__(
        self, architecture: str = "NHITS", seed: int = 0, max_steps: int = 300, **params: Any
    ) -> None:
        self.architecture = architecture
        self.name = architecture.lower()
        self.seed = seed
        self.max_steps = max_steps
        self.input_size = int(params.pop("input_size", INPUT_HOURS))
        self.params = params
        self._nf: Any = None
        self._y_norm: _Normaliser | None = None
        self._x_norm: _Normaliser | None = None
        self._history: pd.DataFrame | None = None
        self._z_range = (-np.inf, np.inf)
        market = load_project_config().market
        self._price_bounds = (market.price_floor, market.price_cap)

    # ------------------------------------------------------------------ data
    def _frame(self, features: pd.DataFrame, y: pd.Series | None) -> pd.DataFrame:
        x = features[list(EXOGENOUS)].astype("float64").to_numpy()
        assert self._x_norm is not None
        exog = np.nan_to_num(self._x_norm.forward(x), nan=0.0)
        frame = pd.DataFrame(exog, columns=list(EXOGENOUS))
        frame.insert(0, "ds", features["ts_utc"].dt.tz_convert(None).to_numpy())
        frame.insert(0, "unique_id", "onzp")
        if y is not None:
            assert self._y_norm is not None
            z = self._y_norm.forward(y.to_numpy(dtype="float64")[:, None])[:, 0]
            frame["y"] = (
                pd.Series(z).interpolate(limit_direction="both").to_numpy()
            )  # e.g. 2025-05-22
        return frame.sort_values("ds").reset_index(drop=True)

    def _model(self) -> Any:
        from neuralforecast import models
        from neuralforecast.losses.pytorch import MQLoss

        cls = getattr(models, self.architecture)
        exog = {"futr_exog_list": list(EXOGENOUS)} if self.architecture != "PatchTST" else {}
        return cls(
            h=24,
            input_size=self.input_size,
            loss=MQLoss(quantiles=list(QUANTILES)),
            max_steps=self.max_steps,
            random_seed=self.seed,
            enable_progress_bar=False,
            logger=False,
            accelerator="cpu",
            enable_model_summary=False,
            **exog,
            **self.params,
        )

    # ------------------------------------------------------------------- api
    def fit(self, features: pd.DataFrame, target: pd.Series, *, as_of: datetime) -> None:
        from neuralforecast import NeuralForecast

        self._y_norm = _Normaliser.fit(target.to_numpy(dtype="float64")[:, None])
        self._x_norm = _Normaliser.fit(features[list(EXOGENOUS)].astype("float64").to_numpy())
        frame = self._frame(features, target)
        self._z_range = (float(frame["y"].min()), float(frame["y"].max()))
        logging.getLogger("pytorch_lightning").setLevel(logging.ERROR)
        logging.getLogger("lightning.pytorch").setLevel(logging.ERROR)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            self._nf = NeuralForecast(models=[self._model()], freq="h")
            self._nf.fit(df=frame)
        self._history = frame

    def observe(self, features: pd.DataFrame, target: pd.Series) -> None:
        """Prices published by the issue time (the engine calls this before each predict)."""
        self._history = self._frame(features, target).tail(self.input_size * 4)

    def predict_quantiles(
        self,
        features: pd.DataFrame,
        *,
        as_of: datetime,
        target_day: date,
        quantiles: Sequence[float] = QUANTILES,
    ) -> pd.DataFrame:
        if self._nf is None or self._history is None or self._y_norm is None:
            raise RuntimeError("fit() first")
        rows = features.sort_values("hour_ending")
        futr = self._frame(rows, None)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            out = self._nf.predict(
                df=self._history, futr_df=futr if self.architecture != "PatchTST" else None
            )
        out = out.sort_values("ds")
        name = type(self._nf.models[0]).__name__
        z = np.column_stack([out[_column(name, q)].to_numpy(dtype="float64") for q in quantiles])
        # sinh() turns an out-of-range network output into an absurd price, so outputs are
        # bounded to the training range (plus a margin) and prices to the settlement bounds.
        z = np.clip(np.nan_to_num(z, nan=0.0), self._z_range[0] - 1.0, self._z_range[1] + 1.0)
        values = np.clip(
            self._y_norm.inverse(z.reshape(-1, 1)).reshape(z.shape), *self._price_bounds
        )
        return to_long(rows["ts_utc"].reset_index(drop=True), values, quantiles)
