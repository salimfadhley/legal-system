"""Cross-audience contradiction detection (the NLI seam detector).

Greenlit by casework 2026-08-10. Pairs one speaker's claims ACROSS the audiences he
addressed (police-facing vs court-facing vs journalist-facing) and asks, of each
candidate pair, whether the two *source_spans* contradict — the semantic check the
surface-form matcher structurally cannot do.

Phases (each independently useful):
- :mod:`goldberg_system.audience` — P0, raw_path → audience (police/court/neidle).
- :mod:`goldberg_system.nli.pairing` — P1, generate cross-audience candidate PAIRS by
  subject similarity (this module; pure, no API).
- P2 (gated behind filing work) — score each candidate's two source_spans with an LLM
  entailment/contradiction pass and emit a VERIFY-worklist.

Everything this subsystem emits is a LEAD to verify by hand against the primary source,
never a finding (casework guardrail 2).
"""

from goldberg_system.nli.pairing import (
    CandidatePair,
    SeamClaim,
    candidate_pairs,
    lexical_similarity,
)

__all__ = [
    "CandidatePair",
    "SeamClaim",
    "candidate_pairs",
    "lexical_similarity",
]
