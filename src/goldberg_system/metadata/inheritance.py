"""Directory-inheritance resolution for ``metadata.yaml`` files.

Ports the legacy ``goldberg-meta`` semantics. Given the ordered list of raw
mappings from a document's ``metadata.yaml`` chain (repository root first, the
document's own leaf last), :func:`resolve_metadata` merges them into a single
:class:`~goldberg_system.metadata.schema.DocumentMetadata`.

Per-field rules:

- **LOCKED** scalars (the default for scalar fields): may be set once in the
  chain; two different non-null values raise :class:`InheritanceConflict`.
- **OVERRIDABLE** scalars: a child silently replaces a parent's value.
- **NON_INHERITED**: only the leaf level's own value is used; parents do not
  propagate.
- **IRREVERSIBLE**: once set truthy anywhere in the chain it stays truthy; a
  later attempt to unset it is ignored.
- **UNION** (the default for list fields): values are merged as an
  order-preserving de-duplicated union.

Fields not explicitly classified follow the type default (scalar -> LOCKED,
list -> UNION, mapping -> NON_INHERITED).
"""

from __future__ import annotations

from typing import Any

from goldberg_system.metadata.schema import DocumentMetadata

OVERRIDABLE: frozenset[str] = frozenset({"date", "topic"})
NON_INHERITED: frozenset[str] = frozenset({"summary", "files"})
IRREVERSIBLE: frozenset[str] = frozenset({"skip"})


class InheritanceConflict(ValueError):
    """Raised when a LOCKED field is given conflicting values in the chain."""


def _model_fields() -> dict[str, Any]:
    return DocumentMetadata.model_fields


def _is_list_field(name: str) -> bool:
    # The list-typed fields on the schema, merged by union.
    return name in {
        "parties",
        "keywords",
        "skip_patterns",
        "matters",
        "entities",
        "relates_to",
    }


def _is_mapping_field(name: str) -> bool:
    return name == "files"


def _union(values: list[list[Any]]) -> list[Any]:
    merged: list[Any] = []
    for value in values:
        for item in value:
            if item not in merged:
                merged.append(item)
    return merged


def resolve_metadata(layers: list[dict[str, Any]]) -> DocumentMetadata:
    """Merge an ordered chain of ``metadata.yaml`` mappings (root -> leaf).

    Args:
        layers: raw mappings, outermost (repository root) first, the document's
            own leaf last. Unknown keys are rejected by the schema.

    Raises:
        InheritanceConflict: if a LOCKED scalar is given two different values.
    """
    if not layers:
        return DocumentMetadata()

    resolved: dict[str, Any] = {}
    known = set(_model_fields())

    keys: list[str] = []
    for layer in layers:
        for key in layer:
            if key in known and key not in keys:
                keys.append(key)

    for key in keys:
        present = [(i, layer[key]) for i, layer in enumerate(layers) if key in layer]
        if not present:
            continue

        if _is_list_field(key):
            resolved[key] = _union([value for _, value in present])
        elif _is_mapping_field(key) or key in NON_INHERITED:
            # NON_INHERITED: only the leaf level's own value is used; parents do
            # not propagate. If the leaf does not set it, it stays unset.
            leaf = layers[-1]
            if key in leaf:
                resolved[key] = leaf[key]
        elif key in IRREVERSIBLE:
            resolved[key] = any(bool(value) for _, value in present)
        elif key in OVERRIDABLE:
            resolved[key] = present[-1][1]
        else:
            # LOCKED (default for scalars): all non-null values must agree.
            distinct = {_hashable(value) for _, value in present if value is not None}
            if len(distinct) > 1:
                raise InheritanceConflict(
                    f"LOCKED field {key!r} has conflicting values: "
                    f"{[value for _, value in present]!r}"
                )
            resolved[key] = present[-1][1]

    return DocumentMetadata(**resolved)


def _hashable(value: Any) -> Any:
    """Best-effort hashable key for conflict comparison of scalar values."""
    if isinstance(value, (list, dict)):
        return repr(value)
    return value
