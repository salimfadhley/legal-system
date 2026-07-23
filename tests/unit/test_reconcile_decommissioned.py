"""WP05 — the polling reconciler daemon is decommissioned (ADR 0013 supersedes 0011).

Guards the removal so it cannot silently regress: the ``goldberg watch`` CLI command
is gone, its event-driven replacement (``goldberg ingest-serve``) is the registered
ingest path, and the ``goldberg_system.reconcile`` daemon package no longer imports.
The *separate* ``goldberg_system.observability.reconcile`` module (drift/completeness
reporting — a different concern) must survive untouched.
"""

from __future__ import annotations

import importlib

import pytest

from goldberg_system.cli import main


def test_watch_command_is_removed() -> None:
    assert "watch" not in main.commands


def test_ingest_serve_is_the_registered_ingest_path() -> None:
    assert "ingest-serve" in main.commands


def test_reconcile_daemon_package_is_gone() -> None:
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("goldberg_system.reconcile")


def test_observability_reconcile_module_survives() -> None:
    mod = importlib.import_module("goldberg_system.observability.reconcile")
    # completeness/drift reporting API is intact (this is NOT the deleted daemon)
    assert hasattr(mod, "reconcile")
    assert hasattr(mod, "Reconciler")
