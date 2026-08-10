"""Audience classification — who a document was addressed to.

The cross-audience contradiction seam (the NLI detector, greenlit 2026-08-10) pairs a
single speaker's claims ACROSS the audiences they spoke to — the prosecutor's
police-facing account vs his court-facing account vs his journalist-facing account —
because that seam is where a shifting account shows, and it is what makes a
machine-found contradiction *actionable*. Pairing within one audience is normal
repetition; pairing across audiences is the signal.

Audience is derived deterministically from ``raw_path`` (no LLM): the folder a document
lives in records who it was sent to or filed with. The rules are an ORDERED table —
first match wins — because some paths are ambiguous by folder but not by content: an
MG6C police form disclosed inside a court bundle is POLICE-audience material (Goldberg
addressing the police), even though it now sits under ``court_correspondence/``. So the
POLICE (mg6c) rule is tested before the COURT (court_correspondence) rule.

This taxonomy is a first pass grounded in the corpus as it stands; casework owns its
refinement. Unmatched paths return ``None`` (most of the corpus — transcripts, social
media, internal analysis — is outside the seam and is not paired).
"""

from __future__ import annotations

import re

# The audiences that define the seam. String constants (not an enum) so they serialise
# straight into a report/JSON worklist without conversion.
POLICE = "police"
COURT = "court"
NEIDLE = "neidle"

#: All seam audiences, for iteration/validation.
AUDIENCES: tuple[str, ...] = (POLICE, COURT, NEIDLE)

# Ordered (compiled-regex, audience) rules; FIRST match wins. Patterns are matched
# against the lower-cased raw_path. Order encodes precedence — see the module docstring
# for why NEIDLE precedes POLICE precedes COURT.
_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    # NEIDLE — correspondence with the journalist (Dan Neidle / Tax Policy Associates)
    # and the solicitors' thread around it. Checked first: a Neidle email that discusses
    # the summons is still Neidle-facing.
    (re.compile(r"/dan_neidle/|goldberg_to_neidle_emails/|neidle_request_for_comment|glp_correspondence/|neidle"), NEIDLE),
    # POLICE — CPS/police correspondence and the MG6C police forms, wherever they now
    # sit (including disclosed inside a court bundle). Before COURT on purpose.
    (re.compile(r"/cps/|\bmg6c\b|/mg6c/|_mg6c|police|south_yorkshire|\bsyp\b|/kent|/essex|d3_referrals_to_authorities"), POLICE),
    # COURT — what was filed with or addressed to the court: the case summary, the
    # summons and its application, the prosecution/court bundles, particulars of claim,
    # and the served court correspondence.
    (re.compile(r"case_summary|case summary|\bsummons\b|prosecution_bundle|court_electronic_bundle|particulars_of_claim|particulars of claim|court_correspondence/"), COURT),
)


def classify_audience(raw_path: str | None) -> str | None:
    """Return the audience a document was addressed to, or ``None`` if outside the seam.

    Deterministic and side-effect free: matches ``raw_path`` against the ordered
    :data:`_RULES` table, first match wins. Returns one of :data:`AUDIENCES` or ``None``.
    """
    if not raw_path:
        return None
    low = raw_path.lower()
    for pattern, audience in _RULES:
        if pattern.search(low):
            return audience
    return None
