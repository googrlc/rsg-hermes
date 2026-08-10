"""Tests for commission DQ / AMS anomaly scan."""
from __future__ import annotations

import os
from datetime import date, datetime
from typing import Any

import pytest

from hermes.jobs import commission_dq as dq


class FakeSupabase:
    def __init__(self, tables: dict[str, list[dict[str, Any]]] | None = None) -> None:
        self.tables: dict[str, list[dict[str, Any]]] = tables or {}

    def select(self, table, *, columns="*", params=None, limit=100):
        rows = self.tables.get(table, [])
        return [dict(r) for r in rows][:limit]


class FakeNotifier:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def post_message(self, *, text: str, blocks=None):
        self.calls.append({"text": text, "blocks": blocks})
        return {"ok": True}


RULE_BOTH = {
    "id": "rule-both",
    "carrier_name": "Acme Mutual",
    "lob": "General Liability",
    "nb_percent": 15,
    "renewal_percent": 12,
    "commission_basis": "as_earned",
    "active": True,
}

RULE_NB_ONLY = {
    "id": "rule-nb-only",
    "carrier_name": "NB Only Carrier",
    "lob": "General Liability",
    "nb_percent": 10,
    "renewal_percent": None,
    "commission_basis": "as_earned",
    "active": True,
}

RULE_REN_ONLY = {
    "id": "rule-ren-only",
    "carrier_name": "Ren Only Carrier",
    "lob": "General Liability",
    "nb_percent": None,
    "renewal_percent": 8,
    "commission_basis": "gross",
    "active": True,
}

PROFILE_ADVANCE = {
    "carrier_name": "TimeCo Insurance",
    "payment_model": "advance",
    "default_nb_percent": 10,
    "default_renewal_percent": 8,
}

TODAY = date(2026, 8, 10)
NOW = datetime(2026, 8, 10, 12, 0, 0)


def cpol(
    policy_number="P1",
    *,
    status="Active",
    carrier="Acme Mutual",
    lob="General Liability",
    premium=1000.0,
    agency_commission_amount=150.0,
    eff="2026-03-01",
    exp="2027-03-01",
    billing_type="Direct Bill",
    agency_fee_amount=None,
    renewed_policy=None,
    business_type="New Business",
):
    return {
        "policy_number": policy_number,
        "policy_guid": f"pg-{policy_number}",
        "status": status,
        "carrier": carrier,
        "lines_of_business": lob,
        "premium_amount": premium,
        "annualized_premium": premium,
        "agency_commission_amount": agency_commission_amount,
        "effective_date": eff,
        "expiration_date": exp,
        "billing_type": billing_type,
        "agency_fee_amount": agency_fee_amount,
        "renewed_policy": renewed_policy,
        "business_type": business_type,
        "active": True,
    }


def led(
    policy_number="P1",
    *,
    carrier="Acme Mutual",
    lob="General Liability",
    client="Acme LLC",
    is_renewal=False,
    premium=1000.0,
    expected=150.0,
    rule_id="rule-both",
    basis="as_earned",
    payment_timing=None,
    billing_type="Direct Bill",
    agency_fee_amount=None,
    recon="pending",
):
    return {
        "id": f"led-{policy_number}",
        "policy_number": policy_number,
        "carrier_name": carrier,
        "lob": lob,
        "client_name": client,
        "is_renewal": is_renewal,
        "gross_premium": premium,
        "expected_commission": expected,
        "reconciliation_status": recon,
        "commission_rule_id": rule_id,
        "commission_basis": basis,
        "payment_timing": payment_timing,
        "billing_type": billing_type,
        "agency_fee_amount": agency_fee_amount,
        "policy_effective_date": "2026-03-01",
    }


def make_supa(*, policies=None, ledger=None, rules=None, profiles=None):
    return FakeSupabase(
        {
            "canonical_policies": policies if policies is not None else [],
            "commission_ledger": ledger if ledger is not None else [],
            "commission_rules": rules if rules is not None else [RULE_BOTH],
            "carrier_commission_profile": profiles if profiles is not None else [],
        }
    )


def ids(findings):
    return {f["id"] for f in findings}


# --- individual check IDs -----------------------------------------------------


def test_dq_nb1_is_renewal_disagrees_with_ams():
    supa = make_supa(
        policies=[
            cpol(
                "P-NB1",
                renewed_policy="OLD-1",
                business_type="Renewal",
                agency_commission_amount=150.0,
            )
        ],
        ledger=[led("P-NB1", is_renewal=False, expected=150.0, basis="gross")],
    )
    findings, _ = dq.scan_commission_dq(supa, today=TODAY)
    assert "DQ-NB1" in ids(findings)
    hit = next(f for f in findings if f["id"] == "DQ-NB1")
    assert hit["severity"] == "High"
    assert hit["policy_number"] == "P-NB1"


