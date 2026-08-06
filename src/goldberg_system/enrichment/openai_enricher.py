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

import tiktoken
from openai import BadRequestError

from goldberg_system.enrichment.adapter import EnrichmentRequest, EnrichmentResult
from goldberg_system.metadata.schema import Claim

# Full-context enrichment (ADR 0006): pass the whole document, not just its head, so
# claims/summary reflect the entire text. WP01/FR-008: the previous *character* cap
# (200k chars) was unsafe — token-dense OCR text runs ~1.5 chars/token, so 200k chars
# is ~128.6k tokens and hard-fails a 128k-context model with `context_length_exceeded`.
# Instead we budget by *tokens* (tiktoken) and defensively shrink-and-retry if the
# model still rejects the request (encoding mismatch backstop).
#
# Body token budget: ~100k tokens leaves real headroom under the 128k context window
# for the system prompt, instructions, and the completion. The whole outgoing user
# message (instructions + context + body) is kept within this budget.
_BODY_TOKEN_BUDGET = 100_000

# Bounded shrink-and-retry: halve the budget on each context-length rejection. Three
# attempts take 100k -> 50k -> 25k; if it still fails the doc is genuinely impossible
# and the caller (processor) should DLQ it — which is the correct outcome.
_MAX_CONTEXT_ATTEMPTS = 3

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
- "claims": an array of the document's factual assertions. Extract every distinct \
assertion, and DECOMPOSE compound statements into ATOMIC claims — one \
subject/predicate/object per claim; never bundle several facts into one. Prefer many \
precise atomic claims over a few broad ones. Each is an object {"subject", \
"predicate", "object", "asserted_by", "polarity", "source_span", "epistemic_status"} \
where "asserted_by" is the person/party making the claim (or null); "polarity" is \
true unless the sentence negates the claim (e.g. "was NOT", "denied", "never"), in \
which case false; "source_span" is the exact sentence the claim was read from; and \
"epistemic_status" is "asserted" if the document states it directly, "inferred" if \
you inferred it rather than reading it stated, or "quoted" if the document is quoting \
another source. Prioritise legally-relevant and potentially-contested assertions, but \
do not omit a material fact for seeming minor; attribute each to its speaker. Return \
[] if none.
Do not include any keys other than those listed."""


def _encoding_for_model(model: str) -> tiktoken.Encoding:
    """Return the tiktoken encoding for ``model``, falling back to cl100k_base.

    Offline caveat (research R5): tiktoken lazily *downloads* an encoding's BPE
    ranks on first use. On an air-gapped host (Halob) pin ``TIKTOKEN_CACHE_DIR`` to
    a pre-populated cache so no network call is attempted. ``encoding_for_model``
    raises ``KeyError`` for an unrecognised model name; in that case we fall back to
    ``cl100k_base``, the encoding for the gpt-4o / gpt-4 family this pipeline uses.
    """
    try:
        return tiktoken.encoding_for_model(model)
    except KeyError:
        return tiktoken.get_encoding("cl100k_base")


def _truncate_to_tokens(text: str, budget: int, model: str) -> str:
    """Truncate ``text`` to at most ``budget`` tokens (keeping the *leading* text).

    The leading text is the most salient (headers, parties, opening claims), so we
    keep the head and drop the tail. Returns ``text`` unchanged when it already fits.
    """
    if budget <= 0:
        return ""
    enc = _encoding_for_model(model)
    tokens = enc.encode(text)
    if len(tokens) <= budget:
        return text
    return enc.decode(tokens[:budget])


def _is_context_length_error(exc: BadRequestError) -> bool:
    """True only for a ``context_length_exceeded`` 400 (narrow, not a blanket catch)."""
    if getattr(exc, "code", None) == "context_length_exceeded":
        return True
    message = str(getattr(exc, "message", "") or exc)
    return "context_length_exceeded" in message or "maximum context length" in message


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

    def _build_messages(
        self, request: EnrichmentRequest, token_budget: int = _BODY_TOKEN_BUDGET
    ) -> list[dict[str, str]]:
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
        # Token-budget the body so the *whole* user message (fixed prefix + body)
        # stays within ``token_budget``. Give the body the budget minus the prefix's
        # token cost, then clamp the assembled message to be safe against token-
        # boundary drift when prefix + body are re-encoded together.
        prefix = f"{_INSTRUCTIONS}\n\n{context}--- DOCUMENT ---\n"
        prefix_tokens = len(_encoding_for_model(self.model).encode(prefix))
        body = _truncate_to_tokens(
            request.markdown, max(token_budget - prefix_tokens, 0), self.model
        )
        user = _truncate_to_tokens(prefix + body, token_budget, self.model)
        return [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user},
        ]

    def enrich(self, request: EnrichmentRequest) -> EnrichmentResult:
        # Defensive shrink-and-retry (WP01/FR-008): tiktoken's budget should keep us
        # under the model's context window, but the encoding can mismatch the model's
        # real tokeniser. If the API still rejects the request with
        # `context_length_exceeded`, halve the budget and retry (bounded). Any other
        # BadRequestError propagates unchanged.
        token_budget = _BODY_TOKEN_BUDGET
        last_error: BadRequestError | None = None
        for _ in range(_MAX_CONTEXT_ATTEMPTS):
            try:
                response = self._client.chat.completions.create(
                    model=self.model,
                    messages=self._build_messages(request, token_budget),
                    response_format={"type": "json_object"},
                    temperature=0,
                )
            except BadRequestError as exc:
                if not _is_context_length_error(exc):
                    raise
                last_error = exc
                token_budget = max(token_budget // 2, 1)
                continue
            content = response.choices[0].message.content or "{}"
            return _parse_result(content)
        # Exhausted attempts on a truly impossible document: re-raise so the
        # processor DLQs it (the correct outcome for an un-enrichable doc).
        assert last_error is not None  # loop only exits here after a caught error
        raise last_error


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
        polarity=_polarity(raw.get("polarity")),
        source_span=_opt_str(raw.get("source_span")),
        epistemic_status=_epistemic_status(raw.get("epistemic_status")),
    )


# The model returns these best-effort; parse them defensively so a missing or
# malformed value never fails extraction (polarity defaults to True — asserted).
_VALID_EPISTEMIC = {"asserted", "inferred", "quoted", "held"}


def _polarity(value: Any) -> bool:
    """True unless the model explicitly says the claim is negated (defaults True)."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() not in {"false", "no", "negated", "0"}
    return True


def _opt_str(value: Any) -> str | None:
    """A trimmed non-empty string, else None."""
    if not isinstance(value, str):
        return None
    trimmed = value.strip()
    return trimmed or None


def _epistemic_status(value: Any) -> str | None:
    """One of the known epistemic statuses, else None (invalid/absent are dropped)."""
    if isinstance(value, str) and value.strip().lower() in _VALID_EPISTEMIC:
        return value.strip().lower()
    return None
