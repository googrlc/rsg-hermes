"""Per-hub AI scoping harness + Carrier hub tools."""
from __future__ import annotations

from hermes.core import nl_agent as A


class FakeSupa:
    def __init__(self, rows):
        self._rows = rows
        self.last = None

    def select(self, table, *, columns="*", params=None, limit=100):
        self.last = (table, params or {})
        return self._rows


# --- scoping harness ---
def test_scoped_tools_filters_to_hub():
    names = {t["function"]["name"] for t in A._scoped_tools(A._TOOLS, "carrier")}
    assert names == A._HUB_TOOLS["carrier"]


def test_scoped_tools_none_or_unknown_hub_unchanged():
    assert A._scoped_tools(A._TOOLS, None) == A._TOOLS
    assert A._scoped_tools(A._TOOLS, "not-a-hub") == A._TOOLS


def test_hub_tools_all_registered():
    for name in A._HUB_TOOLS["carrier"]:
        assert name in A._EXECUTORS, f"{name} missing from _EXECUTORS"
        assert any(t["function"]["name"] == name for t in A._TOOLS), f"{name} missing from _TOOLS"


def test_carrier_hub_cannot_reach_write_tools():
    # A carrier assistant must not carry CRM write tools.
    assert not (A._HUB_TOOLS["carrier"] & A._WRITE_TOOLS)


# --- carrier tools ---
def test_list_carriers(monkeypatch):
    import hermes.integrations.supabase_client as sc
    fake = FakeSupa([{"name": "Travelers", "segment": "Commercial", "lines_of_business": "GL, Auto"}])
    monkeypatch.setattr(sc, "SupabaseClient", lambda *a, **k: fake)
    res = A._exec_list_carriers({"query": "trav", "line_of_business": "GL"})
    assert res.ok and "Travelers" in res.message
    assert fake.last[0] == "carriers"
    assert fake.last[1].get("name") == "ilike.*trav*"
    assert fake.last[1].get("lines_of_business") == "ilike.*GL*"


def test_carrier_appetite_match(monkeypatch):
    import hermes.integrations.supabase_client as sc
    fake = FakeSupa([{"carrier_name": "Progressive", "lob": "Commercial Auto",
                      "appetite_level": "Strong", "min_premium": 1000, "max_premium": 50000,
                      "states_approved": ["GA"]}])
    monkeypatch.setattr(sc, "SupabaseClient", lambda *a, **k: fake)
    res = A._exec_carrier_appetite({"line_of_business": "Commercial Auto", "state": "GA"})
    assert res.ok and "Progressive" in res.message and "Strong" in res.message
    assert fake.last[0] == "carrier_appetite"
    assert fake.last[1].get("lob") == "ilike.*Commercial Auto*"
    # states_approved is a text[] — filtered in Python, not via a PostgREST param
    assert "states_approved" not in fake.last[1]


def test_carrier_appetite_state_filters_and_all(monkeypatch):
    import hermes.integrations.supabase_client as sc
    rows = [
        {"carrier_name": "GA-Only", "lob": "GL", "states_approved": ["GA"]},
        {"carrier_name": "Nationwide-ALL", "lob": "GL", "states_approved": ["ALL"]},
        {"carrier_name": "FL-Only", "lob": "GL", "states_approved": ["FL"]},
    ]
    monkeypatch.setattr(sc, "SupabaseClient", lambda *a, **k: FakeSupa(rows))
    res = A._exec_carrier_appetite({"line_of_business": "GL", "state": "GA"})
    assert "GA-Only" in res.message and "Nationwide-ALL" in res.message  # ALL always matches
    assert "FL-Only" not in res.message


def test_carrier_appetite_empty_is_honest(monkeypatch):
    import hermes.integrations.supabase_client as sc
    monkeypatch.setattr(sc, "SupabaseClient", lambda *a, **k: FakeSupa([]))
    res = A._exec_carrier_appetite({"line_of_business": "Aviation"})
    assert res.ok and "No carriers" in res.message


