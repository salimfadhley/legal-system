"""The Papra adapter — ingest into, and read extracted content from, Papra.

Per ADR 0003, Papra is the ingest + OCR/extraction front-end. Ingestion uses the
watched drop folder (the credential-free path MoS already uses). Reading extracted
content uses Papra's REST API (which needs an API key).
"""

from goldberg_system.papra.ingest import IngestFolder
from goldberg_system.papra.client import (
    HttpResponse,
    HttpTransport,
    PapraClient,
    PapraDocument,
)

__all__ = [
    "IngestFolder",
    "PapraClient",
    "PapraDocument",
    "HttpTransport",
    "HttpResponse",
]
