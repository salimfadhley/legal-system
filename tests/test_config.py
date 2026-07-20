"""Tests for the cross-project configuration loader."""

from __future__ import annotations

from pathlib import Path

import pytest

from goldberg_system.config import default_config_path, load_projects, project_path


def test_default_config_exists() -> None:
    assert default_config_path().is_file()


def test_load_projects_has_four_projects() -> None:
    cfg = load_projects()
    projects = cfg["projects"]
    assert set(projects) == {"system", "raw", "extracted", "casework"}


def test_halob_elasticsearch_configured() -> None:
    cfg = load_projects()
    assert cfg["halob"]["elasticsearch"] == "http://halob:9200"
    assert cfg["halob"]["elasticsearch_index"] == "goldberg_files"


def test_archive_is_recorded_and_separate() -> None:
    cfg = load_projects()
    # the frozen predecessor must be recorded but must NOT be one of the working projects
    assert "archive" in cfg
    assert "archive" not in cfg["projects"]


def test_project_path_returns_path() -> None:
    p = project_path("raw")
    assert isinstance(p, Path)
    assert p.name == "goldberg-raw"


def test_project_path_unknown_raises() -> None:
    with pytest.raises(KeyError):
        project_path("nonexistent")
