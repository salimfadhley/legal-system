"""Tests for the Papra REST client (via an injected fake transport)."""

from __future__ import annotations

from typing import Any

from goldberg_system.papra import HttpTransport, PapraClient

ORG = "org_qcs9nhj2xbf88znpppy7lnba"


class _FakeResponse:
    def __init__(self, payload: Any, status: int = 200) -> None:
        self._payload = payload
        self.status_code = status

    def json(self) -> Any:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class _FakeTransport:
    """Records requests and returns queued responses."""

    def __init__(self, payload: Any) -> None:
        self.payload = payload
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    def request(self, method: str, url: str, **kwargs: Any) -> _FakeResponse:
        self.calls.append((method, url, kwargs))
        return _FakeResponse(self.payload)


def _client(payload: Any) -> tuple[PapraClient, _FakeTransport]:
    transport = _FakeTransport(payload)
    client = PapraClient(
        "https://papra.halob.lan/",
        "key123",
        ORG,
        transport=transport,
    )
    return client, transport


def test_transport_protocol_is_satisfied() -> None:
    assert isinstance(_FakeTransport({}), HttpTransport)


def test_get_document_parses_content_and_metadata() -> None:
    payload = {
        "id": "doc_1",
        "original_name": "test-letter.pdf",
        "mime_type": "application/pdf",
        "content": "Artington Services Limited...",
        "original_sha256_hash": "abcdef",
        "some_unmodelled_field": "ignored",
    }
    client, transport = _client(payload)
    doc = client.get_document("doc_1")
    assert doc.id == "doc_1"
    assert doc.content and doc.content.startswith("Artington")
    assert doc.original_sha256_hash == "abcdef"
    method, url, kwargs = transport.calls[0]
    assert method == "GET"
    assert url.endswith(f"/api/organizations/{ORG}/documents/doc_1")
    assert kwargs["headers"]["Authorization"] == "Bearer key123"


def test_get_content_returns_text() -> None:
    client, _ = _client({"id": "d", "content": "hello"})
    assert client.get_content("d") == "hello"


def test_set_provenance_puts_custom_properties() -> None:
    client, transport = _client({"id": "d"})
    client.set_provenance("d", raw_path="evidence/x/a.pdf", raw_commit="abc123")
    method, url, kwargs = transport.calls[0]
    assert method == "PUT"
    assert url.endswith("/documents/d/custom-properties")
    assert kwargs["json"] == {
        "raw_path": "evidence/x/a.pdf",
        "raw_commit": "abc123",
    }
