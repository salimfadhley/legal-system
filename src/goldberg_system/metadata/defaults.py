"""Light folder-defaults merge (ADR 0004) — the demoted replacement for the
heavyweight goldberg-meta inheritance engine.

Given folder-default layers (outermost/least-specific first) plus the document's
own metadata last, merge them with simple semantics: scalar fields take the last
(most specific) non-null value; list fields are an order-preserving union. There
is **no** locking, no conflict-raising, and no irreversibility — those are gone.
Intended for the few genuinely folder-uniform, human-set fields (the legal-handling
flags, and often ``matters``/``parties``).
"""

from __future__ import annotations

from typing import Any

from goldberg_system.metadata.schema import DocumentMetadata

_LIST_FIELDS = frozenset(
    {"parties", "keywords", "skip_patterns", "matters", "entities", "relates_to"}
)


def merge_folder_defaults(*layers: dict[str, Any]) -> DocumentMetadata:
    """Merge ``layers`` (outermost first, the document's own last)."""
    merged: dict[str, Any] = {}
    for layer in layers:
        for key, value in layer.items():
            if value is None:
                continue
            if key in _LIST_FIELDS and isinstance(value, list):
                existing = merged.get(key, [])
                merged[key] = existing + [v for v in value if v not in existing]
            else:
                merged[key] = value
    return DocumentMetadata(**merged)