class TableSupa:
    """Fake Supabase that answers per table, and records every call."""

    def __init__(self, tables):
        self._tables = tables
        self.calls = []

    def select(self, table, *, columns="*", params=None, limit=100):
        self.calls.append((table, params or {}))
        rows = self._tables.get(table, [])
        return list(rows)


def _patch_supa(monkeypatch, fake):
    import hermes.integrations.supabase_client as sc
    monkeypatch.setattr(sc, "SupabaseClient", lambda *a, **k: fake)


# --- Carrier Desk: appetite rows carry their own caveats ---
def test_appetite_flags_unverified_and_missing_territory(monkeypatch):
    rows = [{"carrier_name": "Progressive", "lob": "Commercial Auto", "appetite_level": "standard",
             "states_approved": ["GA", "AL"], "confidence": "unverified"}]
    _patch_supa(monkeypatch, TableSupa({"carrier_appetite": rows}))
    res = A._exec_carrier_appetite({"line_of_business": "Commercial Auto"})
    assert "standard" in res.message and "GA/AL" in res.message
    assert "unverified" in res.message  # never presented as signed-off


def test_appetite_blank_territory_is_not_nationwide(monkeypatch):
    # 14 live rows have no itemized states because their source doesn't itemize
    # them. Blank must never satisfy a state filter — that invents a licence.
    rows = [
        {"carrier_name": "Itemized", "lob": "GL", "states_approved": ["GA"], "confidence": "verified"},
        {"carrier_name": "NoTerritory", "lob": "GL", "states_approved": [], "confidence": "verified"},
    ]
    _patch_supa(monkeypatch, TableSupa({"carrier_appetite": rows}))
    res = A._exec_carrier_appetite({"line_of_business": "GL", "state": "GA"})
    assert "Itemized" in res.message
    assert "NoTerritory" not in res.message
    assert len(res.data["state_unconfirmed"]) == 1
    assert "no itemized territory" in res.message  # excluded out loud, not silently


def test_appetite_declined_is_a_knockout_not_a_match(monkeypatch):
    rows = [
        {"carrier_name": "Writes", "lob": "WC", "appetite_level": "preferred",
         "states_approved": ["GA"], "confidence": "verified"},
        {"carrier_name": "Declines", "lob": "WC", "appetite_level": "declined",
         "states_approved": ["GA"], "confidence": "verified"},
    ]
    _patch_supa(monkeypatch, TableSupa({"carrier_appetite": rows}))
    res = A._exec_carrier_appetite({"line_of_business": "WC"})
    assert res.data["matches"][0]["carrier_name"] == "Writes"
    assert [r["carrier_name"] for r in res.data["declined"]] == ["Declines"]
    assert "Declined on file" in res.message


def test_appetite_says_when_the_class_filter_found_nothing(monkeypatch):
    rows = [{"carrier_name": "Nationwide", "lob": "GL", "appetite_level": "standard",
             "states_approved": ["GA"], "confidence": "verified", "notes": "restaurants"}]
    _patch_supa(monkeypatch, TableSupa({"carrier_appetite": rows}))
    res = A._exec_carrier_appetite({"line_of_business": "GL", "class_or_naics": "asbestos"})
    # Falls back to the LOB set rather than returning zero — but says so, so an
    # LOB match is never passed off as a class match.
    assert "Nationwide" in res.message
    assert "LOB/state matches only" in res.message


# --- Carrier Desk: contacts / appointment status ---
def test_carrier_contacts_lists_people(monkeypatch):
    fake = TableSupa({
        "carriers": [{"id": "nationwide", "name": "Nationwide"}],
        "carrier_contacts": [{"carrier_id": "nationwide", "name": "Dana Reed", "role": "New Business",
                              "email": "dana@nw.com", "is_primary": True}],
    })
    _patch_supa(monkeypatch, fake)
    res = A._exec_carrier_contacts({"carrier": "nation", "role": "new business"})
    assert res.ok and "Dana Reed" in res.message and "Nationwide" in res.message
    assert fake.calls[0][0] == "carriers"
    assert fake.calls[1][0] == "carrier_contacts"
    assert fake.calls[1][1]["carrier_id"] == 'in.("nationwide")'
    assert fake.calls[1][1]["role"] == "ilike.*new business*"


