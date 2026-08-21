"""Zoho Renewal_Events / Renewals / Deals mapping + Creator override capture."""

from __future__ import annotations

import re
from datetime import date
from unittest.mock import MagicMock

from hermes.sync.zoho_renewals import (
    DEAL_STAGE_30,
    DEAL_STAGE_90,
    DEAL_STAGE_LOST,
    DEAL_STAGE_REQUOTE,
    DEAL_STAGE_WON,
    creator_override_diffs,
    deal_window_stage,
    map_candidate_to_renewal_event,
    map_deal_to_renewal,
    map_projection_to_renewal,
    map_renewal_to_deal,
    resolve_deal_stage,
    run_zoho_renewals_sync,
)
from hermes.renewals.desk import has_pipeline_deal, linked_deal_id

TODAY = date(2026, 8, 18)

CANDIDATE = {
    "id": "cand-1",
    "insured_id": "guid-ins",
    "policy_lineage_id": "guid-ins:gl:CPP1",
    "renewal_event_date": "2026-11-10",
    "nowcerts_policy_guid": "guid-pol",
    "policy_number": "CPP1",
    "insured_active": True,
    "policy_active": True,
    "normalized_status": "Active",
    "branch": "current_term",
    "effective_date": "2025-11-10",
    "expiration_date": "2026-11-10",
    "eligibility_state": "eligible",
    "eligibility_reason": "current term in window",
    "segment": "commercial_mid",
    "line_of_business": "General Liability",
    "client_name": "Acme LLC",
    "in_working_queue": True,
    "risk_status": "SAFE",
    "premium_current": 4200,
    "premium_renewal": None,
}

P85 = {
    "id": "ren-1",
    "policy_number": "CPP1",
    "client_name": "Acme LLC",
    "expiration_date": "2026-11-10",
    "premium_current": 4200,
    "premium_renewal": None,
    "risk_status": "SAFE",
    "ai_strategy_notes": "keep",
    "last_contact_date": None,
}

POLICY = {
    "policy_number": "CPP1",
    "policy_guid": "guid-pol",
    "nowcerts_insured_guid": "guid-ins",
    "carrier": "Travelers",
    "lines_of_business": "General Liability",
    "effective_date": "2025-11-10",
}

_EQ = re.compile(r"\((\w+):equals:([^)]*)\)")


def _parse_equals(criteria: str) -> dict[str, str]:
    return {m.group(1): m.group(2) for m in _EQ.finditer(criteria or "")}


def _field(record: dict, name: str) -> str:
    val = record.get(name)
    if isinstance(val, dict):
        return str(val.get("id") or val.get("name") or "")
    if val is None:
        return ""
    return str(val)


def _supa(*, p85=None, candidates=None, policies=None):
    tables = {
        "renewal_candidates": candidates if candidates is not None else [dict(CANDIDATE)],
        "project_85_renewals": p85 if p85 is not None else [dict(P85)],
        "canonical_policies": policies if policies is not None else [dict(POLICY)],
        "portal_overrides": [],
    }
    supa = MagicMock()
    supa.select.side_effect = lambda table, **k: tables[table]
    return supa


def test_map_candidate_sets_external_id_and_key():
    payload = map_candidate_to_renewal_event(CANDIDATE, account_id="A1", policy_id="P1")
    assert payload["Hermes_Candidate_ID"] == "cand-1"
    assert payload["Renewal_Key"] == "guid-ins|guid-ins:gl:CPP1|2026-11-10"
    assert payload["Eligibility"] == "eligible"
    assert payload["Account_Name"] == {"id": "A1"}
    assert payload["Policy"] == {"id": "P1"}
    assert payload["Name"].startswith("CPP1")


