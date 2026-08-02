"""Carrier parsers, ported from the browser and held to the same behaviour.

These are the cases the TypeScript parsers were written to get right
(``src/parsers/*.test.ts``). They are restated here because the Python path is
now the only writer: if a rule survived only in the retired browser code, it
did not survive at all.
"""

from __future__ import annotations

import csv
import io
from decimal import Decimal

import pytest

from hermes.commissions import carriers as cx
from hermes.commissions import statements as st

pytest.importorskip("openpyxl")

# Progressive's fixed layout (spec §6): 0 Insured 1 Policy 2 Eff 3 Exp 4 Prod
# 6 TranCode 7 TranDate 8 GrossPrem 12 AgencyDue 13 Comm 14 GrossComm 15 NetDue
# 18 MonthEnd.
P_HEADER = [
    "Insured Name", "Policy Number", "Policy Effective Date", "Policy Expiration Date",
    "Prod", "Agt Pre", "Tran Code", "Tran Date", "Gross Premium",
    "Down Payment Collected", "Down Payment Submitted", "Billed Amount",
    "Agency Due", "Comm", "Gross Comm", "Net Due Agent", "Prod Name",
    "Agent Code", "Month End", "Renewal Count",
]


def _p_row(**kw):
    row = [None] * 20
    for index, key in ((0, "insured"), (1, "policy"), (2, "eff"), (3, "exp"),
                       (4, "prod"), (6, "code"), (7, "date"), (8, "gross"),
                       (12, "agency_due"), (13, "rate"), (14, "gross_comm"),
                       (15, "net_due"), (18, "month_end")):
        row[index] = kw.get(key)
    return row


P_ROWS = [
    _p_row(insured="Douglas, Shamira", policy="871502820", eff="03/20/2026",
           exp="09/20/2026", prod="Auto", code="New Business", date="03/20/2026",
           gross=2054, rate=0.1, gross_comm=205.4, net_due=205.4),
    _p_row(insured="Douglas, Shamira", policy="871502820", eff="03/20/2026",
           exp="09/20/2026", prod="Auto", code="Cancel Pro Rate", date="06/08/2026",
           gross=-1317.54, rate=0.1, gross_comm=-131.75, net_due=-131.75),
    _p_row(insured="Fleet LLC", policy="999", eff="01/01/2026", exp="01/01/2027",
           prod="Commercial Auto", code="Renewal", date="01/01/2026",
           gross=5000, rate=0.15, gross_comm=750, net_due=750),
    _p_row(insured="MVR Guy", policy="871502820", eff="03/20/2026", exp="09/20/2026",
           prod="Auto", code="Endorsement", date="05/04/2026", gross=0,
           agency_due=4.8, rate=0, gross_comm=0, net_due=-4.8),
    _p_row(insured="Credit Co", policy="555", eff="02/01/2026", exp="08/01/2026",
           prod="Auto", code="Credit Endorsement", date="02/10/2026",
           gross=-100, rate=0.1, gross_comm=-10, net_due=-10),
]

P_SUMMARY = [
    ["", "", "", ""],
    ["Agent Total", 5636.46, "", 813.65],     # net premium (col 1), commission (col 3)
    ["Net amount due agent", 808.85],
]


def _progressive_xlsx(detailed=P_ROWS, summary=P_SUMMARY) -> bytes:
    from openpyxl import Workbook

    book = Workbook()
    sheet = book.active
    sheet.title = "Detailed"
    sheet.append(P_HEADER)
    for row in detailed:
        sheet.append(row)
    if summary is not None:
        second = book.create_sheet("Summary")
        for row in summary:
            second.append(row)
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


def _parse_progressive(**kw):
    return cx.parse_carrier(_progressive_xlsx(**kw), "DetailedStatement20260630.xlsx")


def _by_code(parse, code):
    return next(line for line in parse.lines if line["transaction_code"] == code)


# --- Progressive --------------------------------------------------------------

def test_progressive_is_detected_and_named_canonically():
    parse = _parse_progressive()
    assert parse.parser_key == cx.PROGRESSIVE_V1
    assert parse.carrier == "Progressive"
    assert len(parse.lines) == 5


def test_an_mvr_line_is_a_fee_not_a_zero_commission():
    """Agency Due with no commission is a chargeback, not the carrier paying $0."""
    fee = next(line for line in _parse_progressive().lines
               if line["transaction_type"] == "fee")
    assert fee["fee_type"] == "MVR"
    assert fee["fee_amount"] == Decimal("4.8")
    assert fee["commission_amount"] == Decimal("0")


def test_a_cancel_stays_a_cancel_and_keeps_its_negative():
    cancel = _by_code(_parse_progressive(), "Cancel Pro Rate")
    assert cancel["transaction_type"] == "cancel"
    assert cancel["commission_amount"] == Decimal("-131.75")


def test_a_credit_endorsement_is_an_adjustment_not_a_cancel():
    """A premium credit is not a policy ending. Reconciliation reads them differently."""
    assert _by_code(_parse_progressive(), "Credit Endorsement")["transaction_type"] == "adjustment"


def test_the_policy_term_survives_in_the_raw_row():
    """Dates aren't on the line, but a disputed commission needs the term."""
    raw = _by_code(_parse_progressive(), "New Business")["raw_row"]
    assert raw["2"] == "03/20/2026"      # policy effective date, fixed position
    assert raw["3"] == "09/20/2026"      # policy expiration date


