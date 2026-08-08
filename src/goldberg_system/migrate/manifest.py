"""The provenance manifest — the SHA-256 join key between goldberg-raw and Papra.

For every file in goldberg-raw we record ``sha256`` (which equals Papra's
``original_sha256_hash``, ADR 0006 spike §1), the git ``raw_path`` + ``raw_commit``,
and the structural metadata resolved from the folder ``metadata.yaml`` chain
(``matters`` from ``case_number``, plus ``party_role`` / ``document_type`` /
``origin``). The pipeline looks an entry up by SHA-256 to attach real provenance
to whatever Papra extracted.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from goldberg_system.metadata import sidecar
from goldberg_system.migrate.allowlist import Allowlist

if TYPE_CHECKING:
    from goldberg_system.metadata.schema import DocumentMetadata


@dataclass(frozen=True)
class ManifestEntry:
    sha256: str
    raw_path: str  # POSIX, relative to the goldberg-raw root
    raw_commit: str  # git commit that last touched the file ("" if not committed yet)
    size: int
    matters: list[str] = field(default_factory=list)
    origin: str = "received"
    party_role: str | None = None
    document_type: str | None = None
    author: str | None = None
    claim_source: str | None = None
    no_index: bool = False  # legally/contractually restricted — never index (recursive)
    no_index_reason: str | None = None
    # --- per-file sidecar prose + receipt-provenance (doc/system/metadata.md) ---
    notes: str | None = None
    method: str | None = None
    date: str | None = None
    date_basis: str | None = None
    date_uncertain: bool = False
    source_channel: str | None = None
    obtained_note: str | None = None
    superseded_by: str | None = None
    # LOUD-but-present: a bad key / malformed layer / no_index-without-reason was
    # rejected. The document is still manifested/ingested; this records why.
    metadata_error: str | None = None


def entry_is_no_index(entry: dict) -> bool:
    """True when a manifest ``entry`` belongs to a ``no_index`` (restricted) subtree.

    The single predicate the ingest-selection paths consult so a restricted document is
    excluded from ingestion consistently (catch-up selection, bulk/​event reingest).
    The manifest still *carries* the entry (provenance survives) — it is only kept out
    of the work that would index it.
    """
    return bool(entry.get("no_index"))


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _resolve_chain(rel: Path, root: Path) -> dict:
    """Merge the metadata chain for ``rel`` — folder ``metadata.yaml`` defaults **plus its
    per-file ``<file>.metadata.yaml`` sidecar** as the final, most-specific layer.

    Delegates to :func:`goldberg_system.metadata.sidecar.resolve_chain` (scalar override,
    list union, whole-layer drop on a bad key). Returns the merged fields only; the loud
    errors are consulted via :func:`resolve_chain_full`. No schema validation here —
    translation to the schema happens at enrichment time.
    """
    return resolve_chain_full(rel, root).fields


def resolve_chain_full(rel: Path, root: Path) -> sidecar.ResolvedMetadata:
    """The full resolution for ``rel`` — merged fields **and** loud metadata errors."""
    return sidecar.resolve_chain(Path(rel), Path(root))


# Archive-vocab keys (folder metadata.yaml / per-file sidecar) that map straight onto the
# same-named DocumentMetadata field — human-set metadata is authoritative over inference.
_PASSTHROUGH_FIELDS = (
    "party_role",
    "document_type",
    "author",
    "claim_source",
    "no_index",
    "no_index_reason",
    # prose + receipt-provenance additions (each flows exactly like claim_source)
    "notes",
    "method",
    "date",
    "date_basis",
    "source_channel",
    "obtained_note",
    "superseded_by",
)


def folder_base_fields(chain: dict) -> dict[str, object]:
    """Translate a resolved metadata ``chain`` into DocumentMetadata field values
    (``model_copy(update=...)`` kwargs).

    The archive-vocab → schema mapping used to overlay authoritative folder/sidecar
    metadata onto a document: ``case_number`` and/or ``matters`` → ``matters`` /
    ``primary_matter`` plus the same-named human-set pass-through fields. Only keys
    actually present are returned, so callers overlay just what the metadata asserts and
    leave every other value untouched. ``date_uncertain`` is computed here (not a
    pass-through) because this path applies via ``model_copy``, which bypasses the schema
    validator. Reused by ``re-enrich`` so a casework metadata edit re-applies without a
    full Docling re-ingest.
    """
    fields: dict[str, object] = {}
    matters = _resolve_matters(chain)
    if matters:
        fields["matters"] = matters
        primary = chain.get("primary_matter")
        fields["primary_matter"] = str(primary) if primary else matters[0]
    for key in _PASSTHROUGH_FIELDS:
        val = chain.get(key)
        if val is not None:
            fields[key] = val
    date_uncertain = sidecar.resolve_date_uncertain(chain)
    if date_uncertain is not None:
        fields["date_uncertain"] = date_uncertain
    return fields


def _resolve_matters(chain: dict) -> list[str]:
    """Union ``case_number`` (archive vocab) with an explicit ``matters`` list (schema vocab)."""
    matters: list[str] = []
    case_number = chain.get("case_number")
    if case_number:
        matters.append(str(case_number))
    for m in chain.get("matters") or []:
        if str(m) not in matters:
            matters.append(str(m))
    return matters


def _last_commit(root: Path, rel: Path) -> str:
    try:
        out = subprocess.run(
            ["git", "-C", str(root), "log", "-1", "--format=%H", "--", str(rel)],
            capture_output=True,
            text=True,
            timeout=30,
        )
        return out.stdout.strip()
    except (subprocess.SubprocessError, OSError):
        return ""


def build_entry(
    root: Path | str,
    rel: Path | str,
    allowlist: Allowlist,
    *,
    with_commit: bool = True,
    known_shas: set[str] | None = None,
) -> ManifestEntry | None:
    """Build the provenance entry for a single ``rel`` file under ``root``.

    Returns ``None`` when the file is not migratable evidence (``.git`` internals,
    a folder ``metadata.yaml``, an ``exclude_globs`` match, or a file outside any
    allowlisted tree) — or when ``known_shas`` is given and the file's content hash
    is already registered (so the ingest catch-up's git-commit lookups are bounded
    to genuinely new files). This is the single per-file derivation reused by both
    the bulk :func:`build_manifest` and the event-driven ingest catch-up.
    """
    root = Path(root)
    rel = Path(rel)
    if rel.parts and rel.parts[0] == ".git":
        return None
    # A metadata carrier (folder defaults OR a per-file sidecar) is never itself a
    # document — the single skip predicate keeps a sidecar from becoming an entry.
    if sidecar.is_sidecar_name(rel.name) or allowlist.is_excluded_file(rel):
        return None
    tree = allowlist.tree_for(rel)
    if tree is None:  # only files under an allowlisted tree get a manifest entry
        return None
    path = root / rel
    sha = _sha256(path)
    if known_shas is not None and sha in known_shas:
        return None
    resolved = resolve_chain_full(rel, root)
    chain = resolved.fields
    date_uncertain = sidecar.resolve_date_uncertain(chain)
    return ManifestEntry(
        sha256=sha,
        raw_path=rel.as_posix(),
        raw_commit=_last_commit(root, rel) if with_commit else "",
        size=path.stat().st_size,
        matters=_resolve_matters(chain),
        origin=tree.origin,
        party_role=_opt_str(chain.get("party_role")),
        document_type=_opt_str(chain.get("document_type")),
        author=_opt_str(chain.get("author")),
        claim_source=_opt_str(chain.get("claim_source")),
        no_index=bool(chain.get("no_index", False)),
        no_index_reason=_opt_str(chain.get("no_index_reason")),
        notes=_opt_str(chain.get("notes")),
        method=_opt_str(chain.get("method")),
        date=_opt_str(chain.get("date")),
        date_basis=_opt_str(chain.get("date_basis")),
        date_uncertain=bool(date_uncertain) if date_uncertain is not None else False,
        source_channel=_opt_str(chain.get("source_channel")),
        obtained_note=_opt_str(chain.get("obtained_note")),
        superseded_by=_opt_str(chain.get("superseded_by")),
        metadata_error=resolved.error_summary,
    )


def _opt_str(value: object) -> str | None:
    """Coerce a chain value to a trimmed non-empty string, else None."""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def build_manifest(
    raw_root: Path | str, allowlist: Allowlist, *, with_commit: bool = True
) -> list[ManifestEntry]:
    """Walk ``raw_root`` and build a manifest entry for every migrated file."""
    root = Path(raw_root)
    entries: list[ManifestEntry] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root)
        entry = build_entry(root, rel, allowlist, with_commit=with_commit)
        if entry is not None:
            entries.append(entry)
    return entries


class Manifest:
    """A loaded provenance manifest, queried by content SHA-256 (ADR 0006 join key)."""

    def __init__(self, by_sha: dict[str, dict]) -> None:
        self._by_sha = {k.lower(): v for k, v in by_sha.items()}

    @classmethod
    def load(cls, path: Path | str) -> "Manifest":
        return cls(json.loads(Path(path).read_text()))

    def __len__(self) -> int:
        return len(self._by_sha)

    def entry_for_sha(self, sha256: str | None) -> dict | None:
        return self._by_sha.get((sha256 or "").lower()) if sha256 else None

    def items(self) -> "list[tuple[str, dict]]":
        """`(sha256, entry)` pairs for every manifested file."""
        return list(self._by_sha.items())

    def base_for_sha(self, sha256: str | None) -> "DocumentMetadata | None":
        """Resolve real provenance + matters for a raw file's SHA-256, or None.

        The SHA-256 is the pipeline correlation ID (== Papra ``original_sha256_hash``
        == the raw file hash), so this serves both the Papra-join path and the
        direct-Docling bulk path.
        """
        from goldberg_system.metadata.schema import DocumentMetadata, Origin

        entry = self.entry_for_sha(sha256)
        if entry is None:
            return None
        matters = list(entry.get("matters") or [])
        origin = entry.get("origin")
        return DocumentMetadata(
            raw_path=entry.get("raw_path"),
            raw_commit=entry.get("raw_commit") or None,
            raw_sha256=entry.get("sha256") or (sha256.lower() if sha256 else None),
            matters=matters,
            primary_matter=matters[0] if matters else None,
            origin=Origin(origin) if origin in ("received", "authored") else None,
            document_type=entry.get("document_type"),
            party_role=entry.get("party_role"),
            author=entry.get("author"),
            claim_source=entry.get("claim_source"),
            no_index=bool(entry.get("no_index", False)),
            no_index_reason=entry.get("no_index_reason"),
            notes=entry.get("notes"),
            method=entry.get("method"),
            date=entry.get("date"),
            date_basis=entry.get("date_basis"),
            date_uncertain=bool(entry.get("date_uncertain", False)),
            source_channel=entry.get("source_channel"),
            obtained_note=entry.get("obtained_note"),
            superseded_by=entry.get("superseded_by"),
            metadata_error=entry.get("metadata_error"),
        )

    def base_for(self, papra_doc: "PapraDocumentLike") -> "DocumentMetadata | None":
        """Resolve provenance for a Papra document (joins on ``original_sha256_hash``)."""
        return self.base_for_sha(getattr(papra_doc, "original_sha256_hash", None))


class PapraDocumentLike(Protocol):  # pragma: no cover - typing aid only
    original_sha256_hash: str | None


def write_manifest(entries: list[ManifestEntry], dest: Path | str) -> Path:
    """Write the manifest as JSON keyed by SHA-256 (the Papra join key)."""
    out = Path(dest)
    out.parent.mkdir(parents=True, exist_ok=True)
    by_sha = {e.sha256: asdict(e) for e in entries}
    out.write_text(json.dumps(by_sha, indent=2, sort_keys=True) + "\n")
    return out
