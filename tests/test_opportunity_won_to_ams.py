"""Won deals reach the AMS; lost deals never do; the type comes from the id."""
from __future__ import annotations

import pytest

from hermes.intake import opportunities as opp
from hermes.sync import opportunity_won as W


class FakeSupa:
    def __init__(self, rows=None, *, boom=False):
        self.rows = rows or {}
        self.boom = boom
        self.inserted: list[tuple[str, dict]] = []
        self.updated: list[tuple[str, str, dict]] = []

    def select(self, table, *, columns=None, params=None, limit=None):
        if self.boom:
            raise RuntimeError("supabase down")
        return list(self.rows.get(table, []))

    def insert(self, table, payload):
        self.inserted.append((table, payload))
        return {"id": "queue-1", **payload}

    def update(self, table, row_id, payload):
        self.updated.append((table, row_id, payload))
        return {"id": row_id, **payload}

    def update_where(self, table, payload, *, filters=None):
        self.updated.append((table, str(filters), payload))
        return [{"id": "queue-1"}]


class FakeNC:
    def __init__(self, *, insured_response=None, policy_response=None, fail_policy=False):
        self.insured_response = insured_response or {"databaseId": "new-guid"}
        self.policy_response = policy_response or {"databaseId": "policy-guid"}
        self.fail_policy = fail_policy
        self.created_insureds: list[dict] = []
        self.policies: list[dict] = []

    def create_insured(self, payload):
        self.created_insureds.append(payload)
        return self.insured_response

    def insert_policy(self, payload):
        if self.fail_policy:
            raise RuntimeError("NowCerts rejected the policy")
        self.policies.append(payload)
        return self.policy_response


def _won(**kw):
    base = {"id": "opp-1", "status": "won", "insured_name": "Acme Holdings",
            "policy_number": "BOP-1001", "line_of_business": "BOP", "carrier": "Travelers",
            "premium_estimate": 9100, "effective_date": "2026-08-01", "expiration_date": "2027-08-01"}
    base.update(kw)
    return base


# --- what may be pushed -------------------------------------------------------
def test_a_won_deal_with_a_policy_number_is_pushable():
    W.check_pushable(_won())          # does not raise


def test_a_lost_deal_is_never_pushed():
    """It was never coverage. It stays here as next year's remarket list."""
    with pytest.raises(W.NotPushable, match="lost deal is never pushed"):
        W.check_pushable(_won(status="lost"))


def test_an_open_deal_is_not_pushed():
    with pytest.raises(W.NotPushable):
        W.check_pushable(_won(status="open"))


def test_a_won_deal_without_a_policy_number_is_refused_with_something_actionable():
    """Inventing a policy number puts junk in the system of record."""
    with pytest.raises(W.NotPushable, match="policy number"):
        W.check_pushable(_won(policy_number=None))


def test_a_deal_already_in_the_ams_is_not_pushed_twice():
    with pytest.raises(W.NotPushable, match="already in NowCerts"):
        W.check_pushable(_won(nowcerts_policy_guid="policy-guid"))


def test_staging_queues_an_approved_job():
    supa = FakeSupa()
    W.stage_won(supa, _won(), approved_by="lamar@risksolutionsgroup.net")
    table, payload = supa.inserted[0]
    assert payload["object_type"] == "opportunity_won"
    assert payload["approved_by"] == "lamar@risksolutionsgroup.net"
    assert payload["payload"]["policy_number"] == "BOP-1001"


def test_staging_a_lost_deal_raises_rather_than_queueing():
    supa = FakeSupa()
    with pytest.raises(W.NotPushable):
        W.stage_won(supa, _won(status="lost"), approved_by="lamar@risksolutionsgroup.net")
    assert supa.inserted == []


# --- the executor -------------------------------------------------------------
def _job(**kw):
    payload = {"opportunity_id": "opp-1", "insured_name": "Acme Holdings",
               "policy_number": "BOP-1001", "line_of_business": "BOP",
               "carrier": "Travelers", "premium": 9100,
               "effective_date": "2026-08-01", "expiration_date": "2027-08-01"}
    payload.update(kw)
    return {"id": "queue-1", "payload": payload}


def test_an_existing_client_gets_a_policy_and_no_new_insured():
    """They already have an id — that is what makes them a client."""
    supa = FakeSupa({"outbound_sync_queue": [_job(insured_id="known-guid")]})
    nc = FakeNC()
    out = W.run_opportunity_won_executor(supa=supa, nowcerts=nc, limit=1)
    assert out["completed"] == 1
    assert nc.created_insureds == []
    assert nc.policies[0]["InsuredDatabaseId"] == "known-guid"
    assert nc.policies[0]["Number"] == "BOP-1001"


