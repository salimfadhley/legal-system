"""Tests for the secrets loader (env-first, whitespace-safe)."""

from __future__ import annotations

import pytest

from goldberg_system.secrets import load_openai_settings


def test_reads_from_env_and_strips_whitespace(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "  sk-test-abcdefghijklmnopqrst  ")
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    settings = load_openai_settings()
    assert settings.api_key == "sk-test-abcdefghijklmnopqrst"


def test_env_base_url_and_org(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-abcdefghijklmnopqrst")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://example.test/v1")
    monkeypatch.setenv("OPENAI_ORG_ID", "org_x")
    settings = load_openai_settings()
    assert settings.base_url == "https://example.test/v1"
    assert settings.organization == "org_x"


def test_missing_key_raises(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    # stub out .env loading (else load_dotenv finds the repo .env by caller
    # location) and point HOME at an empty dir so no secrets.toml is found
    monkeypatch.setattr("goldberg_system.secrets._load_dotenv", lambda: None)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(RuntimeError):
        load_openai_settings(app_dir="goldberg-nonexistent")
