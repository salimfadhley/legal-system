"""Load the platform's cross-project configuration (config/projects.yaml).

This module is the one place that knows where the sibling repos and Halob
services live. Everything else asks it rather than hard-coding paths.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

# repo layout: <root>/src/goldberg_system/config.py -> parents[2] == <root>
_REPO_ROOT = Path(__file__).resolve().parents[2]

# Env override so a pip-installed container (where the repo layout above does not
# hold) can point at a host-tuned config, e.g. GOLDBERG_PROJECTS_CONFIG=/app/config/projects.yaml.
_CONFIG_ENV = "GOLDBERG_PROJECTS_CONFIG"


def default_config_path() -> Path:
    """Return the config path: the ``GOLDBERG_PROJECTS_CONFIG`` env override if
    set, else the bundled ``config/projects.yaml`` next to the repo."""
    env = os.environ.get(_CONFIG_ENV)
    if env:
        return Path(env)
    return _REPO_ROOT / "config" / "projects.yaml"


def load_projects(path: Path | str | None = None) -> dict[str, Any]:
    """Load and return the projects configuration as a dict.

    Args:
        path: Optional override for the config file location.

    Raises:
        FileNotFoundError: if the config file does not exist.
    """
    cfg_path = Path(path) if path is not None else default_config_path()
    if not cfg_path.is_file():
        raise FileNotFoundError(f"projects config not found: {cfg_path}")
    with cfg_path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if not isinstance(data, dict):
        raise ValueError(f"projects config is not a mapping: {cfg_path}")
    return data


def project_path(name: str, path: Path | str | None = None) -> Path:
    """Return the filesystem Path for a named sibling project.

    Args:
        name: One of the keys under `projects` (system, raw, extracted, casework).

    Raises:
        KeyError: if the project name is unknown.
    """
    projects = load_projects(path).get("projects", {})
    if name not in projects:
        raise KeyError(f"unknown project '{name}'; known: {sorted(projects)}")
    return Path(projects[name]["path"])
