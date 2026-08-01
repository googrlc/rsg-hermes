"""The lead station — leads the agency owns, worked in the CRM, never in the AMS."""
from __future__ import annotations

import pytest

from hermes import leads as L


class FakeSupa:
    """Enough of the Supabase client for the lead paths."""

    def __init__(self, rows=None, *, fail_on=None):
        self.rows = rows or {}
        self.fail_on = fail_on or set()
        self.inserted: list[tuple[str, dict]] = []
        self.updated: list[tuple[str, str, dict]] = []
        self.deleted: list[tuple[str, str]] = []
        self.selects: list[tuple[str, dict]] = []

    def select(self, table, *, columns=None, params=None, limit=None):
        self.selects.append((table, params or {}))
        if table in self.fail_on:
            raise RuntimeError("supabase down")
        return list(self.rows.get(table, []))

    def insert(self, table, payload):
        self.inserted.append((table, payload))
        return {"id": f"{table}-1", **payload}

    def update(self, table, row_id, payload):
        self.updated.append((table, row_id, payload))
        base = next((r for r in self.rows.get(table, []) if str(r.get("id")) == str(row_id)), {})
        return {**base, "id": row_id, **payload}

    def delete(self, table, row_id):
        self.deleted.append((table, row_id))
        return {"ok": True}


class FakeNC:
    def __init__(self, insureds=None, *, boom=False):
        self._i = insureds or []
        self.boom = boom

    def fetch_insureds(self, *, page_size=100, since=None, max_pages=1000):
        if self.boom:
            raise RuntimeError("NowCerts timed out")
        return list(self._i)


def _lead(**kw):
    base = {"id": "lead-1", "name": "Prospect Co", "status": "new"}
    base.update(kw)
    return base


# --- creating and working a lead ---------------------------------------------
def test_a_lead_needs_only_a_name():
    supa = FakeSupa()
    lead = L.create_lead(supa, {"name": "Jane Roe"}, created_by="lamar@risksolutionsgroup.net")
    table, payload = supa.inserted[0]
    assert table == "crm_leads"
    assert payload["name"] == "Jane Roe"
    assert payload["status"] == "new"
    assert lead["created_by_email"] == "lamar@risksolutionsgroup.net"


def test_a_nameless_lead_is_refused():
    with pytest.raises(ValueError, match="name"):
        L.create_lead(FakeSupa(), {"phone": "770-555-0100"})
    with pytest.raises(ValueError):
        L.create_lead(FakeSupa(), {"name": "   "})


def test_creating_a_lead_never_writes_to_the_ams():
    """The rule: a lead is not a record of insurance."""
    supa = FakeSupa()
    L.create_lead(supa, {"name": "Jane Roe"})
    assert {t for t, _ in supa.inserted} == {"crm_leads"}


def test_a_bad_status_is_refused():
    with pytest.raises(ValueError, match="status"):
        L.create_lead(FakeSupa(), {"name": "Jane", "status": "smoking hot"})
    with pytest.raises(ValueError, match="status"):
        L.update_lead(FakeSupa(), "lead-1", {"status": "maybe"})


def test_api_owned_fields_cannot_be_written_by_hand():
    """An editable converted_opportunity_id points a lead at somebody else's deal."""
    supa = FakeSupa()
    L.update_lead(supa, "lead-1", {"status": "working", "converted_opportunity_id": "opp-9",
                                   "converted_at": "2020-01-01", "id": "other"})
    _, _, payload = supa.updated[0]
    assert payload == {"status": "working"}


def test_an_empty_edit_is_refused_rather_than_written():
    with pytest.raises(ValueError):
        L.update_lead(FakeSupa(), "lead-1", {"nonsense": 1})


# --- notes --------------------------------------------------------------------
def test_a_note_is_appended_with_its_author():
    supa = FakeSupa()
    L.add_note(supa, "lead-1", "  Left a voicemail.  ", author_email="gretchen@risksolutionsgroup.net")
    table, payload = supa.inserted[0]
    assert table == "crm_lead_notes"
    assert payload["body"] == "Left a voicemail."
    assert payload["lead_id"] == "lead-1"


def test_an_empty_note_is_refused():
    with pytest.raises(ValueError):
        L.add_note(FakeSupa(), "lead-1", "   ")


def test_notes_come_back_newest_first():
    supa = FakeSupa({"crm_lead_notes": [{"id": "n1"}]})
    L.list_notes(supa, "lead-1")
    _, params = supa.selects[0]
    assert params["order"].startswith("created_at.desc")


# --- the combined list --------------------------------------------------------
def test_the_list_carries_both_sources():
    supa = FakeSupa({"crm_leads": [_lead()]})
    nc = FakeNC([{"id": "g9", "commercialName": "AMS Prospect", "prospectType": "Prospect"}])
    out = L.combined_leads(supa, nc)
    assert out["crm_count"] == 1 and out["ams_count"] == 1
    assert {l["source"] for l in out["leads"]} == {"crm", "nowcerts"}


