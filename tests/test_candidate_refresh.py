"""Tests for the renewal candidate refresh (build, dedup, lineage, projection)."""

from __future__ import annotations

from datetime import date, timedelta

from hermes.renewals import candidate_refresh as cr

TODAY = date(2026, 7, 15)


def cpol(*, policy_number, eff, exp, status="Active", insured="ins1",
         lob="General Liability", renewed_policy="", premium=1000.0, active=None):
    return {
        "policy_guid": f"guid-{policy_number}",
        "nowcerts_insured_guid": insured,
        "policy_number": policy_number,
        "lines_of_business": lob,
        "status": status,
        "active": active if active is not None else status in ("Active", "Renewing", "Up for Renewal"),
        "effective_date": (TODAY + timedelta(days=eff)).isoformat(),
        "expiration_date": (TODAY + timedelta(days=exp)).isoformat(),
        "premium_amount": premium,
        "annualized_premium": premium,
        "renewed_policy": renewed_policy,
    }


ACTIVE_INS = {"ins1": {"active": True, "name": "Acme LLC"}}


def build(policies, insured=None):
    return cr.build_candidates(policies, insured or ACTIVE_INS, today=TODAY, now_iso="2026-07-15T00:00:00Z")


# --- build_candidates ---------------------------------------------------------
def test_current_term_yields_one_eligible():
    rows = build([cpol(policy_number="C1", eff=-100, exp=60)])
    assert len(rows) == 1
    r = rows[0]
    assert r["eligibility_state"] == "eligible"
    assert r["branch"] == "current_term"
    assert r["client_name"] == "Acme LLC"
    assert r["risk_status"] in ("SAFE", "AT_RISK", "CRITICAL")
    assert r["insured_active"] is True


def test_staged_and_predecessor_dedup_to_one_event():
    p0 = cpol(policy_number="P0", eff=-300, exp=65)                       # current term
    p1 = cpol(policy_number="P1", eff=65, exp=430, status="Renewing", renewed_policy="P0")  # staged successor
    rows = build([p0, p1])
    eligible = [r for r in rows if r["eligibility_state"] == "eligible"]
    assert len(eligible) == 1
    assert eligible[0]["branch"] == "staged_next_term"
    assert eligible[0]["policy_number"] == "P1"
    assert eligible[0]["predecessor_policy_number"] == "P0"
    # both policies collapse to a single renewal-event identity
    assert len({(r["insured_id"], r["policy_lineage_id"], r["renewal_event_date"]) for r in rows}) == 1


def test_dead_policy_excluded():
    rows = build([cpol(policy_number="D1", eff=-400, exp=-30, status="Cancelled")])
    assert rows[0]["eligibility_state"] == "excluded"
    assert "Cancelled" in rows[0]["eligibility_reason"]


def test_inactive_insured_excluded():
    rows = cr.build_candidates(
        [cpol(policy_number="I1", eff=-100, exp=60, insured="ins2")],
        {"ins2": {"active": False, "name": "Dormant Co"}},
        today=TODAY, now_iso="2026-07-15T00:00:00Z",
    )
    assert rows[0]["eligibility_state"] == "excluded"
    assert "insured" in rows[0]["eligibility_reason"]


def test_medicare_mapd_eligible_annual():
    rows = build([cpol(policy_number="M1", eff=-200, exp=100, lob="MAPD")])
    r = rows[0]
    assert r["eligibility_state"] == "eligible"
    assert r["segment"] == "medicare"
    assert r["renewal_event_date"] == "2026-08-01"


# --- run_refresh --------------------------------------------------------------
class FakeSupa:
    def __init__(self, policies):
        self.policies = policies
        self.upserts: list[tuple[str, dict]] = []
        self.updates: list[tuple] = []
        self.deletes: list[str] = []
        self.p85: list[dict] = []

    def select(self, table, *, columns="*", params=None, limit=100):
        if table == "canonical_policies":
            return list(self.policies)
        if table == "renewal_actions":
            return []
        if table == "project_85_renewals":
            return list(self.p85)
        return []

    def upsert(self, table, payload, *, on_conflict="id"):
        self.upserts.append((table, payload))
        return {"id": f"{table}-id", **payload}

    def update(self, table, rid, payload):
        self.updates.append((table, rid, payload))
        return {"id": rid, **payload}

    def update_where(self, table, payload, *, filters):
        self.updates.append((table, filters, payload))
        return [payload]

    def delete(self, table, rid):
        self.deletes.append(rid)


class FakeNowCerts:
    def __init__(self, insured):
        self._insured = insured

    def fetch_insureds(self):
        return [{"id": g, "active": v["active"], "commercialName": v.get("name")}
                for g, v in self._insured.items()]


def test_run_refresh_dry_run_is_side_effect_free():
    supa = FakeSupa([cpol(policy_number="C1", eff=-100, exp=60),
                     cpol(policy_number="D1", eff=-400, exp=-30, status="Cancelled")])
    nc = FakeNowCerts(ACTIVE_INS)
    summary = cr.run_refresh(supa=supa, nowcerts=nc, today=TODAY, dry_run=True)
    assert summary["dry_run"] is True
    assert summary["eligible"] == 1
    assert summary["excluded"] == 1
    assert supa.upserts == []  # nothing written
    assert "sample_eligible" in summary


def test_run_refresh_live_upserts_and_projects():
    supa = FakeSupa([cpol(policy_number="C1", eff=-100, exp=60)])
    nc = FakeNowCerts(ACTIVE_INS)
    summary = cr.run_refresh(supa=supa, nowcerts=nc, today=TODAY, dry_run=False)
    assert summary["eligible"] == 1
    tables = [t for t, _ in supa.upserts]
    assert "renewal_candidates" in tables
    assert "project_85_renewals" in tables  # eligible projected
    assert summary["projected"] == 1
