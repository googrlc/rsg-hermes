"""Tests for the commission ingest (hermes/commissions).

Covers the build-spec's required cases: rule-lookup precedence, no-rule path,
idempotent re-run, purge-tag exclusion, and watermark advance — plus the unit
pieces (field extraction, expected math, dry-run). No live NowCerts/Supabase.
"""

from __future__ import annotations

import json

import pytest

from hermes.commissions import config, mapping, state
from hermes.commissions.rules import compute_expected, find_rule
from hermes.commissions.sweep import run


# --- Fixtures & fakes ------------------------------------------------------

RULES = [
    # AmTrust WC — ALL states, priority 2 (LOWER number)
    {
        "id": "r-amtrust-all", "carrier_name": "AmTrust", "lob": "Workers Comp",
        "state": "ALL", "nb_percent": 16.0, "renewal_percent": 10.0, "flat_fee": None,
        "commission_method": "% of Premium", "commission_basis": "as_earned",
        "lookup_priority": 2, "revenue_split_percent": 100.0,
    },
    # AmTrust WC — GA-specific, priority 3 (HIGHER number). Exact-state must still win.
    {
        "id": "r-amtrust-ga", "carrier_name": "AmTrust", "lob": "Workers Comp",
        "state": "GA", "nb_percent": 18.0, "renewal_percent": 12.0, "flat_fee": None,
        "commission_method": "% of Premium", "commission_basis": "as_earned",
        "lookup_priority": 3, "revenue_split_percent": 100.0,
    },
    # Attune BOP — paid in advance
    {
        "id": "r-attune-bop", "carrier_name": "Attune", "lob": "BOP", "state": "ALL",
        "nb_percent": 15.0, "renewal_percent": 15.0, "flat_fee": None,
        "commission_method": "% of Premium", "commission_basis": "advance",
        "lookup_priority": 1, "revenue_split_percent": 100.0,
    },
]


class FakeSupabase:
    def __init__(self, rules=RULES, existing_ids=()):
        self._rules = list(rules)
        self._existing = [{"nowcerts_policy_id": i} for i in existing_ids]
        self.upserts: list[dict] = []

    def select(self, table, *, columns="*", params=None, limit=100):
        if table == config.RULES_TABLE:
            return list(self._rules)
        if table == config.LEDGER_TABLE:
            return list(self._existing)
        return []

    def upsert(self, table, payload, *, on_conflict="id"):
        self.upserts.append(payload)
        return payload


class FakeNowCerts:
    def __init__(self, policies):
        self._policies = list(policies)
        self.since_called_with = "UNSET"

    def fetch_policies(self, *, page_size=100, since=None, max_pages=1000):
        self.since_called_with = since
        return list(self._policies)


class FakeSlack:
    def __init__(self):
        self.posts: list[str] = []

    def post_message(self, *, text, blocks=None):
        self.posts.append(text)
        return {"ok": True}


def _policy(**over):
    base = {
        "DatabaseId": "nc-1", "Number": "WC-001", "CarrierName": "AmTrust",
        "LineOfBusiness": "Workers Comp", "StateCode": "GA", "PremiumAmount": 10000,
        "EffectiveDate": "2026-03-01", "InsuredCommercialName": "Acme LLC",
        "BusinessType": "New", "ChangeDate": "2026-06-01T00:00:00Z",
    }
    base.update(over)
    return base


@pytest.fixture
def tmp_watermark(tmp_path, monkeypatch):
    wm = tmp_path / "wm.json"
    monkeypatch.setattr(config, "WATERMARK_FILE", str(wm))
    return wm


# --- Rule lookup precedence ------------------------------------------------

def test_find_rule_exact_state_beats_all_even_with_higher_priority():
    r = find_rule(RULES, carrier="AmTrust", lob="Workers Comp", state="GA")
    assert r["id"] == "r-amtrust-ga"  # GA (prio 3) beats ALL (prio 2)


def test_find_rule_falls_back_to_all_when_no_specific_state():
    r = find_rule(RULES, carrier="AmTrust", lob="Workers Comp", state="FL")
    assert r["id"] == "r-amtrust-all"


