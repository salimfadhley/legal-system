"""Synthetic adversarial documents — probe the pipeline's limits deliberately.

Each generator writes a file into ``dest_dir`` and returns its path. Used by the
hard-case suite to test theories about where extraction/enrichment break, without
waiting for a real document to expose the limit.
"""

from __future__ import annotations

from pathlib import Path

# A minimal but valid single-page PDF (no text) — exercises the "empty extraction" path.
_EMPTY_PDF = (
    b"%PDF-1.4\n"
    b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
    b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
    b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]>>endobj\n"
    b"xref\n0 4\n0000000000 65535 f \n0000000009 00000 n \n"
    b"0000000052 00000 n \n0000000101 00000 n \n"
    b"trailer<</Size 4/Root 1 0 R>>\nstartxref\n178\n%%EOF\n"
)


def huge_text(dest_dir: Path, *, chars: int = 200_000) -> Path:
    """A very large plain-text doc — tests full-context enrichment / no truncation."""
    para = (
        "Simon Goldberg asserted that the documents remain confidential. "
        "The defence submits the prosecuting entity's identity is unclear. "
    )
    dest = Path(dest_dir) / "synthetic-huge-text.txt"
    dest.write_text((para * (chars // len(para) + 1))[:chars])
    return dest


def empty_pdf(dest_dir: Path) -> Path:
    """A valid PDF with no extractable text — tests the empty-extraction path."""
    dest = Path(dest_dir) / "synthetic-empty.pdf"
    dest.write_bytes(_EMPTY_PDF)
    return dest


def latin1_text(dest_dir: Path) -> Path:
    """Non-UTF-8 (latin-1) text — tests encoding robustness in passthrough."""
    dest = Path(dest_dir) / "synthetic-latin1.txt"
    # latin-1-safe accented text (no em-dash — that isn't in latin-1)
    dest.write_bytes("Réunion, café, naïve, £100, Señor Fadhley\n".encode("latin-1"))
    return dest


def near_empty(dest_dir: Path) -> Path:
    """A one-word text file — minimal but non-empty content."""
    dest = Path(dest_dir) / "synthetic-near-empty.txt"
    dest.write_text("Goldberg")
    return dest


GENERATORS = {
    "huge_text": huge_text,
    "empty_pdf": empty_pdf,
    "latin1_text": latin1_text,
    "near_empty": near_empty,
}


def make_synthetic(kind: str, dest_dir: Path) -> Path:
    """Generate the synthetic document ``kind`` into ``dest_dir``."""
    if kind not in GENERATORS:
        raise KeyError(f"unknown synthetic kind: {kind} (have {sorted(GENERATORS)})")
    return GENERATORS[kind](Path(dest_dir))
