"""Statement PDFs — rows out of a document that was never meant to be parsed.

Until now a PDF statement was refused outright, and the refusal was right for
the reason it gave: a mis-read column on money data does not look like a
failure, it looks like a successful parse. But refusing also meant the carriers
who only ever send PDFs stayed off the reconciliation surface entirely, which is
its own silent hole. So PDFs are read here, in two tiers, and **neither tier is
trusted enough to commit on its own** — see ``requires_confirmation`` in
``statements.py``.

    TIER_TEXT   the PDF has a text layer; PyMuPDF's table finder reads it.
                Deterministic — the same file yields the same rows forever.
    TIER_OCR    no usable text layer (a scan, or a fax of a scan). Pages are
                rendered and read by the vision model through the same LiteLLM
                gateway intake already uses.

Tier 1 is attempted first and always preferred: a text layer is ground truth,
and asking a model to read something Postgres could have read is how you get a
plausible number instead of the right one.

Both tiers emit **raw header->cell dicts**, exactly the shape ``parse_row``
already receives from CSV and XLSX. That is deliberate: the alias table, the
money/rate/date coercion, the subtotal-line exclusion and the whole staging
ladder stay in one place, and a PDF line reaches the ledger through the same
code path as every other line.

Everything degrades to ``[]`` plus a warning. A PDF that cannot be read is a
batch a human is told about, never an empty statement that looks reconciled.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)

TIER_TEXT = "pdf_text"
TIER_OCR = "pdf_ocr"

# Bound cost and latency the same way intake's OCR tier does, and with the same
# env vars, so one setting governs both.
MAX_OCR_PAGES = int(os.environ.get("HERMES_OCR_MAX_PAGES", "15"))
OCR_DPI = int(os.environ.get("HERMES_OCR_DPI", "180"))

# A page with fewer characters than this has no usable text layer — it is a
# scan. 40 is comfortably below a real statement page and comfortably above the
# stray "Page 1 of 4" a scanner's own header sometimes carries.
MIN_TEXT_CHARS_PER_PAGE = 40

# A table row that is mostly empty is a layout artefact (a spanning title, a
# rule, a footer). Requiring two populated cells drops those without dropping a
# sparse-but-real line.
MIN_POPULATED_CELLS = 2

_OCR_SYSTEM = (
    "You transcribe commission statements from insurance carriers. The images "
    "are pages of one statement. Return ONLY JSON: "
    '{"columns": [...], "rows": [[...], ...]} where "columns" are the table\'s '
    "column headings verbatim and each row is that table's cells in the same "
    "order, one array per statement line. Transcribe numbers, policy numbers "
    "and dates EXACTLY as printed, including $ signs, commas, and parentheses "
    "for negatives. Do not compute, correct, reformat, or summarize anything. "
    "Do not include subtotal, total, or page-footer lines. If a page has no "
    "statement table, contribute no rows for it."
)


@dataclass
class PdfExtraction:
    """Raw rows lifted from a PDF, plus how they were lifted."""

    rows: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    method: str = TIER_TEXT
    pages: int = 0
    # The document itself could not be opened — as opposed to opening fine and
    # containing no table. There is nothing for the OCR tier to render either,
    # so it is not attempted.
    unreadable: bool = False

    @property
    def is_ocr(self) -> bool:
        return self.method == TIER_OCR


class PdfUnreadable(RuntimeError):
    """The PDF could not be opened. Carries the reason, which the reviewer sees.

    Distinguishing "this build has no PDF library" from "this file is not a
    readable PDF" matters: one is an ops problem to fix on the box, the other is
    a bad file to re-export. A single message for both sends whoever is holding
    the statement to the wrong place.
    """


def _open(content: bytes):
    """Open a PDF with PyMuPDF under either import name.

    Raises ``PdfUnreadable`` rather than returning None so the caller reports
    *why* — see the class docstring.
    """
    try:
        import pymupdf as fitz          # PyMuPDF 1.24+ primary name
    except ImportError:
        try:
            import fitz                 # legacy import name
        except ImportError as exc:
            raise PdfUnreadable(
                "PyMuPDF is not installed — PDF statements cannot be read on this build"
            ) from exc
    try:
        return fitz.open(stream=content, filetype="pdf")
    except Exception as exc:  # noqa: BLE001
        log.info("statement pdf: could not open document: %s", exc)
        raise PdfUnreadable(
            "the file could not be opened as a PDF — it may be corrupt, "
            "password-protected, or not actually a PDF"
        ) from exc


def _header_index(cells: list[str]) -> dict[int, str]:
    """Map column position -> heading, for the cells that carry a heading.

    Blank headings are dropped rather than named ``column_3``: an unnamed column
    matches no alias, so inventing a name for it only makes the raw row noisier.
    """
    return {i: text for i, text in enumerate(cells) if text}


def _looks_like_header(cells: list[str]) -> bool:
    """Is this row the table's heading row?

    Reuses the statement alias table so "Policy #", "policy_number" and
    "POLICY NO." all qualify — the same vocabulary the CSV and XLSX readers use
    to find their header. A heading needs a policy column AND a money column;
    one alone is a coincidence.
    """
    from hermes.commissions.statements import _ALIASES, _key

    keys = {_key(c) for c in cells}
    policy = {_key("policy_number"), *_ALIASES["policy_number"]}
    amount = {_key("commission_amount"), *_ALIASES["commission_amount"]}
    return bool(keys & policy and keys & amount)


def _rows_from_table(table: list[list[Any]]) -> tuple[list[dict[str, Any]], bool]:
    """One extracted table -> raw dicts. Returns (rows, found_header).

    A table whose heading row we cannot identify contributes nothing. Guessing
    that row 1 is the header is how a carrier's address block becomes column
    names and every line parses to None.
    """
    cleaned = [
        ["" if cell is None else str(cell).replace("\n", " ").strip() for cell in row]
        for row in table
    ]
    header_at = next(
        (i for i, row in enumerate(cleaned[:10]) if _looks_like_header(row)), None
    )
    if header_at is None:
        return [], False

    header = _header_index(cleaned[header_at])
    out: list[dict[str, Any]] = []
    for row in cleaned[header_at + 1:]:
        populated = sum(1 for i in header if i < len(row) and row[i])
        if populated < MIN_POPULATED_CELLS:
            continue
        out.append({name: (row[i] if i < len(row) else "") for i, name in header.items()})
    return out, True


def read_text_tables(content: bytes) -> PdfExtraction:
    """Tier 1 — the PDF's own text layer, via PyMuPDF's table finder.

    Multi-page statements repeat their heading on every page, so each page's
    tables are read independently and concatenated. A page whose table has no
    identifiable header is skipped and counted, never guessed at.
    """
    result = PdfExtraction(method=TIER_TEXT)
    try:
        doc = _open(content)
    except PdfUnreadable as exc:
        result.warnings.append(str(exc))
        result.unreadable = True
        return result

    text_chars = 0
    headerless_pages = 0
    with doc:
        result.pages = doc.page_count
        for page in doc:
            try:
                text_chars += len(page.get_text() or "")
            except Exception:  # noqa: BLE001
                pass
            try:
                tables = page.find_tables()
            except Exception:  # noqa: BLE001 — a page that won't tabulate is not fatal
                log.exception("statement pdf: table find failed on a page")
                continue
            page_rows = 0
            for table in tables:
                try:
                    extracted = table.extract()
                except Exception:  # noqa: BLE001
                    continue
                rows, found = _rows_from_table(extracted)
                if not found:
                    continue
                result.rows.extend(rows)
                page_rows += len(rows)
            if not page_rows:
                headerless_pages += 1

    if result.pages and text_chars < MIN_TEXT_CHARS_PER_PAGE * result.pages:
        result.warnings.append(
            f"PDF has little or no text layer ({text_chars} chars over "
            f"{result.pages} page(s)) — it looks scanned"
        )
    if result.rows and headerless_pages:
        result.warnings.append(
            f"{headerless_pages} page(s) yielded no statement table and were skipped"
        )
    return result


def _render_pages(content: bytes) -> list[bytes]:
    """Render up to ``MAX_OCR_PAGES`` pages to PNG. ``[]`` if that isn't possible."""
    try:
        doc = _open(content)
    except PdfUnreadable:
        return []
    images: list[bytes] = []
    try:
        with doc:
            for page in doc:
                if len(images) >= MAX_OCR_PAGES:
                    break
                images.append(page.get_pixmap(dpi=OCR_DPI).tobytes("png"))
    except Exception:  # noqa: BLE001
        log.exception("statement pdf: page render failed")
        return []
    return images


