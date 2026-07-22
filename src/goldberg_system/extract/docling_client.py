"""Direct docling-serve client — the bulk extraction path (M8 fix).

Papra 26.4.0 ignores its Docling config and falls back to a slow, crash-prone
internal OCR, so the bulk migration extracts by calling ``docling-serve`` directly:
``goldberg-raw`` file → Docling → markdown. Docling is healthy, fast, and
purpose-built for OCR/layout; this bypasses Papra's broken extraction entirely
(Papra stays for the live single-doc drop workflow).

Uses Docling's **async** convert flow (submit → poll → result) so large scanned
PDFs aren't bounded by the server's synchronous ``DOCLING_SERVE_MAX_SYNC_WAIT`` (120s)
— which was failing ~15% of the corpus's biggest evidence. Text/data files
(``.md``/``.txt``/``.json``/``.tsv``/``.csv``) are passed through unchanged — Docling
can't (and shouldn't) OCR them.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import requests

# Extensions read as-is (already text / structured data Docling can't parse).
_PASSTHROUGH = {".md", ".markdown", ".txt", ".text", ".json", ".tsv", ".csv"}


class DoclingError(RuntimeError):
    pass


class DoclingClient:
    """Convert a file to markdown via docling-serve's async convert flow."""

    def __init__(
        self,
        base_url: str,
        *,
        request_timeout: float = 60.0,
        max_wait: float = 900.0,
        poll_interval: float = 3.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.request_timeout = request_timeout  # per-HTTP-call timeout
        self.max_wait = max_wait  # total time to wait for one conversion
        self.poll_interval = poll_interval
        self._sleep = time.sleep

    @classmethod
    def from_env(cls) -> "DoclingClient":
        import os

        try:
            from dotenv import load_dotenv

            load_dotenv()
        except ImportError:
            pass
        # default to the tunnelled/local port; on Halob use http://docling:5001
        return cls(
            os.environ.get("GOLDBERG_DOCLING_URL", "http://localhost:5001"),
            max_wait=float(os.environ.get("GOLDBERG_DOCLING_MAX_WAIT", "900")),
        )

    def health(self) -> bool:
        try:
            r = requests.get(f"{self.base_url}/health", timeout=10)
            return r.ok and r.json().get("status") == "ok"
        except (requests.RequestException, ValueError):
            return False

    def convert_file(self, path: Path | str) -> str:
        """Return the markdown extraction for ``path`` (passthrough for text/data)."""
        path = Path(path)
        if path.suffix.lower() in _PASSTHROUGH:
            return path.read_text(errors="replace")

        # Wrap transient network errors (connection drop, timeout) as DoclingError so
        # a blip fails just this document, never the whole run (a crash mid-bulk).
        try:
            task_id = self._submit(path)
            self._await(task_id)
            return self._result(task_id)
        except requests.RequestException as exc:
            raise DoclingError(f"docling connection error: {exc}") from exc

    # ── async flow ────────────────────────────────────────────────────────────

    def _submit(self, path: Path) -> str:
        with path.open("rb") as fh:
            resp = requests.post(
                f"{self.base_url}/v1/convert/file/async",
                files={"files": (path.name, fh)},
                data={"to_formats": "md"},
                timeout=self.request_timeout,
            )
        if not resp.ok:
            raise DoclingError(f"docling submit {resp.status_code}: {resp.text[:200]}")
        task_id = resp.json().get("task_id")
        if not task_id:
            raise DoclingError("docling submit returned no task_id")
        return str(task_id)

    def _await(self, task_id: str) -> None:
        deadline = self.max_wait
        waited = 0.0
        while waited <= deadline:
            resp = requests.get(
                f"{self.base_url}/v1/status/poll/{task_id}",
                timeout=self.request_timeout,
            )
            if not resp.ok:
                raise DoclingError(
                    f"docling poll {resp.status_code}: {resp.text[:120]}"
                )
            status = resp.json().get("task_status")
            if status == "success":
                return
            if status == "failure":
                raise DoclingError(
                    f"docling conversion failed: {resp.json().get('error_message')}"
                )
            self._sleep(self.poll_interval)
            waited += self.poll_interval
        raise DoclingError(f"docling conversion timed out after {self.max_wait}s")

    def _result(self, task_id: str) -> str:
        resp = requests.get(
            f"{self.base_url}/v1/result/{task_id}", timeout=self.request_timeout
        )
        if not resp.ok:
            raise DoclingError(f"docling result {resp.status_code}: {resp.text[:120]}")
        payload: dict[str, Any] = resp.json()
        md = (payload.get("document") or {}).get("md_content")
        # No md_content means Docling found no extractable text (blank/graphic-only
        # image, empty PDF, a non-document file). That's an *empty* result, not an
        # error — return "" so the pipeline records it as skipped-empty, not a failure.
        return md if md is not None else ""
