"""hermes-gretch: per-instance identity, persona, agent_id stamping, memory scope, COI."""
import uuid

import pytest

from hermes.core import identity
from hermes.command_center import store
from hermes.operations import crm_queue_worker
from hermes.integrations import supermemory_client
from hermes.deliverables import acord25


class FakeSupa:
    def __init__(self):
        self.tables: dict[str, dict[str, dict]] = {}

    def insert(self, table, payload):
        row = dict(payload)
        row.setdefault("id", str(uuid.uuid4()))
        self.tables.setdefault(table, {})[row["id"]] = row
        return row

    def update(self, table, record_id, payload):
        row = self.tables[table][record_id]
        row.update(payload)
        return row

    def select(self, table, columns=None, params=None, limit=None):
        return list(self.tables.get(table, {}).values())[: limit or 999]


# ── identity ────────────────────────────────────────────────────────────────
def test_agent_id_defaults_and_override(monkeypatch):
    monkeypatch.delenv("HERMES_AGENT_ID", raising=False)
    assert identity.agent_id() == identity.DEFAULT_AGENT_ID == "hermes"
    monkeypatch.setenv("HERMES_AGENT_ID", "hermes-gretch")
    assert identity.agent_id() == "hermes-gretch"


def test_memory_scope_falls_back_to_agent_id(monkeypatch):
    monkeypatch.setenv("HERMES_AGENT_ID", "hermes-gretch")
    monkeypatch.delenv("HERMES_MEMORY_SCOPE", raising=False)
    assert identity.memory_scope() == "hermes-gretch"
    monkeypatch.setenv("HERMES_MEMORY_SCOPE", "shared")
    assert identity.memory_scope() == "shared"


def test_load_persona_missing_file_is_empty(monkeypatch, tmp_path):
    identity.load_persona.cache_clear()
    assert identity.load_persona(str(tmp_path / "nope.md")) == ""
    p = tmp_path / "soul.md"
    p.write_text("I am Gretchen's assistant.")
    identity.load_persona.cache_clear()
    assert "Gretchen" in identity.load_persona(str(p))


def test_disabled_tools_parsing(monkeypatch):
    monkeypatch.delenv("HERMES_DISABLED_TOOLS", raising=False)
    assert identity.disabled_tools() == frozenset()
    monkeypatch.setenv("HERMES_DISABLED_TOOLS", "web_research, foo ,")
    assert identity.disabled_tools() == frozenset({"web_research", "foo"})


def test_web_research_is_gateable():
    # The gate only matters if the tool is actually in the registry — guard against
    # a rename silently making HERMES_DISABLED_TOOLS=web_research a no-op.
    from hermes.core import nl_agent

    names = {t["function"]["name"] for t in nl_agent._TOOLS}
    assert "web_research" in names
    disabled = frozenset({"web_research"})
    active = [t for t in nl_agent._TOOLS if t["function"]["name"] not in disabled]
    assert "web_research" not in {t["function"]["name"] for t in active}
    assert "find_client" in {t["function"]["name"] for t in active}  # CRM tools remain


# ── persona overlay in the system prompt ─────────────────────────────────────
def test_compose_system_prompt_uses_persona(monkeypatch, tmp_path):
    from hermes.core import nl_agent

    identity.load_persona.cache_clear()
    monkeypatch.delenv("HERMES_PERSONA_FILE", raising=False)
    default = nl_agent._compose_system_prompt()
    assert "Lamar" in default                          # default identity
    assert "use the tools" in default                  # shared platform guide always present

    soul = tmp_path / "SOUL-GRETCHEN.md"
    soul.write_text("You are Hermes for Gretchen. Talk to Gretchen in plain English.")
    monkeypatch.setenv("HERMES_PERSONA_FILE", str(soul))
    identity.load_persona.cache_clear()
    gretch = nl_agent._compose_system_prompt()
    assert "Gretchen" in gretch
    assert "Lamar" not in gretch                        # persona replaced the identity
    assert "use the tools" in gretch                    # but platform guide stayed


