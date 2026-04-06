"""Configuration loader with YAML + ${ENV_VAR} interpolation."""

import os
import re
from pathlib import Path

import yaml


_ENV_PATTERN = re.compile(r"\$\{(\w+)(?::([^}]*))?\}")


def _interpolate_env(value: str) -> str:
    """Replace ${VAR} or ${VAR:default} with environment variable values."""
    def _replace(match):
        var_name = match.group(1)
        default = match.group(2)
        val = os.environ.get(var_name)
        if val is not None:
            return val
        if default is not None:
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


def load_config(path: str | Path) -> dict:
    """Load and validate a joshua YAML config file."""
    path = Path(path).expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(f"Config not found: {path}")

    with open(path) as f:
        raw = yaml.safe_load(f)

    if not isinstance(raw, dict):
        raise ValueError(f"Config must be a YAML mapping, got {type(raw).__name__}")

    config = _walk_interpolate(raw)

    # Expand ~ in paths
    if "project" in config and "path" in config["project"]:
        config["project"]["path"] = str(Path(config["project"]["path"]).expanduser())
    if "memory" in config and "state_dir" in config["memory"]:
        config["memory"]["state_dir"] = str(Path(config["memory"]["state_dir"]).expanduser())
    if "tracker" in config and "dir" in config.get("tracker", {}):
        config["tracker"]["dir"] = str(Path(config["tracker"]["dir"]).expanduser())

    _validate(config)
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
