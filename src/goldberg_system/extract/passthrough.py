"""Passthrough extraction for inputs that are already markdown/plaintext.

Reports (`.md`) and analysis are already markdown (per ``doc/design.md``); text
files need no extraction. This normalises line endings and trims trailing
whitespace so downstream enrichment gets clean input.
"""

from __future__ import annotations


def passthrough_markdown(text: str) -> str:
    """Return ``text`` normalised (CRLF -> LF, trailing whitespace trimmed)."""
    normalised = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.rstrip() for line in normalised.split("\n")]
    return "\n".join(lines).strip() + "\n"
