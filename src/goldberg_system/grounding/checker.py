"""The three-outcome classifier, citation-without-source with blast radius, and the layer
signal — assembled into a sortable :class:`GroundingReport`.

Quote verdicts:

* **GREEN** — a held primary text is the SUBJECT of the cited authority AND the quote is
  present in it verbatim (after the allowed normalisation).
* **RED** — a held primary text IS the subject of that authority, but the quoted string is
  ABSENT from it (the fabrication / misquote), OR the held authority is REPEALED and cited
  live. This is the whole point of the tool.
* **AMBER** — no primary text for that authority is held at all: unsupported, not necessarily
  wrong.

Citation-without-source: any recognised authority reference in a citing layer for which NO
primary text is held, ranked by *blast radius* (how many distinct files rely on it).

Layer signal: every finding is labelled with its source layer, and the report sorts so a
served + RED finding — an error already in a filing that left the building — sits at the very
top, ahead of everything else regardless of kind.
"""

from __future__ import annotations

import enum
import re
from dataclasses import dataclass, field
from pathlib import Path

from goldberg_system.grounding.authorities import (
    AuthorityRef,
    find_authorities,
)
from goldberg_system.grounding.primary import PrimaryIndex
from goldberg_system.grounding.quotes import Quote, find_quotes


class Verdict(enum.Enum):
    GREEN = "GREEN"
    RED = "RED"
    AMBER = "AMBER"


# Kinds of finding.
QUOTE = "quote"
CITATION_WITHOUT_SOURCE = "citation_without_source"
REPEALED_CITATION = "repealed_citation"
MALFORMED_CITATION = "malformed_citation"


class Layer(enum.Enum):
    """Source layers, ordered by how consequential an error there is.

    SERVED is top: an error in ``evidence_of_service/sent`` is already made, not a draft.
    """

    SERVED = 3
    AUTHORITIES = 2
    REPORTS = 1
    ANALYSIS = 0
    OTHER = -1


# Sub-paths (relative to the raw root) that define each citing layer we scan. Authorities are
# the ground truth, not a citing layer, so they are not scanned for findings.
_LAYER_DIRS: tuple[tuple[str, Layer], tuple[str, Layer], tuple[str, Layer]] = (
    ("evidence/evidence_of_service/sent", Layer.SERVED),
    ("reports", Layer.REPORTS),
    ("analysis", Layer.ANALYSIS),
)

# Only text we can test verbatim. ``.eml`` is deliberately EXCLUDED: served emails are raw
# MIME (quoted-printable / base64, HTML, and reply ``>`` quoting) — scanning them raw yields
# garbage "quotes" and would flood the report with false RED, the exact cry-wolf failure the
# self-test guards against. Decoding .eml bodies to plain text is a separate, future step.
_SCANNED_SUFFIXES = frozenset({".md", ".txt"})

# How near (characters) a quote must sit to a citation to be "a quote of that authority".
# Tight enough to capture the direct "In X v Y [cite], '…'" / "'…' (X v Y [cite])" attribution
# pattern where a fabrication lives, without sweeping in unrelated quotes (a witness statement
# that merely happens to sit a paragraph away from a citation).
PROXIMITY_CHARS = 200

# A cheap, no-network shape check: a ``[YEAR]`` opener that looks like the start of a neutral
# citation (followed by capitalised court-ish tokens) but does NOT parse as one — e.g. a
# missing number or a lower-case garbled court. Flagged, not classified.
_CITATION_OPENER = re.compile(r"\[(?:19|20)\d{2}\]\s+[A-Za-z]")


def _malformed_shapes(text: str, parsed: list[AuthorityRef]) -> list[tuple[str, int]]:
    """Return ``(raw, start)`` for each citation-shaped opener the real recogniser missed."""
    parsed_starts = {ref.start for ref in parsed if ref.kind == "neutral_citation"}
    out: list[tuple[str, int]] = []
    for m in _CITATION_OPENER.finditer(text):
        if m.start() in parsed_starts:
            continue
        # Skip openers that ARE the prefix of a parsed neutral citation (offsets can differ
        # by leading whitespace); only flag genuinely unparsed ``[YEAR] Word…`` shapes.
        if any(abs(m.start() - s) <= 1 for s in parsed_starts):
            continue
        out.append((text[m.start() : m.start() + 40].splitlines()[0], m.start()))
    return out