def test_map_projection_create_sets_identified_and_window():
    payload = map_projection_to_renewal(
        P85,
        policy={"carrier": "Travelers", "lines_of_business": "General Liability"},
        creating=True,
        today=TODAY,
    )
    assert payload["Desk_Stage"] == "Identified"
    assert payload["Stage"] == "Identified"
    assert payload["Window_Bucket"] == "90"
    assert payload["Carrier"] == "Travelers"
    assert payload["Hermes_Renewal_ID"] == "ren-1"


def test_map_projection_update_omits_desk_owned_fields():
    payload = map_projection_to_renewal(P85, creating=False, today=TODAY)
    assert "Desk_Stage" not in payload
    assert "Stage" not in payload
    assert "Disposition" not in payload
    assert "Recommended_Action" not in payload
    assert "Touch_Early" not in payload
    assert payload["Policy_Number"] == "CPP1"
    assert payload["Window_Bucket"] == "90"


def test_creator_premium_edit_becomes_an_override():
    diffs = creator_override_diffs(
        P85,
        {"Policy_Number": "CPP1", "Premium_Current": 4500, "Dismissed": False},
    )
    assert len(diffs) == 1
    assert diffs[0]["field_name"] == "premium_current"
    assert diffs[0]["override_value"] == 4500
    assert diffs[0]["original_value"] == 4200
    assert diffs[0]["entity_key"] == "CPP1"
    assert diffs[0]["approved_by"] == "zoho_creator"


def test_matching_active_override_is_not_recaptured():
    diffs = creator_override_diffs(
        P85,
        {"Policy_Number": "CPP1", "Premium_Current": 4500},
        active_overrides={("CPP1", "premium_current"): 4500},
    )
    assert diffs == []


def test_dismissed_on_zoho_is_captured():
    diffs = creator_override_diffs(
        P85,
        {"Policy_Number": "CPP1", "Dismissed": True},
    )
    assert any(d["field_name"] == "dismissed" and d["override_value"] is True for d in diffs)


def test_empty_zoho_field_is_not_a_creator_edit():
    diffs = creator_override_diffs(
        P85,
        {"Policy_Number": "CPP1", "Strategy_Notes": None},
    )
    assert diffs == []


def test_deal_window_stage_follows_calendar_not_personal_bucket():
    assert deal_window_stage("2026-11-10", today=TODAY) == DEAL_STAGE_90
    assert deal_window_stage("2026-09-10", today=TODAY) == DEAL_STAGE_30
    assert deal_window_stage("2026-08-01", today=TODAY) == DEAL_STAGE_30


def test_resolve_deal_stage_terminal_and_protected():
    assert resolve_deal_stage(dismissed=True, expiration="2026-11-10", today=TODAY) == DEAL_STAGE_LOST
    assert resolve_deal_stage(
        desk_stage="Closed",
        disposition="renewed",
        expiration="2026-11-10",
        today=TODAY,
    ) == DEAL_STAGE_WON
    assert resolve_deal_stage(
        existing_stage=DEAL_STAGE_REQUOTE,
        expiration="2026-11-10",
        today=TODAY,
    ) == DEAL_STAGE_REQUOTE
    assert resolve_deal_stage(
        recommended_action="REMARKET_FULL",
        expiration="2026-11-10",
        today=TODAY,
    ) == DEAL_STAGE_REQUOTE


def test_map_renewal_to_deal_sets_pipeline_type_and_window_stage():
    payload = map_renewal_to_deal(
        P85,
        policy=POLICY,
        account_id="A1",
        creating=True,
        today=TODAY,
    )
    assert payload["Opportunity_Type"] == "Renewals"
    assert payload["Hermes_Opportunity_ID"] == "ren-1"
    assert payload["Bound_Policy_Number"] == "CPP1"
    assert payload["Stage"] == DEAL_STAGE_90
    assert payload["Deal_Status"] == "open"
    assert payload["Account_Name"] == {"id": "A1"}
    assert "Pipeline" not in payload  # no ZOHO_RENEWALS_PIPELINE_ID in tests


