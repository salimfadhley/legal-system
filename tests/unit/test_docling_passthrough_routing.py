"""DoclingClient.convert_file routing: subtitle/text formats bypass docling.

Regression guard for the fix that stopped .vtt/.srt failing extraction — they are plain
text (the caption lines are a video's transcript) and must be read directly (through the
subtitle cleaner), not submitted to docling-serve. A non-docling extension returns text
WITHOUT any network call, so these tests use an unreachable base_url and still pass.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from goldberg_system.extract.docling_client import _SUBTITLE, DoclingClient


def _client() -> DoclingClient:
    # base_url is deliberately unreachable: subtitle/text reads must not touch the network.
    return DoclingClient("http://127.0.0.1:9")


@pytest.mark.parametrize("ext", [".vtt", ".srt"])
def test_subtitles_route_off_docling(ext: str) -> None:
    assert ext in _SUBTITLE


def test_vtt_is_cleaned_not_sent_to_docling(tmp_path: Path) -> None:
    vtt = tmp_path / "clip.en.vtt"
    # auto-caption VTT: header + timing + inline word-timing tags + rolling duplicate
    vtt.write_text(
        "WEBVTT\nKind: captions\nLanguage: en\n\n"
        "00:00:01.000 --> 00:00:04.000 align:start position:0%\n"
        "These<00:00:02.080><c> are</c><00:00:02.560><c> nicknames</c>\n\n"
        "00:00:04.000 --> 00:00:07.000\n"
        "These are nicknames\n",  # rolling duplicate of the de-tagged line above
        encoding="utf-8",
    )
    out = _client().convert_file(vtt)
    assert "These are nicknames" in out
    assert "<" not in out and "-->" not in out and "WEBVTT" not in out
    assert out.count("These are nicknames") == 1  # consecutive duplicate collapsed


def test_srt_is_cleaned_not_sent_to_docling(tmp_path: Path) -> None:
    srt = tmp_path / "clip.srt"
    srt.write_text(
        "1\n00:00:01,000 --> 00:00:04,000\n"
        "The material sought is not necessary.\n\n"
        "2\n00:00:04,000 --> 00:00:06,000\n"
        "That is the whole of it.\n",
        encoding="utf-8",
    )
    out = _client().convert_file(srt)
    assert "The material sought is not necessary." in out
    assert "That is the whole of it." in out
    assert "-->" not in out and "00:00" not in out
