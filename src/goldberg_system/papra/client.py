"""A minimal Papra REST client — read extracted content + stamp provenance.

Per ADR 0003, once a document is ingested the pipeline retrieves its extracted
markdown (``content``) and metadata from Papra's REST API, and stamps
``raw_path``/``raw_commit`` back onto the Papra document as custom properties so
the git-raw and Papra stores are cross-linked.

The HTTP layer is injected (see :class:`HttpTransport`) so the client is unit
testable without a live Papra or an API key. The default transport uses
``requests``. Endpoints follow Papra's documented API
(``/api/organizations/:orgId/documents/:id``).
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel


class PapraDocument(BaseModel):
    """The subset of a Papra document the pipeline consumes.

    Papra's REST API returns camelCase fields (``originalName``, ``mimeType``,
    ``originalSha256Hash``); the alias generator maps those onto these snake_case
    fields, while ``populate_by_name`` keeps snake_case input working too.
    """

    model_config = ConfigDict(
        extra="ignore", alias_generator=to_camel, populate_by_name=True
    )

    id: str
    name: str | None = None
    original_name: str | None = None
    mime_type: str | None = None
    content: str | None = None
    original_sha256_hash: str | None = None


class HttpResponse(Protocol):
    """The response shape the client needs (satisfied by ``requests.Response``)."""

    status_code: int

    def json(self) -> Any: ...

    def raise_for_status(self) -> None: ...


@runtime_checkable
class HttpTransport(Protocol):
    """An injectable HTTP transport (satisfied by ``requests``/a fake in tests)."""

    def request(self, method: str, url: str, **kwargs: Any) -> HttpResponse: ...


class _RequestsTransport:
    """Default transport backed by ``requests`` (imported lazily)."""

    def request(self, method: str, url: str, **kwargs: Any) -> HttpResponse:
        import requests

        return requests.request(method, url, **kwargs)


class PapraClient:
    """Read documents from, and stamp provenance onto, a Papra organisation."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        org_id: str,
        transport: HttpTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.org_id = org_id
        self._http: HttpTransport = transport or _RequestsTransport()

    @classmethod
    def from_env(cls, transport: HttpTransport | None = None) -> PapraClient:
        """Build a client from ``PAPRA_BASE_URL`` / ``PAPRA_API_KEY`` /
        ``PAPRA_LEGAL_ORG_ID`` (loaded from a gitignored ``.env`` if present)."""
        import os

        try:
            from dotenv import load_dotenv

            load_dotenv()
        except ImportError:
            pass
        return cls(
            base_url=os.environ["PAPRA_BASE_URL"],
            api_key=os.environ["PAPRA_API_KEY"],
            org_id=os.environ["PAPRA_LEGAL_ORG_ID"],
            transport=transport,
        )

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}"}

    def _doc_url(self, document_id: str) -> str:
        return (
            f"{self.base_url}/api/organizations/{self.org_id}"
            f"/documents/{document_id}"
        )

    def _docs_url(self) -> str:
        return f"{self.base_url}/api/organizations/{self.org_id}/documents"

    def list_documents(
        self, *, page_index: int = 0, page_size: int = 100, search: str | None = None
    ) -> list[PapraDocument]:
        """List documents in the organisation (one page).

        The list projection may omit full ``content``; fetch the single document
        (or the webhook payload) when the text is needed.
        """
        # Papra caps pageSize at 100.
        params: dict[str, Any] = {
            "pageIndex": page_index,
            "pageSize": min(page_size, 100),
        }
        if search:
            params["searchQuery"] = search
        resp = self._http.request(
            "GET", self._docs_url(), headers=self._headers(), params=params
        )
        resp.raise_for_status()
        payload = resp.json()
        docs = (
            payload.get("documents", payload) if isinstance(payload, dict) else payload
        )
        return [PapraDocument.model_validate(d) for d in docs]

    def get_document(self, document_id: str) -> PapraDocument:
        """Fetch a document (including its extracted ``content``).

        Papra wraps the document in a ``{"document": {...}}`` envelope.
        """
        resp = self._http.request(
            "GET", self._doc_url(document_id), headers=self._headers()
        )
        resp.raise_for_status()
        payload = resp.json()
        if isinstance(payload, dict) and "document" in payload:
            payload = payload["document"]
        return PapraDocument.model_validate(payload)

    def get_content(self, document_id: str) -> str | None:
        """Return the extracted/OCR'd text for a document, if any."""
        return self.get_document(document_id).content

    def set_provenance(
        self, document_id: str, *, raw_path: str, raw_commit: str
    ) -> None:
        """Stamp ``raw_path``/``raw_commit`` onto the Papra document.

        Uses Papra's per-document custom-property update endpoint. The exact
        payload shape should be confirmed against a live Papra before relying on
        it in the pipeline (no API key was available at authoring time).
        """
        url = f"{self._doc_url(document_id)}/custom-properties"
        payload = {"raw_path": raw_path, "raw_commit": raw_commit}
        resp = self._http.request("PUT", url, headers=self._headers(), json=payload)
        resp.raise_for_status()