def test_find_rule_is_case_insensitive_on_carrier_and_lob():
    r = find_rule(RULES, carrier="  amtrust ", lob="workers comp", state="ga")
    assert r["id"] == "r-amtrust-ga"


def test_find_rule_no_match_returns_none():
    assert find_rule(RULES, carrier="Nonexistent", lob="Workers Comp", state="GA") is None


# --- Expected-commission math ----------------------------------------------

def test_compute_expected_new_vs_renewal():
    ga = find_rule(RULES, carrier="AmTrust", lob="Workers Comp", state="GA")
    assert compute_expected(ga, gross_premium=10000, is_renewal=False) == 1800.0
    assert compute_expected(ga, gross_premium=10000, is_renewal=True) == 1200.0


def test_compute_expected_none_without_premium():
    ga = find_rule(RULES, carrier="AmTrust", lob="Workers Comp", state="GA")
    assert compute_expected(ga, gross_premium=None, is_renewal=False) is None


# --- Field extraction / mapping --------------------------------------------

def test_extract_fields_handles_camelcase_and_commas():
    fields = mapping.extract_fields(
        {
            "databaseId": "nc-9", "policyNumber": "P9", "carrierName": "Attune",
            "lineOfBusiness": "BOP", "state": "TX", "premium": "1,250.50",
            "effectiveDate": "05/01/2026", "insuredCommercialName": "Zeta Co",
            "isRenewal": True,
        }
    )
    assert fields["nowcerts_policy_id"] == "nc-9"
    assert fields["carrier"] == "Attune"
    assert fields["gross_premium"] == 1250.50
    assert fields["effective_date"] == "2026-05-01"
    assert fields["is_renewal"] is True


def test_extract_client_name_from_bare_insured_key():
    # NowCerts' normalized feed exposes the client under a bare "insured" key.
    fields = mapping.extract_fields({"DatabaseId": "nc-x", "insured": "Southeast Transport"})
    assert fields["client_name"] == "Southeast Transport"


def test_build_ledger_row_matched_sets_expected_and_status():
    fields = mapping.extract_fields(_policy())
    rule = find_rule(RULES, carrier="AmTrust", lob="Workers Comp", state="GA")
    row = mapping.build_ledger_row(fields, rule, 1800.0)
    assert row["reconciliation_status"] == config.STATUS_PENDING
    assert row["expected_commission"] == 1800.0
    assert row["commission_rule_id"] == "r-amtrust-ga"
    assert row["rsg_net_commission"] == 1800.0
    assert row["nowcerts_policy_id"] == "nc-1"


def test_build_ledger_row_unmatched_is_needs_rule_with_null_expected():
    fields = mapping.extract_fields(_policy(CarrierName="Mystery Co"))
    row = mapping.build_ledger_row(fields, None, None)
    assert row["reconciliation_status"] == config.STATUS_NEEDS_RULE
    assert row["expected_commission"] is None
    assert row["commission_rule_id"] is None


def test_build_ledger_row_skips_when_no_id_or_no_date():
    assert mapping.build_ledger_row({"policy_number": "X"}, None, None) is None
    no_date = mapping.extract_fields(_policy(EffectiveDate="", ChangeDate=""))
    assert mapping.build_ledger_row(no_date, None, None) is None


# --- Purge exclusion -------------------------------------------------------

def test_is_purged_detects_tag_in_list_and_notes():
    assert mapping.is_purged({"Tags": ["PURGE-POLICY-2026-07"]}) is True
    assert mapping.is_purged({"Notes": "flagged PURGE-POLICY-2026-07 pending delete"}) is True
    assert mapping.is_purged({"Tags": ["keep"]}) is False


# --- Full run() integration ------------------------------------------------

def test_run_computes_and_upserts_matched_policy(tmp_watermark):
    supa = FakeSupabase()
    nc = FakeNowCerts([_policy()])
    res = run(nc, supa, notifier=FakeSlack(), now_iso="2026-07-06T12:00:00Z")
    assert res.ok and res.inserted == 1 and res.updated == 0
    assert len(supa.upserts) == 1
    payload = supa.upserts[0]
    assert payload["expected_commission"] == 1800.0
    assert payload["commission_rule_id"] == "r-amtrust-ga"
    assert payload["reconciliation_status"] == config.STATUS_PENDING


