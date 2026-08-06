"""Tests for the OpenAI-backed enricher (via a fake client)."""

from __future__ import annotations

import json
from typing import Any

from goldberg_system.enrichment import (
    EnrichmentAdapter,
    EnrichmentRequest,
    OpenAIEnricher,
)
from goldberg_system.metadata import DocumentMetadata


class _FakeCompletions:
    def __init__(self, content: str) -> None:
        self._content = content
        self.last_kwargs: dict[str, Any] | None = None

    def create(self, **kwargs: Any) -> Any:
        self.last_kwargs = kwargs
        message = type("M", (), {"content": self._content})()
        choice = type("C", (), {"message": message})()
        return type("R", (), {"choices": [choice]})()


class _FakeClient:
    def __init__(self, content: str) -> None:
        self.chat = type("Chat", (), {"completions": _FakeCompletions(content)})()


def _request() -> EnrichmentRequest:
    return EnrichmentRequest(
        doc_id="d",
        markdown="Dear Sir, please discontinue the prosecution.",
        metadata=DocumentMetadata(matters=["422500059892"], parties=["CPS"]),
    )


def test_satisfies_adapter_protocol() -> None:
    assert isinstance(OpenAIEnricher(_FakeClient("{}")), EnrichmentAdapter)


def test_enrich_parses_structured_json_and_drops_malformed_claims() -> None:
    payload = json.dumps(
        {
            "summary": "CPS asked to discontinue.",
            "long_summary": "A longer paragraph of context.",
            "keywords": ["cps", "discontinuance"],
            "entities": ["CPS", "Goldberg"],
            "author": "Asif Akram",
            "document_type": "email",
            "claims": [
                {
                    "subject": "prosecuting_entity",
                    "predicate": "is",
                    "object": "Empower the People",
                    "asserted_by": "Goldberg",
                },
                {"subject": "", "predicate": "", "object": ""},  # malformed → dropped
            ],
        }
    )
    enricher = OpenAIEnricher(_FakeClient(payload), model="gpt-4o-mini")
    result = enricher.enrich(_request())

    assert result.summary.startswith("CPS")
    assert result.long_summary
    assert "cps" in result.keywords
    assert result.author == "Asif Akram"
    assert result.document_type == "email"
    assert len(result.claims) == 1
    assert result.claims[0].object == "Empower the People"

    kwargs = enricher._client.chat.completions.last_kwargs  # type: ignore[attr-defined]
    assert kwargs is not None
    assert kwargs["model"] == "gpt-4o-mini"
    assert kwargs["response_format"] == {"type": "json_object"}
    # known context (matters/parties) is passed into the prompt
    assert "422500059892" in kwargs["messages"][1]["content"]


def test_enrich_parses_claim_graph_fields() -> None:
    payload = json.dumps(
        {
            "summary": "s",
            "claims": [
                {
                    "subject": "s.5 PfHA",
                    "predicate": "was",
                    "object": "in force",
                    "asserted_by": "Goldberg",
                    "polarity": False,
                    "source_span": "s.5 was not in force at the material time.",
                    "epistemic_status": "inferred",
                },
                {
                    # missing/invalid claim-graph fields → safe defaults, not a failure
                    "subject": "prosecutor",
                    "predicate": "is",
                    "object": "EtP",
                    "epistemic_status": "wobble",  # invalid → None
                },
            ],
        }
    )
    result = OpenAIEnricher(_FakeClient(payload)).enrich(_request())
    assert len(result.claims) == 2
    negated = result.claims[0]
    assert negated.polarity is False
    assert negated.source_span.startswith("s.5 was not")
    assert negated.epistemic_status == "inferred"
    defaulted = result.claims[1]
    assert defaulted.polarity is True  # defaults to asserted
    assert defaulted.source_span is None
    assert defaulted.epistemic_status is None  # invalid value dropped


