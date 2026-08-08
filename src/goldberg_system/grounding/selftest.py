"""The non-negotiable self-test: prove the checker works BEFORE it reports anything.

Casework insisted, twice: a checker that silently passes everything is worse than no checker,
because it launders unverified material as checked. Their first hand-rolled version failed to
all-RED (a regex, not a fixed-string, match on bracketed citations) and could equally have
failed all-GREEN with reversed polarity. So before the tool prints a single real result it
builds a tiny corpus in a temp directory with two planted cases and checks the verdicts:

* a KNOWN-GOOD quote that genuinely appears in a held primary text — MUST come back GREEN;
* a KNOWN-BAD quote that is absent from that same held primary text — MUST come back RED.

If either verdict is wrong, :func:`run_selftest` raises :class:`SelfTestError` and the CLI
exits non-zero without printing any real findings.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from goldberg_system.grounding.checker import QUOTE, Verdict, check_root

# The held ground truth: a primary text whose SUBJECT is [1999] EWHC 999 (Admin), containing
# one distinctive verbatim sentence.
_PRIMARY = """---
title: "Selftest v Selftest [1999] EWHC 999 (Admin) — the planted authority"
citation: "[1999] EWHC 999 (Admin)"
---

# Selftest v Selftest [1999] EWHC 999 (Admin)

> "The quality of mercy is not strained; it droppeth as the gentle rain from heaven."
"""

# A citing document that quotes the held sentence verbatim next to the citation → GREEN.
_GOOD = """# Known-good analysis

As held in [1999] EWHC 999 (Admin), "The quality of mercy is not strained; it droppeth
as the gentle rain from heaven."
"""

# A citing document that attributes a FABRICATED sentence to the same held authority → RED.
_BAD = """# Known-bad analysis

The court in [1999] EWHC 999 (Admin) said "The defendant shall be liable for treble costs
in every case without exception whatsoever."
"""


class SelfTestError(RuntimeError):
    """Raised when the grounding self-test does not produce its planted GREEN + RED verdicts."""


def _run() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "authorities_primary_text").mkdir()
        (root / "analysis").mkdir()
        (root / "authorities_primary_text" / "1999_selftest_EWHC_999_Admin.md").write_text(
            _PRIMARY, encoding="utf-8"
        )
        (root / "analysis" / "known_good.md").write_text(_GOOD, encoding="utf-8")
        (root / "analysis" / "known_bad.md").write_text(_BAD, encoding="utf-8")

        report = check_root(root)
        verdicts = {
            f.file: f.verdict
            for f in report.findings
            if f.kind == QUOTE and f.verdict is not None
        }
        good = verdicts.get("analysis/known_good.md")
        bad = verdicts.get("analysis/known_bad.md")
        if good is not Verdict.GREEN:
            raise SelfTestError(
                f"KNOWN-GOOD quote classified {good!r}, expected GREEN — refusing to report"
            )
        if bad is not Verdict.RED:
            raise SelfTestError(
                f"KNOWN-BAD quote classified {bad!r}, expected RED — refusing to report"
            )


def run_selftest() -> None:
    """Run the embedded self-test, raising :class:`SelfTestError` on any wrong verdict."""
    _run()
