"""Grounding checker (mechanical, NOT an LLM): catch fabricated or misquoted legal
authority before it reaches a court filing.

The pipeline is deliberately deterministic and auditable:

1. ``normalize`` — the ONLY text transformations allowed on a quote before it is tested
   verbatim: whitespace, smart quotes, and ellipses. Nothing else (no case-folding, no
   punctuation stripping) — a laxer normaliser would launder a misquote as a match.
2. ``authorities`` — recognise authority references (neutral citations, statute sections,
   procedural rules) in free text and reduce each to a canonical *authority key*.
3. ``primary`` — load the held ground-truth (``authorities_primary_text/``) and index it by
   the authority key(s) that are the SUBJECT of each file (from its identity zone —
   frontmatter citation, title, filename — never a mere mention inside a judgment body).
4. ``quotes`` — find quoted strings and their offsets.
5. ``checker`` — the three-outcome classifier (GREEN / RED / AMBER), citation-without-source
   with blast radius, and the layer signal (served > authorities > reports > analysis).
6. ``selftest`` — an embedded known-good / known-bad fixture the tool runs BEFORE it reports
   anything; if the verdicts are not exactly GREEN and RED it RAISES and refuses to print.
"""

from __future__ import annotations

from goldberg_system.grounding.authorities import (
    AuthorityRef,
    authority_keys,
    find_authorities,
)
from goldberg_system.grounding.checker import (
    Finding,
    GroundingReport,
    Verdict,
    check_root,
)
from goldberg_system.grounding.normalize import normalize_quote
from goldberg_system.grounding.primary import (
    PrimaryAuthority,
    PrimaryIndex,
    load_primary_texts,
)
from goldberg_system.grounding.quotes import Quote, find_quotes
from goldberg_system.grounding.selftest import SelfTestError, run_selftest

__all__ = [
    "AuthorityRef",
    "Finding",
    "GroundingReport",
    "PrimaryAuthority",
    "PrimaryIndex",
    "Quote",
    "SelfTestError",
    "Verdict",
    "authority_keys",
    "check_root",
    "find_authorities",
    "find_quotes",
    "load_primary_texts",
    "normalize_quote",
    "run_selftest",
]
