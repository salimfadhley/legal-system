"""DoclingClient.convert_file routing: subtitle/text formats bypass docling.

Regression guard for the fix that stopped .vtt/.srt failing extraction — they are
plain text (the caption lines are a video's transcript) and must be read directly, not
submitted to docling-serve. A passthrough extension returns the file text WITHOUT any
network call, so these tests use an unreachable base_url and still pass.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from goldberg_system.extract.docling_client import _PASSTHROUGH, DoclingClient


def _client() -> DoclingClient:
    # base_url is deliberately unreachable: passthrough must not touch the network.
    return DoclingClient("http://127.0.0.1:9")


@pytest.mark.parametrize("ext", [".vtt", ".srt"])
def test_subtitles_are_passthrough_extensions(ext: str) -> None:
    assert ext in _PASSTHROUGH


def test_vtt_is_read_as_text_not_sent_to_docling(tmp_path: Path) -> None:
    vtt = tmp_path / "clip.en.vtt"
    body = (
        "WEBVTT\n\n"
        "00:00:01.000 --> 00:00:04.000\n"
        "Simon Goldberg discusses the loan agreement.\n\n"
        "00:00:04.000 --> 00:00:07.000\n"
        "He says it was never repaid.\n"
    )
    vtt.write_text(body, encoding="utf-8")
    out = _client().convert_file(vtt)
    # returned verbatim (the caption text — the evidential content — is present)
    assert "Simon Goldberg discusses the loan agreement." in out
    assert "never repaid" in out


def test_srt_is_read_as_text_not_sent_to_docling(tmp_path: Path) -> None:
    srt = tmp_path / "clip.srt"
    body = (
        "1\n00:00:01,000 --> 00:00:04,000\n"
        "The material sought is not necessary.\n\n"
        "2\n00:00:04,000 --> 00:00:06,000\n"
        "That is the whole of it.\n"
    )
    srt.write_text(body, encoding="utf-8")
    out = _client().convert_file(srt)
    assert "The material sought is not necessary." in out