def test_carrier_contacts_not_in_roster_means_not_appointed(monkeypatch):
    fake = TableSupa({"carriers": [], "carrier_contacts": []})
    _patch_supa(monkeypatch, fake)
    res = A._exec_carrier_contacts({"carrier": "Chubb"})
    assert res.ok and "not appointed" in res.message
    assert "appointment gap" in res.message
    assert [c[0] for c in fake.calls] == ["carriers"]  # never looked for people


def test_carrier_contacts_appointed_but_nobody_on_file(monkeypatch):
    # Opposite answer to the one above — the distinction is the whole point.
    _patch_supa(monkeypatch, TableSupa({
        "carriers": [{"id": "rli", "name": "RLI", "underwriting_hotline": "800-555-0100"}],
        "carrier_contacts": [],
    }))
    res = A._exec_carrier_contacts({"carrier": "RLI"})
    assert res.ok and "Appointed at RLI" in res.message
    assert "800-555-0100" in res.message  # the route that does exist


# --- Carrier Desk: class codes ---
def test_resolve_class_code_numeric_is_a_prefix_lookup(monkeypatch):
    fake = TableSupa({"wc_class_codes": [{"wc_code": "5183", "description": "Plumbing NOC & Drivers",
                                          "notes": "Master per Basic Manual - Georgia, 11/1/2021."}]})
    _patch_supa(monkeypatch, fake)
    res = A._exec_resolve_class_code({"query": "5183", "code_system": "wc"})
    assert res.ok and "Plumbing NOC & Drivers" in res.message
    assert "Georgia" in res.message  # the row's provenance note survives
    assert fake.calls[0][1]["wc_code"] == "like.5183*"


def test_resolve_class_code_surfaces_do_not_quote(monkeypatch):
    _patch_supa(monkeypatch, TableSupa({"wc_class_codes": [
        {"wc_code": "5037", "description": "Plumbing NOC",
         "notes": "DISPUTED 2026-07-24 - DO NOT QUOTE. Believed to be Painting: Metal Structures."}]}))
    res = A._exec_resolve_class_code({"query": "5037", "code_system": "wc"})
    assert "DO NOT QUOTE" in res.message and "⛔" in res.message


def test_resolve_class_code_asks_wc_or_gl_when_both_hit(monkeypatch):
    _patch_supa(monkeypatch, TableSupa({
        "wc_class_codes": [{"wc_code": "5183", "description": "Plumbing NOC & Drivers"}],
        "gl_class_codes": [{"gl_code": "98483", "description": "Plumbing"}],
        "naics_codes": [],
    }))
    res = A._exec_resolve_class_code({"query": "plumbing"})
    assert "WC and GL both matched" in res.message  # ask, don't pick


def test_resolve_class_code_nothing_found_invents_nothing(monkeypatch):
    _patch_supa(monkeypatch, TableSupa({}))
    res = A._exec_resolve_class_code({"query": "orbital debris removal"})
    assert res.ok and "won't invent a code" in res.message


def test_class_code_appetite_direct_link(monkeypatch):
    fake = TableSupa({"vw_carrier_appetite_class_resolved": [
        {"carrier_name": "LIBERTY MUTUAL", "lob": "General Liability", "appetite_level": "preferred",
         "appetite_confidence": "verified", "states_approved": ["GA", "FL"], "code_system": "gl",
         "code": "91340", "code_description": "Carpentry--Residential", "eligibility": "eligible",
         "match_method": "explicit_source", "link_confidence": "verified"}]})
    _patch_supa(monkeypatch, fake)
    res = A._exec_class_code_appetite({"code": "91340", "code_system": "gl", "state": "GA"})
    assert res.ok and "LIBERTY MUTUAL" in res.message and "direct code link" in res.message
    assert fake.calls[0][1]["code"] == "eq.91340"
    assert "unverified" not in res.message  # this one is signed off


