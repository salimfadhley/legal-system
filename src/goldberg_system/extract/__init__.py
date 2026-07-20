"""Extraction gap-fillers — the formats Papra does not extract for us.

Per ADR 0003, Papra (backed by Docling) handles PDF/scan/docx/image extraction.
This package covers what Papra does not: ``.eml`` emails (and their attachment
manifest) and trivial passthrough of already-markdown/plaintext inputs.
"""

from goldberg_system.extract.eml import (
    AttachmentRef,
    ExtractedEmail,
    eml_to_markdown,
)
from goldberg_system.extract.passthrough import passthrough_markdown

__all__ = [
    "eml_to_markdown",
    "ExtractedEmail",
    "AttachmentRef",
    "passthrough_markdown",
]