# ── agent_id stamping on writes ──────────────────────────────────────────────
def test_enqueue_crm_write_stamps_agent_id(monkeypatch):
    monkeypatch.setenv("HERMES_AGENT_ID", "hermes-gretch")
    supa = FakeSupa()
    row = crm_queue_worker.enqueue_crm_write(
        supa, entity_type="Contact", payload={"name": "X"}, created_by_role="gretchen",
    )
    assert row["agent_id"] == "hermes-gretch"


def test_create_submission_stamps_agent_id(monkeypatch):
    monkeypatch.setenv("HERMES_AGENT_ID", "hermes-gretch")
    supa = FakeSupa()
    sub = store.create_submission(supa, lane="gretchen-personal-lines", client_name="Jane Roe")
    assert sub["agent_id"] == "hermes-gretch"


# ── supermemory scope isolation ──────────────────────────────────────────────
def test_supermemory_scope_tag_added_on_write(monkeypatch):
    sent = {}

    class FakeSession:
        def request(self, method, url, headers=None, json=None, timeout=None):
            sent["payload"] = json

            class R:
                ok = True
                content = b"{}"

                def json(self_inner):
                    return {}
            return R()

    c = supermemory_client.SupermemoryClient(api_key="k", scope="hermes-gretch")
    c.session = FakeSession()
    c.add_document("hello", container_tags=["hermes-docs"])
    assert "scope:hermes-gretch" in sent["payload"]["containerTags"]


def test_supermemory_unscoped_unchanged(monkeypatch):
    c = supermemory_client.SupermemoryClient(api_key="k")  # no scope
    assert c._with_scope(["hermes-docs"]) == ["hermes-docs"]


# ── Medicare-lane PHI guard (rule 3c) ────────────────────────────────────────
def test_redact_phi_strips_mbi_ssn_and_eligibility():
    from hermes.core import phi

    assert phi.contains_phi("MBI 1EG4-TE5-MK73 on file")
    out = phi.redact_phi("client 1EG4-TE5-MK73, SSN 123-45-6789, diagnosed with ESRD")
    assert "1EG4" not in out and "123-45-6789" not in out
    assert "diagnos" not in out.lower() and "ESRD" not in out
    assert phi.REDACTED in out


def test_build_medicare_memory_is_allowlist_only():
    from hermes.core import phi

    mem = phi.build_medicare_memory(
        client_name="Mary Smith",
        crm_link="https://crm/Account/abc123",
        task_context="callback re: 1EG4-TE5-MK73 plan question",  # stray MBI
    )
    assert set(mem["row"].keys()) == {"client_name", "crm_link", "task_context", "lane"}
    assert "1EG4" not in mem["content"]                       # redacted in content
    assert "1EG4" not in mem["row"]["task_context"]           # and in the row
    assert mem["metadata"]["crm_link"] == "https://crm/Account/abc123"
    assert "type:interaction" in mem["container_tags"]


def test_is_medicare_context():
    from hermes.core import phi

    assert phi.is_medicare_context(["gretchen-memory", "lane:gretchen-medicare"])
    assert phi.is_medicare_context(["type:medicare"])
    assert not phi.is_medicare_context(["client:acme", "type:dec_page"])


def test_add_document_redacts_phi_for_medicare_tags():
    sent = {}

    class FakeSession:
        def request(self, method, url, headers=None, json=None, timeout=None):
            sent["payload"] = json

            class R:
                ok = True
                content = b"{}"

                def json(self_inner):
                    return {}
            return R()

    c = supermemory_client.SupermemoryClient(api_key="k")
    c.session = FakeSession()
    c.add_document("note 1EG4-TE5-MK73", container_tags=["lane:gretchen-medicare"],
                   metadata={"client_name": "Mary", "detail": "SSN 123-45-6789"})
    assert "1EG4" not in sent["payload"]["content"]
    assert "123-45-6789" not in sent["payload"]["metadata"]["detail"]


