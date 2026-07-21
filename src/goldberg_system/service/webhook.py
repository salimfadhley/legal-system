"""Parse Papra webhook payloads.

Papra sends Standard Webhooks; the ``document:created`` event carries the document
object. We only need the event type and the document id (we fetch the full content
via the API for reliability). Signature verification is optional (LAN-internal);
see ADR 0005.
"""

from __future__ import annotations

import json
from typing import Any


def parse_papra_event(body: bytes) -> tuple[str | None, str | None]:
    """Return ``(event_type, document_id)`` from a Papra webhook body."""
    data: Any = json.loads(body)
    if not isinstance(data, dict):
        return None, None
    event = data.get("type") or data.get("event")
    payload = data.get("data")
    if not isinstance(payload, dict):
        payload = data
    doc_id = (
        payload.get("id")
        or payload.get("documentId")
        or payload.get("document_id")
        or data.get("documentId")
    )
    return event, doc_id
