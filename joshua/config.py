"""Configuration loader with YAML + ${ENV_VAR} interpolation."""

from __future__ import annotations

import os
import re
from pathlib import Path

import yaml

from pydantic import ValidationError

from joshua.config_schema import JoshuaConfig


_ENV_PATTERN = re.compile(r"\$\{(\w+)(?::([^}]*))?\}")


_DANGEROUS_DEFAULT = re.compile(r"[;&|`\n\r]|\$[\({a-zA-Z]")


def _interpolate_env(value: str) -> str:
    """Replace ${VAR} or ${VAR:default} with environment variable values.

    Default values are checked for shell metacharacters to prevent injection
    via crafted configs (e.g. ${MISSING:rm -rf /}).
    """
    def _replace(match):
        var_name = match.group(1)
        default = match.group(2)
        val = os.environ.get(var_name)
        if val is not None:
            return val
        if default is not None:
            if _DANGEROUS_DEFAULT.search(default):
                raise ValueError(
                    f"${{{var_name}}} default value contains shell metacharacters: "
                    f"'{default[:50]}'. Use a safe literal or set the env var."
                )
            return default
        return match.group(0)  # leave as-is if not found and no default

    return _ENV_PATTERN.sub(_replace, value)


def _walk_interpolate(obj):
    """Recursively interpolate env vars in all string values."""
    if isinstance(obj, str):
        return _interpolate_env(obj)
    if isinstance(obj, dict):
        return {k: _walk_interpolate(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_walk_interpolate(i) for i in obj]
    return obj


def _deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge override into base. Override wins on conflicts."""
    result = dict(base)
    for k, v in override.items():
        if k in result and isinstance(result[k], dict) and isinstance(v, dict):
            result[k] = _deep_merge(result[k], v)
        else:
            result[k] = v
    return result


def _resolve_base(raw: dict, config_path: Path, _depth: int = 0) -> dict:
    """Resolve `base:` inheritance — load parent config and deep-merge."""
    if _depth > 10:
        raise ValueError("Config inheritance depth > 10 — possible circular reference")
    base_ref = raw.pop("base", None)
    if not base_ref:
        return raw
    base_path = (config_path.parent / base_ref).resolve()
    if not base_path.exists():
        raise FileNotFoundError(f"base config not found: {base_ref} (resolved: {base_path})")
    with open(base_path) as f:
        base_raw = yaml.safe_load(f) or {}
    base_raw = _resolve_base(base_raw, base_path, _depth + 1)
    return _deep_merge(base_raw, raw)


def load_config(path: str | Path) -> dict:
    """Load and validate a joshua YAML config file."""
    path = Path(path).expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(f"Config not found: {path}")

    with open(path) as f:
        raw = yaml.safe_load(f)

    if not isinstance(raw, dict):
        raise ValueError(f"Config must be a YAML mapping, got {type(raw).__name__}")

    raw = _resolve_base(raw, path)

    config = _walk_interpolate(raw)

    # Expand ~ in paths
    if "project" in config and "path" in config["project"]:
        config["project"]["path"] = str(Path(config["project"]["path"]).expanduser())
    if "memory" in config and "state_dir" in config["memory"]:
        config["memory"]["state_dir"] = str(Path(config["memory"]["state_dir"]).expanduser())
    if "tracker" in config and "dir" in config.get("tracker", {}):
        config["tracker"]["dir"] = str(Path(config["tracker"]["dir"]).expanduser())

    _validate(config)

    # Validate with Pydantic schema
    try:
        JoshuaConfig.model_validate(config)
    except ValidationError as e:
        errors = []
        for err in e.errors():
            loc = " -> ".join(str(x) for x in err["loc"])
            errors.append(f"  {loc}: {err['msg']}")
        raise ValueError("Config validation failed:\n" + "\n".join(errors)) from None

    return config


def _validate(config: dict):
    """Validate required config fields."""
    if "project" not in config:
        raise ValueError("Config must have a 'project' section")
    project = config["project"]
    if "name" not in project:
        raise ValueError("project.name is required")
    if "path" not in project:
        raise ValueError("project.path is required")

    # Runner defaults to claude if not specified
    config.setdefault("runner", {"type": "claude"})
    runner = config["runner"]
    valid_types = {"claude", "codex", "aider", "custom"}
    runner_type = runner.get("type", "claude")
    if runner_type not in valid_types:
        raise ValueError(f"runner.type must be one of {valid_types}, got '{runner_type}'")

    if "agents" not in config:
        raise ValueError("Config must have an 'agents' section with at least one agent")