def test_enricher_instructions_request_the_claim_graph_fields() -> None:
    # the extraction prompt must actually ask for the new per-claim fields
    enricher = OpenAIEnricher(_FakeClient("{}"))
    enricher.enrich(_request())
    prompt = enricher._client.chat.completions.last_kwargs["messages"][1]["content"]  # type: ignore[attr-defined]
    assert "polarity" in prompt
    assert "source_span" in prompt
    assert "epistemic_status" in prompt


def test_empty_json_gives_empty_result() -> None:
    result = OpenAIEnricher(_FakeClient("{}")).enrich(_request())
    assert result.summary == ""
    assert result.claims == []


class _FakeCompletionsWithReason:
    """A fake that lets a test set ``finish_reason`` and count calls."""

    def __init__(self, content: str, finish_reason: str | None = None) -> None:
        self._content = content
        self._finish_reason = finish_reason
        self.calls = 0

    def create(self, **kwargs: Any) -> Any:
        self.calls += 1
        message = type("M", (), {"content": self._content})()
        choice = type(
            "C", (), {"message": message, "finish_reason": self._finish_reason}
        )()
        return type("R", (), {"choices": [choice]})()


class _FakeClientWithReason:
    def __init__(self, content: str, finish_reason: str | None = None) -> None:
        self.chat = type(
            "Chat",
            (),
            {"completions": _FakeCompletionsWithReason(content, finish_reason)},
        )()


def test_truncated_json_does_not_crash_returns_empty() -> None:
    # A truncated/invalid completion must degrade to an empty result, not raise.
    truncated = '{"summary": "half a resu'
    result = OpenAIEnricher(_FakeClient(truncated)).enrich(_request())
    assert result.summary == ""
    assert result.claims == []


def test_finish_reason_length_is_soft_failure_and_retries() -> None:
    # finish_reason=length means the model ran out of output tokens; the enricher must
    # NOT parse the truncated JSON — it retries (shrinking budget) and, if it never
    # completes, returns a partial/empty result rather than crashing the batch.
    client = _FakeClientWithReason('{"summary": "truncat', finish_reason="length")
    result = OpenAIEnricher(client).enrich(_request())
    assert result.summary == ""  # truncated JSON was not emitted as a real result
    # retried the full bounded budget rather than accepting the first truncated body
    assert client.chat.completions.calls > 1  # type: ignore[attr-defined]


def test_non_string_triple_is_coerced_not_a_pydantic_error() -> None:
    # The model sometimes returns a number/bool for a triple field; that previously
    # raised pydantic string_type and failed the whole doc. It must coerce instead.
    payload = json.dumps(
        {
            "summary": "s",
            "claims": [
                {"subject": 2025, "predicate": True, "object": "in force"},
            ],
        }
    )
    result = OpenAIEnricher(_FakeClient(payload)).enrich(_request())
    assert len(result.claims) == 1
    assert result.claims[0].subject == "2025"
    assert result.claims[0].predicate == "True"


def test_claim_date_confidence_derived_from_parsed_defensively() -> None:
    payload = json.dumps(
        {
            "summary": "s",
            "claims": [
                {
                    "subject": "s.5 PfHA",
                    "predicate": "was",
                    "object": "in force",
                    "claim_date": "2024-01-02",
                    "confidence": 0.9,
                    "derived_from": ["gb_1", "gb_2"],
                },
                {
                    "subject": "x",
                    "predicate": "is",
                    "object": "y",
                    "confidence": "not-a-number",  # malformed → ignored
                    "derived_from": "gb_3",  # not a list → ignored
                    "claim_date": 12345,  # not a string → ignored
                },
            ],
        }
    )
    result = OpenAIEnricher(_FakeClient(payload)).enrich(_request())
    first, second = result.claims
    assert first.claim_date == "2024-01-02"
    assert first.confidence == 0.9
    assert first.derived_from == ["gb_1", "gb_2"]
    # malformed values are ignored, not fatal
    assert second.confidence is None
    assert second.derived_from == []
    assert second.claim_date is None
