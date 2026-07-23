"""Token-safety tests for :class:`OpenAIEnricher` (WP01, FR-008).

The enricher must never send a body that blows the model's context window, and
must recover from a ``context_length_exceeded`` 400 by shrinking and retrying —
so no arbitrarily large OCR document can hard-fail enrichment.

All tests use a fake OpenAI-like client (records the messages it is sent, and can
be told to raise a context-length 400 for the first N calls), so there is no
network dependency.
"""

from __future__ import annotations

import json
from typing import Any

from openai import BadRequestError

from goldberg_system.enrichment import (
    EnrichmentRequest,
    EnrichmentResult,
    OpenAIEnricher,
)
from goldberg_system.enrichment.openai_enricher import (
    _BODY_TOKEN_BUDGET,
    _encoding_for_model,
)
from goldberg_system.metadata import DocumentMetadata

_MODEL = "gpt-4o-mini"

_VALID_PAYLOAD = json.dumps(
    {
        "summary": "A summary.",
        "long_summary": "A longer paragraph.",
        "keywords": ["alpha", "beta"],
        "entities": ["CPS"],
        "author": "Someone",
        "document_type": "email",
        "claims": [],
    }
)


def _context_length_error() -> BadRequestError:
    """Build a ``context_length_exceeded`` 400 the way OpenAI raises it."""
    body = {
        "error": {
            "message": (
                "This model's maximum context length is 128000 tokens. However "
                "your messages resulted in 200000 tokens."
            ),
            "type": "invalid_request_error",
            "code": "context_length_exceeded",
        }
    }
    response = type("Resp", (), {"status_code": 400, "headers": {}, "request": None})()
    return BadRequestError(
        message=body["error"]["message"],
        response=response,  # type: ignore[arg-type]
        body=body["error"],
    )


class _RecordingCompletions:
    """Records every ``create`` call; optionally raises a context-length 400.

    ``fail_first`` controls how many initial calls raise the context-length error
    before a success is returned. The recorded ``calls`` list holds the ``messages``
    kwarg of every call (including the ones that raised), so tests can assert on
    exactly what was *sent*.
    """

    def __init__(self, content: str, fail_first: int = 0) -> None:
        self._content = content
        self._fail_first = fail_first
        self.calls: list[list[dict[str, str]]] = []

    def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs["messages"])
        if len(self.calls) <= self._fail_first:
            raise _context_length_error()
        message = type("M", (), {"content": self._content})()
        choice = type("C", (), {"message": message})()
        return type("R", (), {"choices": [choice]})()


class _FakeClient:
    def __init__(self, content: str, fail_first: int = 0) -> None:
        self.completions = _RecordingCompletions(content, fail_first=fail_first)
        self.chat = type("Chat", (), {"completions": self.completions})()


def _request(markdown: str) -> EnrichmentRequest:
    return EnrichmentRequest(
        doc_id="d",
        markdown=markdown,
        metadata=DocumentMetadata(matters=["422500059892"], parties=["CPS"]),
    )


def _user_body_tokens(messages: list[dict[str, str]]) -> int:
    """Token count of the user message as the enricher itself would encode it."""
    enc = _encoding_for_model(_MODEL)
    return len(enc.encode(messages[1]["content"]))


def test_a_oversized_body_is_truncated_to_token_budget() -> None:
    # A document far larger than the budget: ~4x the budget in tokens. Using a word
    # that is comfortably >=1 token keeps this well over budget without a huge string.
    huge = "prosecution discontinuance " * (_BODY_TOKEN_BUDGET)
    client = _FakeClient(_VALID_PAYLOAD)
    enricher = OpenAIEnricher(client, model=_MODEL)  # type: ignore[arg-type]

    result = enricher.enrich(_request(huge))

    assert isinstance(result, EnrichmentResult)
    assert len(client.completions.calls) == 1  # fit first time, no retry
    # The outgoing user message (instructions + context + body) stays within budget.
    assert _user_body_tokens(client.completions.calls[0]) <= _BODY_TOKEN_BUDGET


def test_b_context_length_400_then_success_via_shrink_and_retry() -> None:
    # First call raises context_length_exceeded, second succeeds. enrich() must
    # shrink the body and retry, returning a normal result — no error propagated.
    huge = "prosecution discontinuance " * (_BODY_TOKEN_BUDGET)
    client = _FakeClient(_VALID_PAYLOAD, fail_first=1)
    enricher = OpenAIEnricher(client, model=_MODEL)  # type: ignore[arg-type]

    result = enricher.enrich(_request(huge))

    assert isinstance(result, EnrichmentResult)
    assert result.summary == "A summary."
    assert len(client.completions.calls) == 2  # one failure, one success
    # The retry sent strictly fewer body tokens than the first attempt.
    first = _user_body_tokens(client.completions.calls[0])
    second = _user_body_tokens(client.completions.calls[1])
    assert second < first


def test_c_small_document_unchanged_single_call() -> None:
    small = "Dear Sir, please discontinue the prosecution."
    client = _FakeClient(_VALID_PAYLOAD)
    enricher = OpenAIEnricher(client, model=_MODEL)  # type: ignore[arg-type]

    result = enricher.enrich(_request(small))

    assert isinstance(result, EnrichmentResult)
    assert len(client.completions.calls) == 1
    # The whole small document survived into the prompt untouched.
    assert small in client.completions.calls[0][1]["content"]
    assert _user_body_tokens(client.completions.calls[0]) <= _BODY_TOKEN_BUDGET
