"""Drop raw files into a Papra organisation's watched ingest folder.

This is the credential-free ingest path (the same drop-folder mechanism Mind of
Steele already uses): Papra's inotify watcher picks up files written into
``<ingest_root>/<org_id>/`` and ingests + extracts them. Papra requires the
per-organisation sub-folder — files in the ingest root itself are ignored.
"""

from __future__ import annotations

import shutil
from pathlib import Path


class IngestFolder:
    """Writes files into ``<ingest_root>/<org_id>/`` for Papra to ingest."""

    def __init__(self, ingest_root: Path | str, org_id: str) -> None:
        self.ingest_root = Path(ingest_root)
        self.org_id = org_id

    @property
    def org_dir(self) -> Path:
        """The per-organisation ingest sub-folder Papra actually watches."""
        return self.ingest_root / self.org_id

    def _ensure_dir(self) -> None:
        self.org_dir.mkdir(parents=True, exist_ok=True)

    def drop_file(self, source: Path | str, *, name: str | None = None) -> Path:
        """Copy ``source`` into the org ingest folder; return the written path."""
        source = Path(source)
        self._ensure_dir()
        dest = self.org_dir / (name or source.name)
        shutil.copy2(source, dest)
        return dest

    def drop_bytes(self, content: bytes, name: str) -> Path:
        """Write ``content`` as ``name`` into the org ingest folder."""
        self._ensure_dir()
        dest = self.org_dir / name
        dest.write_bytes(content)
        return dest