def test_segment_and_month_key_are_derived():
    commercial = next(line for line in _parse_progressive().lines
                      if line["lob"] == "Commercial Auto")
    assert commercial["segment"] == "commercial"
    assert commercial["month_key"] == 202601


def test_the_carrier_states_its_own_totals_so_the_crosscheck_is_automatic():
    """205.4 - 131.75 + 750 + 0 - 10 = 813.65, which is what the Summary sheet says.

    Read off the file, this crosscheck runs whether or not the uploader typed
    anything — and an unverifiable batch is precisely the one a bad parse walks
    through.
    """
    parse = _parse_progressive()
    assert parse.stated_commission == Decimal("813.65")

    check = st.crosscheck(parse.lines, stated_commission=parse.stated_commission)
    assert check.parsed_commission == Decimal("813.65")
    assert check.ok and check.verifiable


def test_a_progressive_file_with_no_summary_says_it_cannot_be_checked():
    parse = _parse_progressive(summary=None)
    assert any("no carrier total" in w for w in parse.warnings)
    assert parse.stated_commission is None


# --- NEXT ---------------------------------------------------------------------

N_HEADER = [
    "Policy Number", "LOB", "Business Name", "Statement Date", "Effective Date",
    "Expiration Date", "New Renewal", "Agent Commission",
    "Agency Commission Paid to Date", "Agency Commission Paid this Month",
    "Total Premium Collected to Date", "Premium Collected this Month", "Policy Status",
]
N_DATA = [
    "NX-100", "General Liability", "Acme LLC", "2026-05-31", "2026-01-01",
    "2026-12-31", "New", 0.15, 500, 42.5, 3400, 283.33, "Active",
]


def _csv_bytes(rows) -> bytes:
    buffer = io.StringIO()
    csv.writer(buffer).writerows(rows)
    return buffer.getvalue().encode()


def test_next_takes_the_incremental_column_not_the_cumulative_one():
    """The whole reason this parser exists.

    'Paid to Date' is 500 and 'Paid this Month' is 42.50. Loading the cumulative
    figure would re-book every prior month on every monthly statement.
    """
    parse = cx.parse_carrier(_csv_bytes([N_HEADER, N_DATA]), "NEXT_May2026.csv")

    assert parse.parser_key == cx.NEXT_V1
    line = parse.lines[0]
    assert line["commission_amount"] == Decimal("42.5")
    assert line["gross_premium"] == Decimal("283.33")
    assert line["commission_rate"] == Decimal("0.15")


def test_next_carries_carrier_segment_and_type():
    parse = cx.parse_carrier(_csv_bytes([N_HEADER, N_DATA]), "NEXT_May2026.csv")
    line = parse.lines[0]
    assert parse.carrier == "NEXT INS US CO"
    assert line["segment"] == "commercial"
    assert line["month_key"] == 202605
    assert line["transaction_type"] == "new"
    # As-earned: NEXT prints no totals row, so there is nothing to tie to.
    assert parse.stated_commission is None


def test_next_is_header_indexed_so_a_column_reorder_is_safe():
    reordered = [
        "Business Name", "Policy Number", "Agent Commission",
        "Agency Commission Paid this Month", "Premium Collected this Month",
        "Statement Date", "New Renewal", "LOB",
    ]
    data = ["Acme LLC", "NX-200", 0.2, 99.99, 500, "2026-06-30", "Renewal", "BOP"]

    parse = cx.parse_carrier(_csv_bytes([reordered, data]), "x.csv")
    line = parse.lines[0]
    assert line["policy_number"] == "NX-200"
    assert line["commission_amount"] == Decimal("99.99")
    assert line["transaction_type"] == "renewal"
    assert line["month_key"] == 202606


def test_the_as_earned_choice_is_stated_on_the_batch():
    """A reviewer has to be able to see which column was loaded."""
    parse = cx.parse_carrier(_csv_bytes([N_HEADER, N_DATA]), "NEXT_May2026.csv")
    assert any("INCREMENTAL" in w for w in parse.warnings)


# --- the sniff ----------------------------------------------------------------

def test_an_unmapped_statement_is_not_guessed_at():
    """No carrier parser claims it; the generic alias reader takes over."""
    rows = [["Policy #", "Carrier", "Premium"], ["A", "B", 1]]
    assert cx.parse_carrier(_csv_bytes(rows), "other.csv") is None


def test_a_renamed_progressive_file_is_still_recognised():
    """Detection sniffs content, not just the filename."""
    parse = cx.parse_carrier(_progressive_xlsx(), "statement-copy.xlsx")
    assert parse is not None and parse.parser_key == cx.PROGRESSIVE_V1


def test_the_carrier_parser_wins_over_the_generic_reader():
    """Both could read this file. The one that knows the fee rule must win."""
    parsed = st.parse_file(_progressive_xlsx(), "DetailedStatement20260630.xlsx")

    assert parsed.parser_key == cx.PROGRESSIVE_V1
    assert parsed.carrier == "Progressive"
    assert parsed.stated_commission == Decimal("813.65")
    assert any(line["transaction_type"] == "fee" for line in parsed.lines)


def test_an_excel_serial_date_is_not_read_as_a_number():
    """CSV exports from the same systems can carry the raw serial."""
    assert cx.as_iso_date(46023) == "2026-01-01"      # days since 1899-12-30
    assert cx.as_iso_date("06/08/2026") == "2026-06-08"
    assert cx.as_iso_date("not a date") is None