def test_map_deal_to_renewal_links_related_deal():
    deal = {
        "id": "z-deal-9",
        "Hermes_Opportunity_ID": "",
        "Bound_Policy_Number": "HO-88",
        "Insured_Name": "James Wilson",
        "Line_of_Business": "Homeowners",
        "Expiration_Date": "2026-09-18",
        "Stage": DEAL_STAGE_30,
        "Amount": 1800,
        "Carrier": "Safeco",
    }
    payload = map_deal_to_renewal(deal, creating=True, today=TODAY)
    assert payload["Policy_Number"] == "HO-88"
    assert payload["Related_Deal"] == {"id": "z-deal-9"}
    assert payload["Deal_Id"] == {"id": "z-deal-9"}
    assert payload["Desk_Stage"] == "Identified"
    assert payload["Stage"] == "Identified"
    assert payload["Dismissed"] is False
    assert payload["Hermes_Renewal_ID"]
    lost = map_deal_to_renewal({**deal, "Stage": DEAL_STAGE_LOST}, creating=True, today=TODAY)
    assert lost["Dismissed"] is True
    assert lost["Desk_Stage"] == "Closed"


class FakeZoho:
    def __init__(self):
        self.upserts = []
        self.updates = []
        self.existing = {}
        self.records = {}

    def seed(self, module: str, record: dict, *index_keys: str) -> dict:
        rec = dict(record)
        rid = str(rec.get("id") or f"{module}-{len(self.records) + 1}")
        rec["id"] = rid
        self.records[rid] = (module, rec)
        for key in index_keys:
            if key:
                self.existing[(module, key)] = rec
        return rec

    def _all(self, module: str) -> list[dict]:
        out = []
        seen = set()
        for (mod, _key), rec in self.existing.items():
            rid = str(rec.get("id") or "")
            if mod == module and rid and rid not in seen:
                out.append(rec)
                seen.add(rid)
        for rid, (mod, rec) in self.records.items():
            if mod == module and rid not in seen:
                out.append(rec)
                seen.add(rid)
        return out

    def search_records(self, module, criteria):
        equals = _parse_equals(criteria)
        if not equals:
            return []
        hits = []
        for rec in self._all(module):
            if all(_field(rec, k) == v for k, v in equals.items()):
                hits.append(rec)
        return hits

    def _find_first(self, module, criteria):
        equals = _parse_equals(criteria)
        if len(equals) == 1:
            value = next(iter(equals.values()))
            direct = self.existing.get((module, value))
            if direct:
                return direct
        hits = self.search_records(module, criteria)
        return hits[0] if hits else None

    def iter_records(self, module, *, criteria=None, per_page=200, max_pages=50, fields=None):
        if criteria:
            yield from self.search_records(module, criteria)
        else:
            yield from self._all(module)

    def get_record(self, module, record_id):
        for rec in self._all(module):
            if str(rec.get("id")) == str(record_id):
                return rec
        return None

    def upsert_by_field(self, module, record, *, match_field, match_value=None):
        value = match_value if match_value is not None else record.get(match_field)
        self.upserts.append((module, match_field, value, record))
        existing = self.existing.get((module, value))
        if existing is None:
            for rec in self._all(module):
                if _field(rec, match_field) == str(value):
                    existing = rec
                    break
        action = "updated" if existing else "created"
        rid = str((existing or {}).get("id") or f"{module}-{value}")
        merged = {**(existing or {}), **record, "id": rid}
        self.existing[(module, value)] = merged
        self.records[rid] = (module, merged)
        return {"id": rid, "action": action}

    def update_record(self, module, record_id, record):
        self.updates.append((module, record_id, record))
        existing = self.get_record(module, record_id) or {"id": record_id}
        merged = {**existing, **record, "id": str(record_id)}
        self.records[str(record_id)] = (module, merged)
        for key, rec in list(self.existing.items()):
            if key[0] == module and str(rec.get("id")) == str(record_id):
                self.existing[key] = merged
        return {"id": str(record_id), "action": "updated"}


