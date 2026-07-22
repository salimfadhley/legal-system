"""The evidence allowlist: which archive trees migrate into goldberg-raw.

Loaded from ``config/evidence-allowlist.yaml``. The migration is allowlist-driven
because the frozen archive mixes evidence with the old project's code, build junk,
and authored work product (ADR 0006, spike §3).
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass
from pathlib import Path

import yaml

from goldberg_system.config import config_dir


@dataclass(frozen=True)
class IncludedTree:
    """A top-level archive tree selected for migration."""

    name: str
    origin: str  # "received" | "authored"
    note: str = ""


@dataclass(frozen=True)
class Allowlist:
    """The parsed allowlist: included trees, excluded trees, and file-glob exclusions."""

    included: dict[str, IncludedTree]
    excluded: dict[str, str]
    exclude_globs: tuple[str, ...]

    @classmethod
    def load(cls, path: Path | str | None = None) -> "Allowlist":
        cfg = Path(path) if path else config_dir() / "evidence-allowlist.yaml"
        data = yaml.safe_load(cfg.read_text()) or {}
        included = {
            name: IncludedTree(
                name=name,
                origin=str((spec or {}).get("origin", "received")),
                note=str((spec or {}).get("note", "")),
            )
            for name, spec in (data.get("include") or {}).items()
        }
        excluded = {
            name: str(reason) for name, reason in (data.get("exclude") or {}).items()
        }
        globs = tuple(str(g) for g in (data.get("exclude_globs") or ()))
        return cls(included=included, excluded=excluded, exclude_globs=globs)

    def tree_for(self, relpath: str | Path) -> IncludedTree | None:
        """Return the IncludedTree owning ``relpath`` (relative to archive root), or None."""
        first = Path(relpath).parts[0] if Path(relpath).parts else ""
        return self.included.get(first)

    def is_excluded_file(self, relpath: str | Path) -> bool:
        """True if ``relpath`` matches any ``exclude_globs`` pattern."""
        rel = str(relpath)
        return any(fnmatch.fnmatch(rel, pat) for pat in self.exclude_globs)
