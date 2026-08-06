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
