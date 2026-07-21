"""A concrete :class:`EnrichmentAdapter` backed by OpenAI.

Mirrors Mind of Steele's ``llm_support`` approach (cloud OpenAI is permitted per
ADR 0001 / the charter's logged data-boundary exception). Produces the attributed
enrichment — summary, long summary, keywords, entities, speaker/author,
classification, and comparable **claims** — that ``assemble_enriched_document``
folds into the document frontmatter.

The OpenAI client is injected so the enricher is unit-testable without a network
call; :meth:`from_settings` builds a live client from the secrets loader.
"""

from __future__ import annotations

import json
from typing import Any, Protocol

from goldberg_system.enrichment.adapter import EnrichmentRequest, EnrichmentResult
from goldberg_system.metadata.schema import Claim

_MAX_BODY_CHARS = 12000

_SYSTEM_PROMPT = (
    "You are a meticulous legal-document analyst helping a defendant prepare their "
    "case. You extract accurate, attributed structured metadata from documents. "
    "You never invent facts. Respond with a single valid JSON object only."
)

_INSTRUCTIONS = """\
Analyse the document below and return a JSON object with exactly these keys:
- "summary": one or two sentences.
- "long_summary": a short paragraph.
- "keywords": 5-12 salient terms (array of strings).
- "entities": people, organisations and references mentioned (array of strings).
- "author": who wrote or is speaking in this document, or null if unclear.
- "document_type": a short classification (e.g. "email", "court order", \
"witness statement", "legal research").
- "claims": an array of the document's key factual assertions, each an object \
{"subject", "predicate", "object", "asserted_by"} where "asserted_by" is the \
person/party making the claim (or null). Focus on legally-relevant assertions; \
attribute them to the speaker. Return [] if none.
Do not include any keys other than those listed."""


class _OpenAILike(Protocol):
    @property
    def chat(self) -> Any: ...


class OpenAIEnricher:
    """Enrich a document via an OpenAI chat-completions client."""

    def __init__(self, client: _OpenAILike, model: str = "gpt-4o-mini") -> None:
        self._client = client
        self.model = model

    @classmethod
    def from_settings(cls, model: str = "gpt-4o-mini") -> OpenAIEnricher:
        """Build a live enricher from the OpenAI secrets (env / secrets.toml)."""
        from openai import OpenAI

        from goldberg_system.secrets import load_openai_settings

        settings = load_openai_settings()
        client = OpenAI(
            api_key=settings.api_key,
            base_url=settings.base_url,
            organization=settings.organization,
        )
        return cls(client, model=model)

    def _build_messages(self, request: EnrichmentRequest) -> list[dict[str, str]]:
        md = request.metadata
        context_bits = []
        if md.matters:
            context_bits.append(f"matters: {', '.join(md.matters)}")
        if md.parties:
            context_bits.append(f"known parties: {', '.join(md.parties)}")
        context = (
            ("Known context — " + "; ".join(context_bits) + "\n\n")
            if context_bits
            else ""
        )
        body = request.markdown[:_MAX_BODY_CHARS]
        user = f"{_INSTRUCTIONS}\n\n{context}--- DOCUMENT ---\n{body}"
        return [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user},
        ]

    def enrich(self, request: EnrichmentRequest) -> EnrichmentResult:
        response = self._client.chat.completions.create(
            model=self.model,
            messages=self._build_messages(request),
            response_format={"type": "json_object"},
            temperature=0,
        )
        content = response.choices[0].message.content or "{}"
        return _parse_result(content)


def _parse_result(content: str) -> EnrichmentResult:
    data = json.loads(content)
    return EnrichmentResult(
        summary=str(data.get("summary", "")),
        long_summary=data.get("long_summary"),
        keywords=[str(k) for k in data.get("keywords", []) if k],
        entities=[str(e) for e in data.get("entities", []) if e],
        author=data.get("author") or None,
        document_type=data.get("document_type") or None,
        claims=[c for c in (_claim(x) for x in data.get("claims", [])) if c],
    )


def _claim(raw: Any) -> Claim | None:
    if not isinstance(raw, dict):
        return None
    subject = str(raw.get("subject", "")).strip()
    predicate = str(raw.get("predicate", "")).strip()
    obj = str(raw.get("object", "")).strip()
    if not (subject and predicate and obj):
        return None
    asserted_by = raw.get("asserted_by")
    return Claim(
        subject=subject,
        predicate=predicate,
        object=obj,
        asserted_by=str(asserted_by) if asserted_by else None,
    )
