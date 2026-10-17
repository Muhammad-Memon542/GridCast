"""Point-in-time feature builder (plan §7.5, M1-T8).

``build_features(inputs, target_day, issue)`` returns one row per hour of ``target_day``
using only input rows with ``available_at_utc <= issue``. Every row also carries
``_max_available_at``, the latest availability among the inputs it used, so the leakage
tests can check the guarantee directly.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from functools import cache
from pathlib import Path

import holidays
import pandas as pd
from pydantic import BaseModel, ConfigDict, Field

from gridcast.config import Settings, load_yaml
from gridcast.quality.schemas import WEATHER_VARS
from gridcast.timeutil.ieso import TS_DTYPE, he_to_ts_utc

FEATURES_CONFIG_FILE = "features.yaml"
SOURCE_RANK = {"ieso": 0, "gridstatus": 1}


class FeatureConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    price_lags_days: tuple[int, ...] = Field(min_length=1)
    rt_lags_days: tuple[int, ...]
    demand_lags_days: tuple[int, ...]
    weather_weights: dict[str, float]
    gen_lags_days: tuple[int, ...] = ()
    gen_features: dict[str, tuple[str, ...]] = Field(default_factory=dict)
    """Fuel -> columns of ``gen_by_fuel`` to lag, e.g. ``{"nuclear": ("capability_mw",)}``."""
    intertie_lags_days: tuple[int, ...] = ()


def load_feature_config(path: Path | None = None) -> FeatureConfig:
    if path is None:
        path = Settings().configs_dir / FEATURES_CONFIG_FILE
    return FeatureConfig.model_validate(load_yaml(path))


@dataclass(frozen=True)
class FeatureInputs:
    """Silver tables the builder reads. Tests pass synthetic frames with the same columns."""

    da_ozp: pd.DataFrame
    rt_hourly: pd.DataFrame
    demand: pd.DataFrame
    weather: pd.DataFrame
    gen_by_fuel: pd.DataFrame | None = None
    intertie: pd.DataFrame | None = None

    @classmethod
    def from_silver(cls, data_dir: Path) -> FeatureInputs:
        def read(name: str) -> pd.DataFrame:
            frame = pd.read_parquet(data_dir / "silver" / name)
            return frame.drop(columns=["month"], errors="ignore")

        return cls(
            da_ozp=read("da_ozp"),
            rt_hourly=read("rt_ozp_hourly"),
            demand=read("demand"),
            weather=read("weather_fcst"),
            gen_by_fuel=read("gen_by_fuel"),
            intertie=read("intertie"),
        )


def known_at(frame: pd.DataFrame, issue: datetime) -> pd.DataFrame:
    """Rows already published at ``issue``. The single gate every input passes through."""
    return frame[frame["available_at_utc"] <= pd.Timestamp(issue)]


def latest_per_ts(frame: pd.DataFrame, ts_col: str = "ts_utc") -> pd.DataFrame:
    """One row per timestamp: IESO before Grid Status, then the highest version."""
    if frame.empty:
        return frame
    ranked = frame.assign(
        _rank=frame["source"].map(SOURCE_RANK) if "source" in frame else 0,
        _version=frame["version"].astype("float64").fillna(0) if "version" in frame else 0,
    )
    ranked = ranked.sort_values([ts_col, "_rank", "_version"], ascending=[True, True, False])
    return ranked.drop_duplicates(ts_col, keep="first").drop(columns=["_rank", "_version"])


def choose_lead(
    valid: datetime, issue: datetime, leads: tuple[int, ...], latency: timedelta
) -> int | None:
    """Smallest lead N with valid - N days + latency <= issue (ADR-003); None if none qualifies."""
    for lead in sorted(leads):
        if valid - timedelta(days=lead) + latency <= issue:
            return lead
    return None


@cache
def _ontario_holidays(year: int) -> frozenset[date]:
    return frozenset(holidays.country_holidays("CA", subdiv="ON", years=year))


def _lagged(
    target: pd.DataFrame, table: pd.DataFrame, value: str, lag_days: int, name: str
) -> tuple[pd.Series, pd.Series]:
    """Value of ``table`` at the same hour ``lag_days`` earlier, plus its availability."""
    shifted = target["ts_utc"] - pd.Timedelta(days=lag_days)
    lookup = table.set_index("ts_utc")
    values = (
        shifted.map(lookup[value])
        if not lookup.empty
        else pd.Series(float("nan"), index=target.index)
    )
    avail = (
        shifted.map(lookup["available_at_utc"])
        if not lookup.empty
        else pd.Series(pd.NaT, index=target.index, dtype=TS_DTYPE)
    )
    return values.rename(name).astype("float64"), avail.astype(TS_DTYPE)


def build_features(
    inputs: FeatureInputs,
    target_day: date,
    issue: datetime,
    config: FeatureConfig,
) -> pd.DataFrame:
    hours = pd.Series(range(1, 25), dtype="int8")
    out = pd.DataFrame({"delivery_date": [target_day] * 24, "hour_ending": hours})
    out["ts_utc"] = he_to_ts_utc(out["delivery_date"], out["hour_ending"])
    out["dow"] = pd.Series([target_day.weekday()] * 24, dtype="int8")
    out["month"] = pd.Series([target_day.month] * 24, dtype="int8")
    out["is_holiday"] = target_day in _ontario_holidays(target_day.year)
    out["is_weekend"] = target_day.weekday() >= 5
    availability: list[pd.Series] = []

    da = latest_per_ts(known_at(inputs.da_ozp, issue))
    for lag in config.price_lags_days:
        values, avail = _lagged(out, da, "zonal_price", lag, f"da_lag{lag}")
        out[values.name] = values
        availability.append(avail)

    rt = latest_per_ts(known_at(inputs.rt_hourly, issue))
    for lag in config.rt_lags_days:
        values, avail = _lagged(out, rt, "lmp_mean", lag, f"rt_lag{lag}")
        out[values.name] = values
        availability.append(avail)

    demand = latest_per_ts(known_at(inputs.demand, issue))
    for lag in config.demand_lags_days:
        values, avail = _lagged(out, demand, "ontario_demand", lag, f"demand_lag{lag}")
        out[values.name] = values
        availability.append(avail)

    if inputs.gen_by_fuel is not None:
        gen = known_at(inputs.gen_by_fuel, issue)
        for fuel, columns in config.gen_features.items():
            rows = gen[gen["fuel"] == fuel].drop_duplicates("ts_utc", keep="last")
            for col in columns:
                for lag in config.gen_lags_days:
                    name = f"{fuel}_{col.removesuffix('_mw')}_lag{lag}"
                    values, avail = _lagged(out, rows, col, lag, name)
                    out[name] = values
                    availability.append(avail)

    if inputs.intertie is not None:
        intertie = latest_per_ts(known_at(inputs.intertie, issue))
        for lag in config.intertie_lags_days:
            for col, short in [
                ("total_flow_mw", "flow"),
                ("imports_sched_mw", "imports"),
                ("exports_sched_mw", "exports"),
            ]:
                values, avail = _lagged(out, intertie, col, lag, f"intertie_{short}_lag{lag}")
                out[values.name] = values
                availability.append(avail)

    weather, weather_avail, lead = _weather_features(out, inputs.weather, issue, config)
    out = pd.concat([out, weather], axis=1)
    out["weather_lead_days"] = lead
    availability.append(weather_avail)

    out["_max_available_at"] = pd.concat(availability, axis=1).max(axis=1).astype(TS_DTYPE)
    return out


def _weather_features(
    target: pd.DataFrame, weather: pd.DataFrame, issue: datetime, config: FeatureConfig
) -> tuple[pd.DataFrame, pd.Series, pd.Series]:
    """Weighted mean over points of the freshest forecast already published at ``issue``."""
    known = known_at(weather, issue)
    known = known[
        known["valid_ts_utc"].isin(target["ts_utc"]) & known["point"].isin(config.weather_weights)
    ]
    # Freshest = smallest lead among published values (ADR-003 rule, via available_at).
    known = known.sort_values(["point", "valid_ts_utc", "lead_days"]).drop_duplicates(
        ["point", "valid_ts_utc"], keep="first"
    )
    weighted = known.assign(_w=known["point"].map(config.weather_weights).astype("float64"))
    rows = []
    for valid, group in weighted.groupby("valid_ts_utc", sort=True):
        row: dict[str, object] = {"ts_utc": valid}
        for var in WEATHER_VARS:
            ok = group[var].notna()
            w = group.loc[ok, "_w"]
            row[f"wx_{var}"] = (
                float((group.loc[ok, var] * w).sum() / w.sum()) if w.sum() > 0 else float("nan")
            )
        row["_avail"] = group["available_at_utc"].max()
        row["_lead"] = group["lead_days"].max()
        rows.append(row)
    columns = ["ts_utc", *[f"wx_{v}" for v in WEATHER_VARS], "_avail", "_lead"]
    agg = pd.DataFrame(rows, columns=columns)
    agg["ts_utc"] = agg["ts_utc"].astype(TS_DTYPE)
    agg["_avail"] = agg["_avail"].astype(TS_DTYPE)
    merged = target[["ts_utc"]].merge(agg, on="ts_utc", how="left")
    merged.index = target.index
    result = merged[[f"wx_{v}" for v in WEATHER_VARS]].astype("float64")
    avail, lead = merged["_avail"], merged["_lead"]
    return result, avail.astype(TS_DTYPE), lead.astype("Int8")