def test_class_code_appetite_bridges_through_naics(monkeypatch):
    fake = TableSupa({
        "vw_carrier_appetite_class_resolved": [],
        "vw_who_writes_naics": [
            {"naics_code": "238220", "naics_title": "HVAC Contractors", "carrier_name": "Travelers",
             "lob": "General Liability", "appetite_level": "standard", "appetite_confidence": "unverified",
             "states_approved": ["GA"], "code_system": "gl", "matched_code": "98482",
             "matched_code_description": "HVAC", "eligibility": "eligible"}],
    })
    _patch_supa(monkeypatch, fake)
    res = A._exec_class_code_appetite({"code": "238220"})
    assert res.ok and "Travelers" in res.message
    assert "bridged via NAICS" in res.message   # labelled as bridged, not passed off as direct
    assert "unverified" in res.message
    assert [c[0] for c in fake.calls] == ["vw_carrier_appetite_class_resolved", "vw_who_writes_naics"]


def test_class_code_appetite_empty_is_a_gap_not_a_declination(monkeypatch):
    # The failure mode this desk must never have: an empty join reported as
    # "no carriers write this".
    _patch_supa(monkeypatch, TableSupa({"vw_carrier_appetite_class_resolved": [], "vw_who_writes_naics": []}))
    res = A._exec_class_code_appetite({"code": "5183"})
    assert res.ok
    assert "not a declination" in res.message
    assert "match_carrier_appetite" in res.message  # names the fallback


def test_class_code_appetite_flags_machine_derived_links(monkeypatch):
    _patch_supa(monkeypatch, TableSupa({"vw_carrier_appetite_class_resolved": [
        {"carrier_name": "Guessed", "lob": "GL", "appetite_level": "standard", "states_approved": ["GA"],
         "code": "1234", "eligibility": "eligible", "match_method": "embedding",
         "link_confidence": "unverified"}]}))
    res = A._exec_class_code_appetite({"code": "1234"})
    assert "machine-derived" in res.message


def test_class_code_appetite_lists_a_carriers_codes(monkeypatch):
    fake = TableSupa({"vw_carrier_appetite_class_resolved": [
        {"carrier_name": "CNA", "lob": "Business Policy", "code_system": "carrier", "code": "87210",
         "code_description": None, "eligibility": "eligible", "link_confidence": "verified",
         "states_approved": []}]})
    _patch_supa(monkeypatch, fake)
    res = A._exec_class_code_appetite({"carrier": "CNA"})
    assert res.ok and "87210" in res.message
    assert fake.calls[0][1]["carrier_name"] == "ilike.*CNA*"


def test_carrier_hub_carries_the_class_and_contact_lane():
    carrier = A._HUB_TOOLS["carrier"]
    assert {"resolve_class_code", "class_code_appetite", "carrier_contacts"} <= carrier
    assert not (carrier & A._WRITE_TOOLS)          # enrichment is proposed, not written
    assert not (carrier & A._HUB_TOOLS["crm"])     # stays out of the client lane


def test_carrier_persona_only_promises_tools_the_desk_carries():
    """The persona tells the desk what it can do; the hub decides what it can call.

    When those drift, the desk promises a lookup it cannot run — which reads to the
    user as a refusal or, worse, gets answered from the model's own market
    knowledge. Every tool the persona names must be in the carrier hub's set.
    """
    from hermes.core import identity

    persona = identity.load_named_persona("carrier")
    every_tool = {t["function"]["name"] for t in A._TOOLS}
    named = {n for n in every_tool if f"`{n}`" in persona}
    assert named, "the carrier persona names no tools at all"
    assert named <= A._HUB_TOOLS["carrier"]
    # The desk is read-only today; the persona must not imply otherwise.
    assert "You do not write" in persona


