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
    res = A._exec_list_carriers(None, {"query": "trav", "line_of_business": "GL"})
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
    res = A._exec_carrier_appetite(None, {"line_of_business": "Commercial Auto", "state": "GA"})
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
    res = A._exec_carrier_appetite(None, {"line_of_business": "GL", "state": "GA"})
    assert "GA-Only" in res.message and "Nationwide-ALL" in res.message  # ALL always matches
    assert "FL-Only" not in res.message


def test_carrier_appetite_empty_is_honest(monkeypatch):
    import hermes.integrations.supabase_client as sc
    monkeypatch.setattr(sc, "SupabaseClient", lambda *a, **k: FakeSupa([]))
    res = A._exec_carrier_appetite(None, {"line_of_business": "Aviation"})
    assert res.ok and "No carriers" in res.message


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
    res = A._exec_commission_summary(None, {})
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
    res = A._exec_commission_shortfalls(None, {})
    # Beta (missing $800) ranks above Acme (underpaid $600); Gamma (paid) excluded
    assert res.data["count"] == 2 and res.data["total"] == 1400
    assert res.message.index("Beta") < res.message.index("Acme")
    assert "Gamma" not in res.message


def test_commission_shortfalls_none(monkeypatch):
    import hermes.integrations.supabase_client as sc
    rows = [{"client_name": "Ok", "carrier_name": "X", "expected_commission": "100", "actual_commission": "100", "reconciliation_status": "paid"}]
    monkeypatch.setattr(sc, "SupabaseClient", lambda *a, **k: FakeSupa(rows))
    res = A._exec_commission_shortfalls(None, {})
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
    res = A._exec_find_client(None, {"query": "dream"})
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
    res = A._exec_client_policies(None, {"client": "Dream Chaser"})
    assert res.ok and "Dream Chaser Trucking" in res.message and "Commercial Auto" in res.message
    assert "1 active of 1" in res.message
    assert two.calls == ["canonical_clients", "canonical_policies"]


def test_list_intake_filters_status(monkeypatch):
    import hermes.integrations.supabase_client as sc
    fake = FakeSupa([{"client_identifier": "Acme", "intake_kind": "commercial", "status": "awaiting_approval"}])
    monkeypatch.setattr(sc, "SupabaseClient", lambda *a, **k: fake)
    res = A._exec_list_intake(None, {"status": "awaiting_approval"})
    assert res.ok and "Acme" in res.message
    assert fake.last[0] == "intake_submissions"
    assert fake.last[1].get("status") == "eq.awaiting_approval"


def test_all_hub_tools_resolve():
    # every tool named in any hub must exist as a schema and an executor
    for hub, names in A._HUB_TOOLS.items():
        for name in names:
            assert name in A._EXECUTORS, f"{hub}:{name} not in _EXECUTORS"
            assert any(t["function"]["name"] == name for t in A._TOOLS), f"{hub}:{name} not in _TOOLS"
