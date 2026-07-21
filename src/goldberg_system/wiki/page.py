"""Render SilverBullet wiki pages from enriched-corpus data (pure functions).

These build the *content* of a Layer-2 page (frontmatter + body) following the
``the_goldberg_files`` SCHEMA conventions: lowercase-hyphen slug, required
frontmatter, ≥2 outbound ``[[wikilinks]]``, and ``sources`` citing the corpus
(``raw_path``) so every assertion traces back to evidence (ADR 0007 §6).

This is the deterministic rendering half of the M11 author sink; the LLM-authored
prose + SCHEMA-tag validation is layered on top in the sink itself.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


def slugify(name: str) -> str:
    """`Simon Goldberg` -> `simon-goldberg` (lowercase-hyphen, SCHEMA filename rule)."""
    s = re.sub(r"[^\w\s-]", "", name.lower()).strip()
    return re.sub(r"[\s_]+", "-", s)


@dataclass
class EntityClaim:
    """One attributed claim about (or by) the entity, with its source."""

    subject: str
    predicate: str
    object: str
    asserted_by: str | None
    raw_path: str | None
    doc_id: str | None


@dataclass
class EntityPage:
    name: str
    tags: list[str] = field(default_factory=list)
    created: str = ""  # ISO date; passed in (Date.now is unavailable/undesired here)
    updated: str = ""
    claims: list[EntityClaim] = field(default_factory=list)
    related: list[str] = field(default_factory=list)  # entity names → wikilinks
    summary: str | None = None

    @property
    def slug(self) -> str:
        return slugify(self.name)

    @property
    def path(self) -> str:
        return f"entities/{self.slug}.md"


def _yaml_list(items: list[str]) -> str:
    return "[" + ", ".join(items) + "]"


def render_entity_page(page: EntityPage) -> str:
    """Render an `entities/<slug>.md` page from enriched-corpus signal."""
    sources: list[str] = []
    for c in page.claims:
        if c.raw_path and c.raw_path not in sources:
            sources.append(c.raw_path)

    fm = [
        "---",
        f"title: {page.name}",
        f"created: {page.created}",
        f"updated: {page.updated}",
        "type: entity",
        f"tags: {_yaml_list(page.tags)}",
        "sources:",
        *[f"  - {s}" for s in sources],
        "---",
        "",
    ]

    body = [f"# {page.name}", ""]
    if page.summary:
        body += [page.summary, ""]

    # ≥2 outbound wikilinks (SCHEMA lint rule) via related entities
    links = [slugify(r) for r in page.related]
    if links:
        body += ["## Related", "", *[f"- [[{link}]]" for link in links], ""]

    if page.claims:
        body += ["## Attributed claims", ""]
        for c in page.claims:
            src = f" — `{c.raw_path}`" if c.raw_path else ""
            by = f" *(asserted by {c.asserted_by})*" if c.asserted_by else ""
            body.append(f"- **{c.subject}** {c.predicate} **{c.object}**{by}{src}")
        body.append("")

    return "\n".join(fm + body)