def test_dq_nb2_missing_ledger_row_in_seed_window():
    supa = make_supa(
        policies=[cpol("P-NB2", agency_commission_amount=100.0)],
        ledger=[],
    )
    findings, _ = dq.scan_commission_dq(supa, today=TODAY)
    assert "DQ-NB2" in ids(findings)
    assert next(f for f in findings if f["id"] == "DQ-NB2")["severity"] == "High"


def test_dq_nb2_respects_seed_window():
    supa = make_supa(
        policies=[cpol("P-OLD", eff="2025-06-01", agency_commission_amount=100.0)],
        ledger=[],
    )
    findings, _ = dq.scan_commission_dq(supa, today=TODAY)
    assert "DQ-NB2" not in ids(findings)


def test_dq_rate1_ams_vs_ledger_drift():
    supa = make_supa(
        policies=[cpol("P-R1", agency_commission_amount=200.0)],
        ledger=[led("P-R1", expected=100.0, basis="gross")],
    )
    findings, _ = dq.scan_commission_dq(supa, today=TODAY)
    assert "DQ-RATE1" in ids(findings)
    assert next(f for f in findings if f["id"] == "DQ-RATE1")["severity"] == "High"


def test_dq_rate2_renewal_matched_nb_only_rule():
    supa = make_supa(
        policies=[
            cpol(
                "P-R2R",
                carrier="NB Only Carrier",
                renewed_policy="OLD-9",
                business_type="Renewal",
                status="Renewed",
                agency_commission_amount=100.0,
            )
        ],
        ledger=[
            led(
                "P-R2R",
                carrier="NB Only Carrier",
                is_renewal=True,
                expected=100.0,
                rule_id="rule-nb-only",
                basis="gross",
            )
        ],
        rules=[RULE_NB_ONLY],
    )
    findings, _ = dq.scan_commission_dq(supa, today=TODAY)
    assert "DQ-RATE2" in ids(findings)
    assert next(f for f in findings if f["id"] == "DQ-RATE2")["is_renewal"] is True


def test_dq_rate2_nb_matched_renewal_only_rule():
    supa = make_supa(
        policies=[
            cpol(
                "P-R2N",
                carrier="Ren Only Carrier",
                business_type="New Business",
                agency_commission_amount=80.0,
            )
        ],
        ledger=[
            led(
                "P-R2N",
                carrier="Ren Only Carrier",
                is_renewal=False,
                expected=80.0,
                rule_id="rule-ren-only",
                basis="gross",
            )
        ],
        rules=[RULE_REN_ONLY],
    )
    findings, _ = dq.scan_commission_dq(supa, today=TODAY)
    assert "DQ-RATE2" in ids(findings)
    assert next(f for f in findings if f["id"] == "DQ-RATE2")["is_renewal"] is False


def test_dq_time1_payment_model_mismatch():
    supa = make_supa(
        policies=[cpol("P-T1", carrier="TimeCo Insurance", agency_commission_amount=100.0)],
        ledger=[
            led(
                "P-T1",
                carrier="TimeCo Insurance",
                expected=100.0,
                rule_id=None,
                basis="gross",
                payment_timing="As Earned",
            )
        ],
        rules=[],
        profiles=[PROFILE_ADVANCE],
    )
    findings, _ = dq.scan_commission_dq(supa, today=TODAY)
    assert "DQ-TIME1" in ids(findings)
    assert next(f for f in findings if f["id"] == "DQ-TIME1")["severity"] == "Med"


def test_dq_bill1_ledger_missing_billing_type():
    supa = make_supa(
        policies=[
            cpol(
                "P-B1",
                billing_type="Agency Bill",
                agency_fee_amount=25.0,
                agency_commission_amount=150.0,
            )
        ],
        ledger=[
            led(
                "P-B1",
                expected=150.0,
                billing_type=None,
                agency_fee_amount=25.0,
                basis="gross",
            )
        ],
    )
    findings, _ = dq.scan_commission_dq(supa, today=TODAY)
    assert "DQ-BILL1" in ids(findings)
    assert "DQ-BILL2" not in ids(findings)


