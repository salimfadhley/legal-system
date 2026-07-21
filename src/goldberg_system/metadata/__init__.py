"""Document metadata: the frontmatter schema, folder-defaults, and serialization.

Per ADR 0004 the primary representation of an extracted document is a markdown
file with a YAML frontmatter header (see :mod:`goldberg_system.metadata.frontmatter`).
The legacy directory-inheritance resolver (:mod:`goldberg_system.metadata.inheritance`)
is retained but demoted; :func:`merge_folder_defaults` is the light replacement.
"""

from goldberg_system.metadata.schema import (
    Claim,
    DisclosureStatus,
    DocumentMetadata,
    HandlingFlags,
    Origin,
    Role,
    Sensitivity,
    SourceChannel,
)
from goldberg_system.metadata.defaults import merge_folder_defaults
from goldberg_system.metadata.frontmatter import (
    parse_frontmatter_document,
    to_frontmatter_document,
)
from goldberg_system.metadata.inheritance import (
    InheritanceConflict,
    resolve_metadata,
)

__all__ = [
    "DocumentMetadata",
    "Claim",
    "HandlingFlags",
    "Origin",
    "Role",
    "Sensitivity",
    "SourceChannel",
    "DisclosureStatus",
    "merge_folder_defaults",
    "to_frontmatter_document",
    "parse_frontmatter_document",
    "resolve_metadata",
    "InheritanceConflict",
]
