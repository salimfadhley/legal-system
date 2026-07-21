"""Populate goldberg-raw from the frozen archive, allowlist-driven (ADR 0006).

Copies the selected evidence trees into goldberg-raw preserving their relative
paths and ``metadata.yaml`` files, stamps each tree root with its ``origin`` /
``role: input`` classification, and configures git-LFS for binary types (ADR 0002).
The originals are read-only; nothing in the archive is mutated.
"""

from __future__ import annotations

import shutil
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from goldberg_system.migrate.allowlist import Allowlist

# Binary types tracked by git-LFS (ADR 0002: large/binary → LFS, text stays in git).
LFS_EXTENSIONS: tuple[str, ...] = (
    "pdf",
    "png",
    "jpg",
    "jpeg",
    "gif",
    "tiff",
    "tif",
    "webp",
    "bmp",
    "heic",
    "mp4",
    "mov",
    "avi",
    "mkv",
    "webm",
    "mp3",
    "wav",
    "m4a",
    "ogg",
    "zip",
    "gz",
    "tar",
    "docx",
    "xlsx",
    "pptx",
    "odt",
)


@dataclass
class PopulateReport:
    trees: dict[str, int] = field(default_factory=dict)  # tree name -> files copied
    files_copied: int = 0
    bytes_copied: int = 0
    skipped_excluded: int = 0

    @property
    def total(self) -> int:
        return self.files_copied


def gitattributes_content(extensions: tuple[str, ...] = LFS_EXTENSIONS) -> str:
    lines = [
        "# git-LFS tracking for binary evidence (ADR 0002). Text stays in plain git.",
    ]
    for ext in extensions:
        lines.append(f"*.{ext} filter=lfs diff=lfs merge=lfs -text")
        lines.append(f"*.{ext.upper()} filter=lfs diff=lfs merge=lfs -text")
    return "\n".join(lines) + "\n"


def _stamp_tree_origin(dest_tree: Path, origin: str) -> None:
    """Ensure the tree-root metadata.yaml records origin + role=input (merge, don't clobber)."""
    meta = dest_tree / "metadata.yaml"
    data: dict = {}
    if meta.is_file():
        try:
            data = yaml.safe_load(meta.read_text()) or {}
        except yaml.YAMLError:
            data = {}
    data.setdefault("origin", origin)
    data.setdefault("role", "input")
    meta.write_text(yaml.safe_dump(data, sort_keys=False))


def populate_raw(
    archive_root: Path | str,
    raw_root: Path | str,
    allowlist: Allowlist,
    *,
    dry_run: bool = False,
) -> PopulateReport:
    """Copy the allowlisted trees from ``archive_root`` into ``raw_root``."""
    src_root = Path(archive_root)
    dst_root = Path(raw_root)
    report = PopulateReport()
    counts: Counter[str] = Counter()

    if not dry_run:
        dst_root.mkdir(parents=True, exist_ok=True)
        (dst_root / ".gitattributes").write_text(gitattributes_content())

    for name, tree in sorted(allowlist.included.items()):
        src_tree = src_root / name
        if not src_tree.is_dir():
            continue
        for src in sorted(src_tree.rglob("*")):
            if not src.is_file():
                continue
            rel = src.relative_to(src_root)
            if allowlist.is_excluded_file(rel):
                report.skipped_excluded += 1
                continue
            counts[name] += 1
            report.files_copied += 1
            report.bytes_copied += src.stat().st_size
            if dry_run:
                continue
            dst = dst_root / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
        if not dry_run and counts[name]:
            _stamp_tree_origin(dst_root / name, tree.origin)

    report.trees = dict(counts)
    return report
