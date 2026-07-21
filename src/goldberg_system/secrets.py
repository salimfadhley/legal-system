"""Typed secrets loading.

Adapted from Mind of Steele's ``summarize/secrets.py`` and improved:

- **Environment first.** Reads from the environment (populated from a gitignored
  ``.env`` via ``python-dotenv`` when present), then falls back to
  ``~/.config/goldberg/secrets.toml`` — so the same code works on the Mac (dev)
  and on Halob (the service supplies env vars).
- **Whitespace-safe.** Strips stray whitespace from keys (MoS's stored key had a
  leading space that broke the ``sk-`` check).
- **No global mutation.** Returns typed settings rather than mutating
  ``os.environ`` as a side effect.
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


class OpenAISettings(BaseModel):
    """Validated OpenAI credentials/config."""

    api_key: str = Field(min_length=20, description="OpenAI API key (usually 'sk-…')")
    base_url: str | None = None
    organization: str | None = None


def _secrets_toml(app_dir: str = "goldberg") -> dict[str, Any]:
    path = Path.home() / ".config" / app_dir / "secrets.toml"
    if not path.exists():
        return {}
    return tomllib.loads(path.read_text(encoding="utf-8"))


def _load_dotenv() -> None:
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        pass


def load_openai_settings(app_dir: str = "goldberg") -> OpenAISettings:
    """Load OpenAI settings from the environment, then ``secrets.toml``.

    Raises:
        RuntimeError: if no API key can be found in either source.
    """
    _load_dotenv()
    api_key = os.environ.get("OPENAI_API_KEY")
    base_url = os.environ.get("OPENAI_BASE_URL")
    organization = os.environ.get("OPENAI_ORG_ID")

    if not api_key:
        openai_toml = _secrets_toml(app_dir).get("openai", {})
        api_key = openai_toml.get("api_key")
        base_url = base_url or openai_toml.get("base_url")
        organization = organization or openai_toml.get("organization")

    if api_key:
        api_key = api_key.strip()
    if not api_key:
        raise RuntimeError(
            "OpenAI API key not found. Set OPENAI_API_KEY (e.g. in .env) or add "
            "[openai] api_key to ~/.config/goldberg/secrets.toml."
        )
    return OpenAISettings(
        api_key=api_key,
        base_url=base_url.strip() if base_url else None,
        organization=organization.strip() if organization else None,
    )
