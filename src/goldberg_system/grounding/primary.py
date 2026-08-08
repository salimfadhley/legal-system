"""Load the held ground truth (``authorities_primary_text/``) and index it by SUBJECT.

The load-bearing rule, and a guard casework named explicitly: a primary-text file grounds an
authority only when that authority is its **subject**, not merely **mentioned** somewhere in
its body. A judgment routinely cites a dozen other cases; those citations do not make the
file the held source for them. So the subject keys are drawn ONLY from a file's *identity
zone*:

* the frontmatter ``citation`` field (and other citation-bearing fields), which lists the
  case's own neutral citation(s);
* the first heading / title;
* the filename, with underscores read as spaces so ``EWCA_Civ`` keys the same as ``EWCA Civ``
  (never raw-filename equality — another named guard).

The body is used only as the verbatim text a quote is tested against, never for subject.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from goldberg_system.grounding.authorities import (
    authority_keys,
    neutral_key_from_filename,
)
from goldberg_system.grounding.normalize import normalize_quote

# Frontmatter fields whose values name the file's own authority (its subject). ``mis_cited_as``
# is deliberately excluded — it records how the authority is WRONGLY cited, not what it is.
_SUBJECT_FIELDS = ("citation", "title", "also_cited_as", "neutral_citation")

_FRONTMATTER = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.DOTALL)
_FIRST_H1 = re.compile(r"^#\s+(.*)$", re.MULTILINE)


def _split_frontmatter(raw: str) -> tuple[str, str]:
    """Return ``(frontmatter_text, body)``; frontmatter is empty when absent."""
    m = _FRONTMATTER.match(raw)
    if not m:
        return "", raw
    return m.group(1), raw[m.end() :]


def _identity_zone(raw: str, path: Path) -> str:
    """The text from which SUBJECT keys may be drawn: frontmatter subject fields, the first
    H1, and the de-underscored filename. Never the body."""
    frontmatter, body = _split_frontmatter(raw)
    parts: list[str] = []
    for line in frontmatter.splitlines():
        stripped = line.strip()
        for field_name in _SUBJECT_FIELDS:
            if stripped.lower().startswith(f"{field_name}:") or stripped.startswith("- "):
                parts.append(stripped)
                break
    h1 = _FIRST_H1.search(body)
    if h1:
        parts.append(h1.group(1))
    parts.append(path.stem.replace("_", " "))
    return "\n".join(parts)


def _is_repealed(raw: str, path: Path) -> bool:
    """A held-but-DEAD authority: a ``REPEALED_…_DO_NOT_CITE`` filename, or a first heading
    that shouts REPEALED / DO NOT CITE. A live citation to one of these is itself a RED."""
    name = path.name.upper()
    if "REPEALED" in name or "DO_NOT_CITE" in name:
        return True
    head = raw[:400].upper()
    return "REPEALED" in head or "DO NOT CITE" in head


@dataclass(frozen=True)
class PrimaryAuthority:
    """One held primary-text file, reduced to what the checker needs."""

    path: Path
    subject_keys: frozenset[str]
    normalized_text: str
    repealed: bool

    def contains(self, normalized_quote: str) -> bool:
        """Fixed-string test: is the (already normalised) quote present verbatim?"""
        return normalized_quote in self.normalized_text


def load_primary_text(path: Path) -> PrimaryAuthority:
    """Load and reduce a single primary-text file."""
    raw = path.read_text(encoding="utf-8", errors="replace")
    keys = set(authority_keys(_identity_zone(raw, path)))
    # A neutral citation carried only by the filename (no frontmatter, brackets stripped).
    filename_key = neutral_key_from_filename(path.stem)
    if filename_key is not None:
        keys.add(filename_key)
    return PrimaryAuthority(
        path=path,
        subject_keys=frozenset(keys),
        normalized_text=normalize_quote(raw),
        repealed=_is_repealed(raw, path),
    )


def load_primary_texts(root: Path) -> list[PrimaryAuthority]:
    """Load every ``.md`` / ``.txt`` primary-text file under ``root``.

    ``root`` is the ``authorities_primary_text/`` directory. Files with no recognisable
    subject key are still loaded (they simply ground nothing) so the count is honest.
    """
    root = Path(root)
    out: list[PrimaryAuthority] = []
    if not root.is_dir():
        return out
    for path in sorted(root.rglob("*")):
        if path.is_file() and path.suffix.lower() in {".md", ".txt"}:
            out.append(load_primary_text(path))
    return out


@dataclass
class PrimaryIndex:
    """Authority key -> the held primary-text file(s) whose subject that key is."""

    by_key: dict[str, list[PrimaryAuthority]] = field(default_factory=dict)

    @classmethod
    def build(cls, authorities: list[PrimaryAuthority]) -> PrimaryIndex:
        by_key: dict[str, list[PrimaryAuthority]] = {}
        for auth in authorities:
            for key in auth.subject_keys:
                by_key.setdefault(key, []).append(auth)
        return cls(by_key=by_key)

    @classmethod
    def load(cls, root: Path) -> PrimaryIndex:
        return cls.build(load_primary_texts(root))

    def get(self, key: str) -> list[PrimaryAuthority]:
        return self.by_key.get(key, [])

    def held(self, key: str) -> bool:
        return key in self.by_key