def test_a_converted_lead_gets_an_insured_created_first():
    supa = FakeSupa({"outbound_sync_queue": [_job()]})
    nc = FakeNC()
    W.run_opportunity_won_executor(supa=supa, nowcerts=nc, limit=1)
    assert nc.created_insureds[0]["CommercialName"] == "Acme Holdings"
    assert nc.policies[0]["InsuredDatabaseId"] == "new-guid"


def test_a_won_deal_is_filed_as_coverage_not_a_quote():
    """IsQuote=True files it as a quote and it never counts in the book."""
    supa = FakeSupa({"outbound_sync_queue": [_job(insured_id="g1")]})
    nc = FakeNC()
    W.run_opportunity_won_executor(supa=supa, nowcerts=nc, limit=1)
    assert nc.policies[0]["IsQuote"] is False


def test_a_personal_lead_is_created_as_a_person():
    supa = FakeSupa({"outbound_sync_queue": [_job(insured_name="Jane Roe", insured_type="Personal")]})
    nc = FakeNC()
    W.run_opportunity_won_executor(supa=supa, nowcerts=nc, limit=1)
    assert nc.created_insureds[0] == {"FirstName": "Jane", "LastName": "Roe"}


def test_the_deal_is_linked_to_what_it_became():
    """Without the insured id written back, a converted lead is not cross-sellable."""
    supa = FakeSupa({"outbound_sync_queue": [_job()]})
    W.run_opportunity_won_executor(supa=supa, nowcerts=FakeNC(), limit=1)
    linked = [u for u in supa.updated if u[0] == "opportunities"][0][2]
    assert linked["insured_id"] == "new-guid"
    assert linked["nowcerts_policy_guid"] == "policy-guid"
    assert linked["ams_pushed_at"]


def test_a_failed_push_is_recorded_not_swallowed():
    supa = FakeSupa({"outbound_sync_queue": [_job(insured_id="g1")]})
    out = W.run_opportunity_won_executor(supa=supa, nowcerts=FakeNC(fail_policy=True), limit=1)
    assert out["failed"] == 1
    failed = [u for u in supa.updated if u[0] == "outbound_sync_queue"][-1][2]
    assert failed["status"] == "failed"
    assert "rejected the policy" in failed["last_error"]
    # The deal must NOT claim to be in the AMS when it is not.
    assert not [u for u in supa.updated if u[0] == "opportunities"]


def test_dry_run_writes_nothing():
    supa = FakeSupa({"outbound_sync_queue": [_job()]})
    nc = FakeNC()
    out = W.run_opportunity_won_executor(supa=supa, nowcerts=nc, limit=1, dry_run=True)
    assert out["completed"] == 0
    assert nc.created_insureds == [] and nc.policies == []
    assert out["previews"][0]["creates_insured"] is True


# --- the type comes from the id ----------------------------------------------
def test_no_insured_id_is_new_business():
    assert opp.derive_opportunity_type(FakeSupa(), None, "BOP") == opp.TYPE_NEW_BUSINESS
    assert opp.derive_opportunity_type(FakeSupa(), "  ", "BOP") == opp.TYPE_NEW_BUSINESS


def test_a_client_without_this_line_is_a_cross_sell():
    supa = FakeSupa({"canonical_policies": [
        {"lines_of_business": "Commercial Auto", "active": True},
    ]})
    assert opp.derive_opportunity_type(supa, "g1", "BOP") == opp.TYPE_CROSS_SELL


def test_a_client_who_already_has_this_line_is_an_upsell():
    supa = FakeSupa({"canonical_policies": [
        {"lines_of_business": "BOP", "active": True},
    ]})
    assert opp.derive_opportunity_type(supa, "g1", "bop") == opp.TYPE_UPSELL


def test_a_lapsed_line_is_a_cross_sell_not_an_upsell():
    """An inactive policy is not something to sell more OF."""
    supa = FakeSupa({"canonical_policies": [{"lines_of_business": "BOP", "active": False}]})
    assert opp.derive_opportunity_type(supa, "g1", "BOP") == opp.TYPE_CROSS_SELL


def test_an_unreadable_book_never_downgrades_a_client_to_new_business():
    """The id already proves they are a client — that is the part that matters."""
    assert opp.derive_opportunity_type(FakeSupa(boom=True), "g1", "BOP") == opp.TYPE_CROSS_SELL
