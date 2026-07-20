"""Tests for the .eml -> markdown extractor."""

from __future__ import annotations

from email.message import EmailMessage

from goldberg_system.extract import eml_to_markdown


def _build_eml(*, html_body: bool = False, with_attachment: bool = True) -> bytes:
    msg = EmailMessage()
    msg["Subject"] = "Request for CPS Discontinuance"
    msg["From"] = "Asif Akram <asif@example.gov.uk>"
    msg["To"] = "a@x.com, b@y.com"
    msg["Cc"] = "c@z.com"
    msg["Date"] = "Mon, 12 May 2025 09:00:00 +0100"
    if html_body:
        # HTML-only email (no plain alternative), to exercise the HTML->text path.
        msg.set_content(
            "<html><body><p>Hello &amp; welcome</p></body></html>", subtype="html"
        )
    else:
        msg.set_content("Dear Sir,\n\nPlease discontinue.\n\nRegards")
    if with_attachment:
        msg.add_attachment(
            b"%PDF-1.4 fake",
            maintype="application",
            subtype="pdf",
            filename="exhibit.pdf",
        )
    return msg.as_bytes()


def test_parses_headers() -> None:
    email = eml_to_markdown(_build_eml())
    assert email.subject == "Request for CPS Discontinuance"
    assert "asif@example.gov.uk" in (email.from_addr or "")
    assert email.to_addrs == ["a@x.com", "b@y.com"]
    assert email.cc_addrs == ["c@z.com"]
    assert "2025" in (email.date or "")


def test_extracts_plain_body() -> None:
    email = eml_to_markdown(_build_eml())
    assert "Please discontinue." in email.body


def test_lists_attachments_without_extracting_them() -> None:
    email = eml_to_markdown(_build_eml())
    assert len(email.attachments) == 1
    att = email.attachments[0]
    assert att.filename == "exhibit.pdf"
    assert att.content_type == "application/pdf"
    assert att.size_bytes > 0


def test_no_attachments() -> None:
    email = eml_to_markdown(_build_eml(with_attachment=False))
    assert email.attachments == []


def test_html_body_is_stripped_to_text() -> None:
    email = eml_to_markdown(_build_eml(html_body=True))
    assert "Hello & welcome" in email.body
    assert "<p>" not in email.body


def test_to_markdown_renders_headers_body_and_manifest() -> None:
    md = eml_to_markdown(_build_eml()).to_markdown()
    assert "# Request for CPS Discontinuance" in md
    assert "**From:**" in md
    assert "Please discontinue." in md
    assert "## Attachments" in md
    assert "exhibit.pdf" in md
