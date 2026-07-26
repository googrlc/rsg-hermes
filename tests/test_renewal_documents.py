"""Tests for renewal PDF generation + Nextcloud filing.

reportlab is a real dep (PDF bytes are asserted). Nextcloud WebDAV is mocked —
no live server is contacted. Synthetic identifiers only.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from hermes.commands import renewal_documents as rd
from hermes.core.dispatcher import DispatchResult, Dispatcher
from hermes.integrations.nextcloud_client import NextcloudClient, NextcloudError
from hermes.renewals import pdf


# ---------------------------------------------------------------- test doubles

def _worksheet(number="TST-0001"):
    return {
        "policyNumber": number, "accountName": "Acme LLC", "carrier": "Test Carrier",
        "line_of_business": "Commercial Auto", "effective_date": "2026-01-01",
        "expiration_date": "2027-01-01", "current_premium": 5000, "pipeline_stage": "Active",
        "policy_guid": "g1",
    }


def _detail(number="TST-0001"):
    return {
        "databaseId": "g1", "insuredDatabaseId": "i1", "policyNumber": number,
        "carrierName": "Test Carrier", "lineOfBusiness": "Commercial Auto",
        "effectiveDate": "2026-01-01", "expirationDate": "2027-01-01",
        "premium": 5000, "policyStatus": "Active",
    }


def _nc_ams(detail):
    m = MagicMock()
    want = detail.get("policyNumber")
    m.find_policy_by_number.side_effect = lambda n: (detail if n == want else None)
    m.is_insured_active.return_value = True
    return m


def _candidate(number="TST-0001"):
    return {"insured_id": "i1", "policy_lineage_id": "l1", "renewal_event_date": "2027-01-01",
            "client_name": "Acme LLC", "segment": "commercial_small", "policy_number": number}


def _supa(tables):
    s = MagicMock()
    s.select.side_effect = lambda table, **kw: tables.get(table, [])
    return s


def _resp(code, text=""):
    r = MagicMock(); r.status_code = code; r.text = text
    return r


# ---------------------------------------------------------------- pdf.py

def test_build_renewal_pdf_returns_pdf_bytes():
    data = pdf.build_renewal_pdf(_worksheet())
    assert isinstance(data, bytes) and data[:5] == b"%PDF-"
    assert len(data) > 500


def test_default_filename_sanitizes():
    assert pdf.default_filename({"policyNumber": "TST/00 01"}) == "TST-00-01-renewal-worksheet.pdf"
    assert pdf.default_filename({}) == "policy-renewal-worksheet.pdf"


# ---------------------------------------------------------------- nextcloud_client.py

def test_nextcloud_not_configured_raises():
    nc = NextcloudClient(url="", user="", app_password="")
    assert nc.is_configured() is False
    with pytest.raises(NextcloudError):
        nc.put_file("Clients/X/f.pdf", b"x")


def test_nextcloud_file_document_puts_and_makes_dirs():
    sess = MagicMock()
    sess.request.return_value = _resp(201)   # MKCOL
    sess.put.return_value = _resp(201)
    nc = NextcloudClient(url="https://nc.example.com", user="root", app_password="pw", session=sess)
    res = nc.file_document(content=b"data", filename="f.pdf", client="Acme LLC", category="Renewal Reviews")
    assert res["path"] == "Clients/Acme LLC/Renewal Reviews/f.pdf"
    assert sess.put.call_count == 1
    assert sess.request.call_count == 3          # MKCOL for each of 3 ancestor dirs
    put_url = sess.put.call_args[0][0]
    assert "Acme%20LLC" in put_url               # spaces URL-encoded


def test_nextcloud_put_error_raises():
    sess = MagicMock()
    sess.request.return_value = _resp(201)
    sess.put.return_value = _resp(500, "boom")
    nc = NextcloudClient(url="https://nc.example.com", user="root", app_password="pw", session=sess)
    with pytest.raises(NextcloudError):
        nc.file_document(content=b"x", filename="f.pdf", client="X")


# ---------------------------------------------------------------- generate_pdf_handle

def test_generate_and_file_ok():
    supa = _supa({
        "renewal_candidates": [_candidate()],
        "agency_crm_cases": [{"id": "case-1", "policy_number": "TST-0001", "insured_database_id": "i1"}],
    })
    ncloud = MagicMock()
    ncloud.is_configured.return_value = True
    ncloud.file_document.return_value = {"path": "Clients/Acme LLC/Renewal Reviews/TST-0001-renewal-worksheet.pdf",
                                         "url": "https://nc/x"}
    r = rd.generate_pdf_handle(
        "generate the renewal pdf for policy TST-0001",
        supa=supa, nowcerts=_nc_ams(_detail()), nextcloud=ncloud,
    )
    assert r.ok and r.data["filed"] is True
    assert r.data["case_linked"] is True
    ncloud.file_document.assert_called_once()
    supa.update.assert_called_once()             # case.nextcloud_path linked


def test_generate_without_nextcloud_configured():
    supa = _supa({"renewal_candidates": [_candidate()], "agency_crm_cases": []})
    ncloud = MagicMock()
    ncloud.is_configured.return_value = False
    r = rd.generate_pdf_handle(
        "generate the renewal pdf for policy TST-0001",
        supa=supa, nowcerts=_nc_ams(_detail()), nextcloud=ncloud,
    )
    assert r.ok and r.data["filed"] is False
    assert "configured" in r.message.lower() and "nextcloud" in r.message.lower()
    ncloud.file_document.assert_not_called()


# ---------------------------------------------------------------- routing

def _dispatcher():
    d = Dispatcher(use_openai=False)
    d.supa = MagicMock()
    return d


def test_generate_pdf_routes():
    with patch("hermes.commands.renewal_documents.generate_pdf_handle") as h:
        h.return_value = DispatchResult(True, "pdf")
        _dispatcher().dispatch("generate the renewal pdf for policy TST-0001")
        h.assert_called_once()


def test_file_to_nextcloud_routes():
    with patch("hermes.commands.renewal_documents.generate_pdf_handle") as h:
        h.return_value = DispatchResult(True, "filed")
        _dispatcher().dispatch("file the renewal worksheet to nextcloud for policy TST-0001")
        h.assert_called_once()