def _renewal_writes(zoho: FakeZoho) -> list[dict]:
    from_upsert = [rec for mod, _f, _v, rec in zoho.upserts if mod == "Renewals"]
    from_update = [rec for mod, _rid, rec in zoho.updates if mod == "Renewals"]
    return from_upsert + from_update


def test_run_sync_upserts_event_then_renewal_and_captures_override():
    p85 = dict(P85)
    zoho = FakeZoho()
    zoho.seed(
        "Renewals",
        {
            "id": "z-ren",
            "Hermes_Renewal_ID": "ren-1",
            "Policy_Number": "CPP1",
            "Premium_Current": 4500,
            "Dismissed": False,
        },
        "ren-1",
    )
    supa = _supa(p85=[p85])
    result = run_zoho_renewals_sync(supa=supa, zoho=zoho, today=TODAY, dry_run=False)
    assert result.ok
    assert result.events_created == 1
    assert result.renewals_updated == 1
    assert result.deals_created == 1
    assert result.overrides_captured == 1
    assert any(c.args[0] == "portal_overrides" for c in supa.insert.call_args_list)
    event_upsert = next(u for u in zoho.upserts if u[0] == "Renewal_Events")
    assert event_upsert[3]["Hermes_Candidate_ID"] == "cand-1"
    renewal_write = next(rec for rec in _renewal_writes(zoho) if rec.get("Policy_Number") == "CPP1")
    assert "Desk_Stage" not in renewal_write
    assert renewal_write["Premium_Current"] == 4500
    assert renewal_write["Related_Deal"] == {"id": "Deals-ren-1"}
    assert renewal_write["Deal_Id"] == {"id": "Deals-ren-1"}
    deal_upsert = next(u for u in zoho.upserts if u[0] == "Deals")
    assert deal_upsert[3]["Opportunity_Type"] == "Renewals"
    assert deal_upsert[3]["Bound_Policy_Number"] == "CPP1"
    assert deal_upsert[3]["Stage"] == DEAL_STAGE_90


def test_run_sync_does_not_overwrite_existing_related_deal():
    zoho = FakeZoho()
    zoho.seed(
        "Deals",
        {
            "id": "keep-me",
            "Hermes_Opportunity_ID": "ren-1",
            "Opportunity_Type": "Renewals",
            "Bound_Policy_Number": "CPP1",
            "Stage": DEAL_STAGE_REQUOTE,
        },
        "ren-1",
        "CPP1",
    )
    zoho.seed(
        "Renewals",
        {
            "id": "z-ren",
            "Hermes_Renewal_ID": "ren-1",
            "Policy_Number": "CPP1",
            "Related_Deal": {"id": "keep-me"},
            "Dismissed": False,
        },
        "ren-1",
    )
    result = run_zoho_renewals_sync(supa=_supa(), zoho=zoho, today=TODAY, dry_run=False)
    assert result.ok
    assert result.deals_updated == 1
    renewal_write = next(rec for rec in _renewal_writes(zoho))
    assert "Related_Deal" not in renewal_write
    assert "Deal_Id" not in renewal_write
    assert zoho.updates
    deal_updates = [u for u in zoho.updates if u[0] == "Deals"]
    assert deal_updates[0][1] == "keep-me"
    assert deal_updates[0][2]["Stage"] == DEAL_STAGE_REQUOTE


