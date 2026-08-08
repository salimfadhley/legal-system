"""Per-file sidecars + the folder→file metadata resolution chain (doc/system/metadata.md).

Two carriers, one ordered chain (least-specific first):

    repo-root metadata.yaml  →  …  →  leaf-folder metadata.yaml  →  <file>.metadata.yaml

The **last** layer that sets a field wins (scalars override, lists union). A per-file
``<filename.ext>.metadata.yaml`` sidecar is simply the final, most-specific layer — so
every field (``author``, ``matters``, ``claim_source``, ``no_index`` and the prose /
provenance additions) works per file, not just per folder.

This module is the single source of truth for:

* the **allowed key vocabulary** (both carriers share it) — the basis of bad-key detection;
* the **merge** (scalar override + list union) and the whole-layer **drop-on-bad-key** rule;
* the ``notes`` **annotation fence** appended to indexed content;
* the ``metadata lint`` self-testing validator.

Two principles from the design doc are enforced here, never advisory:

1. **Human metadata beats inference** — resolved values overlay the LLM's guesses.
2. **Never fail silently** — a typo'd key, an orphan sidecar, or malformed YAML is
   *loud*: the offending **layer is dropped** (its values are NOT applied) and the
   document still ingests with default/inherited metadata, carrying a visible
   ``metadata_error``. Turning a typo into a *missing document* is the exact failure
   this avoids.

**The ``no_index`` exclusion dimension is the one exception — it FAILS CLOSED.** For
every ORDINARY field the risk of a bad layer is *losing a document*, so we fail open
(ingest anyway, loudly). For ``no_index`` the risk is the opposite and far worse —
*exposing legally-restricted material* — so any **apparent** exclusion attempt resolves
to do-not-index and is recorded loudly (see :class:`_NoIndexResolution`):

* ``no_index`` set truthy;
* a fuzzy/typo variant of the key present *at all* (``noindex``, ``no-index``,
  ``NO_INDEX``, ``no _index`` — anything that normalises to ``"noindex"``);
* ``no_index`` present but the value is not a clean boolean;
* ``no_index: true`` with a missing/empty ``no_index_reason``;
* a layer that will not parse (malformed YAML / non-mapping) — we cannot prove it was
  NOT an exclusion, so the subtree it governs fails closed.

It is a **one-way latch**: ``no_index`` resolves as a logical OR across the chain, so a
narrower ``no_index: false`` can never defeat a parent's exclusion (lifting one is a
deliberate edit at the level that set it, visible in git). ``no_index_category``
(``legally_obligatory`` | ``housekeeping``) is machine-distinguishable and defaults to
the safer ``legally_obligatory`` so an unclassified exclusion alarms loudly.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

# The suffix that marks a per-file sidecar: ``report.pdf.metadata.yaml`` → ``report.pdf``.
SIDECAR_SUFFIX = ".metadata.yaml"
# The folder-defaults filename (the least-specific carriers in the chain).
FOLDER_METADATA_NAME = "metadata.yaml"

# The shared vocabulary for BOTH carriers. Any other key is a typo/unknown key and makes
# the whole layer a hard error (its values are not applied). Kept deliberately explicit —
# a silently-ignored typo is the false-comfort failure the design doc calls out.
ALLOWED_KEYS: frozenset[str] = frozenset(
    {
        # --- archive / structural vocab (folder metadata.yaml, ADR 0004) ---
        "case_number",
        "matters",
        "primary_matter",
        "origin",
        "role",
        "topic",
        "date",
        "party_role",
        "parties",
        "document_type",
        "keywords",
        # --- human-authored authority (override inference) ---
        "author",
        "claim_source",
        "no_index",
        "no_index_reason",
        "no_index_category",
        # --- prose + receipt-provenance additions (doc/system/metadata.md) ---
        "notes",
        "method",
        "date_basis",
        "date_uncertain",
        "source_channel",
        "obtained_note",
        "superseded_by",
    }
)

# List-valued keys are UNIONed across chain layers (augment); every other key is a scalar
# and the most-specific layer OVERRIDES (last-writer-wins).
LIST_KEYS: frozenset[str] = frozenset({"matters", "parties", "keywords"})

# --- the fail-closed exclusion dimension (no_index) -------------------------------------
NO_INDEX_KEY = "no_index"
NO_INDEX_REASON_KEY = "no_index_reason"
NO_INDEX_CATEGORY_KEY = "no_index_category"

# The two recognised exclusion classes. ``legally_obligatory`` (court undertaking /
# privilege / statutory restriction, e.g. CPIA s.17) is an INCIDENT if wrongly indexed;
# ``housekeeping`` (build artefact / duplicate / derived) is noise. A missing/unknown
# category defaults to ``legally_obligatory`` — the SAFER reading — so an unclassified
# exclusion alarms loudly, not quietly.
CATEGORY_LEGALLY_OBLIGATORY = "legally_obligatory"
CATEGORY_HOUSEKEEPING = "housekeeping"
RECOGNISED_NO_INDEX_CATEGORIES: frozenset[str] = frozenset(
    {CATEGORY_LEGALLY_OBLIGATORY, CATEGORY_HOUSEKEEPING}
)

# A key "means no_index" if, lowercased with all whitespace/underscore/hyphen separators
# stripped, it equals ``"noindex"``. This catches every fuzzy/typo variant casework named
# (``noindex``, ``no-index``, ``No_Index``, ``NO_INDEX``, ``no _index``, …). The canonical
# spelling is ``no_index``; ANY other spelling that matches is treated as an apparent
# exclusion attempt (we cannot trust a value written under a misspelled key).
_NOINDEX_NORMALISED = "noindex"
_KEY_SEPARATORS = re.compile(r"[\s_\-]+")


def _normalise_key(key: object) -> str:
    return _KEY_SEPARATORS.sub("", str(key).lower())


def is_no_index_key(key: object) -> bool:
    """True if ``key`` is ``no_index`` OR a case/separator/typo variant of it."""
    return _normalise_key(key) == _NOINDEX_NORMALISED

# The value that means a date is asserted at face value — the only ``date_basis`` for which
# ``date_uncertain`` defaults to False when a date is present (any other basis ⇒ uncertain).
DATE_BASIS_ON_ITS_FACE = "on its face"

# The exact fence wrapping a casework ``notes`` block appended to the indexed content, so it
# can never be mistaken for the document's own words (doc/system/metadata.md §3b).
ANNOTATION_OPEN = "[ANNOTATION — casework, not part of this document]"
ANNOTATION_CLOSE = "[/ANNOTATION]"


def is_sidecar_name(name: str) -> bool:
    """True if ``name`` is a metadata carrier (folder defaults or a per-file sidecar).

    The single predicate the ingest skip consults so a metadata file is never itself
    ingested as a document (extends the existing ``metadata.yaml`` skip to sidecars).
    """
    return name == FOLDER_METADATA_NAME or name.endswith(SIDECAR_SUFFIX)


def sidecar_target(sidecar: Path) -> Path:
    """The evidence file a ``<file>.metadata.yaml`` sidecar annotates (strip the suffix)."""
    return sidecar.with_name(sidecar.name[: -len(SIDECAR_SUFFIX)])


def build_annotation(notes: str) -> str:
    """Render a casework ``notes`` block inside the immutable annotation fence.

    The fence is fixed text so the note can never be read back as the document's own
    words. Appended to the indexed/stored content (never mutating the original).
    """
    return f"{ANNOTATION_OPEN}\n{notes.strip()}\n{ANNOTATION_CLOSE}"


def append_annotation(body: str, notes: str | None) -> str:
    """Append a fenced ``notes`` annotation to ``body`` (returns ``body`` unchanged if no note)."""
    if not (notes and notes.strip()):
        return body
    return body.rstrip("\n") + "\n\n" + build_annotation(notes)


@dataclass(frozen=True)
class ResolvedMetadata:
    """The outcome of resolving one file's metadata chain.

    ``fields`` holds only the values from **valid** layers (a layer with an unknown key
    is dropped whole); ``errors`` holds every loud failure (unknown keys, malformed
    YAML, ``no_index`` without a reason) so ingest can surface them without ever
    dropping the document.
    """

    fields: dict[str, object] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    @property
    def error_summary(self) -> str | None:
        """A single ``metadata_error`` string for the manifest/status, or None if clean."""
        return "; ".join(self.errors) if self.errors else None


def validate_layer(
    data: dict, source: str, *, ignore_no_index_variants: bool = False
) -> list[str]:
    """Return loud messages for every unknown key in one metadata ``data`` layer.

    ``ignore_no_index_variants`` omits keys that normalise to ``no_index`` from the
    unknown-key complaint — the fail-closed exclusion resolver already reports a
    suspected-typo error for those, so the ordinary path must not double-report them.
    """
    unknown = sorted(
        k
        for k in data
        if k not in ALLOWED_KEYS
        and not (ignore_no_index_variants and is_no_index_key(k))
    )
    if not unknown:
        return []
    return [f"{source}: unknown key(s) {unknown} — layer rejected, values NOT applied"]


def _merge_layer(merged: dict[str, object], data: dict) -> None:
    """Overlay a validated ``data`` layer onto ``merged`` (scalars override, lists union)."""
    for key, value in data.items():
        if value in (None, ""):
            continue  # an unset value never overrides an inherited one
        if key in LIST_KEYS and isinstance(value, list):
            existing = merged.get(key)
            base = list(existing) if isinstance(existing, list) else []
            for item in value:
                if item not in base:
                    base.append(item)
            merged[key] = base
        else:
            merged[key] = value


def _chain_layers(rel: Path, root: Path) -> list[Path]:
    """The ordered metadata files for ``rel``: root→leaf folder defaults, then the sidecar."""
    layers: list[Path] = []
    cur = root
    for seg in ("", *rel.parts[:-1]):  # folders only, least-specific first
        cur = cur / seg if seg else cur
        layers.append(cur / FOLDER_METADATA_NAME)
    layers.append((root / rel).with_name(rel.name + SIDECAR_SUFFIX))  # most-specific
    return layers


@dataclass
class _NoIndexResolution:
    """Fold the ``no_index`` exclusion dimension across a file's chain — FAIL CLOSED.

    ``no_index`` is a logical OR / one-way latch: once ANY layer sets it true — or merely
    *appears* to attempt an exclusion — no narrower layer can turn it off. Every ambiguity
    (a key typo, an unparseable value, a missing reason, a layer that would not parse)
    resolves to do-not-index and is recorded loudly. The category resolves to the safer
    ``legally_obligatory`` unless a well-formed layer explicitly and unambiguously says
    ``housekeeping``.
    """

    latched: bool = False  # an exclusion (or apparent attempt) has fired → do-not-index
    mentioned: bool = False  # some no_index key appeared (even a clean ``false``)
    ambiguous: bool = False  # an apparent attempt we could not confirm safe
    unknown_category: bool = False
    explicit_reason: str | None = None
    explicit_categories: set[str] = field(default_factory=set)
    errors: list[str] = field(default_factory=list)

    def note_unparseable_layer(self, source: str, detail: str) -> None:
        """A layer that would not parse governs its subtree — fail it closed."""
        self.latched = True
        self.mentioned = True
        self.ambiguous = True
        self.errors.append(
            f"{source}: {detail} — cannot rule out an exclusion; "
            f"EXCLUDED fail-closed (do-not-index)"
        )

    def examine(self, data: dict, source: str) -> None:
        """Inspect ONE parsed mapping's RAW keys for an apparent exclusion attempt.

        Run on the raw layer BEFORE the unknown-key drop that governs ordinary fields, so
        a fail-closed signal is never lost when the layer is dropped for an unrelated typo.
        """
        typos = sorted(k for k in data if is_no_index_key(k) and k != NO_INDEX_KEY)
        if typos:  # a misspelled key present at all ⇒ assume exclusion was intended
            self.latched = True
            self.mentioned = True
            self.ambiguous = True
            self.errors.append(
                f"{source}: suspected no_index typo {typos} — an apparent exclusion "
                f"attempt; EXCLUDED fail-closed (do-not-index)"
            )
        if NO_INDEX_KEY in data:
            self.mentioned = True
            value = data[NO_INDEX_KEY]
            if not isinstance(value, bool):  # ``maybe``, ``"true"``, 1, … — untrustworthy
                self.latched = True
                self.ambiguous = True
                self.errors.append(
                    f"{source}: no_index value {value!r} is not a clean boolean — "
                    f"EXCLUDED fail-closed (do-not-index)"
                )
            elif value is True:
                self.latched = True
                reason = data.get(NO_INDEX_REASON_KEY)
                if reason is None or not str(reason).strip():
                    self.ambiguous = True
                    self.errors.append(
                        f"{source}: no_index set without no_index_reason — "
                        f"EXCLUDED fail-closed (do-not-index)"
                    )
                else:
                    self.explicit_reason = str(reason).strip()
            # value is False ⇒ contributes nothing (OR-latch never lets it unset a parent).
        category = data.get(NO_INDEX_CATEGORY_KEY)
        if category is not None and str(category).strip():
            value = str(category).strip()
            if value in RECOGNISED_NO_INDEX_CATEGORIES:
                self.explicit_categories.add(value)
            else:
                self.unknown_category = True
                self.errors.append(
                    f"{source}: unknown no_index_category {value!r} — defaulting to "
                    f"{CATEGORY_LEGALLY_OBLIGATORY} (the safer reading)"
                )

    def resolved_reason(self) -> str:
        if self.explicit_reason:
            return self.explicit_reason
        return (
            "fail-closed: an apparent exclusion could not be confirmed safe "
            "(see metadata_error)"
        )

    def resolved_category(self) -> str:
        # Any ambiguity, an unknown category, or an explicit legally_obligatory ⇒ the
        # serious class. Only a clean, explicit, housekeeping-ONLY exclusion is noise.
        if (
            self.ambiguous
            or self.unknown_category
            or CATEGORY_LEGALLY_OBLIGATORY in self.explicit_categories
        ):
            return CATEGORY_LEGALLY_OBLIGATORY
        if self.explicit_categories == {CATEGORY_HOUSEKEEPING}:
            return CATEGORY_HOUSEKEEPING
        return CATEGORY_LEGALLY_OBLIGATORY  # missing category ⇒ safer reading

    def apply(self, merged: dict[str, object]) -> None:
        """Overwrite the exclusion keys on ``merged`` with the latched resolution."""
        if self.latched:
            merged[NO_INDEX_KEY] = True
            merged[NO_INDEX_REASON_KEY] = self.resolved_reason()
            merged[NO_INDEX_CATEGORY_KEY] = self.resolved_category()
        elif self.mentioned:
            merged[NO_INDEX_KEY] = False


def resolve_chain(rel: Path, root: Path) -> ResolvedMetadata:
    """Resolve ``rel``'s full metadata chain (folder defaults + its per-file sidecar).

    Ordinary fields **fail OPEN** exactly as before: a layer with an unknown key,
    malformed YAML, or a non-mapping body is dropped whole (its values are not applied)
    and the failure is recorded — never raised — so the document still ingests.

    The ``no_index`` exclusion dimension **fails CLOSED** and is resolved SEPARATELY
    (:class:`_NoIndexResolution`), examining every layer's raw data *independently* of the
    unknown-key drop — so a fail-closed signal survives a layer that is dropped for an
    unrelated typo, and an unparseable layer still excludes the subtree it governs. The
    resolution is a one-way OR-latch, so a narrower ``no_index: false`` can never unset a
    parent's exclusion.
    """
    merged: dict[str, object] = {}
    errors: list[str] = []
    no_index = _NoIndexResolution()
    for layer in _chain_layers(rel, root):
        if not layer.is_file():
            continue
        source = layer.relative_to(root).as_posix()
        try:
            data = yaml.safe_load(layer.read_text()) or {}
        except yaml.YAMLError as exc:
            errors.append(f"{source}: malformed YAML ({exc.__class__.__name__}) — layer rejected")
            no_index.note_unparseable_layer(source, f"malformed YAML ({exc.__class__.__name__})")
            continue
        if not isinstance(data, dict):
            errors.append(f"{source}: not a mapping — layer rejected")
            no_index.note_unparseable_layer(source, "metadata layer is not a mapping")
            continue
        # Exclusion dimension FIRST, on the raw layer — never lost to the ordinary drop.
        no_index.examine(data, source)
        layer_errors = validate_layer(data, source, ignore_no_index_variants=True)
        if layer_errors:
            errors.extend(layer_errors)
            continue  # DROP the whole layer for ORDINARY fields — values NOT applied
        _merge_layer(merged, data)

    errors.extend(no_index.errors)
    no_index.apply(merged)  # the latched, fail-closed exclusion resolution wins
    return ResolvedMetadata(fields=merged, errors=errors)


def resolve_date_uncertain(chain: dict) -> bool | None:
    """Resolve ``date_uncertain`` from a resolved chain, or None to leave the default.

    An explicit ``date_uncertain`` in the chain wins. Otherwise, when a ``date`` is
    present it defaults **True** unless ``date_basis`` is exactly ``"on its face"``. With
    no date at all we return None (leave the field at its default — nothing to qualify).
    """
    if "date_uncertain" in chain:
        return bool(chain["date_uncertain"])
    if chain.get("date"):
        return chain.get("date_basis") != DATE_BASIS_ON_ITS_FACE
    return None


# --- lint self-test fixtures (embedded, so the linter can prove itself) -----------------

# A known-GOOD sidecar the self-test asserts the linter passes.
_SELFTEST_GOOD = "author: Paul Keitch\nclaim_source: Paul Keitch\nnotes: |\n  Exhibit SM/01.\n"
# A known-BAD sidecar (a typo'd key) the self-test asserts the linter flags.
_SELFTEST_BAD = "authour: Paul Keitch\n"


# Fields that assert authority over inference (they OVERRIDE the LLM's guess). Setting one
# without a ``method`` is an unexplained override — a guess indistinguishable from a check —
# so ``metadata lint`` WARNs (it does not fail: the override may well be right, it is just
# unexplained). This is the same principle as the grounding checker: a claim to have verified
# something must say how.
AUTHORITATIVE_FIELDS: frozenset[str] = frozenset({"author", "claim_source"})


@dataclass(frozen=True)
class LintFinding:
    """One problem (or warning) found by ``metadata lint`` (or ``ok`` for a clean file).

    ``ok`` is False only for hard errors (which fail CI). A warning keeps ``ok=True`` and
    sets ``level="warn"`` so it is surfaced without gating.
    """

    path: str
    ok: bool
    detail: str
    level: str = "error"


def lint_file(path: Path, root: Path) -> list[LintFinding]:
    """Lint one metadata carrier: malformed YAML, non-mapping, unknown keys, no_index
    rules, and (for a sidecar) an orphan target. Returns one clean finding when valid."""
    source = path.relative_to(root).as_posix() if path.is_relative_to(root) else path.name
    findings: list[LintFinding] = []
    is_sidecar = path.name.endswith(SIDECAR_SUFFIX) and path.name != FOLDER_METADATA_NAME
    if is_sidecar and not sidecar_target(path).exists():
        findings.append(
            LintFinding(source, False, f"orphan sidecar — no target file {sidecar_target(path).name!r}")
        )
    try:
        data = yaml.safe_load(path.read_text()) or {}
    except yaml.YAMLError as exc:
        findings.append(LintFinding(source, False, f"malformed YAML ({exc.__class__.__name__})"))
        return findings
    if not isinstance(data, dict):
        findings.append(LintFinding(source, False, "not a mapping"))
        return findings
    for msg in validate_layer(data, source, ignore_no_index_variants=True):
        findings.append(LintFinding(source, False, msg.split(": ", 1)[1]))
    # The fail-closed exclusion dimension: a typo variant, an unparseable value, or a
    # reason-less no_index is a HARD error at lint time too (the resolver would exclude
    # the subtree — better to catch the mistake before ingest).
    _excl = _NoIndexResolution()
    _excl.examine(data, source)
    for msg in _excl.errors:
        findings.append(LintFinding(source, False, msg.split(": ", 1)[1]))
    # A method-less authoritative override is a WARNING — only when the file is otherwise
    # error-free (a hard error already speaks louder, and we must not overwrite it in a
    # path→finding map). The override may be correct; it is simply unexplained.
    if not any(not f.ok for f in findings):
        set_authoritative = sorted(
            k for k in AUTHORITATIVE_FIELDS if data.get(k) not in (None, "")
        )
        if set_authoritative and data.get("method") in (None, ""):
            findings.append(
                LintFinding(
                    source,
                    True,
                    f"{', '.join(set_authoritative)} set without a method — an "
                    "unexplained override (a guess indistinguishable from a check)",
                    level="warn",
                )
            )
    if not findings:
        findings.append(LintFinding(source, True, "ok"))
    return findings


def lint_root(root: Path) -> list[LintFinding]:
    """Lint every folder ``metadata.yaml`` and ``*.metadata.yaml`` sidecar under ``root``."""
    root = Path(root)
    findings: list[LintFinding] = []
    for path in sorted(root.rglob("*")):
        if path.is_file() and is_sidecar_name(path.name):
            findings.extend(lint_file(path, root))
    return findings


def _selftest_lint() -> None:
    """Prove the linter flags a known-bad file and passes a known-good one.

    A linter that silently passes everything is worse than none — so ``metadata lint``
    runs this against embedded fixtures FIRST and refuses to report if it fails.
    """
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        troot = Path(tmp)
        (troot / "good.pdf").write_text("x")
        (troot / f"good.pdf{SIDECAR_SUFFIX}").write_text(_SELFTEST_GOOD)
        (troot / "bad.pdf").write_text("x")
        (troot / f"bad.pdf{SIDECAR_SUFFIX}").write_text(_SELFTEST_BAD)
        findings = lint_root(troot)
        by_ok = {f.path: f.ok for f in findings}
        good_key = f"good.pdf{SIDECAR_SUFFIX}"
        bad_key = f"bad.pdf{SIDECAR_SUFFIX}"
        if not by_ok.get(good_key, False):
            raise SelfTestError("lint self-test failed: known-GOOD sidecar was flagged")
        if by_ok.get(bad_key, True):
            raise SelfTestError("lint self-test failed: known-BAD sidecar was NOT flagged")


class SelfTestError(RuntimeError):
    """Raised when ``metadata lint``'s self-test fails — the linter refuses to report."""


def run_selftest() -> None:
    """Public entry point: run the embedded self-test, raising :class:`SelfTestError`."""
    _selftest_lint()
