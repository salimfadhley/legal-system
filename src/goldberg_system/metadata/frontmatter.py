"""Serialise/parse a document as markdown + YAML frontmatter (ADR 0004).

Each extracted document is one markdown file: a YAML frontmatter prelude carrying
the :class:`DocumentMetadata`, and the body carrying the extracted text. Uses
``python-frontmatter`` (a dependency). Only non-default fields are written, so the
frontmatter stays clean; absent fields parse back to their (safe) defaults.
"""

from __future__ import annotations

import frontmatter

from goldberg_system.metadata.schema import DocumentMetadata


def to_frontmatter_document(metadata: DocumentMetadata, body: str) -> str:
    """Render ``metadata`` + ``body`` as a markdown-with-frontmatter document."""
    data = metadata.model_dump(mode="json", exclude_defaults=True)
    post = frontmatter.Post(body, **data)
    return frontmatter.dumps(post) + "\n"


def parse_frontmatter_document(text: str) -> tuple[DocumentMetadata, str]:
    """Parse a markdown-with-frontmatter document into metadata + body."""
    post = frontmatter.loads(text)
    metadata = DocumentMetadata.model_validate(post.metadata)
    return metadata, post.content
