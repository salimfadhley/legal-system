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
2. **Never fail silently** — a typo'd key, an orphan sidecar, a ``no_index`` with no
   reason, or malformed YAML is *loud*: the offending **layer is dropped** (its values
   are NOT applied) and the document still ingests with default/inherited metadata,
   carrying a visible ``metadata_error``. Turning a typo into a *missing document* is
   the exact failure this avoids.
"""

from __future__ import annotations

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


def validate_layer(data: dict, source: str) -> list[str]:
    """Return loud messages for every unknown key in one metadata ``data`` layer."""
    unknown = sorted(k for k in data if k not in ALLOWED_KEYS)
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


def resolve_chain(rel: Path, root: Path) -> ResolvedMetadata:
    """Resolve ``rel``'s full metadata chain (folder defaults + its per-file sidecar).

    Every existing layer is validated in order. A layer with an unknown key, malformed
    YAML, or a non-mapping body is **dropped whole** (its values are not applied) and the
    failure is recorded — never raised — so the document still ingests. Finally, a
    resolved ``no_index`` without a ``no_index_reason`` is itself a bad-key-class error:
    the ``no_index`` is rejected (forced False) and the failure recorded loudly.
    """
    merged: dict[str, object] = {}
    errors: list[str] = []
    for layer in _chain_layers(rel, root):
        if not layer.is_file():
            continue
        source = layer.relative_to(root).as_posix()
        try:
            data = yaml.safe_load(layer.read_text()) or {}
        except yaml.YAMLError as exc:
            errors.append(f"{source}: malformed YAML ({exc.__class__.__name__}) — layer rejected")
            continue
        if not isinstance(data, dict):
            errors.append(f"{source}: not a mapping — layer rejected")
            continue
        layer_errors = validate_layer(data, source)
        if layer_errors:
            errors.extend(layer_errors)
            continue  # DROP the whole layer — its values are NOT applied
        _merge_layer(merged, data)

    if merged.get("no_index") and not merged.get("no_index_reason"):
        errors.append(
            f"{rel.as_posix()}: no_index set without no_index_reason — no_index REJECTED"
        )
        merged["no_index"] = False

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


@dataclass(frozen=True)
class LintFinding:
    """One problem found by ``metadata lint`` (or ``ok`` for a clean file)."""

    path: str
    ok: bool
    detail: str


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
    for msg in validate_layer(data, source):
        findings.append(LintFinding(source, False, msg.split(": ", 1)[1]))
    if data.get("no_index") and not data.get("no_index_reason"):
        findings.append(LintFinding(source, False, "no_index set without no_index_reason"))
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
