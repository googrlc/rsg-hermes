"""Top-model intake synthesis — extraction, coercion, gap-fill, model routing."""
from __future__ import annotations

import json
from datetime import date

from hermes.command_center import synthesis as S
from hermes.command_center.submission import IntakeMeta, SourceChannel, SubmissionObject


class _Completions:
    def __init__(self, content):
        self.content = content
        self.kwargs = None

    def create(self, **kwargs):
        self.kwargs = kwargs
        msg = type("M", (), {"content": self.content})
        choice = type("C", (), {"message": msg})
        return type("R", (), {"choices": [choice]})


class _Chat:
    def __init__(self, content):
        self.completions = _Completions(content)


class FakeOAI:
    def __init__(self, content):
        self.chat = _Chat(content)


def _sub(**o):
    return SubmissionObject(submission_id="s1", intake=IntakeMeta(channel=SourceChannel.WEBUI), **o)


# --- synthesize_fields ---
def test_synthesize_filters_to_known_confident_fields(monkeypatch):
    import hermes_core.llm_client as llm
    payload = json.dumps({
        "insured_name": "Acme LLC", "current_premium": "1200", "xdate": "2027-01-15",
        "email": "", "bogus_field": "x",
    })
    fake = FakeOAI(payload)
    monkeypatch.setattr(llm, "get_client", lambda: fake)
    monkeypatch.setattr(llm, "resolve_model", lambda m: m or "fallback")
    monkeypatch.setenv("HERMES_SYNTHESIS_MODEL", "top-claude")
    out = S.synthesize_fields("some dec page text", doc_type="dec_page")
    assert out == {"insured_name": "Acme LLC", "current_premium": "1200", "xdate": "2027-01-15"}
    # routed to the top model group, not the default agent model
    assert fake.chat.completions.kwargs["model"] == "top-claude"


def test_synthesize_empty_text_no_call():
    assert S.synthesize_fields("   ") == {}


def test_synthesize_no_llm_returns_empty(monkeypatch):
    import hermes_core.llm_client as llm

    def boom():
        raise RuntimeError("no key configured")

    monkeypatch.setattr(llm, "get_client", boom)
    assert S.synthesize_fields("text") == {}


def test_synthesize_bad_json_returns_empty(monkeypatch):
    import hermes_core.llm_client as llm
    monkeypatch.setattr(llm, "get_client", lambda: FakeOAI("this is not json"))
    monkeypatch.setattr(llm, "resolve_model", lambda m: "top")
    assert S.synthesize_fields("text") == {}


# --- apply_synthesis ---
def test_apply_coerces_and_gapfills_with_provenance():
    sub = _sub()
    S.apply_synthesis(sub, {
        "insured_name": "Acme LLC", "current_premium": "1,200.50",
        "xdate": "2027-01-15", "email": "a@b.com", "fein": "12-3456789",
    }, source="synthesis:top-claude")
    assert sub.client_name == "Acme LLC"
    assert sub.current_premium == 1200.50
    assert sub.current_policy_expiration == date(2027, 1, 15)
    assert sub.applicant.email == "a@b.com"
    assert sub.applicant.fein == "12-3456789"
    assert sub.enrichment.sources["client_name"] == "synthesis:top-claude"
    assert sub.enrichment.sources["applicant.email"] == "synthesis:top-claude"


def test_apply_never_overwrites_existing():
    sub = _sub(client_name="Existing Co")
    S.apply_synthesis(sub, {"insured_name": "Synthesized Co"}, source="x")
    assert sub.client_name == "Existing Co"          # gap-fill only
    assert "client_name" not in sub.enrichment.sources


def test_apply_drops_uncoercible_date():
    sub = _sub()
    S.apply_synthesis(sub, {"xdate": "not-a-date"}, source="x")
    assert sub.current_policy_expiration is None


def test_synthesis_model_env_override(monkeypatch):
    monkeypatch.setenv("HERMES_SYNTHESIS_MODEL", "claude-opus-group")
    assert S.synthesis_model() == "claude-opus-group"
