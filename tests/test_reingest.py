"""Tests for the direct-Docling client + bulk reingest (M8 fix)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from goldberg_system.extract.docling_client import DoclingClient, DoclingError
from goldberg_system.migrate.manifest import Manifest
from goldberg_system.migrate.reingest import reingest_from_raw


def test_passthrough_reads_text_without_calling_docling(tmp_path: Path) -> None:
    md = tmp_path / "a.md"
    md.write_text("# hello\n\nbody")
    # base_url unreachable on purpose — passthrough must not hit the network
    assert DoclingClient("http://127.0.0.1:1").convert_file(md) == "# hello\n\nbody"


class _FakeResp:
    def __init__(self, payload: Any, ok: bool = True, status: int = 200) -> None:
        self._p = payload
        self.ok = ok
        self.status_code = status
        self.text = json.dumps(payload)

    def json(self) -> Any:
        return self._p


def _async_docling(
    monkeypatch, *, status: str = "success", md: str | None = "extracted text"
):
    """Wire Docling's async submit→poll→result flow to fakes."""
    import goldberg_system.extract.docling_client as mod

    monkeypatch.setattr(
        mod.requests, "post", lambda *a, **k: _FakeResp({"task_id": "t1"})
    )

    def fake_get(url: str, **k: Any) -> _FakeResp:
        if "/status/poll/" in url:
            return _FakeResp({"task_status": status, "error_message": "boom"})
        return _FakeResp({"document": ({"md_content": md} if md is not None else {})})

    monkeypatch.setattr(mod.requests, "get", fake_get)


def test_convert_file_async_parses_md_content(tmp_path: Path, monkeypatch) -> None:
    pdf = tmp_path / "a.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    _async_docling(monkeypatch, status="success", md="extracted text")
    client = DoclingClient("http://x")
    client._sleep = lambda *_: None  # type: ignore[method-assign]
    assert client.convert_file(pdf) == "extracted text"


def test_convert_file_raises_on_failure_status(tmp_path: Path, monkeypatch) -> None:
    pdf = tmp_path / "a.pdf"
    pdf.write_bytes(b"%PDF")
    _async_docling(monkeypatch, status="failure")
    client = DoclingClient("http://x")
    client._sleep = lambda *_: None  # type: ignore[method-assign]
    try:
        client.convert_file(pdf)
        assert False, "should have raised"
    except DoclingError as e:
        assert "boom" in str(e)


def test_json_and_tsv_are_passthrough(tmp_path: Path) -> None:
    j = tmp_path / "data.json"
    j.write_text('{"a": 1}')
    # unreachable base_url — passthrough (json) must not hit the network
    assert DoclingClient("http://127.0.0.1:1").convert_file(j) == '{"a": 1}'


# ── reingest loop (with fakes) ────────────────────────────────────────────────


class _FakeDocling:
    def __init__(self, texts: dict[str, str]) -> None:
        self._texts = texts  # raw_path -> extracted text ("" = empty)

    def convert_file(self, path: Path) -> str:
        return self._texts.get(str(path).split("/raw/")[-1], "some text")


class _FakeEnricher:
    def enrich(self, request):  # type: ignore[no-untyped-def]
        from goldberg_system.enrichment.adapter import EnrichmentResult

        return EnrichmentResult(summary="s", keywords=[], entities=[], claims=[])


class _FakeSink:
    def __init__(self) -> None:
        self.written: list[str] = []

    @property
    def name(self) -> str:
        return "fake"

    def write(self, document):  # type: ignore[no-untyped-def]
        from goldberg_system.sinks.base import SinkResult

        self.written.append(document.raw_path)
        return SinkResult(sink="fake", ok=True)


def _manifest(tmp: Path) -> tuple[Manifest, Path]:
    root = tmp / "raw"
    (root / "evidence").mkdir(parents=True)
    (root / "evidence" / "a.pdf").write_bytes(b"%PDF")
    (root / "evidence" / "b.mp4").write_bytes(b"video")
    by_sha = {
        "sha_a": {
            "raw_path": "evidence/a.pdf",
            "raw_commit": "c1",
            "sha256": "sha_a",
            "matters": ["M1"],
        },
        "sha_b": {
            "raw_path": "evidence/b.mp4",
            "raw_commit": "c1",
            "sha256": "sha_b",
            "matters": [],
        },
        "sha_missing": {
            "raw_path": "evidence/gone.pdf",
            "raw_commit": "c1",
            "sha256": "sha_missing",
            "matters": [],
        },
    }
    return Manifest(by_sha), root


def test_reingest_indexes_skips_media_and_missing(tmp_path: Path) -> None:
    manifest, root = _manifest(tmp_path)
    sink = _FakeSink()
    report = reingest_from_raw(
        root,
        manifest,
        _FakeDocling({"evidence/a.pdf": "hello"}),
        _FakeEnricher(),
        [sink],
    )
    assert report.indexed == 1  # a.pdf
    assert report.skipped_media == 1  # b.mp4
    assert report.missing_file == 1  # gone.pdf
    assert sink.written == ["evidence/a.pdf"]


def test_reingest_only_filter_and_empty(tmp_path: Path) -> None:
    manifest, root = _manifest(tmp_path)
    sink = _FakeSink()
    report = reingest_from_raw(
        root,
        manifest,
        _FakeDocling({"evidence/a.pdf": ""}),
        _FakeEnricher(),
        [sink],
        only={"evidence/a.pdf"},
    )
    assert report.processed == 1 and report.skipped_empty == 1 and report.indexed == 0