def layer_of(rel_path: Path) -> Layer:
    posix = rel_path.as_posix()
    if "evidence_of_service/sent" in posix:
        return Layer.SERVED
    if posix.startswith("authorities_primary_text/"):
        return Layer.AUTHORITIES
    if posix.startswith("reports/"):
        return Layer.REPORTS
    if posix.startswith("analysis/"):
        return Layer.ANALYSIS
    return Layer.OTHER


@dataclass(frozen=True)
class Finding:
    """One grounding finding. ``verdict`` is set for quote findings; citation-without-source
    carries RED-severity by convention. ``blast_radius`` is set for citation-without-source."""

    kind: str
    layer: Layer
    file: str
    authority_key: str
    verdict: Verdict | None = None
    quote: str | None = None
    detail: str = ""
    blast_radius: int | None = None

    def severity(self) -> int:
        """RED / citation-without-source / repealed = 2, AMBER = 1, GREEN = 0 — for sorting."""
        if self.kind in (
            CITATION_WITHOUT_SOURCE,
            REPEALED_CITATION,
            MALFORMED_CITATION,
        ):
            return 2
        if self.verdict is Verdict.RED:
            return 2
        if self.verdict is Verdict.AMBER:
            return 1
        return 0

    def sort_key(self) -> tuple[int, int, int, str]:
        # layer first (served on top), then severity, then blast radius, then path — so
        # "served + RED" is the very first row, always.
        return (
            -self.layer.value,
            -self.severity(),
            -(self.blast_radius or 0),
            self.file,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "layer": self.layer.name.lower(),
            "file": self.file,
            "authority_key": self.authority_key,
            "verdict": self.verdict.value if self.verdict else None,
            "quote": self.quote,
            "detail": self.detail,
            "blast_radius": self.blast_radius,
        }


def _quote_distance(quote: Quote, a: AuthorityRef) -> int:
    if a.end <= quote.start:
        return quote.start - a.end
    if a.start >= quote.end:
        return a.start - quote.end
    return 0


def _near_authorities(quote: Quote, authorities: list[AuthorityRef]) -> list[AuthorityRef]:
    """Authorities within :data:`PROXIMITY_CHARS` of the quote, nearest first."""
    scored = [
        (_quote_distance(quote, a), i, a) for i, a in enumerate(authorities)
    ]
    return [a for dist, _i, a in sorted(scored) if dist <= PROXIMITY_CHARS]


def classify_quote(
    quote: Quote, authority: AuthorityRef, index: PrimaryIndex
) -> tuple[Verdict, str]:
    """Classify one quote attributed to ``authority`` against the held primary texts."""
    prims = index.get(authority.key)
    if not prims:
        return Verdict.AMBER, f"no primary text held for {authority.raw!r}"
    live = [p for p in prims if not p.repealed]
    dead = [p for p in prims if p.repealed]
    if not live and dead:
        return (
            Verdict.RED,
            f"cites REPEALED authority {authority.raw!r} "
            f"(held as {dead[0].path.name}) — do not cite",
        )
    if any(p.contains(quote.normalized) for p in live):
        return Verdict.GREEN, f"verbatim match in {authority.raw!r}"
    return (
        Verdict.RED,
        f"quote ABSENT from held primary text for {authority.raw!r} "
        f"({live[0].path.name}) — fabricated, misquoted, or a passage outside the held "
        "text; VERIFY at source",
    )


def classify_quote_near(
    quote: Quote, near: list[AuthorityRef], index: PrimaryIndex
) -> tuple[Verdict, str, AuthorityRef]:
    """Classify a quote against ALL authorities cited near it (nearest first).

    A quote is GREEN if it matches the held primary text of ANY nearby-cited authority — so a
    genuine quote of case A is not marked RED merely because case B is also cited nearby and
    happens to be closer. Only when no nearby authority's held text contains the quote does it
    fall to RED (attributed to the nearest HELD authority) or AMBER (none held nearby).
    """
    # 1. GREEN wins outright, whichever near authority it matches.
    for a in near:
        verdict, detail = classify_quote(quote, a, index)
        if verdict is Verdict.GREEN:
            return verdict, detail, a
    # 2. No verbatim match anywhere near — the verdict follows the NEAREST citation (what the
    #    quote is cited to): held-live → RED (absent), repealed → RED, unheld → AMBER. This is
    #    what keeps a quote of an UNHELD case AMBER even when some other, held case is cited
    #    nearby: we do not mis-attribute the miss to the held neighbour.
    verdict, detail = classify_quote(quote, near[0], index)
    return verdict, detail, near[0]


