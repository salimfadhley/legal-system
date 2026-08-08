"""The durable exclusion registry — deliberately-purged documents that must NEVER be
re-added by an automated process (the CPR-32.12 class-fault fix).

Marking a folder ``no_index`` stops *future* ingestion, and :func:`goldberg_system.deindex.deindex`
purges what was already indexed. But neither of those *remembers* that a purge was a
**deliberate** act: the catch-up/reconcile healer treats "present in goldberg-raw,
absent from the index" as a gap to fill, so it silently re-ingested four restricted
documents that had been purged under a court undertaking. A system that silently ADDS
a restricted document is worse than one that silently drops.

This module is the memory. Every deliberate purge appends a line to a **tracked,
append-only, human-readable JSONL registry** (``config/exclusion-registry.jsonl``):

    {"raw_path": "...", "raw_sha256": "...", "reason": "...", "timestamp": "...Z", "source": "deindex"}

The healer (catch-up selection + reconcile) and the sink (defense in depth) consult a
loaded :class:`ExclusionRegistry` — keyed for O(1) lookup by ``raw_path`` **and** by
``raw_sha256`` — so a registered exclusion is classified ``deliberately_excluded`` and
is *never* selected for ingestion. Encountering one that would have been added is LOUD
(see :mod:`goldberg_system.observability.restricted_alert`).

Path matching mirrors ``deindex``'s literal ``raw_path``-prefix purge: a registered
entry whose ``raw_path`` is a prefix of a candidate path excludes that candidate. This
is deliberately **fail-safe** — over-matching only ever excludes *more*, never re-adds.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from pathlib import Path

from goldberg_system.config import config_dir
from goldberg_system.provenance import now_iso

log = logging.getLogger("goldberg.exclusion")

REGISTRY_FILENAME = "exclusion-registry.jsonl"

# How a registry entry came to be recorded.
SOURCE_DEINDEX = "deindex"
SOURCE_MANUAL = "manual"
VALID_SOURCES = (SOURCE_DEINDEX, SOURCE_MANUAL)


@dataclass(frozen=True)
class ExclusionEntry:
    """One deliberate exclusion — a purge that must never be automatically undone."""

    raw_path: str
    reason: str
    timestamp: str
    source: str  # deindex | manual
    raw_sha256: str | None = None

    def __post_init__(self) -> None:
        # Normalise the content hash to lowercase so the persisted record and every
        # lookup key agree regardless of how the entry was constructed.
        if self.raw_sha256:
            object.__setattr__(self, "raw_sha256", self.raw_sha256.lower())

    def to_json(self) -> str:
        """One compact, stable JSONL line (sorted keys → deterministic diffs)."""
        return json.dumps(asdict(self), sort_keys=True, ensure_ascii=False)

    @classmethod
    def from_dict(cls, data: dict) -> "ExclusionEntry":
        raw_sha = data.get("raw_sha256")
        return cls(
            raw_path=str(data["raw_path"]),
            reason=str(data.get("reason") or ""),
            timestamp=str(data.get("timestamp") or ""),
            source=str(data.get("source") or SOURCE_MANUAL),
            raw_sha256=str(raw_sha).lower() if raw_sha else None,
        )


def default_registry_path() -> Path:
    """The tracked registry file: ``<config_dir>/exclusion-registry.jsonl``."""
    return config_dir() / REGISTRY_FILENAME


class ExclusionRegistry:
    """Loaded exclusions, indexed for O(1) lookup by ``raw_path`` and ``raw_sha256``.

    Immutable after construction (the file is append-only; reload to see new entries).
    An empty registry (the common case — nothing purged yet, or the file is absent)
    excludes nothing, so consulting it is always safe.
    """

    def __init__(self, entries: Iterable[ExclusionEntry] = ()) -> None:
        self._entries: list[ExclusionEntry] = []
        self._by_path: dict[str, ExclusionEntry] = {}
        self._by_sha: dict[str, ExclusionEntry] = {}
        for entry in entries:
            self._add(entry)

    def _add(self, entry: ExclusionEntry) -> None:
        self._entries.append(entry)
        # First writer wins for a given key so the *earliest* (most authoritative)
        # reason is what a lookup surfaces; every entry is still kept for the audit list.
        self._by_path.setdefault(entry.raw_path, entry)
        if entry.raw_sha256:
            self._by_sha.setdefault(entry.raw_sha256.lower(), entry)

    # ── construction ─────────────────────────────────────────────────────────
    @classmethod
    def load(cls, path: Path | str) -> "ExclusionRegistry":
        """Load from a JSONL file. Missing file → empty registry (excludes nothing).

        Blank lines and ``#``-prefixed comment lines are ignored so the file can carry
        a human-readable header. A malformed line is skipped with a warning rather than
        crashing the pipeline — a corrupt registry must never take the system down.
        """
        p = Path(path)
        entries: list[ExclusionEntry] = []
        if p.is_file():
            for raw_line in p.read_text(encoding="utf-8").splitlines():
                line = raw_line.strip()
                if not line or line.startswith("#"):
                    continue
                try:
                    entries.append(ExclusionEntry.from_dict(json.loads(line)))
                except (json.JSONDecodeError, KeyError, TypeError, ValueError):
                    log.warning(
                        "exclusion-registry: skipping malformed line: %s", line[:120]
                    )
        return cls(entries)

    @classmethod
    def load_default(cls) -> "ExclusionRegistry":
        """Load the tracked default registry (:func:`default_registry_path`)."""
        return cls.load(default_registry_path())

    # ── lookup ───────────────────────────────────────────────────────────────
    @property
    def entries(self) -> list[ExclusionEntry]:
        return list(self._entries)

    def __len__(self) -> int:
        return len(self._entries)

    def __bool__(self) -> bool:
        return bool(self._entries)

    def entry_for_sha(self, raw_sha256: str | None) -> ExclusionEntry | None:
        if not raw_sha256:
            return None
        return self._by_sha.get(raw_sha256.lower())

    def entry_for_path(self, raw_path: str | None) -> ExclusionEntry | None:
        """The exclusion covering ``raw_path`` (exact, then literal-prefix), or None."""
        if not raw_path:
            return None
        exact = self._by_path.get(raw_path)
        if exact is not None:
            return exact
        # Literal-prefix fallback mirroring deindex's ``prefix`` purge: a purged folder
        # prefix excludes everything committed beneath it. Fail-safe over-matching.
        for entry in self._entries:
            if entry.raw_path and raw_path.startswith(entry.raw_path):
                return entry
        return None

    def match(
        self, *, raw_path: str | None = None, raw_sha256: str | None = None
    ) -> ExclusionEntry | None:
        """The registered exclusion matching either key (sha checked first), or None."""
        hit = self.entry_for_sha(raw_sha256)
        if hit is not None:
            return hit
        return self.entry_for_path(raw_path)

    def match_entry(self, entry: dict) -> ExclusionEntry | None:
        """Match a manifest/document dict (``raw_path`` + ``sha256``/``raw_sha256``)."""
        return self.match(
            raw_path=entry.get("raw_path"),
            raw_sha256=entry.get("sha256") or entry.get("raw_sha256"),
        )

    def excludes(
        self, *, raw_path: str | None = None, raw_sha256: str | None = None
    ) -> bool:
        return self.match(raw_path=raw_path, raw_sha256=raw_sha256) is not None

    def excludes_entry(self, entry: dict) -> bool:
        return self.match_entry(entry) is not None


# ── append (the only mutation — the file is append-only) ──────────────────────
def append_exclusion(path: Path | str, entry: ExclusionEntry) -> None:
    """Append one entry as a JSONL line, creating the file/dir if needed."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as fh:
        fh.write(entry.to_json() + "\n")


def record_exclusion(
    raw_path: str,
    *,
    reason: str,
    source: str,
    raw_sha256: str | None = None,
    timestamp: str | None = None,
    path: Path | str | None = None,
) -> ExclusionEntry:
    """Build and append an :class:`ExclusionEntry`; return it.

    ``path`` defaults to the tracked :func:`default_registry_path`. ``reason`` is
    required (an exclusion with no recorded reason is exactly the silent act this
    registry exists to prevent).
    """
    if not reason or not reason.strip():
        raise ValueError("an exclusion requires a non-empty reason")
    if source not in VALID_SOURCES:
        raise ValueError(f"source must be one of {VALID_SOURCES}, got {source!r}")
    entry = ExclusionEntry(
        raw_path=raw_path,
        reason=reason.strip(),
        timestamp=timestamp or now_iso(),
        source=source,
        raw_sha256=raw_sha256.lower() if raw_sha256 else None,
    )
    append_exclusion(path or default_registry_path(), entry)
    return entry
