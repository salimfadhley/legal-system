"""The typed metadata schema for a Goldberg document.

This is the M1 port of the legacy ``goldberg-meta`` ``MetadataSchema`` plus the
additions agreed in ``doc/design.md`` (the two-axis taxonomy, ``matters`` as a
list, the ``author``/speaker dimension, provenance, the human-authored
legal-handling flags, and the Papra cross-store mapping from ADR 0003).

Population is **two-tier**: most fields are machine-derivable and optional, while
the legal-handling flags are human-authored and **default to the safe (most
protective) value** so nothing is treated as disclosable until a human clears it.
"""

from __future__ import annotations

from enum import Enum

from typing import Any

from pydantic import BaseModel, ConfigDict, model_validator


class Origin(str, Enum):
    """Where a document came from."""

    RECEIVED = "received"
    AUTHORED = "authored"


class Role(str, Enum):
    """What a document is *for* in the system."""

    INPUT = "input"  # indexed knowledge the system reasons over
    OUTPUT = "output"  # a deliverable we produce to share


class Sensitivity(str, Enum):
    """Handling sensitivity. ``SENSITIVE`` is the safe default."""

    SENSITIVE = "sensitive"
    NORMAL = "normal"


class DisclosureStatus(str, Enum):
    """CPIA disclosure status. ``UNKNOWN`` is the safe default."""

    SERVED = "served"
    UNUSED = "unused"
    UNDISCLOSED = "undisclosed"
    OWN = "own"
    UNKNOWN = "unknown"


class SourceChannel(str, Enum):
    """How the document was obtained. ``UNKNOWN`` is the safe default."""

    OFFICIAL_DISCLOSURE = "official_disclosure"
    OWN_RECORDS = "own_records"
    THIRD_PARTY = "third_party"
    UNOFFICIAL = "unofficial"
    UNKNOWN = "unknown"


class HandlingFlags(BaseModel):
    """Legal-handling flags — **human-authored**, defaulting to the safe value.

    The LLM must not invent these. Until a human sets them (``reviewed=True``),
    every flag holds its most-protective value so material is never treated as
    disclosable/non-privileged by default.
    """

    model_config = ConfigDict(extra="forbid")

    cpia_s17: bool = True
    privileged: bool = True
    sensitivity: Sensitivity = Sensitivity.SENSITIVE
    disclosure_status: DisclosureStatus = DisclosureStatus.UNKNOWN
    source_channel: SourceChannel = SourceChannel.UNKNOWN
    reviewed: bool = False

    @property
    def requires_caution(self) -> bool:
        """True unless a human has reviewed and cleared the protective flags."""
        if not self.reviewed:
            return True
        return (
            self.cpia_s17
            or self.privileged
            or self.sensitivity is Sensitivity.SENSITIVE
        )


class Claim(BaseModel):
    """An attributed assertion extracted from a document.

    Comparable across the corpus so contradictions (a party's account shifting
    over time) become queryable. Lives in the document frontmatter (ADR 0004).

    The fields below the triple are the "claim graph" additions (contradiction
    detection, thread ``20260806T073358Z_claim-graph-for-contradiction-detection``).
    Every one is optional with a safe default, so claims serialized before these
    fields existed still validate — additive, no migration, no reindex. They stay
    **unpopulated** on existing documents until a re-enrich (``goldberg reingest``)
    re-reads the source text.
    """

    model_config = ConfigDict(extra="forbid")

    subject: str
    predicate: str
    object: str
    asserted_by: str | None = None  # the speaker/author making the claim

    # --- claim-graph additions (all optional & additive) ---
    polarity: bool = True
    # ``False`` = the claim is negated ("was NOT ..."). Opposite polarity on the
    # same normalized (subject, predicate) is the deterministic contradiction signal.
    epistemic_status: str | None = None
    # "asserted" | "inferred" | "quoted" | "held" — how the assertion is held. An
    # ``inferred`` claim restated as fact in our own work product is the integrity
    # failure the detector exists to catch.
    source_span: str | None = None
    # the exact sentence/quote the claim was read from (grounding / auditability).
    claim_date: str | None = None
    # ISO date the assertion was *made* — distinct from the document's own date.
    confidence: float | None = None
    # the extractor's self-rated certainty (0..1), or None if not rated.
    derived_from: list[str] = []
    # doc_ids / claim refs this was inferred from — the retraction/propagation hook
    # (TMS-lite): mark a source retracted, follow ``derived_from`` to what it taints.


