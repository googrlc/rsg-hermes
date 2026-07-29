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


# --- class-code reference (gl_class_codes / wc_class_codes) ---
GL_ROWS = [
    {"id": "a", "gl_code": "91341", "description": "Carpentry--Interior",
     "search_keywords": "carpentry interior finish trim cabinets countertops",
     "typical_businesses": "Finish carpenter, cabinet installer",
     "notes": "Finish carpentry: higher skill than rough framing. Rough framing belongs on 91340."},
    {"id": "b", "gl_code": "91340",
     "description": "Carpentry--Construction of Residential Property Not Exceeding Three Stories in Height",
     "search_keywords": "carpentry residential rough framing remodel",
     "typical_businesses": "Framing contractor", "notes": "Structural rough framing."},
    {"id": "c", "gl_code": "91343", "description": "Carpentry--Shop Only",
     "search_keywords": None, "typical_businesses": None, "notes": None},
]


class CodeTablesFake:
    """Serves the two manual tables; anything else comes back empty."""

    def __init__(self, gl=None, wc=None, other=None):
        self.gl, self.wc, self.other = gl or [], wc or [], other or []

    def select(self, table, *, columns="*", params=None, limit=100):
        if table == "gl_class_codes":
            return self.gl
        if table == "wc_class_codes":
            return self.wc
        return self.other


def test_lookup_class_code_by_code(monkeypatch):
    import hermes.integrations.supabase_client as sc
    monkeypatch.setattr(sc, "SupabaseClient", lambda *a, **k: CodeTablesFake(gl=GL_ROWS))
    res = A._exec_lookup_class_code({"query": "ISO 91341"})
    assert res.ok
    assert "Carpentry--Interior" in res.message
    assert "countertops" in res.message                 # the scope text, not just the number
    assert res.data["codes"][0]["code"] == "91341"      # exact hit outranks its siblings


def test_lookup_class_code_reverse_from_description(monkeypatch):
    """The high-leverage direction: the producer knows what the business DOES."""
    import hermes.integrations.supabase_client as sc
    monkeypatch.setattr(sc, "SupabaseClient", lambda *a, **k: CodeTablesFake(gl=GL_ROWS))
    res = A._exec_lookup_class_code({"query": "cabinets and countertops install"})
    assert res.ok and res.data["codes"][0]["code"] == "91341"


def test_lookup_class_code_flags_rows_with_no_detail(monkeypatch):
    """A thin row must announce it's thin — ranking low is a gap in our data,
    not evidence the code is wrong for the risk."""
    import hermes.integrations.supabase_client as sc
    monkeypatch.setattr(sc, "SupabaseClient", lambda *a, **k: CodeTablesFake(gl=[GL_ROWS[2]]))
    res = A._exec_lookup_class_code({"query": "91343"})
    assert res.ok and "no scope detail recorded" in res.message


def test_lookup_class_code_no_match_is_honest(monkeypatch):
    import hermes.integrations.supabase_client as sc
    monkeypatch.setattr(sc, "SupabaseClient", lambda *a, **k: CodeTablesFake(gl=GL_ROWS))
    res = A._exec_lookup_class_code({"query": "aviation hull"})
    assert res.ok and "No manual class code matches" in res.message


def test_appointments_by_line_groups_panel(monkeypatch):
    import hermes.integrations.supabase_client as sc

    class PanelFake:
        def select(self, table, *, columns="*", params=None, limit=100):
            if table == "carriers":
                return [{"id": "lm", "name": "Liberty Mutual", "lines_of_business": ["General Liability"]},
                        {"id": "isc", "name": "ISC", "general_agent": "Wholesale Co",
                         "lines_of_business": ["General Liability"]}]
            return [{"carrier_id": "lm", "lob": "General Liability", "appetite_level": "preferred"}]

    monkeypatch.setattr(sc, "SupabaseClient", lambda *a, **k: PanelFake())
    res = A._exec_appointments_by_line({"line_of_business": "General Liability"})
    assert res.ok
    assert "Liberty Mutual (direct) — preferred" in res.message
    assert "ISC (via Wholesale Co)" in res.message


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


# --- the portal's screen names are the hub names -----------------------------
# The portal sends `hub: state.screen`. Every desk screen it has must resolve to
# a real hub here, or that desk answers with the full toolset and no persona —
# which is what its Finance screen was doing.
PORTAL_DESK_SCREENS = ["crm", "renewals", "carrier", "intake", "cases", "finance"]


def test_every_portal_desk_screen_resolves_to_a_hub():
    for screen in PORTAL_DESK_SCREENS:
        key = A._hub_key(screen)
        assert key in A._HUB_TOOLS, f"portal screen {screen!r} has no hub"
        assert key in A._HUB_PERSONA, f"portal screen {screen!r} has no persona"


def test_the_finance_screen_is_the_commissions_desk():
    """The one name that does not match, and the reason the alias map exists."""
    assert A._hub_key("finance") == "commissions"
    names = {t["function"]["name"] for t in A._scoped_tools(A._TOOLS, "finance")}
    assert names == A._HUB_TOOLS["commissions"]
    assert names != {t["function"]["name"] for t in A._TOOLS}   # scoped, not everything


def test_hub_names_are_case_and_space_insensitive():
    assert A._hub_key("  Finance ") == "commissions"
    assert A._hub_key(None) == ""


def test_the_renewals_desk_exists_and_carries_the_worklist():
    assert "renewals_overview" in A._HUB_TOOLS["renewals"]


def test_every_tool_named_by_every_hub_is_registered():
    """The check that catches a hub pointing at a tool that was renamed or never
    existed — the desk would resolve to an empty toolset and answer from memory."""
    for hub, tools in A._HUB_TOOLS.items():
        for name in tools:
            assert any(t["function"]["name"] == name for t in A._TOOLS), \
                f"{hub} names {name!r}, which is not in _TOOLS"
            assert name in A._EXECUTORS, f"{hub} names {name!r}, which has no executor"


def test_no_desk_can_reach_a_write_tool():
    for hub, tools in A._HUB_TOOLS.items():
        assert not (tools & A._WRITE_TOOLS), f"{hub} can write"


def test_every_persona_named_by_a_hub_exists_on_disk():
    from hermes.core.identity import load_named_persona

    for hub, key in A._HUB_PERSONA.items():
        assert load_named_persona(key).strip(), f"{hub} names persona {key!r} with no file"


# --- model per desk ----------------------------------------------------------
def test_judgment_desks_get_the_escalation_model():
    """A class code or a commission shortfall is a judgment the agency acts on;
    a CRM lookup is reading a row back. They should not run on the same model."""
    from hermes.core.llm_client import resolve_model

    for desk in ("carrier", "commissions", "renewals"):
        assert A._HUB_MODEL[desk] == "hard_judgment_escalation"
    assert "crm" not in A._HUB_MODEL      # lookups stay on the default group
    assert "intake" not in A._HUB_MODEL
    assert A._HUB_MODEL.get(A._hub_key("finance")) == A._HUB_MODEL["commissions"]
    assert resolve_model(A._HUB_MODEL.get(A._hub_key("crm"))) == resolve_model(None)