def test_dq_bill2_agency_bill_null_fee():
    supa = make_supa(
        policies=[cpol("P-B2", billing_type="Agency Bill", agency_commission_amount=150.0)],
        ledger=[
            led(
                "P-B2",
                expected=150.0,
                billing_type="Agency Bill",
                agency_fee_amount=None,
                basis="gross",
            )
        ],
    )
    findings, _ = dq.scan_commission_dq(supa, today=TODAY)
    assert "DQ-BILL2" in ids(findings)
    assert next(f for f in findings if f["id"] == "DQ-BILL2")["severity"] == "Info"


def test_dq_blind_null_expected_commission():
    supa = make_supa(
        policies=[cpol("P-BLIND", agency_commission_amount=150.0)],
        ledger=[led("P-BLIND", expected=None, basis="gross")],
    )
    findings, _ = dq.scan_commission_dq(supa, today=TODAY)
    assert "DQ-BLIND" in ids(findings)
    assert next(f for f in findings if f["id"] == "DQ-BLIND")["severity"] == "High"


def test_chargeback_skips_dq_blind():
    supa = make_supa(
        policies=[cpol("P-CB", agency_commission_amount=0.0)],
        ledger=[led("P-CB", expected=0, recon="chargeback", basis="gross")],
    )
    findings, _ = dq.scan_commission_dq(supa, today=TODAY)
    assert "DQ-BLIND" not in ids(findings)


# --- scan / run behavior ------------------------------------------------------


def test_clean_book_zero_high_findings():
    supa = make_supa(
        policies=[cpol("P-CLEAN", agency_commission_amount=150.0)],
        ledger=[led("P-CLEAN", expected=150.0, is_renewal=False, basis="as_earned")],
    )
    findings, _ = dq.scan_commission_dq(supa, today=TODAY)
    high = [f for f in findings if f["severity"] == "High"]
    assert high == []


def test_dry_run_does_not_post():
    notifier = FakeNotifier()
    result = dq.run_commission_dq(
        supa=make_supa(
            policies=[cpol("P-CLEAN", agency_commission_amount=150.0)],
            ledger=[led("P-CLEAN", expected=150.0, basis="as_earned")],
        ),
        notifier=notifier,
        dry_run=True,
        now=NOW,
    )
    assert result.ok
    assert result.posted is False
    assert result.skipped is False
    assert notifier.calls == []


def test_idempotent_skip_without_force(tmp_path, monkeypatch):
    state = tmp_path / "commission_dq_state.json"
    monkeypatch.setenv("HERMES_COMMISSION_DQ_STATE_FILE", str(state))
    notifier = FakeNotifier()
    supa = make_supa(
        policies=[cpol("P-CLEAN", agency_commission_amount=150.0)],
        ledger=[led("P-CLEAN", expected=150.0, basis="as_earned")],
    )
    first = dq.run_commission_dq(supa=supa, notifier=notifier, force=True, now=NOW)
    second = dq.run_commission_dq(supa=supa, notifier=notifier, now=NOW)
    assert first.posted is True
    assert second.skipped is True
    assert second.posted is False
    assert len(notifier.calls) == 1


def test_limit_caps_findings():
    supa = make_supa(
        policies=[
            cpol("P-A", agency_commission_amount=200.0),
            cpol("P-B", agency_commission_amount=200.0),
            cpol("P-C", agency_commission_amount=200.0),
        ],
        ledger=[
            led("P-A", expected=100.0, basis="gross"),
            led("P-B", expected=100.0, basis="gross"),
            led("P-C", expected=100.0, basis="gross"),
        ],
    )
    findings, _ = dq.scan_commission_dq(supa, today=TODAY, limit=2)
    assert len(findings) == 2


def test_one_bad_ledger_row_does_not_abort_scan(monkeypatch):
    original = dq._check_ledger_row

    def flaky(row, **kwargs):
        if str(row.get("policy_number") or "") == "P-BAD":
            raise RuntimeError("boom")
        return original(row, **kwargs)

    monkeypatch.setattr(dq, "_check_ledger_row", flaky)
    supa = make_supa(
        policies=[
            cpol("P-BAD", agency_commission_amount=150.0),
            cpol("P-OK", agency_commission_amount=200.0),
        ],
        ledger=[
            led("P-BAD", expected=150.0, basis="gross"),
            led("P-OK", expected=100.0, basis="gross"),
        ],
    )
    findings, warnings = dq.scan_commission_dq(supa, today=TODAY)
    assert any("P-BAD" in w for w in warnings)
    assert "DQ-RATE1" in ids(findings)
    assert any(f["policy_number"] == "P-OK" for f in findings)
