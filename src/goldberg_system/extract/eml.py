"""Extract an ``.eml`` email into markdown + an attachment manifest.

Papra's bundled extractor returns nothing for ``.eml`` (verified: empty content),
so the pipeline handles emails itself. This module parses the headers and body and
lists attachments. The attachment *files* are ingested separately (a PDF
attachment goes through Papra/Docling); here we only record a manifest so the
email document references them.
"""

from __future__ import annotations

import html
import re
from email import message_from_bytes
from email.message import EmailMessage
from email.policy import default
from typing import cast

from pydantic import BaseModel, ConfigDict

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t]+")


class AttachmentRef(BaseModel):
    """A reference to one email attachment (the file is ingested separately)."""

    model_config = ConfigDict(extra="forbid")

    filename: str
    content_type: str
    size_bytes: int


class ExtractedEmail(BaseModel):
    """The parsed content of an email."""

    model_config = ConfigDict(extra="forbid")

    subject: str | None = None
    from_addr: str | None = None
    to_addrs: list[str] = []
    cc_addrs: list[str] = []
    date: str | None = None
    body: str = ""
    attachments: list[AttachmentRef] = []

    def to_markdown(self) -> str:
        """Render a readable markdown document for the email."""
        lines: list[str] = [f"# {self.subject or '(no subject)'}", ""]
        if self.from_addr:
            lines.append(f"**From:** {self.from_addr}")
        if self.to_addrs:
            lines.append(f"**To:** {', '.join(self.to_addrs)}")
        if self.cc_addrs:
            lines.append(f"**Cc:** {', '.join(self.cc_addrs)}")
        if self.date:
            lines.append(f"**Date:** {self.date}")
        lines += ["", self.body.strip(), ""]
        if self.attachments:
            lines.append("## Attachments")
            for att in self.attachments:
                lines.append(
                    f"- `{att.filename}` ({att.content_type}, {att.size_bytes} bytes)"
                )
        return "\n".join(lines).rstrip() + "\n"


def _html_to_text(content: str) -> str:
    text = _TAG_RE.sub(" ", content)
    text = html.unescape(text)
    text = _WS_RE.sub(" ", text)
    return "\n".join(line.strip() for line in text.splitlines())


def _addresses(msg: EmailMessage, field: str) -> list[str]:
    header = msg[field]
    if header is None:
        return []
    addresses = getattr(header, "addresses", None)
    if addresses:
        return [str(a) for a in addresses]
    return [str(header)]


def _body(msg: EmailMessage) -> str:
    part = msg.get_body(preferencelist=("plain", "html"))
    if part is None:
        return ""
    body_part = cast(EmailMessage, part)
    content = body_part.get_content()
    if not isinstance(content, str):
        content = str(content)
    if body_part.get_content_type() == "text/html":
        content = _html_to_text(content)
    return content.strip()


def _attachments(msg: EmailMessage) -> list[AttachmentRef]:
    refs: list[AttachmentRef] = []
    for part in msg.iter_attachments():
        attachment = cast(EmailMessage, part)
        payload = attachment.get_payload(decode=True)
        size = len(payload) if isinstance(payload, (bytes, bytearray)) else 0
        refs.append(
            AttachmentRef(
                filename=attachment.get_filename() or "(unnamed)",
                content_type=attachment.get_content_type(),
                size_bytes=size,
            )
        )
    return refs


def eml_to_markdown(raw: bytes) -> ExtractedEmail:
    """Parse raw ``.eml`` bytes into an :class:`ExtractedEmail`."""
    msg = cast(EmailMessage, message_from_bytes(raw, policy=default))
    subject = msg["subject"]
    from_addr = msg["from"]
    return ExtractedEmail(
        subject=str(subject) if subject is not None else None,
        from_addr=str(from_addr) if from_addr is not None else None,
        to_addrs=_addresses(msg, "to"),
        cc_addrs=_addresses(msg, "cc"),
        date=str(msg["date"]) if msg["date"] is not None else None,
        body=_body(msg),
        attachments=_attachments(msg),
    )