def test_pipeline_deal_without_desk_row_creates_desk():
    zoho = FakeZoho()
    zoho.seed(
        "Deals",
        {
            "id": "crm-only",
            "Opportunity_Type": "Renewals",
            "Bound_Policy_Number": "HO-88",
            "Insured_Name": "James Wilson",
            "Line_of_Business": "Homeowners",
            "Expiration_Date": "2026-09-18",
            "Stage": DEAL_STAGE_30,
            "Carrier": "Safeco",
            "Amount": 1800,
        },
        "crm-only",
    )
    result = run_zoho_renewals_sync(
        supa=_supa(p85=[], candidates=[]),
        zoho=zoho,
        today=TODAY,
        dry_run=False,
    )
    assert result.ok
    assert result.pipeline_desk_created == 1
    desk_upsert = next(u for u in zoho.upserts if u[0] == "Renewals")
    assert desk_upsert[3]["Policy_Number"] == "HO-88"
    assert desk_upsert[3]["Related_Deal"] == {"id": "crm-only"}
    assert desk_upsert[3]["Deal_Id"] == {"id": "crm-only"}
    assert desk_upsert[3]["Desk_Stage"] == "Identified"
    assert desk_upsert[3]["Stage"] == "Identified"


def test_lombardo_shaped_row_links_deal_without_minting_a_leftover():
    """Live Catalyst: Renewals row has Policy_Number, empty Deal_Id, no Hermes_Renewal_ID."""
    zoho = FakeZoho()
    live = zoho.seed(
        "Renewals",
        {
            "id": "7529682000000707078",
            "Client_Name": "Lombardo, Tiffany",
            "Policy_Number": "991540615",
            "Carrier": "PROGRESSIVE MOUNTAIN INS CO",
            "Expiration_Date": "2026-08-15",
            "Deal_Id": None,
            "Related_Deal": None,
            "Stage": "Identified",
            "Dismissed": False,
        },
        "991540615",
    )
    p85 = {
        "id": "p85-lombardo",
        "policy_number": "991540615",
        "client_name": "Lombardo, Tiffany",
        "expiration_date": "2026-08-15",
        "premium_current": 3225,
        "premium_renewal": None,
        "risk_status": "RENEWED",
        "ai_strategy_notes": None,
        "last_contact_date": None,
    }
    policy = {
        "policy_number": "991540615",
        "policy_guid": "9200743b-e3c4-4d93-9c30-348349df1894",
        "nowcerts_insured_guid": "c2e05679-f7c7-4dfa-98a0-ccb7d8ab1112",
        "carrier": "PROGRESSIVE MOUNTAIN INS CO",
        "lines_of_business": "Auto",
        "effective_date": "2026-02-15",
    }
    result = run_zoho_renewals_sync(
        supa=_supa(p85=[p85], candidates=[], policies=[policy]),
        zoho=zoho,
        today=TODAY,
        dry_run=False,
    )
    assert result.ok
    assert result.renewals_created == 0
    assert result.renewals_updated == 1
    assert result.deals_created == 1
    renewals = [rec for _rid, (mod, rec) in zoho.records.items() if mod == "Renewals"]
    assert len(renewals) == 1
    linked = renewals[0]
    assert linked["id"] == live["id"]
    assert linked_deal_id(linked)
    assert has_pipeline_deal(linked)
    write = _renewal_writes(zoho)[0]
    assert write["Related_Deal"]["id"]
    assert write["Deal_Id"]["id"]
    assert write["Hermes_Renewal_ID"] == "p85-lombardo"
    assert "Desk_Stage" not in write


def test_worklist_hides_desk_only_leftovers():
    leftover = {"id": "desk-only", "Policy_Number": "991540615", "Deal_Id": None}
    linked = {"id": "ok", "Deal_Id": {"id": "deal-1"}}
    related = {"id": "ok2", "Related_Deal": {"id": "deal-2"}}
    assert has_pipeline_deal(leftover) is False
    assert has_pipeline_deal(linked) is True
    assert has_pipeline_deal(related) is True


def test_dismissed_desk_moves_deal_to_not_renewed():
    payload = map_renewal_to_deal(
        {**P85, "dismissed": True},
        policy=POLICY,
        desk_row={"Dismissed": True},
        today=TODAY,
        creating=True,
    )
    assert payload["Stage"] == DEAL_STAGE_LOST
    assert payload["Deal_Status"] == "lost"
