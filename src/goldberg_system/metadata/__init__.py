"""Document metadata: the typed schema and directory-inheritance resolver."""

from goldberg_system.metadata.schema import (
    DisclosureStatus,
    DocumentMetadata,
    HandlingFlags,
    Origin,
    Role,
    Sensitivity,
    SourceChannel,
)
from goldberg_system.metadata.inheritance import (
    InheritanceConflict,
    resolve_metadata,
)

__all__ = [
    "DocumentMetadata",
    "HandlingFlags",
    "Origin",
    "Role",
    "Sensitivity",
    "SourceChannel",
    "DisclosureStatus",
    "resolve_metadata",
    "InheritanceConflict",
]
