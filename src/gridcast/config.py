"""Configuration: environment settings and the typed, pre-registered project config.

Two layers, deliberately separate:

* :class:`Settings` - per-machine values from environment variables or ``.env``
  (paths, contact details for the User-Agent, API keys). Secrets are ``SecretStr``
  so they never appear in logs, reprs or result files.
* :class:`ProjectConfig` - research constants loaded from ``configs/project.yaml``
  (issue time, quantile grid, splits, battery defaults). These are pre-registered;
  :meth:`ProjectConfig.config_hash` is stamped on every result file.
"""

from __future__ import annotations

import hashlib
import json
import math
from datetime import date, time, timedelta
from itertools import pairwise
from pathlib import Path
from typing import Any, Literal, Self
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    field_validator,
    model_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict

from gridcast import __version__

REPO_ROOT = Path(__file__).resolve().parents[2]
PROJECT_CONFIG_FILE = "project.yaml"


class Settings(BaseSettings):
    """Deployment settings. Read from ``GRIDCAST_*`` environment variables or ``.env``."""

    model_config = SettingsConfigDict(
        env_prefix="GRIDCAST_",
        env_file=REPO_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
    )

    env: Literal["dev", "ci", "prod"] = "dev"
    data_dir: Path = REPO_ROOT / "data"
    configs_dir: Path = REPO_ROOT / "configs"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    contact: str | None = None
    """Email or URL put in the User-Agent so data providers can reach us [OWNER]."""
    http_min_interval_s: float = Field(default=1.0, ge=1.0)
    """Minimum seconds between requests to one host (plan §0 rule 10: <= 1 request/second)."""
    gridstatus_api_key: SecretStr | None = Field(
        default=None, validation_alias="GRIDSTATUS_API_KEY"
    )
    """Read from ``GRIDSTATUS_API_KEY``, the name the gridstatusio client also uses."""

    @property
    def user_agent(self) -> str:
        contact = self.contact or "contact-unset"
        return f"GridCast/{__version__} (non-commercial research; +{contact})"


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class MarketConfig(_Frozen):
    timezone: str
    report_utc_offset_hours: int
    """IESO reports index hours on Eastern Standard Time all year (decisions.md, V-02)."""
    go_live: date
    price_floor: float
    """Settlement floor for energy, $/MWh (decisions.md, V-04)."""
    price_cap: float
    """Maximum market clearing price, $/MWh (decisions.md, V-04)."""

    @field_validator("timezone")
    @classmethod
    def _known_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"unknown IANA time zone: {value!r}") from exc
        return value

    @model_validator(mode="after")
    def _floor_below_cap(self) -> Self:
        if self.price_floor >= self.price_cap:
            raise ValueError("price_floor must be below price_cap")
        return self


class ForecastConfig(_Frozen):
    issue_time_local: time
    issue_day_offset: int = Field(le=-1)
    """Days between the issue date and the target day; -1 means D-1."""
    quantiles: tuple[float, ...]

    @field_validator("quantiles")
    @classmethod
    def _valid_grid(cls, value: tuple[float, ...]) -> tuple[float, ...]:
        if not all(0.0 < q < 1.0 for q in value):
            raise ValueError("quantile levels must lie strictly between 0 and 1")
        if any(b <= a for a, b in pairwise(value)):
            raise ValueError("quantile levels must be strictly increasing")
        if 0.5 not in value:
            raise ValueError("quantile grid must contain the median")
        if not all(any(math.isclose(1.0 - q, p) for p in value) for q in value):
            raise ValueError("quantile grid must be symmetric around 0.5")
        return value


class Split(_Frozen):
    start: date
    end: date | None = None
    """Inclusive. ``None`` means open-ended (latest available data)."""

    @model_validator(mode="after")
    def _ordered(self) -> Self:
        if self.end is not None and self.end < self.start:
            raise ValueError(f"split ends ({self.end}) before it starts ({self.start})")
        return self


class SplitsConfig(_Frozen):
    old_market: Split
    validation: Split
    test: Split

    @model_validator(mode="after")
    def _contiguous(self) -> Self:
        named = [
            ("old_market", self.old_market),
            ("validation", self.validation),
            ("test", self.test),
        ]
        for (name, split), (next_name, nxt) in pairwise(named):
            if split.end is None:
                raise ValueError(f"only the test split may be open-ended, not {name}")
            if nxt.start != split.end + timedelta(days=1):
                raise ValueError(f"{next_name} must start the day after {name} ends")
        return self


class BatteryConfig(_Frozen):
    power_mw: float = Field(gt=0)
    duration_h: float = Field(gt=0)
    soc_min_frac: float = Field(ge=0, le=1)
    soc_max_frac: float = Field(ge=0, le=1)
    round_trip_efficiency: float = Field(gt=0, le=1)
    degradation_cost_per_mwh: float = Field(ge=0)

    @model_validator(mode="after")
    def _soc_bounds(self) -> Self:
        if self.soc_min_frac >= self.soc_max_frac:
            raise ValueError("soc_min_frac must be below soc_max_frac")
        return self

    @property
    def energy_mwh(self) -> float:
        return self.power_mw * self.duration_h

    @property
    def one_way_efficiency(self) -> float:
        """Charge and discharge efficiency, each sqrt(round trip) (plan §11)."""
        return math.sqrt(self.round_trip_efficiency)


class ProjectConfig(_Frozen):
    market: MarketConfig
    forecast: ForecastConfig
    splits: SplitsConfig
    battery: BatteryConfig

    @model_validator(mode="after")
    def _validation_starts_at_go_live(self) -> Self:
        if self.splits.validation.start != self.market.go_live:
            raise ValueError("the validation split must start on the market go-live date")
        return self

    def config_hash(self) -> str:
        """SHA-256 of the canonical JSON form; independent of YAML key order and comments."""
        canonical = json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def load_yaml(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a YAML mapping at the top level")
    return data


def load_project_config(path: Path | None = None) -> ProjectConfig:
    """Load and validate ``configs/project.yaml`` (or ``path``)."""
    if path is None:
        path = Settings().configs_dir / PROJECT_CONFIG_FILE
    return ProjectConfig.model_validate(load_yaml(path))