# --- commissions hub ---
def test_commissions_hub_scoped_and_registered():
    names = {t["function"]["name"] for t in A._scoped_tools(A._TOOLS, "commissions")}
    assert names == A._HUB_TOOLS["commissions"]
    for name in A._HUB_TOOLS["commissions"]:
        assert name in A._EXECUTORS
    assert not (A._HUB_TOOLS["commissions"] & A._WRITE_TOOLS)


def test_commission_summary(monkeypatch):
    import hermes.integrations.supabase_client as sc
    rows = [
        {"carrier_name": "Travelers", "expected_commission": "1000", "actual_commission": "400", "reconciliation_status": "underpaid"},
        {"carrier_name": "Progressive", "expected_commission": "500", "actual_commission": "500", "reconciliation_status": "paid"},
    ]
    monkeypatch.setattr(sc, "SupabaseClient", lambda *a, **k: FakeSupa(rows))
    res = A._exec_commission_summary({})
    assert res.ok
    assert res.data["expected"] == 1500 and res.data["received"] == 900 and res.data["outstanding"] == 600
    assert "underpaid: 1" in res.message


def test_commission_shortfalls_ranks_owed(monkeypatch):
    import hermes.integrations.supabase_client as sc
    rows = [
        {"client_name": "Acme", "carrier_name": "Travelers", "expected_commission": "1000", "actual_commission": "400", "reconciliation_status": "underpaid"},
        {"client_name": "Beta", "carrier_name": "Hartford", "expected_commission": "800", "actual_commission": "0", "reconciliation_status": "missing_statement"},
        {"client_name": "Gamma", "carrier_name": "Progressive", "expected_commission": "500", "actual_commission": "500", "reconciliation_status": "paid"},
    ]
    monkeypatch.setattr(sc, "SupabaseClient", lambda *a, **k: FakeSupa(rows))
    res = A._exec_commission_shortfalls({})
    # Beta (missing $800) ranks above Acme (underpaid $600); Gamma (paid) excluded
    assert res.data["count"] == 2 and res.data["total"] == 1400
    assert res.message.index("Beta") < res.message.index("Acme")
    assert "Gamma" not in res.message


def test_commission_shortfalls_none(monkeypatch):
    import hermes.integrations.supabase_client as sc
    rows = [{"client_name": "Ok", "carrier_name": "X", "expected_commission": "100", "actual_commission": "100", "reconciliation_status": "paid"}]
    monkeypatch.setattr(sc, "SupabaseClient", lambda *a, **k: FakeSupa(rows))
    res = A._exec_commission_shortfalls({})
    assert res.ok and "No outstanding commission shortfalls" in res.message


# --- crm + intake hubs ---
def test_crm_and_intake_hubs_scoped_and_registered():
    for hub in ("crm", "intake"):
        names = {t["function"]["name"] for t in A._scoped_tools(A._TOOLS, hub)}
        assert names == A._HUB_TOOLS[hub]
        for name in A._HUB_TOOLS[hub]:
            assert name in A._EXECUTORS
        assert not (A._HUB_TOOLS[hub] & A._WRITE_TOOLS)  # read-only hubs


def test_find_client(monkeypatch):
    import hermes.integrations.supabase_client as sc
    fake = FakeSupa([{"insured_name": "Dream Chaser Trucking", "client_type": "Commercial",
                      "city": "Atlanta", "state": "Georgia", "nowcerts_insured_guid": "g1"}])
    monkeypatch.setattr(sc, "SupabaseClient", lambda *a, **k: fake)
    res = A._exec_find_client({"query": "dream"})
    assert res.ok and "Dream Chaser Trucking" in res.message
    assert fake.last[0] == "canonical_clients"
    assert fake.last[1].get("insured_name") == "ilike.*dream*"


