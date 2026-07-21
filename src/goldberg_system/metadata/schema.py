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
    """

    model_config = ConfigDict(extra="forbid")

    subject: str
    predicate: str
    object: str
    asserted_by: str | None = None  # the speaker/author making the claim


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
    matters: list[str] = []
    primary_matter: str | None = None
    origin: Origin | None = None
    role: Role | None = None
    entities: list[str] = []
    claims: list[Claim] = []

    # provenance (first-class): the derived doc links back to raw path + commit
    raw_path: str | None = None  # path to the original raw file
    raw_commit: str | None = None
    ingested_at: str | None = None  # ISO 8601 UTC timestamp of ingestion/processing

    # Papra cross-store mapping (ADR 0003)
    papra_document_id: str | None = None

    relates_to: list[str] = []

    handling: HandlingFlags = HandlingFlags()

    @model_validator(mode="after")
    def _primary_matter_is_a_matter(self) -> DocumentMetadata:
        if self.primary_matter is not None and self.primary_matter not in self.matters:
            raise ValueError(
                f"primary_matter {self.primary_matter!r} must be one of "
                f"matters {self.matters!r}"
            )
        return self
