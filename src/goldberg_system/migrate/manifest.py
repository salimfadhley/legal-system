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

import yaml

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


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _resolve_chain(rel: Path, root: Path) -> dict:
    """Merge ``metadata.yaml`` from ``root`` down to ``rel``'s folder (child overrides).

    Plain-dict merge over the archive's own vocabulary (``case_number`` etc.); no
    schema validation here — translation to the schema happens at enrichment time.
    """
    merged: dict = {}
    cur = root
    for seg in ("", *rel.parts[:-1]):  # folders only, root first
        cur = cur / seg if seg else cur
        meta = cur / "metadata.yaml"
        if meta.is_file():
            try:
                data = yaml.safe_load(meta.read_text()) or {}
            except yaml.YAMLError:
                data = {}
            merged.update({k: v for k, v in data.items() if v not in (None, "")})
    return merged


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
    is already registered (so the reconciler's per-cycle git-commit lookups are
    bounded to genuinely new files). This is the single per-file derivation reused
    by both the bulk :func:`build_manifest` and the polling reconciler.
    """
    root = Path(root)
    rel = Path(rel)
    if rel.parts and rel.parts[0] == ".git":
        return None
    if rel.name == "metadata.yaml" or allowlist.is_excluded_file(rel):
        return None
    tree = allowlist.tree_for(rel)
    if tree is None:  # only files under an allowlisted tree get a manifest entry
        return None
    path = root / rel
    sha = _sha256(path)
    if known_shas is not None and sha in known_shas:
        return None
    chain = _resolve_chain(rel, root)
    case_number = chain.get("case_number")
    return ManifestEntry(
        sha256=sha,
        raw_path=rel.as_posix(),
        raw_commit=_last_commit(root, rel) if with_commit else "",
        size=path.stat().st_size,
        matters=[str(case_number)] if case_number else [],
        origin=tree.origin,
        party_role=chain.get("party_role"),
        document_type=chain.get("document_type"),
    )


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
