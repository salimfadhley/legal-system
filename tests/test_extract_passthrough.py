"""Tests for passthrough extraction (already-markdown/plaintext inputs)."""

from __future__ import annotations

from goldberg_system.extract import passthrough_markdown


def test_normalises_crlf() -> None:
    assert passthrough_markdown("a\r\nb\r\n") == "a\nb\n"


def test_trims_trailing_whitespace_per_line() -> None:
    assert passthrough_markdown("a   \nb\t\n") == "a\nb\n"


def test_strips_leading_and_trailing_blank_lines() -> None:
    assert passthrough_markdown("\n\n# Title\n\n") == "# Title\n"
