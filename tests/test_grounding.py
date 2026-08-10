"""Grounding checker — the deterministic (non-LLM) authority verifier.

Every failure mode casework named is encoded here as its own test, plus the three-outcome
classifier, the layer signal, blast radius, and the embedded self-test's contract.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from goldberg_system.grounding import (
    SelfTestError,
    check_root,
    find_authorities,
    find_quotes,
    normalize_quote,
    run_selftest,
)
from goldberg_system.grounding.authorities import authority_keys
from goldberg_system.grounding.checker import (
    CITATION_WITHOUT_SOURCE,
    QUOTE,
    Layer,
    Verdict,
)
from goldberg_system.grounding.primary import PrimaryIndex, load_primary_text


# --------------------------------------------------------------------------------------
# normalisation — ONLY whitespace, smart quotes, ellipses; nothing else
# --------------------------------------------------------------------------------------


def test_normalize_folds_smart_quotes_and_whitespace() -> None:
    assert normalize_quote("“hello   world”") == '"hello world"'
    assert normalize_quote("it’s") == "it's"
    assert normalize_quote("a\n\n  b\tc") == "a b c"


def test_normalize_unifies_ellipses() -> None:
    assert normalize_quote("a…b") == "a...b"
    assert normalize_quote("a . . . b") == "a ... b"
    assert normalize_quote("a....b") == "a...b"


def test_normalize_does_not_change_case_or_strip_punctuation() -> None:
    # a misquote must NOT be laundered by over-eager normalisation
    assert normalize_quote("The Defendant.") == "The Defendant."
    assert normalize_quote("The Defendant") != normalize_quote("the defendant")


# --------------------------------------------------------------------------------------
# GUARD: fixed-string, never regex — bracketed neutral citations must be found + keyed
# --------------------------------------------------------------------------------------


def test_neutral_citation_with_brackets_is_recognised() -> None:
    # The brackets are regex character classes; a non-fixed lookup silently matches nothing.
    refs = find_authorities("see [2008] EWHC 148 (Admin) at [55]")
    keys = {r.key for r in refs}
    assert "ncit:2008 ewhc 148 admin" in keys


def test_neutral_citation_variants_key_identically() -> None:
    # division bracketed vs folded into the court name → same canonical key
    a = authority_keys("[2001] EWHC 493 (Admin)")
    b = authority_keys("[2001] EWHC Admin 493")
    assert a == b == {"ncit:2001 ewhc 493 admin"}


def test_ewca_civ_and_crim_keep_court_token() -> None:
    assert "ncit:2011 ewca civ 1233" in authority_keys("[2011] EWCA Civ 1233")
    assert "ncit:2008 ewca crim 2989" in authority_keys("[2008] EWCA Crim 2989")


def test_primary_lookup_uses_fixed_string_not_regex(tmp_path: Path) -> None:
    # A quote containing regex metacharacters must be matched literally.
    root = _mini_corpus(
        tmp_path,
        primary={
            "case.md": '---\ncitation: "[2008] EWHC 148 (Admin)"\n---\n# C\n'
            '> "particularised in the information laid (see s.2(1)) at [55]."\n'
        },
        analysis={
            "a.md": 'In [2008] EWHC 148 (Admin) the court required matters '
            '"particularised in the information laid (see s.2(1)) at [55]."\n'
        },
    )
    report = check_root(root)
    verdict = _quote_verdicts(report)["analysis/a.md"]
    assert verdict is Verdict.GREEN


# --------------------------------------------------------------------------------------
# GUARD: "grounded" = a primary-text FILE whose SUBJECT is the authority — not a mention
# --------------------------------------------------------------------------------------


def test_mention_inside_another_judgment_is_not_grounding(tmp_path: Path) -> None:
    # crawford.md is the held subject for [2008] EWHC 148; it MENTIONS Lau v DPP [2013]
    # EWHC 100 (Admin) in its body. A quote cited to Lau must NOT be grounded by that mention.
    root = _mini_corpus(
        tmp_path,
        primary={
            "crawford.md": '---\ncitation: "[2008] EWHC 148 (Admin)"\n---\n'
            "# Crawford\n> \"as was done in Lau v DPP [2013] EWHC 100 (Admin).\"\n"
        },
        analysis={
            "a.md": 'Per Lau v DPP [2013] EWHC 100 (Admin), "the conduct must be grave."\n'
        },
    )
    report = check_root(root)
    # No primary text has [2013] EWHC 100 as its SUBJECT → AMBER, never GREEN.
    v = _quote_verdicts(report)["analysis/a.md"]
    assert v is Verdict.AMBER
    index = PrimaryIndex.load(root / "authorities_primary_text")
    assert index.held("ncit:2008 ewhc 148 admin")
    assert not index.held("ncit:2013 ewhc 100 admin")


# --------------------------------------------------------------------------------------
# GUARD: filename underscores must not defeat matching (EWCA_Civ vs EWCA Civ)
# --------------------------------------------------------------------------------------


def test_filename_underscores_do_not_defeat_subject_key(tmp_path: Path) -> None:
    # A primary file with NO frontmatter citation, subject carried only by its filename.
    p = tmp_path / "2011_thomas_v_news_group_EWCA_Civ_1233.md"
    p.write_text("# Thomas v News Group\n> \"a quote here that is long enough to test.\"\n")
    prim = load_primary_text(p)
    assert "ncit:2011 ewca civ 1233" in prim.subject_keys


# --------------------------------------------------------------------------------------
# GUARD: honour REPEALED primary texts — a live citation to a dead authority is RED
# --------------------------------------------------------------------------------------


def test_quote_matching_repealed_primary_is_red_not_green(tmp_path: Path) -> None:
    # The quote genuinely appears in the repealed provision — but because the only held
    # primary is REPEALED, it must be RED, never GREEN.
    root = _mini_corpus(
        tmp_path,
        primary={
            "REPEALED_1997_pfha_s5_DO_NOT_CITE.md": "# REPEALED s.5 PfHA — DO NOT CITE\n"
            '> "a restraining order may be made on conviction under this section."\n'
        },
        analysis={
            "a.md": 'Under s.5 PfHA "a restraining order may be made on conviction under '
            'this section."\n'
        },
    )
    index = PrimaryIndex.load(root / "authorities_primary_text")
    assert index.held("stat:protection from harassment act 1997 s5")
    report = check_root(root)
    quote_findings = [f for f in report.findings if f.kind == QUOTE]
    assert quote_findings and all(f.verdict is Verdict.RED for f in quote_findings)
    assert "REPEALED" in quote_findings[0].detail.upper()


def test_live_citation_to_repealed_authority_flagged_without_a_quote(tmp_path: Path) -> None:
    from goldberg_system.grounding.checker import REPEALED_CITATION

    root = _mini_corpus(
        tmp_path,
        primary={
            "REPEALED_1997_pfha_s5_DO_NOT_CITE.md": "# REPEALED s.5 PfHA — DO NOT CITE\n"
            "This section no longer exists.\n"
        },
        analysis={"a.md": "Under s.5 PfHA the court may make a restraining order.\n"},
    )
    report = check_root(root)
    reps = [f for f in report.findings if f.kind == REPEALED_CITATION]
    assert reps and reps[0].verdict is Verdict.RED
    # a held (repealed) citation is NOT reported as citation-without-source
    assert not [f for f in report.findings if f.kind == CITATION_WITHOUT_SOURCE]


# --------------------------------------------------------------------------------------
# GUARD: never trust a `verified:` flag or a "Verification Report" title as truth
# --------------------------------------------------------------------------------------


def test_verified_flag_does_not_make_a_fabricated_quote_green(tmp_path: Path) -> None:
    root = _mini_corpus(
        tmp_path,
        primary={
            "case.md": '---\ncitation: "[2020] UKSC 1"\nverified: true\n---\n# Case\n'
            '> "the genuine holding is a single specific sentence here."\n'
        },
        analysis={
            # The analysis file even titles itself a Verification Report and sets verified.
            "vr.md": "# Verification Report (verified: true)\n\n"
            'Per [2020] UKSC 1, "this fabricated holding was never in the judgment at all."\n'
        },
    )
    report = check_root(root)
    assert _quote_verdicts(report)["analysis/vr.md"] is Verdict.RED


# --------------------------------------------------------------------------------------
# the three-outcome classifier
# --------------------------------------------------------------------------------------


def test_green_red_amber(tmp_path: Path) -> None:
    root = _mini_corpus(
        tmp_path,
        primary={
            "held.md": '---\ncitation: "[2019] EWHC 1709 (Admin)"\n---\n# Held\n'
            '> "the applicant must demonstrate a real prospect of success."\n'
        },
        analysis={
            "green.md": 'See [2019] EWHC 1709 (Admin): "the applicant must demonstrate a '
            'real prospect of success."\n',
            "red.md": 'See [2019] EWHC 1709 (Admin): "the applicant need not demonstrate '
            'anything whatsoever."\n',
            "amber.md": 'See [2099] EWHC 1 (Admin): "an authority we simply do not hold '
            'anywhere in the library."\n',
        },
    )
    report = check_root(root)
    v = _quote_verdicts(report)
    assert v["analysis/green.md"] is Verdict.GREEN
    assert v["analysis/red.md"] is Verdict.RED
    assert v["analysis/amber.md"] is Verdict.AMBER


def test_quote_far_from_any_citation_is_not_checked(tmp_path: Path) -> None:
    body = 'A long quote "with no authority anywhere near it at all in this file."\n'
    root = _mini_corpus(tmp_path, primary={}, analysis={"a.md": body})
    report = check_root(root)
    assert not [f for f in report.findings if f.kind == QUOTE]


# --------------------------------------------------------------------------------------
# citation-without-source, ranked by blast radius
# --------------------------------------------------------------------------------------


def test_citation_without_source_ranked_by_blast_radius(tmp_path: Path) -> None:
    root = _mini_corpus(
        tmp_path,
        primary={},  # nothing held → every citation is unsupported
        analysis={
            "a.md": "relies on [2050] EWHC 5 (Admin)\n",
            "b.md": "also [2050] EWHC 5 (Admin) and [2051] EWHC 6 (Admin)\n",
            "c.md": "again [2050] EWHC 5 (Admin)\n",
        },
    )
    report = check_root(root)
    cws = [f for f in report.sorted_findings() if f.kind == CITATION_WITHOUT_SOURCE]
    by_key = {f.authority_key: f for f in cws}
    assert by_key["ncit:2050 ewhc 5 admin"].blast_radius == 3
    assert by_key["ncit:2051 ewhc 6 admin"].blast_radius == 1
    # highest blast radius sorts before the lower one within the same layer/severity
    order = [f.authority_key for f in cws]
    assert order.index("ncit:2050 ewhc 5 admin") < order.index("ncit:2051 ewhc 6 admin")


def test_fabricated_crimpr_rule_surfaces_as_unsupported(tmp_path: Path) -> None:
    # The motivating case: a fabricated Criminal Procedure Rule with no held primary text.
    root = _mini_corpus(
        tmp_path,
        primary={},
        analysis={"a.md": "as required by CrimPR Part 99 the filing must be served twice\n"},
    )
    report = check_root(root)
    keys = {f.authority_key for f in report.findings if f.kind == CITATION_WITHOUT_SOURCE}
    assert "rule:criminal procedure rules 99" in keys


# --------------------------------------------------------------------------------------
# layer signal — served + RED sorts to the very top
# --------------------------------------------------------------------------------------


def test_served_red_outranks_everything(tmp_path: Path) -> None:
    root = _mini_corpus(
        tmp_path,
        primary={
            "held.md": '---\ncitation: "[2020] UKSC 9"\n---\n# H\n'
            '> "the true and genuine holding of the court is written here."\n'
        },
        analysis={
            "a.md": 'Per [2020] UKSC 9, "the true and genuine holding of the court is '
            'written here."\n',  # GREEN in analysis
        },
        served={
            "filing.md": 'Per [2020] UKSC 9, "a fabricated holding served to the court '
            'that was never in the judgment."\n',  # RED in a served filing
        },
    )
    report = check_root(root)
    top = report.sorted_findings()[0]
    assert top.layer is Layer.SERVED
    assert top.verdict is Verdict.RED
    assert top.file.endswith("filing.md")


# --------------------------------------------------------------------------------------
# the embedded self-test
# --------------------------------------------------------------------------------------


def test_selftest_passes() -> None:
    run_selftest()  # must not raise: GREEN for known-good, RED for known-bad


def test_selftest_raises_when_classifier_polarity_reversed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Simulate a broken classifier (the all-GREEN reversed-polarity failure) and prove the
    # self-test catches it and REFUSES to report.
    import goldberg_system.grounding.checker as checker
    import goldberg_system.grounding.selftest as selftest

    def _always_green(quote, authority, index):  # type: ignore[no-untyped-def]
        return Verdict.GREEN, "forced green"

    monkeypatch.setattr(selftest, "check_root", checker.check_root)
    monkeypatch.setattr(checker, "classify_quote", _always_green)
    with pytest.raises(SelfTestError):
        selftest.run_selftest()


def test_find_quotes_ignores_short_fragments() -> None:
    quotes = find_quotes('He said "no" and then "yes" to the whole thing entirely okay."')
    # "no" / "yes" are too short; nothing substantial here
    assert all(len(q.normalized) >= 25 for q in quotes)


# --------------------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------------------


def _mini_corpus(
    root: Path,
    *,
    primary: dict[str, str] | None = None,
    analysis: dict[str, str] | None = None,
    reports: dict[str, str] | None = None,
    served: dict[str, str] | None = None,
) -> Path:
    """Build a throwaway raw-corpus tree with the given files in each layer."""
    (root / "authorities_primary_text").mkdir(parents=True, exist_ok=True)
    for name, text in (primary or {}).items():
        (root / "authorities_primary_text" / name).write_text(text, encoding="utf-8")
    for layer, files in (
        ("analysis", analysis),
        ("reports", reports),
        ("evidence/evidence_of_service/sent", served),
    ):
        if not files:
            continue
        base = root / layer
        base.mkdir(parents=True, exist_ok=True)
        for name, text in files.items():
            (base / name).write_text(text, encoding="utf-8")
    return root


def _quote_verdicts(report) -> dict[str, Verdict]:  # type: ignore[no-untyped-def]
    return {
        f.file: f.verdict
        for f in report.findings
        if f.kind == QUOTE and f.verdict is not None
    }


def test_sent_bundles_served_content_vs_draft_layering() -> None:
    """sent_bundles/ mixes served text (SERVED) with internal drafts (not SERVED)."""
    from pathlib import Path
    from goldberg_system.grounding.checker import Layer, layer_of

    base = "evidence/evidence_of_service/sent_bundles/2026-08-06_x/"
    assert layer_of(Path(base + "2026-08-06_sent-text.txt")) is Layer.SERVED
    assert layer_of(Path(base + "covering-email.txt")) is Layer.SERVED
    assert layer_of(Path(base + "2026-08-06_email_as_sent.txt")) is Layer.SERVED
    # internal working files are drafts, NOT served — must not inflate the served tier
    assert layer_of(Path(base + "WORK-PRODUCT_strategy-at-the-time.md")) is Layer.ANALYSIS
    assert layer_of(Path(base + "SIMULATION_anticipated.md")) is Layer.ANALYSIS
    assert layer_of(Path(base + "SERVICE-RECORD.md")) is Layer.ANALYSIS
    # the real sent/ dir stays SERVED
    assert layer_of(Path("evidence/evidence_of_service/sent/letter.md")) is Layer.SERVED
