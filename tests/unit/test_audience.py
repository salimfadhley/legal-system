"""Tests for raw_path → audience classification (the NLI cross-audience seam)."""

from __future__ import annotations

import pytest

from goldberg_system.audience import COURT, NEIDLE, POLICE, classify_audience


@pytest.mark.parametrize(
    "raw_path,expected",
    [
        # NEIDLE — journalist/solicitor correspondence
        ("evidence/dan_neidle/glp_correspondence/20260513_160604-simon-goldberg-questions.eml", NEIDLE),
        ("evidence/simon_goldberg/goldberg_to_neidle_emails/feb goldberg email.eml", NEIDLE),
        ("evidence/artington_legal/neidle_request_for_comment/2026_02_25_request.eml", NEIDLE),
        # POLICE — CPS + MG6C forms, wherever they sit
        ("evidence/cps/asif_akram/External Email - Re_ Witness statements.eml", POLICE),
        ("evidence/r_v_fanthom_and_deacon/mg6c/mg6c_t20240030.txt", POLICE),
        # the precedence case: an MG6C police form disclosed inside a COURT bundle is
        # POLICE-audience, not COURT — the ordered rule must catch it before COURT.
        ("evidence/court_correspondence/2026_07_20_post_cps_case_management_and_disclosure/served_items/MG6C_Item_168_Brief_Update_re_the_Police.pdf", POLICE),
        # COURT — case summary, summons, prosecution bundle
        ("evidence/simon_goldberg/2025_10_10_original_prosecution_bundle/case_summary.md", COURT),
        ("evidence/simon_goldberg/2025_10_10_summons_to_edwards/Summons.pdf", COURT),
        ("evidence/simon_goldberg/2026_06_30_court_electronic_bundle/extracted/SUMMONS_APPLICATION_granted.md", COURT),
        # a Neidle email that mentions the summons stays NEIDLE (precedence)
        ("evidence/dan_neidle/glp_correspondence/re-private-prosecution-summons.eml", NEIDLE),
        # outside the seam → None
        ("evidence/transcripts/mind_of_steele/shorts/2026-03-10_short.md", None),
        ("analysis/summaries/case_against_salim_fadhley.md", None),
        ("", None),
        (None, None),
    ],
)
def test_classify_audience(raw_path: str | None, expected: str | None) -> None:
    assert classify_audience(raw_path) == expected


def test_court_correspondence_non_mg6c_is_court() -> None:
    # served court correspondence that is NOT an MG6C police form resolves to COURT
    assert (
        classify_audience(
            "evidence/court_correspondence/2026_07_20_post_cps_case_management_and_disclosure/summers_disclosure_note.pdf"
        )
        == COURT
    )
