"""Tests for subtitle (.vtt/.srt) → readable transcript cleaning."""

from __future__ import annotations

from goldberg_system.extract.subtitles import subtitle_to_text


def test_vtt_strips_header_timings_and_inline_tags() -> None:
    raw = (
        "WEBVTT\nKind: captions\nLanguage: en\n\n"
        "00:00:01.600 --> 00:00:04.230 align:start position:0%\n"
        "These<00:00:02.080><c> are</c><00:00:02.560><c> nicknames</c>\n"
    )
    assert subtitle_to_text(raw) == "These are nicknames"


def test_srt_strips_indices_and_timings() -> None:
    raw = (
        "1\n00:00:01,000 --> 00:00:04,000\n"
        "The material sought is not necessary.\n\n"
        "2\n00:00:04,000 --> 00:00:06,000\n"
        "That is the whole of it.\n"
    )
    assert subtitle_to_text(raw) == (
        "The material sought is not necessary.\nThat is the whole of it."
    )


def test_collapses_consecutive_rolling_duplicates() -> None:
    # auto-captions emit the previous line + one new word each cue; after de-tagging,
    # identical consecutive lines collapse to one.
    raw = (
        "WEBVTT\n\n"
        "00:00:01.000 --> 00:00:02.000\nhello\n\n"
        "00:00:02.000 --> 00:00:03.000\nhello\n\n"
        "00:00:03.000 --> 00:00:04.000\nhello world\n"
    )
    assert subtitle_to_text(raw) == "hello\nhello world"


def test_note_and_style_blocks_dropped() -> None:
    raw = "WEBVTT\n\nNOTE this is a comment\n\n00:00:01.000 --> 00:00:02.000\nreal text\n"
    assert subtitle_to_text(raw) == "real text"


def test_empty_or_markup_only_returns_empty() -> None:
    assert subtitle_to_text("WEBVTT\n\n") == ""
    assert subtitle_to_text("") == ""