def test_our_leads_survive_the_ams_being_down():
    """/api/leads has 502'd in production on this read. Our own leads must not
    disappear because NowCerts was slow."""
    supa = FakeSupa({"crm_leads": [_lead()]})
    out = L.combined_leads(supa, FakeNC(boom=True))
    assert out["crm_count"] == 1
    assert out["ams_count"] == 0
    assert "NowCerts timed out" in out["ams_error"]


def test_a_prospect_we_already_hold_is_shown_once():
    supa = FakeSupa({"crm_leads": [_lead(nowcerts_insured_guid="g9")]})
    nc = FakeNC([{"id": "g9", "commercialName": "Same Person", "prospectType": "Prospect"}])
    out = L.combined_leads(supa, nc)
    assert out["count"] == 1
    assert out["leads"][0]["source"] == "crm"      # ours wins — it carries the notes


def test_the_list_is_ranked_by_x_date():
    supa = FakeSupa({"crm_leads": [_lead()]})
    L.combined_leads(supa, None)
    _, params = supa.selects[0]
    assert params["order"].startswith("x_date.asc")


# --- conversion ---------------------------------------------------------------
def test_converting_opens_a_deal_and_marks_the_lead(monkeypatch):
    supa = FakeSupa({"crm_leads": [_lead(company="Prospect Co", x_date="2026-09-15",
                                         lead_source="referral", owner_email="lamar@x.net")]})
    made = {}

    def fake_create(_supa, **kw):
        made.update(kw)
        return {"id": "opp-1", **kw}, True

    monkeypatch.setattr("hermes_core.opportunities.create_opportunity", fake_create)
    lead, opp = L.convert_to_opportunity(supa, "lead-1", line_of_business="General Liability")

    assert opp["id"] == "opp-1"
    assert made["line_of_business"] == "General Liability"
    assert made["insured_name"] == "Prospect Co"
    # The x-date is why the deal has a deadline — it has to come across.
    assert made["expiration_date"] == "2026-09-15"
    assert made["source"] == "lead-conversion"
    assert lead["status"] == "converted"
    assert lead["converted_opportunity_id"] == "opp-1"


def test_converting_never_writes_to_the_ams(monkeypatch):
    """A converted lead is still only a deal. NowCerts hears about it when it is won."""
    supa = FakeSupa({"crm_leads": [_lead()]})
    monkeypatch.setattr("hermes_core.opportunities.create_opportunity",
                        lambda _s, **kw: ({"id": "opp-1"}, True))
    L.convert_to_opportunity(supa, "lead-1", line_of_business="BOP")
    assert supa.inserted == []          # nothing staged, nothing queued


def test_converting_keeps_the_lead_for_the_upsell(monkeypatch):
    """The lead row is never moved or deleted — next year's cross-sell is worked
    off its history."""
    supa = FakeSupa({"crm_leads": [_lead()]})
    monkeypatch.setattr("hermes_core.opportunities.create_opportunity",
                        lambda _s, **kw: ({"id": "opp-1"}, True))
    L.convert_to_opportunity(supa, "lead-1", line_of_business="BOP")
    assert supa.deleted == []
    assert supa.updated[0][0] == "crm_leads"


def test_converting_needs_a_line_of_business():
    supa = FakeSupa({"crm_leads": [_lead()]})
    with pytest.raises(ValueError, match="line_of_business"):
        L.convert_to_opportunity(supa, "lead-1", line_of_business="  ")


def test_converting_an_unknown_lead_is_an_error():
    with pytest.raises(ValueError, match="not found"):
        L.convert_to_opportunity(FakeSupa(), "nope", line_of_business="BOP")


def test_conversion_runs_against_the_REAL_create_opportunity():
    """Convert a lead without mocking create_opportunity away.

    Every other conversion test monkeypatches it with ``fake_create(_supa, **kw)``,
    which accepts any keyword — so a kwarg the real function does not have passes
    the suite and raises TypeError in production. It did: ``expiration_date`` was
    passed here and never existed on the signature, and the lead station's only
    forward path failed on every call. This test is the one that would have caught
    it, so it deliberately uses the real thing.
    """
    supa = FakeSupa({
        "crm_leads": [_lead(company="Prospect Co", x_date="2026-09-15", lead_type="Commercial")],
        # No existing row → create_opportunity inserts rather than adopting.
        "opportunities": [],
    })
    lead, opp = L.convert_to_opportunity(supa, "lead-1", line_of_business="General Liability")

    table, payload = supa.inserted[0]
    assert table == "opportunities"
    assert payload["line_of_business"] == "General Liability"
    assert payload["insured_name"] == "Prospect Co"
    # The x-date is why the deal has a deadline. It has to land on the row itself,
    # not merely be accepted as an argument.
    assert payload["expiration_date"] == "2026-09-15"
    assert lead["status"] == "converted"
