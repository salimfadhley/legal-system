"""Direct docling-serve client — the bulk extraction path (M8 fix).

Papra 26.4.0 ignores its Docling config and falls back to a slow, crash-prone
internal OCR, so the bulk migration extracts by calling ``docling-serve`` directly:
``goldberg-raw`` file → Docling → markdown. Docling is healthy, fast, and
purpose-built for OCR/layout; this bypasses Papra's broken extraction entirely
(Papra stays for the live single-doc drop workflow).

Text files (``.md``/``.txt``) are passed through unchanged — they need no OCR.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import requests

# Extensions we read as-is (already text); everything else goes to Docling.
_PASSTHROUGH = {".md", ".markdown", ".txt", ".text"}


class DoclingError(RuntimeError):
    pass


class DoclingClient:
    """Convert a file to markdown via docling-serve's ``/v1/convert/file``."""

    def __init__(self, base_url: str, *, timeout: float = 300.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    @classmethod
    def from_env(cls) -> "DoclingClient":
        import os

        try:
            from dotenv import load_dotenv

            load_dotenv()
        except ImportError:
            pass
        # default to the tunnelled/local port; on Halob use http://docling:5001
        return cls(os.environ.get("GOLDBERG_DOCLING_URL", "http://localhost:5001"))

    def health(self) -> bool:
        try:
            r = requests.get(f"{self.base_url}/health", timeout=10)
            return r.ok and r.json().get("status") == "ok"
        except (requests.RequestException, ValueError):
            return False

    def convert_file(self, path: Path | str) -> str:
        """Return the markdown extraction for ``path`` (passthrough for text files)."""
        path = Path(path)
        if path.suffix.lower() in _PASSTHROUGH:
            return path.read_text(errors="replace")

        with path.open("rb") as fh:
            resp = requests.post(
                f"{self.base_url}/v1/convert/file",
                files={"files": (path.name, fh)},
                data={"to_formats": "md"},
                timeout=self.timeout,
            )
        if not resp.ok:
            raise DoclingError(f"docling {resp.status_code}: {resp.text[:200]}")
        payload: dict[str, Any] = resp.json()
        if payload.get("status") not in ("success", "partial_success", None):
            raise DoclingError(
                f"docling status={payload.get('status')}: {payload.get('errors')}"
            )
        md = (payload.get("document") or {}).get("md_content")
        if md is None:
            raise DoclingError("docling returned no md_content")
        return md