def _loads(raw: str) -> dict[str, Any]:
    """Parse the model's JSON, tolerating a ```json fence around it."""
    text = (raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE)
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def read_ocr_tables(content: bytes) -> PdfExtraction:
    """Tier 2 — render the pages and have the vision model transcribe the table.

    Used only when tier 1 found nothing. The model is asked to transcribe, never
    to interpret: totals stay unparsed, negatives keep their parentheses, and
    every number is passed to the same coercion the CSV path uses. What comes
    back is still a machine reading a picture of money, which is exactly why a
    batch from this tier cannot be committed without someone confirming it.
    """
    result = PdfExtraction(method=TIER_OCR)

    images = _render_pages(content)

    result.pages = len(images)
    if not images:
        result.warnings.append(
            "could not render the PDF for OCR (PyMuPDF missing or file unreadable)"
        )
        return result

    try:
        from hermes_core.llm_client import get_client, resolve_model
    except ImportError:
        result.warnings.append("no LLM gateway available for OCR on this build")
        return result

    try:
        client = get_client()
        model = resolve_model(os.environ.get("HERMES_STATEMENT_OCR_MODEL"))
    except Exception as exc:  # noqa: BLE001 — an unset key is a config fact, not a crash
        result.warnings.append(f"OCR unavailable: {exc}")
        return result

    parts: list[dict[str, Any]] = [{
        "type": "text",
        "text": "Transcribe the commission statement table from these pages.",
    }]
    for image in images:
        parts.append({
            "type": "image_url",
            "image_url": {
                "url": "data:image/png;base64," + base64.b64encode(image).decode()
            },
        })

    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": _OCR_SYSTEM},
                {"role": "user", "content": parts},
            ],
        )
        payload = _loads(response.choices[0].message.content or "")
    except Exception as exc:  # noqa: BLE001
        log.exception("statement pdf: OCR call failed")
        result.warnings.append(f"OCR read failed: {exc}")
        return result

    columns = [str(c).strip() for c in (payload.get("columns") or [])]
    rows = payload.get("rows") or []
    if not columns or not isinstance(rows, list):
        result.warnings.append("OCR returned no readable table")
        return result

    for row in rows:
        if not isinstance(row, list):
            continue
        cells = ["" if c is None else str(c).strip() for c in row]
        if sum(1 for c in cells if c) < MIN_POPULATED_CELLS:
            continue
        result.rows.append({
            name: (cells[i] if i < len(cells) else "")
            for i, name in enumerate(columns) if name
        })

    result.warnings.append(
        f"read by OCR from {len(images)} rendered page(s) — every amount must be "
        "checked against the PDF before this batch is approved"
    )
    return result


def read_pdf(content: bytes) -> PdfExtraction:
    """Rows from a statement PDF: text layer first, OCR only if that found none."""
    text_tier = read_text_tables(content)
    if text_tier.rows:
        return text_tier

    if text_tier.unreadable:
        # Nothing to render either, so the OCR tier has no file to look at.
        result = text_tier
    else:
        result = read_ocr_tables(content)
        # Carry the text tier's diagnosis forward — "it looks scanned" is why we
        # fell through, and the reviewer should see that beside the OCR result.
        result.warnings = [*text_tier.warnings, *result.warnings]

    if not result.rows:
        result.warnings.append(
            "no statement lines could be read from this PDF — export it as CSV "
            "or XLSX from the carrier portal and upload that instead"
        )
    return result
