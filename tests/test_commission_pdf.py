"""Statement PDFs: reading them, and the confirmation they cost to commit.

The tests that matter here are not "does it parse" — they are the ones that keep
a PDF from reaching the ledger on the strength of a machine's reading alone.
"""

from __future__ import annotations

import pytest

from hermes.commissions import pdf as pdfmod
from hermes.commissions import statements as st

pymupdf = pytest.importorskip("pymupdf", reason="PDF reading needs PyMuPDF")


def _statement_pdf(rows: list[tuple[str, str, str, str]], *, title: str = "") -> bytes:
    """Build a one-page PDF whose text layer holds a statement-shaped table.

    Drawn as ruled cells rather than plain text: PyMuPDF's table finder keys on
    the ruling lines, which is also what a real carrier statement has.
    """
    doc = pymupdf.open()
    page = doc.new_page()
    header = ("Policy Number", "Insured", "Premium", "Commission")
    x_positions = [40, 170, 320, 430, 540]
    y = 90.0
    row_height = 24.0

    if title:
        page.insert_text((40, 60), title, fontsize=12)

    for row in (header, *rows):
        for index, cell in enumerate(row):
            page.insert_text((x_positions[index] + 4, y + 16), str(cell), fontsize=9)
        # Ruling: the horizontal line above this row, and every vertical.
        page.draw_line(pymupdf.Point(x_positions[0], y), pymupdf.Point(x_positions[-1], y))
        for x in x_positions:
            page.draw_line(pymupdf.Point(x, y), pymupdf.Point(x, y + row_height))
        y += row_height
    page.draw_line(pymupdf.Point(x_positions[0], y), pymupdf.Point(x_positions[-1], y))

    out = doc.tobytes()
    doc.close()
    return out


ROWS = [
    ("MC969126179", "Acme Trucking", "4200.00", "630.00"),
    ("GL-88213", "Bright Cafe LLC", "1800.00", "270.00"),
]


def test_text_layer_pdf_is_read_without_ocr():
    """A digital PDF is ground truth — the vision model is never consulted."""
    extraction = pdfmod.read_pdf(_statement_pdf(ROWS))

    assert extraction.method == pdfmod.TIER_TEXT
    assert extraction.is_ocr is False
    numbers = {row.get("Policy Number") for row in extraction.rows}
    assert {"MC969126179", "GL-88213"} <= numbers


def test_pdf_lines_reach_the_same_parser_as_a_csv():
    """Aliases, money coercion and the type rules apply identically."""
    lines, _warnings, method = st.parse_upload(_statement_pdf(ROWS), "june.pdf")

    assert method == st.METHOD_PDF_TEXT
    by_policy = {line["policy_number"]: line for line in lines}
    assert set(by_policy) == {"MC969126179", "GL-88213"}
    assert by_policy["MC969126179"]["commission_amount"] == st.as_money("630.00")
    assert by_policy["MC969126179"]["insured_name"] == "Acme Trucking"


def test_a_banner_above_the_table_does_not_become_the_header():
    """Carriers print a title block. Row 1 is not assumed to be the header."""
    content = _statement_pdf(ROWS, title="ACME MUTUAL — COMMISSION STATEMENT 06/2026")
    lines, _warnings, _method = st.parse_upload(content, "june.pdf")

    assert {line["policy_number"] for line in lines} == {"MC969126179", "GL-88213"}


def test_a_table_with_no_recognisable_header_yields_nothing():
    """No header, no rows. Guessing the mapping is the failure mode worth avoiding."""
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((40, 60), "Thank you for your business. Questions? Call us.")
    content = doc.tobytes()
    doc.close()

    lines, warnings, _method = st.parse_upload(content, "letter.pdf")
    assert lines == []
    assert any("export it as CSV" in w for w in warnings)


# --- the gate -----------------------------------------------------------------