@dataclass
class GroundingReport:
    """All findings plus enough context to render and to prove the run happened."""

    findings: list[Finding] = field(default_factory=list)
    files_scanned: int = 0
    primary_texts: int = 0

    def sorted_findings(self) -> list[Finding]:
        return sorted(self.findings, key=lambda f: f.sort_key())

    def counts(self) -> dict[str, int]:
        c = {"GREEN": 0, "RED": 0, "AMBER": 0, "citation_without_source": 0}
        for f in self.findings:
            if f.kind == QUOTE and f.verdict is not None:
                c[f.verdict.value] += 1
            elif f.kind == CITATION_WITHOUT_SOURCE:
                c["citation_without_source"] += 1
        return c

    def to_dict(self) -> dict[str, object]:
        return {
            "files_scanned": self.files_scanned,
            "primary_texts": self.primary_texts,
            "counts": self.counts(),
            "findings": [f.to_dict() for f in self.sorted_findings()],
        }


@dataclass
class _UnheldRef:
    """Accumulator for one unsupported authority key across the whole corpus."""

    raw: str
    files: set[str] = field(default_factory=set)
    top_layer: Layer = Layer.OTHER


def _iter_citing_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for sub, _layer in _LAYER_DIRS:
        base = root / sub
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*")):
            if path.is_file() and path.suffix.lower() in _SCANNED_SUFFIXES:
                files.append(path)
    return files


def check_root(root: Path) -> GroundingReport:
    """Run the whole check over a raw corpus ``root`` and return a sortable report."""
    root = Path(root)
    index = PrimaryIndex.load(root / "authorities_primary_text")
    report = GroundingReport(primary_texts=sum(len(v) for v in index.by_key.values()) or 0)
    # primary_texts should count files, not key occurrences — recompute honestly below.
    unique_files = {p.path for prims in index.by_key.values() for p in prims}
    report.primary_texts = len(unique_files)

    # citation-without-source is aggregated across all files to compute blast radius.
    unheld_refs: dict[str, _UnheldRef] = {}

    for path in _iter_citing_files(root):
        rel = path.relative_to(root)
        layer = layer_of(rel)
        text = path.read_text(encoding="utf-8", errors="replace")
        report.files_scanned += 1
        authorities = find_authorities(text)

        # (1) quoted-string verification — quotes that sit near a citation.
        for quote in find_quotes(text):
            near = _near_authorities(quote, authorities)
            if not near:
                continue
            verdict, detail, attributed = classify_quote_near(quote, near, index)
            report.findings.append(
                Finding(
                    kind=QUOTE,
                    layer=layer,
                    file=rel.as_posix(),
                    authority_key=attributed.key,
                    verdict=verdict,
                    quote=quote.raw.strip(),
                    detail=detail,
                )
            )

        # (2b) malformed citation shapes — cheap, no network, flagged not classified.
        for raw_shape, _pos in _malformed_shapes(text, authorities):
            report.findings.append(
                Finding(
                    kind=MALFORMED_CITATION,
                    layer=layer,
                    file=rel.as_posix(),
                    authority_key="",
                    detail=f"citation-shaped but unparseable: {raw_shape!r}",
                )
            )

        # (2c) a live citation to a REPEALED authority is RED on its own — even with no
        # nearby quote (a quote near it is separately RED via classify_quote above).
        seen_repealed: set[str] = set()
        for ref in authorities:
            prims = index.get(ref.key)
            if prims and all(p.repealed for p in prims) and ref.key not in seen_repealed:
                seen_repealed.add(ref.key)
                report.findings.append(
                    Finding(
                        kind=REPEALED_CITATION,
                        layer=layer,
                        file=rel.as_posix(),
                        authority_key=ref.key,
                        verdict=Verdict.RED,
                        detail=(
                            f"cites REPEALED authority {ref.raw!r} "
                            f"(held as {prims[0].path.name}) — do not cite"
                        ),
                    )
                )

        # (2) citation-without-source — accumulate for blast radius.
        for ref in authorities:
            if index.held(ref.key):
                continue
            entry = unheld_refs.setdefault(ref.key, _UnheldRef(raw=ref.raw))
            entry.files.add(rel.as_posix())
            if layer.value > entry.top_layer.value:
                entry.top_layer = layer

    for key, entry in unheld_refs.items():
        report.findings.append(
            Finding(
                kind=CITATION_WITHOUT_SOURCE,
                layer=entry.top_layer,
                file=sorted(entry.files)[0],
                authority_key=key,
                detail=(
                    f"no primary text held for {entry.raw!r}; "
                    f"cited in {len(entry.files)} file(s)"
                ),
                blast_radius=len(entry.files),
            )
        )

    return report