def test_add_document_leaves_non_medicare_untouched():
    sent = {}

    class FakeSession:
        def request(self, method, url, headers=None, json=None, timeout=None):
            sent["payload"] = json

            class R:
                ok = True
                content = b"{}"

                def json(self_inner):
                    return {}
            return R()

    c = supermemory_client.SupermemoryClient(api_key="k")
    c.session = FakeSession()
    # A commercial doc with a number that looks SSN-ish must NOT be altered.
    c.add_document("EIN 12-3456789  refid 123-45-6789", container_tags=["client:acme", "type:dec_page"])
    assert "123-45-6789" in sent["payload"]["content"]


# ── ACORD 25 ─────────────────────────────────────────────────────────────────
def test_from_espo_policy_maps_and_uses_endorsement_flags():
    account = {"name": "Acme LLC", "billing_address_city": "Atlanta", "billing_address_state": "GA"}
    policies = [{
        "lineOfBusiness": "General Liability",
        "carrier": "Travelers", "naic": "12345",
        "policyNumber": "GL-001",
        "effectiveDate": "01/01/2026", "expirationDate": "01/01/2027",
        acord25.POLICY_FIELD_ADDITIONAL_INSURED: True,
        acord25.POLICY_FIELD_WAIVER_OF_SUB: False,
    }]
    coi = acord25.from_espo_policy(policies, account, holder_name="City of Atlanta")
    assert coi.insured_name == "Acme LLC"
    assert coi.holder_name == "City of Atlanta"
    assert len(coi.coverages) == 1
    cov = coi.coverages[0]
    assert cov.kind == "general_liability"
    assert cov.additional_insured is True
    assert cov.waiver_of_subrogation is False

    checks = acord25.acord_checkbox_state(coi)
    assert checks["general_liability:additional_insured"] is True
    assert checks["general_liability:waiver_of_subrogation"] is False


def test_build_field_map_drops_empty_and_places_known():
    coi = acord25.Coi(
        insured_name="Acme LLC", insured_address="Atlanta GA",
        holder_name="City of Atlanta",
        coverages=[acord25.CoverageLine(kind="general_liability", policy_number="GL-001",
                                        eff_date="01/01/2026", exp_date="01/01/2027")],
    )
    fm = acord25.build_field_map(coi)
    assert fm["INSURED"].startswith("Acme LLC")
    assert fm["GL POLICY NUMBER"] == "GL-001"
    assert "DESCRIPTION OF OPERATIONS" not in fm  # empty -> dropped


def test_supabase_logger_stamps_agent_and_never_sent(monkeypatch):
    monkeypatch.setenv("HERMES_AGENT_ID", "hermes-gretch")
    supa = FakeSupa()
    log = acord25.supabase_logger(supa)
    log({"account": "Acme LLC", "holder": "City of Atlanta", "output_path": "/tmp/x.pdf"})
    rows = supa.select("coi_drafts")
    assert rows[0]["agent_id"] == "hermes-gretch"
    assert rows[0]["auto_sent"] is False


def test_draft_coi_never_auto_sends_and_logs(monkeypatch, tmp_path):
    # No real PDF fill — stub fill_pdf so the orchestration is testable without a template.
    monkeypatch.setattr(acord25, "fill_pdf",
                        lambda t, v, o: {"written": o, "placed": list(v), "skipped": []})
    posted = []
    logged = []
    coi = acord25.Coi(insured_name="Acme LLC", holder_name="City of Atlanta",
                      coverages=[acord25.CoverageLine(kind="general_liability", policy_number="GL-001")])
    summary = acord25.draft_coi(
        coi, template_path="ignored.pdf", output_path=str(tmp_path / "out.pdf"),
        account_name="Acme LLC", holder_name="City of Atlanta",
        slack_post=lambda m: posted.append(m),
        supa_log=lambda s: logged.append(s),
    )
    assert summary["auto_sent"] is False
    assert posted and "review before sending" in posted[0]
    assert logged and logged[0]["account"] == "Acme LLC"