class FakeSupa:
    """Enough Supabase to stage and commit one batch."""

    def __init__(self, batch: dict):
        self.batch = batch
        self.updated: dict = {}

    def select(self, table, columns="*", params=None, limit=None):
        if table == st.BATCHES_TABLE:
            return [self.batch]
        if table == st.STAGING_TABLE:
            return [{"id": "l1", "policy_number": "MC969126179",
                     "commission_amount": 630.0, "carrier_name": "Acme"}]
        return []

    def insert(self, table, payload):
        return {"id": "new-id", **payload}

    def update(self, table, record_id, payload):
        self.updated[table] = payload
        return {"id": record_id, **payload}


def _batch(method: str) -> dict:
    return {
        "id": "b1", "ingest_status": st.STATUS_PENDING_REVIEW, "crosscheck_ok": True,
        "extraction_method": method, "is_ocr": method == st.METHOD_PDF_OCR,
        "carrier_name": "Acme", "source_file": "june.pdf",
    }


@pytest.mark.parametrize("method", [st.METHOD_PDF_TEXT, st.METHOD_PDF_OCR])
def test_a_pdf_batch_will_not_commit_unconfirmed(method):
    """Both PDF tiers infer their columns, so both need a human to vouch for them."""
    supa = FakeSupa(_batch(method))

    with pytest.raises(ValueError, match="confirmed_source"):
        st.commit_statement(supa, batch_id="b1", approved_by="lamar@example.com")

    assert supa.updated == {}, "a refused commit must not touch the batch"


def test_a_confirmed_pdf_batch_commits(monkeypatch):
    supa = FakeSupa(_batch(st.METHOD_PDF_OCR))
    monkeypatch.setattr("hermes.commissions.matching.relink_unmatched",
                        lambda *a, **k: _Link())
    monkeypatch.setattr("hermes.commissions.reconcile.run_rollup", lambda *a, **k: _Roll())

    result = st.commit_statement(supa, batch_id="b1", approved_by="lamar@example.com",
                                 confirmed_source=True)

    assert result.committed == 1
    assert supa.updated[st.BATCHES_TABLE]["ingest_status"] == st.STATUS_COMMITTED


def test_a_csv_batch_needs_no_source_confirmation(monkeypatch):
    """A CSV names its own columns — there is nothing extra to attest to."""
    supa = FakeSupa(_batch(st.METHOD_CSV))
    monkeypatch.setattr("hermes.commissions.matching.relink_unmatched",
                        lambda *a, **k: _Link())
    monkeypatch.setattr("hermes.commissions.reconcile.run_rollup", lambda *a, **k: _Roll())

    result = st.commit_statement(supa, batch_id="b1", approved_by="lamar@example.com")

    assert result.committed == 1


class _Link:
    exact = normalized = ledger_rows_created = unmatched = 0
    errors: list[str] = []


class _Roll:
    message = "rollup: examined=0"


def test_a_legacy_pdf_batch_still_needs_confirmation():
    """Found live: the Slack-drop poller that predates this code wrote
    extraction_method='pdf', and two such rows are on file. An exact-match gate
    would wave them through the one check they most need.
    """
    supa = FakeSupa({**_batch(st.METHOD_PDF_TEXT), "extraction_method": "pdf"})

    with pytest.raises(ValueError, match="confirmed_source"):
        st.commit_statement(supa, batch_id="b1", approved_by="lamar@example.com")


def test_an_ocr_flagged_batch_needs_confirmation_whatever_it_calls_its_method():
    """is_ocr is honoured on its own: a machine-read batch is a machine-read
    batch even if the method string says csv."""
    supa = FakeSupa({**_batch(st.METHOD_CSV), "is_ocr": True})

    with pytest.raises(ValueError, match="confirmed_source"):
        st.commit_statement(supa, batch_id="b1", approved_by="lamar@example.com")


@pytest.mark.parametrize("method,is_ocr,expected", [
    ("csv", False, False), ("xlsx", False, False),
    ("pdf", False, True), ("pdf_text", False, True), ("pdf_ocr", False, True),
    ("PDF", False, True), (" pdf_text ", False, True),
    ("csv", True, True), (None, False, False), ("", False, False),
])
def test_which_methods_require_confirmation(method, is_ocr, expected):
    assert st.requires_source_confirmation(method, is_ocr=is_ocr) is expected
