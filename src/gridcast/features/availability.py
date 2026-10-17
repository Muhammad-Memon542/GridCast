"""Publication-lag rules for data without its own vintage stamp (``configs/availability.yaml``)."""

from __future__ import annotations

from datetime import time
from pathlib import Path

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field

from gridcast.config import Settings, load_yaml
from gridcast.timeutil.ieso import TS_DTYPE

AVAILABILITY_CONFIG_FILE = "availability.yaml"


class _Rule(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class LocalTimeRule(_Rule):
    """``local_time`` in America/Toronto on ``delivery_date + days_after``."""

    local_time: time
    days_after: int


class EstTimeRule(_Rule):
    """``est_time`` on the IESO (EST) clock on ``delivery_date + days_after``."""

    est_time: time
    days_after: int


class AfterEndRule(_Rule):
    minutes_after_end: int = Field(ge=0)


class AvailabilityConfig(_Rule):
    listing_margin_minutes: int = Field(ge=0)
    da_ozp_gridstatus: LocalTimeRule
    rt_ozp_gridstatus: AfterEndRule
    demand_ieso: EstTimeRule
    hoep_ieso: EstTimeRule
    gen_month_ieso: EstTimeRule
    intertie_ieso: EstTimeRule


def load_availability_config(path: Path | None = None) -> AvailabilityConfig:
    if path is None:
        path = Settings().configs_dir / AVAILABILITY_CONFIG_FILE
    return AvailabilityConfig.model_validate(load_yaml(path))


def _day_start(delivery_date: pd.Series) -> pd.Series:
    return pd.to_datetime(delivery_date).dt.tz_localize(None).astype("datetime64[us]")


def local_time_rule(delivery_date: pd.Series, rule: LocalTimeRule) -> pd.Series:
    clock = pd.Timedelta(hours=rule.local_time.hour, minutes=rule.local_time.minute)
    local = _day_start(delivery_date) + pd.Timedelta(days=rule.days_after) + clock
    return local.dt.tz_localize("America/Toronto").dt.tz_convert("UTC").astype(TS_DTYPE)


def est_time_rule(delivery_date: pd.Series, rule: EstTimeRule) -> pd.Series:
    clock = pd.Timedelta(hours=rule.est_time.hour + 5, minutes=rule.est_time.minute)
    utc = _day_start(delivery_date) + pd.Timedelta(days=rule.days_after) + clock
    return utc.dt.tz_localize("UTC").astype(TS_DTYPE)


def after_end_rule(interval_end_utc: pd.Series, rule: AfterEndRule) -> pd.Series:
    return (interval_end_utc + pd.Timedelta(minutes=rule.minutes_after_end)).astype(TS_DTYPE)


def ieso_file_available(
    doc_created_at_utc: pd.Series, listing_modified_utc: pd.Series, margin_minutes: int
) -> pd.Series:
    """max(CreatedAt, listing time + margin); the listing alone when CreatedAt is missing."""
    listed = pd.to_datetime(listing_modified_utc, utc=True) + pd.Timedelta(minutes=margin_minutes)
    created = pd.to_datetime(doc_created_at_utc, utc=True)
    return pd.concat([created, listed], axis=1).max(axis=1).astype(TS_DTYPE)
