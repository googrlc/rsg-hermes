"""Tests for the renewal desk routes: get_renewal_queue, open_exact_renewal,
research_renewal_client — routing precedence + handler behavior.

NowCerts + Supabase are mocked. Synthetic identifiers only.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from hermes.commands import renewal_desk as rd
from hermes.agent.dispatcher import Dispatcher
from hermes.core.dispatch import DispatchResult


def _make_dispatcher() -> Dispatcher:
    d = Dispatcher(use_openai=False)
    d.supa = MagicMock()
    return d


def _detail(*, number="TST-0001", guid="pol-guid-1", insured="ins-guid-1",
            lob="Commercial Auto", status="Active", exp="2027-01-01"):
    return {
        "databaseId": guid, "insuredDatabaseId": insured, "policyNumber": number,
        "carrierName": "Test Carrier", "lineOfBusiness": lob,
        "effectiveDate": "2026-01-01", "expirationDate": exp,
        "premium": 5000, "policyStatus": status,
    }


def _nc(detail):
    nc = MagicMock()
    want = detail.get("policyNumber") if isinstance(detail, dict) and not detail.get("_ambiguous") else None

    def _find(number):
        if isinstance(detail, dict) and detail.get("_ambiguous"):
            return detail
        return detail if (detail is not None and number == want) else None

    nc.find_policy_by_number.side_effect = _find
    nc.is_insured_active.return_value = True
    return nc


def _supa(rows):
    supa = MagicMock()
    supa.select.return_value = rows
    return supa


# ============================================================ routing

def test_renewal_queue_routes():
    with patch("hermes.commands.renewal_desk.queue_handle") as h:
        h.return_value = DispatchResult(True, "queue")
        _make_dispatcher().dispatch("show me the renewal queue")
        h.assert_called_once()


def test_open_exact_renewal_routes():
    with patch("hermes.commands.renewal_desk.open_handle") as h:
        h.return_value = DispatchResult(True, "opened")
        _make_dispatcher().dispatch("open renewal for policy TST-0001")
        h.assert_called_once()


def test_research_renewal_routes():
    with patch("hermes.commands.renewal_desk.research_handle") as h:
        h.return_value = DispatchResult(True, "exposures")
        _make_dispatcher().dispatch("research renewal client for Test Corp")
        h.assert_called_once()


def test_research_business_not_hijacked_by_renewal():
    """Intake-style 'research business X' must still hit business_research, not renewal."""
    with patch("hermes.commands.business_research.handle") as br, \
         patch("hermes.commands.renewal_desk.research_handle") as rr:
        br.return_value = DispatchResult(True, "biz research ran")
        d = _make_dispatcher()  # captures patched business_research.handle in _routes
        d.dispatch("research business Acme Plumbing Atlanta")
        br.assert_called_once()
        rr.assert_not_called()


def test_renewal_audit_not_hijacked_by_queue():
    """'renewal audit' must still fall through to revenue, not the queue route."""
    mock_client = MagicMock()
    mock_client.get.return_value = {"list": []}
    with patch("hermes.commands.renewal_desk.queue_handle") as q, \
         patch("hermes.commands.renewal_desk.open_handle") as o:
        _make_dispatcher().dispatch("renewal audit")
        q.assert_not_called()
        o.assert_not_called()


# ============================================================ get_renewal_queue

def test_queue_sorts_critical_first():
    supa = _supa([
        {"client_name": "B", "policy_number": "P2", "risk_status": "AT_RISK",
         "renewal_event_date": "2026-08-01", "line_of_business": "GL",
         "premium_current": 1000, "eligibility_state": "eligible"},
        {"client_name": "A", "policy_number": "P1", "risk_status": "CRITICAL",
         "renewal_event_date": "2026-09-01", "line_of_business": "WC",
         "premium_current": 2000, "eligibility_state": "eligible"},
    ])
    r = rd.queue_handle("renewal queue", supa=supa)
    assert r.ok and r.data["count"] == 2
    assert r.message.index("P1") < r.message.index("P2")  # CRITICAL before AT_RISK


def test_queue_empty():
    r = rd.queue_handle("renewal queue", supa=_supa([]))
    assert r.ok and r.data["count"] == 0
    assert "No eligible renewals" in r.message


def test_queue_needs_supa():
    r = rd.queue_handle("renewal queue", supa=None)
    assert not r.ok


# ============================================================ open_exact_renewal

def test_open_requires_exact_identifier():
    """The core fix: no identifier → refuse, never a general report."""
    r = rd.open_handle("open the renewal please", supa=_supa([]), nowcerts=_nc(None))
    assert not r.ok
    assert r.data.get("need_identifier") is True
    assert "won't substitute" in r.message


def test_open_resolves_by_policy_number():
    nc = _nc(_detail(number="TST-0001"))
    supa = _supa([{"client_name": "Acme LLC", "policy_number": "TST-0001"}])
    r = rd.open_handle("open renewal for policy TST-0001", supa=supa, nowcerts=nc)
    assert r.ok
    assert "TST-0001" in r.message
    assert r.data["source"] == "nowcerts"


def test_open_not_found():
    r = rd.open_handle("open renewal for policy NOPE-9", supa=_supa([]), nowcerts=_nc(None))
    assert not r.ok
    assert r.data.get("reconciliation_needed") is True


# ============================================================ research_renewal_client

def test_research_returns_lob_exposures():
    nc = _nc(_detail(number="TST-0001", lob="Commercial Auto"))
    supa = _supa([{"client_name": "Acme LLC", "policy_number": "TST-0001"}])
    r = rd.research_handle("research the missing exposures for policy TST-0001", supa=supa, nowcerts=nc)
    assert r.ok
    assert "vehicle schedule" in r.message.lower()   # commercial-auto-specific exposure
    assert r.data["line_of_business"] == "Commercial Auto"


def test_research_generic_exposures_for_unknown_lob():
    nc = _nc(_detail(number="TST-0002", lob="Ocean Marine"))
    supa = _supa([{"client_name": "X", "policy_number": "TST-0002"}])
    r = rd.research_handle("research exposures for policy TST-0002", supa=supa, nowcerts=nc)
    assert r.ok
    assert "loss runs" in r.message.lower()


def test_research_needs_target():
    r = rd.research_handle("research renewal client", supa=_supa([]), nowcerts=_nc(None))
    assert not r.ok