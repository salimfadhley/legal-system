"""Find quoted strings and their character offsets in a citing document.

A "quote" is a span the author is presenting as the words of an authority. Two carriers cover
how they appear in this corpus:

* double-quoted spans — straight or smart (``"…"`` / ``“…”``);
* markdown blockquote lines (``> …``), which is how the held provisions themselves are laid
  out and how reports often reproduce them.

The straight single-quote is deliberately NOT a delimiter: in English prose it is almost
always an apostrophe (``Magistrates' Court``, ``the prosecutor's case``), and treating it as a
quote boundary captures enormous junk spans between two possessives — a major false-RED
source. Precision guards reject captures that are clearly not a reproduced passage: a
paragraph break inside the span, markdown link/table syntax, or too few letters. A quote that
is genuinely fabricated still fails the verbatim test; these guards only stop the extractor
inventing "quotes" out of navigation text and possessives.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from goldberg_system.grounding.normalize import normalize_quote

# Shortest quote (in normalised characters) worth verifying. Below this we are looking at a
# defined term or an aside, not a reproduced passage.
MIN_QUOTE_CHARS = 25
# Longest plausible inline quote; beyond this the delimiters are almost certainly unbalanced
# (an opening ``"`` with no close for pages) rather than one passage.
MAX_QUOTE_CHARS = 1500

# Inline double quotes. ``[^"“”]`` matches newlines, so a short wrapped quote is fine; the
# post-match guards reject anything that spans a paragraph or looks like navigation.
_DOUBLE = re.compile(r"[\"“]([^\"“”]{1,%d}?)[\"”]" % MAX_QUOTE_CHARS)
_BLOCKQUOTE = re.compile(r"^[ \t]*>[ \t]?(.+)$", re.MULTILINE)

# Markers that betray a captured span as list / table / navigation text, not a quotation:
# a markdown link ``](``, a table pipe ``| ``, or a line that begins a list/heading.
_NAVIGATION = re.compile(r"\]\(|\| |\n\s*[-*#]")
# A bracketed neutral-citation opener; three or more inside one span means it is a LIST of
# authorities (an index/nav line), not a quotation. A genuine quote rarely stacks citations.
_YEAR_TAG = re.compile(r"\[(?:19|20)\d{2}\]")


@dataclass(frozen=True)
class Quote:
    """One quoted span: ``raw`` as written, ``normalized`` for the verbatim test, and the
    ``start`` / ``end`` offsets in the source document."""

    raw: str
    normalized: str
    start: int
    end: int


def _looks_like_prose(raw: str, normalized: str) -> bool:
    """Reject spans that are navigation/table/list fragments rather than a reproduced quote."""
    if "\n\n" in raw:  # a paragraph break: an unbalanced-delimiter capture, not one passage
        return False
    if _NAVIGATION.search(raw):  # markdown links, table pipes, heading/list markers
        return False
    if len(_YEAR_TAG.findall(raw)) >= 3:  # a stacked list of citations, not a quotation
        return False
    # At least half the characters must be letters — rejects hex / base64 / id noise while
    # keeping ordinary prose (whose non-letter share is spaces and punctuation).
    letters = sum(c.isalpha() for c in normalized)
    return letters * 2 >= len(normalized)


def _add(spans: list[Quote], raw: str, start: int, end: int) -> None:
    normalized = normalize_quote(raw)
    if len(normalized) < MIN_QUOTE_CHARS or len(normalized) > MAX_QUOTE_CHARS:
        return
    if not _looks_like_prose(raw, normalized):
        return
    spans.append(Quote(raw=raw, normalized=normalized, start=start, end=end))


def find_quotes(text: str) -> list[Quote]:
    """Return every substantial quoted span in ``text``, de-duplicated by offset.

    A blockquote and an inline quote can cover overlapping text; we keep the longest span
    starting at each offset so a passage is not counted twice.
    """
    spans: list[Quote] = []
    for m in _DOUBLE.finditer(text):
        _add(spans, m.group(1), m.start(1), m.end(1))
    for m in _BLOCKQUOTE.finditer(text):
        _add(spans, m.group(1), m.start(1), m.end(1))

    # De-duplicate: prefer the longest span at any given start offset.
    best: dict[int, Quote] = {}
    for q in spans:
        cur = best.get(q.start)
        if cur is None or len(q.raw) > len(cur.raw):
            best[q.start] = q
    return sorted(best.values(), key=lambda q: q.start)