class DocumentMetadata(BaseModel):
    """The metadata carried in a document's YAML frontmatter (ADR 0004)."""

    model_config = ConfigDict(extra="forbid")

    # --- ported goldberg-meta fields ---
    document_type: str | None = None
    party_role: str | None = None
    parties: list[str] = []
    keywords: list[str] = []
    date: str | None = None
    topic: str | None = None
    summary: str | None = None
    long_summary: str | None = None
    skip: bool = False
    skip_patterns: list[str] = []
    files: dict[str, dict] = {}

    # --- additions (doc/design.md) ---
    author: str | None = None  # a.k.a. source_party — who is *speaking*

    # --- per-file sidecar prose + receipt-provenance (doc/system/metadata.md) ---
    notes: str | None = None
    # Free casework prose. Two jobs: (1) fed to the enricher as GROUND-TRUTH context
    # before extraction, so summary/claims/attribution reflect it; (2) appended to the
    # indexed content inside an immutable annotation fence (never the document's words).
    method: str | None = None
    # FREE TEXT — what was actually opened/checked and when (e.g. "opened
    # legislation.gov.uk XML for SI 2020/759, diffed"). Never an enum, never a boolean;
    # absence means unverified (there is deliberately NO ``verified`` flag anywhere).
    date_basis: str | None = None
    # Free text: how we know the document's date ("on its face" / "PDF CreationDate" /
    # "served in Oct 2025 bundle").
    date_uncertain: bool = False
    # True whenever ``date`` is set but ``date_basis`` is anything other than "on its
    # face"; a sidecar may set it explicitly. With no date it stays at its default.
    source_channel: str | None = None
    # Free text: how the document reached us (provenance of receipt — legal, not
    # housekeeping). Distinct from the enum ``handling.source_channel``.
    obtained_note: str | None = None  # free text expanding on how it was obtained
    superseded_by: str | None = None  # a doc_id / raw_path that replaces this document

    metadata_error: str | None = None
    # LOUD-but-present marker: a bad key / malformed sidecar / no_index-without-reason
    # dropped a metadata layer. The document still ingests with default/inherited
    # metadata; this records why so the failure is visible and never a silent skip.

    claim_source: str | None = None
    # Authoritative speaker/source for ALL claims in this document; when set (typically
    # via a folder metadata.yaml) it OVERRIDES each claim's LLM-inferred asserted_by.
    # Use only for single-source folders (our own analysis, one show's transcripts) —
    # never a mixed-party folder like an email thread.
    no_index: bool = False
    no_index_reason: str | None = None
    # Mark a legally/contractually RESTRICTED subtree that must never be indexed or
    # searchable (e.g. CPR 32.12 witness statements, or a party's own account that
    # must not surface as if it were evidence). Set on a folder ``metadata.yaml`` it
    # applies RECURSIVELY to everything below (a child may set ``no_index: false`` to
    # re-include a subfolder). ``no_index_reason`` records why. These are enforced at
    # BOTH ends: ingest discovery skips them quietly, and every sink refuses to write a
    # ``no_index`` document (it dead-letters loudly rather than leaking into the index).
    matters: list[str] = []
    primary_matter: str | None = None
    origin: Origin | None = None
    role: Role | None = None
    entities: list[str] = []
    claims: list[Claim] = []

    # provenance (first-class): the derived doc links back to raw path + commit
    raw_path: str | None = None  # path to the original raw file
    raw_commit: str | None = None
    raw_sha256: str | None = None  # sha256 of the raw file — the preservable
    # pipeline correlation ID (== Papra original_sha256_hash == manifest key, ADR 0008)
    ingested_at: str | None = None  # ISO 8601 UTC timestamp of ingestion/processing

    # Papra cross-store mapping (ADR 0003)
    papra_document_id: str | None = None

    relates_to: list[str] = []

    handling: HandlingFlags = HandlingFlags()

    @model_validator(mode="before")
    @classmethod
    def _default_date_uncertain(cls, data: Any) -> Any:
        """Compute ``date_uncertain`` when a date is present but the caller left it unset.

        Defaults True whenever ``date`` is set and ``date_basis`` is anything other than
        "on its face"; an explicit ``date_uncertain`` always wins; with no date it is left
        at its default. Only runs on dict input (fresh construction / frontmatter parse);
        ``model_copy(update=...)`` callers compute it themselves (folder_base_fields).
        """
        if not isinstance(data, dict) or "date_uncertain" in data:
            return data
        if data.get("date"):
            data = dict(data)
            data["date_uncertain"] = data.get("date_basis") != "on its face"
        return data

    @model_validator(mode="after")
    def _primary_matter_is_a_matter(self) -> DocumentMetadata:
        if self.primary_matter is not None and self.primary_matter not in self.matters:
            raise ValueError(
                f"primary_matter {self.primary_matter!r} must be one of "
                f"matters {self.matters!r}"
            )
        return self
