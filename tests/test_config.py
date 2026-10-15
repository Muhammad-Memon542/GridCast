from __future__ import annotations

import copy
from datetime import date, time
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from gridcast.config import (
    REPO_ROOT,
    ProjectConfig,
    Settings,
    load_project_config,
    load_yaml,
)

PROJECT_YAML = REPO_ROOT / "configs" / "project.yaml"
# The grid exactly as written in plan §7.6.
PLAN_QUANTILES = (0.01, *[round(i / 20, 2) for i in range(1, 20)], 0.99)


@pytest.fixture
def raw() -> dict[str, Any]:
    return load_yaml(PROJECT_YAML)


# ------------------------------------------------------------------ Settings


def test_settings_defaults(clean_env: pytest.MonkeyPatch) -> None:
    s = Settings(_env_file=None)
    assert s.env == "dev"
    assert s.http_min_interval_s == 1.0
    assert s.gridstatus_api_key is None
    assert s.configs_dir == REPO_ROOT / "configs"
    assert "contact-unset" in s.user_agent


def test_settings_read_from_environment(clean_env: pytest.MonkeyPatch, tmp_path: Path) -> None:
    clean_env.setenv("GRIDCAST_ENV", "ci")
    clean_env.setenv("GRIDCAST_DATA_DIR", str(tmp_path))
    clean_env.setenv("GRIDCAST_CONTACT", "ops@example.org")
    s = Settings(_env_file=None)
    assert s.env == "ci"
    assert s.data_dir == tmp_path
    assert s.user_agent.endswith("+ops@example.org)")


def test_settings_read_from_env_file(clean_env: pytest.MonkeyPatch, tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("GRIDCAST_LOG_LEVEL=DEBUG\n", encoding="utf-8")
    assert Settings(_env_file=env_file).log_level == "DEBUG"


def test_secret_is_never_rendered(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("GRIDSTATUS_API_KEY", "not-a-real-key-123")
    s = Settings(_env_file=None)
    assert s.gridstatus_api_key is not None
    assert s.gridstatus_api_key.get_secret_value() == "not-a-real-key-123"
    for rendered in (repr(s), str(s), s.model_dump_json()):
        assert "not-a-real-key-123" not in rendered


def test_request_rate_cannot_exceed_one_per_second(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("GRIDCAST_HTTP_MIN_INTERVAL_S", "0.2")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


# ------------------------------------------------------------- ProjectConfig


def test_project_config_matches_plan() -> None:
    cfg = load_project_config(PROJECT_YAML)
    assert cfg.forecast.quantiles == PLAN_QUANTILES
    assert len(cfg.forecast.quantiles) == 21
    assert cfg.forecast.issue_time_local == time(9, 0)
    assert cfg.forecast.issue_day_offset == -1
    assert cfg.market.go_live == date(2025, 5, 1)
    assert cfg.market.report_utc_offset_hours == -5
    assert cfg.splits.validation.end == date(2025, 10, 31)
    assert cfg.splits.test.start == date(2025, 11, 1)
    assert cfg.splits.test.end is None
    assert cfg.battery.energy_mwh == 400.0
    assert cfg.battery.one_way_efficiency**2 == pytest.approx(0.88)


def test_default_path_uses_settings(clean_env: pytest.MonkeyPatch) -> None:
    assert load_project_config() == load_project_config(PROJECT_YAML)


def test_config_hash_ignores_key_order_and_comments(raw: dict[str, Any], tmp_path: Path) -> None:
    reordered = {k: raw[k] for k in reversed(list(raw))}
    path = tmp_path / "reordered.yaml"
    path.write_text(yaml.safe_dump(reordered, sort_keys=False), encoding="utf-8")
    original = load_project_config(PROJECT_YAML)
    assert load_project_config(path).config_hash() == original.config_hash()


def test_config_hash_changes_with_any_value() -> None:
    cfg = load_project_config(PROJECT_YAML)
    changed = cfg.model_copy(update={"battery": cfg.battery.model_copy(update={"power_mw": 50.0})})
    assert len(cfg.config_hash()) == 64
    assert changed.config_hash() != cfg.config_hash()


@pytest.mark.parametrize(
    "quantiles",
    [
        [0.1, 0.5, 0.5, 0.9],  # not strictly increasing
        [0.0, 0.5, 1.0],  # outside (0, 1)
        [0.1, 0.4, 0.9],  # no median
        [0.1, 0.5, 0.8],  # asymmetric
    ],
)
def test_invalid_quantile_grids_rejected(raw: dict[str, Any], quantiles: list[float]) -> None:
    raw["forecast"]["quantiles"] = quantiles
    with pytest.raises(ValidationError):
        ProjectConfig.model_validate(raw)


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("splits", "validation", "start"), date(2025, 5, 2)),  # gap after old market
        (("splits", "test", "start"), date(2025, 10, 31)),  # overlaps validation
        (("splits", "validation", "end"), None),  # only test may be open-ended
        (("splits", "old_market", "end"), date(2018, 1, 1)),  # ends before it starts
        (("market", "go_live"), date(2025, 6, 1)),  # validation must start at go-live
        (("market", "timezone"), "Mars/Olympus_Mons"),
        (("market", "price_floor"), 5000.0),  # floor above cap
        (("battery", "soc_min_frac"), 0.95),  # min above max
        (("forecast", "issue_day_offset"), 0),  # would issue on the delivery day
        (("forecast", "unexpected_key"), 1),  # unknown keys are rejected
    ],
)
def test_invalid_configs_rejected(
    raw: dict[str, Any], path: tuple[str, ...], value: object
) -> None:
    broken = copy.deepcopy(raw)
    node = broken
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = value
    with pytest.raises(ValidationError):
        ProjectConfig.model_validate(broken)


def test_config_is_immutable() -> None:
    cfg = load_project_config(PROJECT_YAML)
    with pytest.raises(ValidationError):
        cfg.battery.power_mw = 1.0  # type: ignore[misc]


def test_non_mapping_yaml_rejected(tmp_path: Path) -> None:
    path = tmp_path / "list.yaml"
    path.write_text("- 1\n- 2\n", encoding="utf-8")
    with pytest.raises(ValueError, match="mapping"):
        load_yaml(path)
