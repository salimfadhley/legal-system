"""Run the hard-case regression suite (M8).

Extracts each registered hard case (real corpus doc or synthetic) via Docling and
checks it against its declared expectation. Extraction is where the pipeline breaks
(the OCR timeout, the unparseable JSON), so the suite is extraction-focused, fast, and
costs no OpenAI — it can run often and gate changes.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from goldberg_system.extract.docling_client import DoclingClient, DoclingError
from goldberg_system.testing.synthetic import make_synthetic

_DEFAULT_REGISTRY = Path(__file__).resolve().parents[3] / "config" / "hard-cases.yaml"


@dataclass
class CaseResult:
    name: str
    kind: str  # "real" | "synthetic"
    ok: bool
    chars: int
    detail: str


def _check(
    expect: dict, content: str | None, error: str | None
) -> tuple[bool, int, str]:
    """Evaluate an extraction against its expectation.

    expect keys: ``min_chars`` (default 1), ``allow_empty``, ``allow_error``,
    ``contains`` (case-insensitive substring).
    """
    if error is not None:
        if expect.get("allow_error"):
            return True, 0, f"expected error: {error[:60]}"
        return False, 0, f"extraction error: {error[:90]}"
    text = (content or "").strip()
    n = len(text)
    if n == 0:
        if expect.get("allow_empty"):
            return True, 0, "empty (allowed)"
        return False, 0, "empty extraction (unexpected)"
    if n < int(expect.get("min_chars", 1)):
        return False, n, f"only {n} chars (< {expect['min_chars']})"
    needle = expect.get("contains")
    if needle and needle.lower() not in text.lower():
        return False, n, f"missing expected text {needle!r}"
    return True, n, f"{n} chars"


def _extract(docling: DoclingClient, path: Path) -> tuple[str | None, str | None]:
    try:
        return docling.convert_file(path), None
    except DoclingError as exc:
        return None, str(exc)
    except OSError as exc:
        return None, f"file error: {exc}"


def run_hard_cases(
    docling: DoclingClient,
    raw_root: Path | str,
    work_dir: Path | str,
    *,
    registry_path: Path | str | None = None,
    only: set[str] | None = None,
) -> list[CaseResult]:
    """Run every hard case (or those named in ``only``) and return per-case results."""
    reg: dict[str, Any] = yaml.safe_load(
        Path(registry_path or _DEFAULT_REGISTRY).read_text()
    )
    raw_root = Path(raw_root)
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    results: list[CaseResult] = []

    for case in reg.get("real") or []:
        name = case["raw_path"]
        if only and name not in only:
            continue
        path = raw_root / name
        if not path.is_file():
            results.append(
                CaseResult(name, "real", False, 0, "missing from goldberg-raw")
            )
            continue
        content, error = _extract(docling, path)
        ok, n, detail = _check(case.get("expect") or {}, content, error)
        results.append(CaseResult(name, "real", ok, n, detail))

    for case in reg.get("synthetic") or []:
        name = case["name"]
        if only and name not in only:
            continue
        path = make_synthetic(case["kind"], work_dir)
        content, error = _extract(docling, path)
        ok, n, detail = _check(case.get("expect") or {}, content, error)
        results.append(CaseResult(name, "synthetic", ok, n, detail))

    return results
