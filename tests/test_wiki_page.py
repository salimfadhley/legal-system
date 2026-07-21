"""Tests for the wiki page renderer (M11, ADR 0007)."""

from __future__ import annotations

import frontmatter

from goldberg_system.wiki.page import (
    EntityClaim,
    EntityPage,
    render_entity_page,
    slugify,
)


def test_slugify() -> None:
    assert slugify("Simon Goldberg") == "simon-goldberg"
    assert slugify("Empower the People (EtP)") == "empower-the-people-etp"


def _page() -> EntityPage:
    return EntityPage(
        name="Simon Goldberg",
        tags=["plaintiff"],
        created="2026-07-21",
        updated="2026-07-21",
        summary="Private prosecutor in the harassment case.",
        related=["Salim Fadhley", "Empower the People"],
        claims=[
            EntityClaim(
                subject="Empower the People",
                predicate="was conceptualised by",
                object="Simone Marshall",
                asserted_by="Simon John Goldberg",
                raw_path="evidence/deacon_v_goldberg/counterclaim.pdf",
                doc_id="gb_a82b",
            )
        ],
    )


def test_entity_page_path_and_slug() -> None:
    assert _page().path == "entities/simon-goldberg.md"


def test_render_entity_page_frontmatter_and_body() -> None:
    md = render_entity_page(_page())
    post = frontmatter.loads(md)
    # required frontmatter present
    assert post["title"] == "Simon Goldberg"
    assert post["type"] == "entity"
    assert post["tags"] == ["plaintiff"]
    # sources cite corpus raw_path (provenance, ADR 0007 §6)
    assert post["sources"] == ["evidence/deacon_v_goldberg/counterclaim.pdf"]
    # ≥2 outbound wikilinks (SCHEMA lint rule)
    assert md.count("[[") >= 2
    assert "[[salim-fadhley]]" in md
    # attributed claim rendered with speaker + source
    assert "asserted by Simon John Goldberg" in md
    assert "conceptualised by" in md


def test_render_handles_no_claims() -> None:
    page = EntityPage(
        name="Test Court",
        tags=[],
        created="2026-07-21",
        updated="2026-07-21",
        related=["Simon Goldberg", "Salim Fadhley"],
    )
    md = render_entity_page(page)
    assert "type: entity" in md
    assert md.count("[[") >= 2  # still meets the link minimum via related
