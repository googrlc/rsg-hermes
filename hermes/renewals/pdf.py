"""Render a renewal worksheet to a PDF (reportlab).

``build_renewal_pdf(worksheet)`` turns the same worksheet dict the on-demand
worksheet handler produces into a filed-ready PDF (bytes). The text content is
the single source of truth — ``worksheet.build_worksheet_content`` — so the PDF
and the chat/text worksheet never drift; this module only lays that markdown-ish
content onto a page.

reportlab is imported lazily so importing this module never hard-fails in an
environment without it; ``build_renewal_pdf`` raises a clear error instead.
"""

from __future__ import annotations

import io
from typing import Any

from hermes.renewals import worksheet


class PdfUnavailableError(RuntimeError):
    """Raised when reportlab is not installed."""


def default_filename(worksheet_dict: dict[str, Any]) -> str:
    """Stable, filesystem-safe filename for a policy's renewal worksheet PDF."""
    import re

    policy = str(worksheet_dict.get("policyNumber") or "policy").strip()
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", policy).strip("-") or "policy"
    return f"{safe}-renewal-worksheet.pdf"


def _lines_to_flowables(content: str, styles: Any):
    """Map the worksheet's markdown-ish text into reportlab flowables."""
    from reportlab.lib.units import inch
    from reportlab.platypus import Paragraph, Spacer

    flowables: list[Any] = []
    for raw in content.splitlines():
        line = raw.rstrip()
        if not line:
            flowables.append(Spacer(1, 0.12 * inch))
            continue
        if line.startswith("## "):
            flowables.append(Paragraph(_esc(line[3:]), styles["h2"]))
        elif line.startswith("# "):
            flowables.append(Paragraph(_esc(line[2:]), styles["title"]))
        elif line.startswith("- "):
            flowables.append(Paragraph(_esc(line[2:]), styles["bullet"], bulletText="•"))
        else:
            flowables.append(Paragraph(_md_bold(_esc(line)), styles["body"]))
    return flowables


def _esc(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _md_bold(text: str) -> str:
    import re

    return re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)


def build_renewal_pdf(worksheet_dict: dict[str, Any]) -> bytes:
    """Render *worksheet_dict* to PDF bytes. Raises PdfUnavailableError if reportlab is missing."""
    try:
        from reportlab.lib.enums import TA_LEFT
        from reportlab.lib.pagesizes import letter
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
        from reportlab.lib.units import inch
        from reportlab.platypus import SimpleDocTemplate
    except ImportError as exc:  # pragma: no cover - env dependent
        raise PdfUnavailableError(
            "reportlab is not installed; run `pip install reportlab` to generate PDFs."
        ) from exc

    content = worksheet.build_worksheet_content(worksheet_dict)

    base = getSampleStyleSheet()
    styles = {
        "title": ParagraphStyle("wtitle", parent=base["Title"], fontSize=16, spaceAfter=10, alignment=TA_LEFT),
        "h2": ParagraphStyle("wh2", parent=base["Heading2"], fontSize=12, spaceBefore=8, spaceAfter=4),
        "body": ParagraphStyle("wbody", parent=base["BodyText"], fontSize=10, leading=14),
        "bullet": ParagraphStyle("wbullet", parent=base["BodyText"], fontSize=10, leading=14,
                                  leftIndent=14, bulletIndent=4),
    }

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=letter,
        leftMargin=0.9 * inch, rightMargin=0.9 * inch,
        topMargin=0.8 * inch, bottomMargin=0.8 * inch,
        title=f"Renewal Worksheet — {worksheet_dict.get('accountName') or worksheet_dict.get('policyNumber') or ''}",
    )
    doc.build(_lines_to_flowables(content, styles))
    return buf.getvalue()
