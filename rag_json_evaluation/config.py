"""Configuration loading without importing the legacy, eager API config."""

from __future__ import annotations

import copy
import os
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv


PACKAGE_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = PACKAGE_ROOT.parent

GROUP_CONFIG_FILES = {
    "G0": "g0_no_rag.yaml",
    "G1": "g1_mixed_uniform.yaml",
    "G2": "g2_routed_uniform.yaml",
    "G3": "g3_routed_differentiated.yaml",
}


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def _read_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Configuration must be a mapping: {path}")
    return data


def load_local_env() -> None:
    """Load optional local env files; never require credentials during import."""
    load_dotenv(PROJECT_ROOT / ".env", override=False)
    load_dotenv(PROJECT_ROOT / "LCA" / ".env", override=False)


def load_config(group: str) -> dict[str, Any]:
    group = group.upper()
    if group not in GROUP_CONFIG_FILES:
        raise ValueError(f"Unknown group {group!r}; expected one of {sorted(GROUP_CONFIG_FILES)}")

    common = _read_yaml(PACKAGE_ROOT / "configs" / "common.yaml")
    specific = _read_yaml(PACKAGE_ROOT / "configs" / GROUP_CONFIG_FILES[group])
    config = _deep_merge(common, specific)
    config["group"] = group

    for key in ("corpus_root", "indexes_root", "outputs_root", "schema_path", "plugin_contract_path"):
        config["paths"][key] = str((PACKAGE_ROOT / config["paths"][key]).resolve())
    return config


def env_value(*names: str, default: str | None = None) -> str | None:
    load_local_env()
    for name in names:
        value = os.environ.get(name)
        if value:
            return value
    return default


def require_env(*names: str) -> str:
    value = env_value(*names)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {' or '.join(names)}")
    return value

