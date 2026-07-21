"""Tests for the hard-case suite runner + synthetic generators (M8)."""

from __future__ import annotations

from pathlib import Path

from goldberg_system.testing import synthetic
from goldberg_system.testing.hard_cases import _check, run_hard_cases


def test_check_expectations() -> None:
    assert _check({"min_chars": 5}, "hello world", None)[0] is True
    assert _check({"min_chars": 50}, "short", None)[0] is False
    assert _check({}, "", None)[0] is False  # empty unexpected
    assert _check({"allow_empty": True}, "", None)[0] is True
    assert _check({}, None, "boom")[0] is False  # error unexpected
    assert _check({"allow_error": True}, None, "boom")[0] is True
    assert _check({"contains": "Fadhley"}, "re: Mr Fadhley", None)[0] is True
    assert _check({"contains": "Goldberg"}, "no match here", None)[0] is False


def test_synthetic_generators(tmp_path: Path) -> None:
    assert (
        synthetic.make_synthetic("huge_text", tmp_path).read_text().__len__() >= 100_000
    )
    assert (
        synthetic.make_synthetic("empty_pdf", tmp_path).read_bytes().startswith(b"%PDF")
    )
    assert "Fadhley" in synthetic.make_synthetic(
        "latin1_text", tmp_path
    ).read_bytes().decode("latin-1")
    assert synthetic.make_synthetic("near_empty", tmp_path).read_text() == "Goldberg"


class _FakeDocling:
    def __init__(self, out: dict[str, str]) -> None:
        self._out = out  # filename -> text ("" empty; "!" raises)

    def convert_file(self, path: Path) -> str:
        from goldberg_system.extract.docling_client import DoclingError

        v = self._out.get(Path(path).name, "some extracted text")
        if v == "!":
            raise DoclingError("simulated failure")
        return v


def test_run_hard_cases_real_and_synthetic(tmp_path: Path) -> None:
    raw = tmp_path / "raw"
    (raw / "evidence").mkdir(parents=True)
    (raw / "evidence" / "big.pdf").write_bytes(b"%PDF")
    registry = tmp_path / "reg.yaml"
    registry.write_text("""
real:
  - raw_path: evidence/big.pdf
    why: t
    expect: { min_chars: 5 }
synthetic:
  - name: near-empty
    kind: near_empty
    why: t
    expect: { min_chars: 3 }
""")
    results = run_hard_cases(
        _FakeDocling({"big.pdf": "plenty of extracted text"}),
        raw,
        tmp_path / "work",
        registry_path=registry,
    )
    by_name = {r.name: r for r in results}
    assert by_name["evidence/big.pdf"].ok
    assert by_name["near-empty"].ok and by_name["near-empty"].kind == "synthetic"


def test_run_hard_cases_flags_regression(tmp_path: Path) -> None:
    raw = tmp_path / "raw"
    (raw / "evidence").mkdir(parents=True)
    (raw / "evidence" / "big.pdf").write_bytes(b"%PDF")
    registry = tmp_path / "reg.yaml"
    registry.write_text(
        "real:\n  - raw_path: evidence/big.pdf\n    why: t\n    expect: { min_chars: 500 }\n"
    )
    # extraction returns too little → the case must FAIL (regression caught)
    results = run_hard_cases(
        _FakeDocling({"big.pdf": "tiny"}),
        raw,
        tmp_path / "work",
        registry_path=registry,
    )
    assert results[0].ok is False and "chars" in results[0].detail