def test_client_policies_resolves_name(monkeypatch):
    import hermes.integrations.supabase_client as sc

    class TwoStep:
        def __init__(self):
            self.calls = []

        def select(self, table, *, columns="*", params=None, limit=100):
            self.calls.append(table)
            if table == "canonical_clients":
                return [{"nowcerts_insured_guid": "g1", "insured_name": "Dream Chaser Trucking"}]
            return [{"policy_number": "P1", "carrier": "Progressive", "lines_of_business": "Commercial Auto",
                     "premium_amount": "8888", "status": "Active", "expiration_date": "2027-05-18", "active": True}]

    two = TwoStep()
    monkeypatch.setattr(sc, "SupabaseClient", lambda *a, **k: two)
    res = A._exec_client_policies({"client": "Dream Chaser"})
    assert res.ok and "Dream Chaser Trucking" in res.message and "Commercial Auto" in res.message
    assert "1 active of 1" in res.message
    assert two.calls == ["canonical_clients", "canonical_policies"]


def test_list_intake_filters_status(monkeypatch):
    import hermes.integrations.supabase_client as sc
    fake = FakeSupa([{"client_identifier": "Acme", "intake_kind": "commercial", "status": "awaiting_approval"}])
    monkeypatch.setattr(sc, "SupabaseClient", lambda *a, **k: fake)
    res = A._exec_list_intake({"status": "awaiting_approval"})
    assert res.ok and "Acme" in res.message
    assert fake.last[0] == "intake_submissions"
    assert fake.last[1].get("status") == "eq.awaiting_approval"


def test_all_hub_tools_resolve():
    # every tool named in any hub must exist as a schema and an executor
    for hub, names in A._HUB_TOOLS.items():
        for name in names:
            assert name in A._EXECUTORS, f"{hub}:{name} not in _EXECUTORS"
            assert any(t["function"]["name"] == name for t in A._TOOLS), f"{hub}:{name} not in _TOOLS"


# --- CRM Desk client-360 expansion (#194): CRM + live AMS + agency CRM + docs ---
def test_crm_hub_carries_client360_tools_read_only():
    crm = A._HUB_TOOLS["crm"]
    assert {"ams_client_snapshot", "crm_client_activity", "client_documents"} <= crm
    # stays read-only and out of the carrier/commissions/intake lanes
    assert not (crm & A._WRITE_TOOLS)
    assert not (crm & A._HUB_TOOLS["carrier"])
    assert not (crm & A._HUB_TOOLS["commissions"])
    assert "list_intake_submissions" not in crm


class FakeNowCerts:
    def __init__(self, *, insureds=None, policies=None, opps=None):
        self._insureds = insureds or []
        self._policies = policies or []
        self._opps = opps or []
        self.searched = None
        self.policies_guid = None

    def search_insureds(self, name, *, top=10):
        self.searched = name
        return self._insureds

    def policies_for_insured(self, guid, *, top=100):
        self.policies_guid = guid
        return self._policies

    def opportunities_for_insured(self, guid):
        return self._opps


def test_ams_client_snapshot_live(monkeypatch):
    import hermes.sync.nowcerts_client as ncmod
    fake = FakeNowCerts(
        insureds=[{"databaseId": "g-123", "commercialName": "Acme Trucking", "active": True}],
        policies=[{"lineOfBusiness": "Commercial Auto", "carrierName": "Progressive",
                   "premium": "8888", "expirationDate": "2027-05-18T00:00:00", "active": True}],
        opps=[{"lineOfBusinessName": "General Liability", "opportunityStageName": "Quoting",
               "neededBy": "2026-09-01T00:00:00"}],
    )
    monkeypatch.setattr(ncmod, "NowCertsClient", lambda *a, **k: fake)
    res = A._exec_ams_snapshot({"client": "acme"})
    assert res.ok
    assert fake.searched == "acme" and fake.policies_guid == "g-123"
    assert "Acme Trucking" in res.message and "Commercial Auto" in res.message
    assert "General Liability" in res.message  # open opportunity surfaced
    assert res.data["insured_guid"] == "g-123"


def test_ams_client_snapshot_no_match(monkeypatch):
    import hermes.sync.nowcerts_client as ncmod
    monkeypatch.setattr(ncmod, "NowCertsClient", lambda *a, **k: FakeNowCerts(insureds=[]))
    res = A._exec_ams_snapshot({"client": "nobody"})
    assert res.ok and "No AMS insured" in res.message


