"""Subtitle (.vtt / .srt) → readable transcript text.

Subtitle files are plain text, but their *raw* form is not readable prose: WebVTT and
SubRip interleave the caption words with cue indices, ``HH:MM:SS --> HH:MM:SS`` timing
lines, cue-setting tokens, and — for YouTube auto-captions — inline word-level timing
tags like ``<00:00:02.080><c> are</c>``. Indexing that verbatim buries the actual speech
(the evidential content of a complained-of video) in markup and makes claims extraction
noisy. This turns it into the transcript a human would read: caption text only,
inline tags stripped, and the rolling-duplicate lines auto-captions emit collapsed.
"""

from __future__ import annotations

import re

# Inline VTT tags: <c>, </c>, <00:00:02.080>, <c.colorE5E5E5>, <v Speaker> …
_INLINE_TAG = re.compile(r"<[^>]*>")
# A cue timing line (both VTT ``.`` and SRT ``,`` millisecond separators).
_TIMING = re.compile(r"\d{1,2}:\d{2}:\d{2}[.,]\d{3}\s*-->")
# VTT header / metadata lines that are never caption text.
_HEADER = re.compile(r"^(WEBVTT|Kind:|Language:|NOTE(\s|$)|STYLE\b|REGION\b)", re.I)
# A bare SRT cue index (a line that is only digits).
_CUE_INDEX = re.compile(r"^\d+$")


def subtitle_to_text(raw: str) -> str:
    """Extract readable caption text from WebVTT/SubRip content.

    Drops headers, cue indices, timing lines and cue-setting tokens; strips inline
    timing/formatting tags; collapses whitespace; and removes CONSECUTIVE duplicate
    lines (auto-captions repeat the previous line plus one new word per cue). Returns
    newline-joined transcript text; empty string if nothing readable remains.
    """
    out: list[str] = []
    for line in raw.splitlines():
        s = line.strip()
        if not s or _HEADER.match(s) or _TIMING.search(s) or _CUE_INDEX.match(s):
            continue
        s = _INLINE_TAG.sub("", s).strip()
        # collapse the whitespace the tag removal can leave behind
        s = re.sub(r"\s{2,}", " ", s)
        if not s:
            continue
        if out and out[-1] == s:  # consecutive rolling-caption duplicate
            continue
        out.append(s)
    return "\n".join(out)
