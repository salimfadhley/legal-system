"""The ONLY normalisation allowed before a quote is tested verbatim.

Casework was explicit (twice): normalise whitespace, smart quotes and ellipses — and
*nothing else*. No case-folding, no punctuation stripping, no markdown removal. A laxer
normaliser is not a convenience: it launders a misquote into a false GREEN. So this module
is deliberately small, and the checker applies the SAME function to both sides (the quote
under test and the held primary text), so the comparison is symmetric.
"""

from __future__ import annotations

import re

# Smart / typographic quotation marks that must fold to their ASCII form so a quote copied
# through a word-processor still matches the held text. Both directions of both kinds, plus
# the prime marks and guillemets that occasionally stand in for quotation marks.
_SMART_QUOTES = {
    "“": '"',  # “ left double
    "”": '"',  # ” right double
    "„": '"',  # „ low double
    "‟": '"',  # ‟ high-reversed double
    "″": '"',  # ″ double prime
    "«": '"',  # « left guillemet
    "»": '"',  # » right guillemet
    "‘": "'",  # ‘ left single
    "’": "'",  # ’ right single
    "‚": "'",  # ‚ low single
    "‛": "'",  # ‛ high-reversed single
    "′": "'",  # ′ prime
}

# A unicode ellipsis or any run of three-or-more (optionally space-separated) dots collapses
# to the canonical three ASCII dots. This unifies how an elision is *written*; it never lets
# an ellipsis match arbitrary omitted words — the verbatim substring test still applies.
_DOT_RUN = re.compile(r"\.[ \t]*\.[ \t]*\.(?:[ \t]*\.)*")
_WHITESPACE = re.compile(r"\s+")


def normalize_quote(text: str) -> str:
    """Return ``text`` with ONLY whitespace, smart quotes and ellipses normalised.

    - smart/typographic quotation marks fold to ASCII ``"`` / ``'``;
    - a unicode ellipsis (``…``) or any run of 3+ dots collapses to ``...``;
    - every run of whitespace (including newlines) collapses to a single space, and the
      result is stripped.

    Case, punctuation and every other character are preserved exactly — the whole point is
    that a match means the words are genuinely present, not merely similar.
    """
    for smart, ascii_ in _SMART_QUOTES.items():
        text = text.replace(smart, ascii_)
    text = text.replace("…", "...")
    text = _DOT_RUN.sub("...", text)
    text = _WHITESPACE.sub(" ", text)
    return text.strip()