def test_run_needs_rule_for_unmatched_carrier(tmp_watermark):
    supa = FakeSupabase()
    nc = FakeNowCerts([_policy(DatabaseId="nc-2", CarrierName="Unknown Co")])
    res = run(nc, supa, notifier=FakeSlack(), now_iso="2026-07-06T12:00:00Z")
    assert res.needs_rule == 1 and res.inserted == 1
    assert supa.upserts[0]["reconciliation_status"] == config.STATUS_NEEDS_RULE
    assert supa.upserts[0]["expected_commission"] is None


def test_run_excludes_purge_tagged_policies(tmp_watermark):
    supa = FakeSupabase()
    nc = FakeNowCerts([_policy(DatabaseId="nc-3", Tags=["PURGE-POLICY-2026-07"])])
    res = run(nc, supa, notifier=FakeSlack(), now_iso="2026-07-06T12:00:00Z")
    assert res.purged_skipped == 1
    assert supa.upserts == []


def test_run_is_idempotent_existing_counts_as_update(tmp_watermark):
    supa = FakeSupabase(existing_ids=["nc-1"])
    nc = FakeNowCerts([_policy()])
    res1 = run(nc, supa, notifier=FakeSlack(), now_iso="2026-07-06T12:00:00Z")
    payload1 = dict(supa.upserts[0])
    res2 = run(nc, supa, notifier=FakeSlack(), now_iso="2026-07-06T12:05:00Z")
    payload2 = dict(supa.upserts[1])
    assert res1.updated == 1 and res1.inserted == 0
    assert res2.updated == 1 and res2.inserted == 0
    # identical payload on re-run (deterministic; statement_date not "today")
    assert payload1 == payload2


def test_run_advances_watermark_and_reads_it_back(tmp_watermark):
    supa = FakeSupabase()
    nc = FakeNowCerts([_policy()])
    run(nc, supa, notifier=FakeSlack(), now_iso="2026-07-06T12:00:00Z")
    assert json.loads(tmp_watermark.read_text())["last_synced_at"] == "2026-07-06T12:00:00Z"
    # a subsequent run passes the stored watermark as the NowCerts `since`
    nc2 = FakeNowCerts([])
    run(nc2, supa, notifier=FakeSlack(), now_iso="2026-07-07T12:00:00Z")
    assert nc2.since_called_with == "2026-07-06T12:00:00Z"


def test_run_uses_default_since_when_no_watermark(tmp_watermark):
    supa = FakeSupabase()
    nc = FakeNowCerts([])
    run(nc, supa, notifier=FakeSlack(), now_iso="2026-07-06T12:00:00Z")
    assert nc.since_called_with == config.DEFAULT_SINCE


def test_run_full_backfill_ignores_watermark(tmp_watermark):
    tmp_watermark.write_text(json.dumps({"last_synced_at": "2026-05-01T00:00:00Z"}))
    supa = FakeSupabase()
    nc = FakeNowCerts([_policy()])
    run(nc, supa, notifier=FakeSlack(), full=True, now_iso="2026-07-06T12:00:00Z")
    assert nc.since_called_with is None  # full book


def test_run_dry_run_writes_nothing_and_posts_nothing(tmp_watermark):
    supa = FakeSupabase()
    slack = FakeSlack()
    nc = FakeNowCerts([_policy()])
    res = run(nc, supa, notifier=slack, dry_run=True, now_iso="2026-07-06T12:00:00Z")
    assert res.inserted == 1  # would-be
    assert supa.upserts == []
    assert slack.posts == []
    assert not tmp_watermark.exists()  # watermark not advanced on dry-run


def test_run_posts_one_line_slack_summary(tmp_watermark):
    supa = FakeSupabase()
    slack = FakeSlack()
    nc = FakeNowCerts([_policy()])
    run(nc, supa, notifier=slack, now_iso="2026-07-06T12:00:00Z")
    assert len(slack.posts) == 1
    assert "Commission ingest" in slack.posts[0]
    assert "1 new" in slack.posts[0]