def test_crm_client_activity(monkeypatch):
    import hermes.integrations.supabase_client as sc

    class CasesThenTasks:
        def __init__(self):
            self.calls = []

        def select(self, table, *, columns="*", params=None, limit=100):
            self.calls.append((table, params or {}))
            if table == "agency_crm_cases":
                return [{"id": "c1", "case_type": "renewal", "title": "Renewal — Acme",
                         "status": "open", "insured_name": "Acme Trucking"}]
            return [{"case_id": "c1", "title": "Build option comparison",
                     "status": "not_started", "due_at": "2026-08-01"}]

    two = CasesThenTasks()
    monkeypatch.setattr(sc, "SupabaseClient", lambda *a, **k: two)
    res = A._exec_crm_activity({"client": "acme"})
    assert res.ok and "Acme Trucking" in res.message and "Renewal — Acme" in res.message
    assert "Build option comparison" in res.message  # task nested under its case
    assert two.calls[0][0] == "agency_crm_cases"
    assert two.calls[0][1].get("insured_name") == "ilike.*acme*"
    assert two.calls[1][0] == "agency_crm_tasks"


class FakeNextcloud:
    def __init__(self, *, tree=None, files=None, configured=True):
        self._tree = tree or {}
        self._files = files or {}
        self.configured = configured
        self.read_calls = []

    def is_configured(self):
        return self.configured

    def list_dir(self, rel_path):
        return self._tree.get(rel_path.strip("/"), [])

    def read_file(self, rel_path):
        self.read_calls.append(rel_path)
        if rel_path not in self._files:
            from hermes.integrations.nextcloud_client import NextcloudError
            raise NextcloudError(f"Not found: {rel_path}")
        return self._files[rel_path]


def _patch_nextcloud(monkeypatch, fake):
    import hermes.integrations.nextcloud_client as ncmod
    monkeypatch.setattr(ncmod, "NextcloudClient", lambda *a, **k: fake)


def test_client_documents_lists(monkeypatch):
    fake = FakeNextcloud(tree={
        "Clients/Acme Trucking": [
            {"name": "COIs", "path": "Clients/Acme Trucking/COIs", "is_dir": True},
            {"name": "readme.txt", "path": "Clients/Acme Trucking/readme.txt", "is_dir": False},
        ],
        "Clients/Acme Trucking/COIs": [
            {"name": "acme-2026.pdf", "path": "Clients/Acme Trucking/COIs/acme-2026.pdf", "is_dir": False},
        ],
    })
    _patch_nextcloud(monkeypatch, fake)
    res = A._exec_client_documents({"client": "Acme Trucking"})
    assert res.ok and "acme-2026.pdf" in res.message and "COIs/" in res.message
    assert "readme.txt" in res.message


def test_client_documents_scope_guard_blocks_traversal(monkeypatch):
    fake = FakeNextcloud(files={})
    _patch_nextcloud(monkeypatch, fake)
    res = A._exec_client_documents({"client": "Acme", "path": "../Other/secret.pdf"})
    assert not res.ok and "isn't allowed" in res.message
    assert fake.read_calls == []  # never touched Nextcloud


def test_client_documents_reads_file(monkeypatch):
    fake = FakeNextcloud(files={"Clients/Acme/COIs/acme.txt": b"hello world"})
    _patch_nextcloud(monkeypatch, fake)
    import hermes.command_center.extract as ex
    monkeypatch.setattr(ex, "read_document_text", lambda p, ocr=True: "COI for Acme — active")
    res = A._exec_client_documents({"client": "Acme", "path": "COIs/acme.txt"})
    assert res.ok and "COI for Acme — active" in res.message
    assert fake.read_calls == ["Clients/Acme/COIs/acme.txt"]


def test_client_documents_unconfigured_is_honest(monkeypatch):
    _patch_nextcloud(monkeypatch, FakeNextcloud(configured=False))
    res = A._exec_client_documents({"client": "Acme"})
    assert not res.ok and "isn't configured" in res.message
