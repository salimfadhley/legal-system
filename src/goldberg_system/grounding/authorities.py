"""Recognise legal-authority references and reduce each to a canonical *authority key*.

Two operations live here and must not be confused:

* **Recognition** (this module) turns free text into a list of :class:`AuthorityRef`. It may
  use regular expressions, because it is *enumerating* references, not looking one up.
* **Lookup** (the checker) tests whether a specific quote or a specific citation is present.
  That is always a FIXED-STRING / canonical-key equality test — never a regex — because a
  neutral citation contains ``[`` and ``]``, which a regex would read as a character class
  and silently match nothing (the exact bug casework hit).

The canonical key is what makes "the citation in a report" and "the subject of a held
primary text" comparable despite surface differences:

* neutral citations:  ``[2008] EWHC 148 (Admin)`` and ``[2008] EWHC 148 (Admin)`` written in
  a filename as ``EWHC_148_Admin`` both reduce to ``ncit:2008 ewhc 148 admin``;
* statute sections:  ``s.7 PfHA``, ``section 7 Protection from Harassment Act 1997`` and the
  frontmatter ``Protection from Harassment Act 1997, s.7(3A)`` all reduce to
  ``stat:protection from harassment act 1997 s7`` (subsection dropped for grounding).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# --- neutral citations ----------------------------------------------------------------

# Divisions that appear bracketed after the number (``[2008] EWHC 148 (Admin)``) OR, in some
# reporters, before it (``[1999] EWHC Admin 242``). We normalise both to the same key by
# always emitting the division last. ``Civ`` / ``Crim`` are NOT here: for EWCA they are part
# of the court name and never a trailing bracketed division.
_DIVISIONS = frozenset(
    {"admin", "kb", "qb", "ch", "fam", "comm", "pat", "tcc", "ipec", "scco"}
)

_NEUTRAL = re.compile(
    r"\[(?P<year>\d{4})\]\s+"
    r"(?P<court>[A-Z]{2,}(?:\s+(?:Civ|Crim|Admin|KB|QB|Ch|Fam|Comm|Pat|TCC|IPEC|SCCO))?)"
    r"\s+(?P<num>\d+)"
    r"(?:\s*\((?P<div>[A-Za-z]+)\))?"
)


# Court identifiers, used only to read a neutral citation out of a FILENAME (where the
# ``[year]`` brackets are gone). We never run this bare form over prose — only over a file's
# own name — because ``2011 ... EWCA Civ 1233`` without brackets is too loose for free text.
_COURTS = frozenset({"ewca", "ewhc", "ukhl", "uksc", "ukpc", "ewcop", "ewfc"})


def _neutral_key(year: str, court: str, num: str, div: str | None) -> str:
    tokens = court.lower().split()
    division = (div or "").lower()
    # A trailing division word folded into the court (``EWHC Admin 242``) becomes the
    # division, so it keys identically to ``[…] EWHC 242 (Admin)``.
    if not division and len(tokens) == 2 and tokens[1] in _DIVISIONS:
        division = tokens[1]
        tokens = tokens[:1]
    court_norm = " ".join(tokens)
    key = f"ncit:{year} {court_norm} {num}"
    if division:
        key += f" {division}"
    return key


# --- statutes -------------------------------------------------------------------------

# Canonical Act name -> the surface forms (full names and abbreviations) that denote it in
# this corpus. Abbreviations are matched as whole tokens so ``POA`` does not fire inside
# ``proposal``. Curated to the Acts actually held/cited here; unknown Acts fall through to
# AMBER (unsupported), which is the safe direction.
_ACTS: dict[str, tuple[str, ...]] = {
    "protection from harassment act 1997": (
        "protection from harassment act 1997",
        "protection from harassment act",
        "pfha",
        "pha 1997",
    ),
    "magistrates' courts act 1980": (
        "magistrates' courts act 1980",
        "magistrates courts act 1980",
        "mca 1980",
        "mca",
    ),
    "prosecution of offences act 1985": (
        "prosecution of offences act 1985",
        "poa 1985",
        "poa",
    ),
    "criminal justice act 1967": ("criminal justice act 1967", "cja 1967"),
    "criminal justice act 2003": ("criminal justice act 2003", "cja 2003"),
    "criminal procedure and investigations act 1996": (
        "criminal procedure and investigations act 1996",
        "cpia 1996",
        "cpia",
    ),
    "coroners and justice act 2009": ("coroners and justice act 2009",),
    "human rights act 1998": ("human rights act 1998", "hra 1998", "hra"),
    "sentencing act 2020": ("sentencing act 2020",),
    "criminal justice act 1988": ("criminal justice act 1988", "cja 1988"),
}

# Surface form -> canonical, longest-first so the fullest name wins over an abbreviation.
_ACT_SURFACES: list[tuple[str, str]] = sorted(
    ((surface, canon) for canon, forms in _ACTS.items() for surface in forms),
    key=lambda pair: len(pair[0]),
    reverse=True,
)

# A section token: ``s.7``, ``s 7``, ``s7``, ``section 7``, ``ss.17-19`` (first number kept).
# The subsection (``(3A)``) is deliberately not part of the key: the held file for s.7 is the
# ground truth for s.7(3A), and the verbatim quote test still catches any misquote.
_SECTION = re.compile(r"(?:\bss?\.?\s?|\bsections?\s+)(\d+)(?:\s*\([0-9A-Za-z]+\))*")

# How near (in characters) a section token and an Act surface must sit to be one reference.
_STATUTE_WINDOW = 60


@dataclass(frozen=True)
class AuthorityRef:
    """One recognised authority reference and where it sits in the source text.

    ``key`` is the canonical authority key (``ncit:…`` / ``stat:…`` / ``rule:…``); ``kind`` is
    the family; ``start`` / ``end`` are character offsets used for the quote-proximity test.
    """

    raw: str
    key: str
    kind: str  # "neutral_citation" | "statute" | "rule"
    start: int
    end: int


def _find_neutral(text: str) -> list[AuthorityRef]:
    refs: list[AuthorityRef] = []
    for m in _NEUTRAL.finditer(text):
        key = _neutral_key(m.group("year"), m.group("court"), m.group("num"), m.group("div"))
        refs.append(
            AuthorityRef(
                raw=m.group(0),
                key=key,
                kind="neutral_citation",
                start=m.start(),
                end=m.end(),
            )
        )
    return refs


def _find_act_surfaces(lower: str) -> list[tuple[int, int, str]]:
    """Locate Act surface forms in the lower-cased text, longest-first, non-overlapping."""
    spans: list[tuple[int, int, str]] = []
    taken: list[tuple[int, int]] = []
    for surface, canon in _ACT_SURFACES:
        start = 0
        while True:
            idx = lower.find(surface, start)
            if idx == -1:
                break
            end = idx + len(surface)
            start = end
            # whole-token boundaries so ``poa`` does not fire inside ``proposal``
            if idx > 0 and (lower[idx - 1].isalnum() or lower[idx - 1] == "_"):
                continue
            if end < len(lower) and (lower[end].isalnum() or lower[end] == "_"):
                continue
            if any(s < end and idx < e for s, e in taken):
                continue
            taken.append((idx, end))
            spans.append((idx, end, canon))
    return spans


def _find_statutes(text: str) -> list[AuthorityRef]:
    lower = text.lower()
    acts = _find_act_surfaces(lower)
    if not acts:
        return []
    refs: list[AuthorityRef] = []
    seen: set[tuple[int, str]] = set()
    for sec in _SECTION.finditer(text):
        secnum = sec.group(1)
        s_start, s_end = sec.start(), sec.end()
        # nearest Act surface whose span is within the window of this section token
        best: tuple[int, int, str] | None = None
        best_dist: int | None = None
        for a_start, a_end, canon in acts:
            if a_start >= s_end:
                dist = a_start - s_end
            elif a_end <= s_start:
                dist = s_start - a_end
            else:
                dist = 0
            if dist <= _STATUTE_WINDOW and (best_dist is None or dist < best_dist):
                best, best_dist = (a_start, a_end, canon), dist
        if best is None:
            continue
        canon = best[2]
        key = f"stat:{canon} s{secnum}"
        start, end = min(s_start, best[0]), max(s_end, best[1])
        dedup = (start, key)
        if dedup in seen:
            continue
        seen.add(dedup)
        refs.append(
            AuthorityRef(raw=text[start:end], key=key, kind="statute", start=start, end=end)
        )
    return refs


# --- procedural rules -----------------------------------------------------------------

# Criminal Procedure Rules — the family the fabricated rule belonged to. ``CrimPR`` and the
# spelled-out name are treated as criminal; ``CPR`` alone is the CIVIL rules and is NOT
# matched here (a real ambiguity in this corpus). A part/rule NUMBER is required: a bare
# "CrimPR" names no specific authority — too vague to ground, and flagging it would be noise.
_CRIMPR = re.compile(
    r"(?:crimpr|criminal\s+procedure\s+rules?)"
    r"[,\s]+(?:part|pt\.?|rule|r\.?)\s*(?P<num>\d+(?:\.\d+)*)",
    re.IGNORECASE,
)


def _find_rules(text: str) -> list[AuthorityRef]:
    refs: list[AuthorityRef] = []
    for m in _CRIMPR.finditer(text):
        key = f"rule:criminal procedure rules {m.group('num')}"
        refs.append(
            AuthorityRef(
                raw=m.group(0).strip(),
                key=key,
                kind="rule",
                start=m.start(),
                end=m.end(),
            )
        )
    return refs


def find_authorities(text: str) -> list[AuthorityRef]:
    """Return every recognised authority reference in ``text``, sorted by position."""
    refs = _find_neutral(text) + _find_statutes(text) + _find_rules(text)
    refs.sort(key=lambda r: r.start)
    return refs


def authority_keys(text: str) -> set[str]:
    """The set of canonical authority keys mentioned in ``text``."""
    return {ref.key for ref in find_authorities(text)}


def neutral_key_from_filename(stem: str) -> str | None:
    """Read a neutral-citation key from a primary-text FILENAME stem, or None.

    Filenames drop the ``[year]`` brackets and glue the citation with underscores
    (``2008_crawford_v_cps_EWHC_148_Admin`` → ``ncit:2008 ewhc 148 admin``,
    ``…_EWCA_Civ_1233`` → ``ncit:2011 ewca civ 1233``). The year is the leading four-digit
    token; the court/number/division are read around the first recognised court token — so
    ``EWCA_Civ`` keys identically to the prose ``EWCA Civ`` (the underscore guard).
    """
    tokens = [t for t in re.split(r"[^A-Za-z0-9]+", stem) if t]
    lower = [t.lower() for t in tokens]
    year = next((t for t in tokens if re.fullmatch(r"(?:19|20)\d{2}", t)), None)
    if year is None:
        return None
    court_idx = next((i for i, t in enumerate(lower) if t in _COURTS), None)
    if court_idx is None:
        return None
    court = tokens[court_idx]
    j = court_idx + 1
    if j < len(tokens) and lower[j] in {"civ", "crim"}:
        court = f"{court} {tokens[j]}"
        j += 1
    if j >= len(tokens) or not tokens[j].isdigit():
        return None
    num = tokens[j]
    j += 1
    div = tokens[j] if j < len(tokens) and lower[j] in _DIVISIONS else None
    return _neutral_key(year, court, num, div)
