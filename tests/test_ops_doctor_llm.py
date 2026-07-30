"""LLM-gateway probe for ``hermes --ops-doctor``.

Every Hermes LLM path routes through ``hermes.core.llm_client``, so a rejected
key (a rotated/expired LiteLLM virtual key → 401 ``token_not_found_in_db``)
takes down all AI features at once. The probe added here surfaces that in the
health check instead of only at agent runtime.
"""

from __future__ import annotations

from hermes.core import llm_client
from hermes.operations import ops_doctor as OD


# ── key_tail: diagnosable, never the secret ──────────────────────────────────
def test_key_tail_shows_last_four_only():
    assert OD._key_tail("sk-abcdefgh1234") == "...1234"
    assert OD._key_tail("") == "(none)"
    assert OD._key_tail("ab") == "(set)"  # too short to reveal a tail


# ── error classification ─────────────────────────────────────────────────────
def test_classify_401_token_not_found_is_named():
    exc = Exception(
        "Error code: 401 - {'error': {'message': 'Authentication Error, "
        "Invalid proxy server token passed. ... Unable to find token in cache "
        "or `LiteLLM_VerificationTokenTable`', 'type': 'token_not_found_in_db'}}"
    )
    out = OD._classify_llm_error(exc)
    assert out.startswith("key rejected by gateway (401)")
    assert "token_not_found_in_db" in out  # original detail preserved


def test_classify_missing_sdk():
    assert OD._classify_llm_error(ModuleNotFoundError("No module named 'openai'")) == (
        "openai SDK not installed"
    )


def test_classify_generic_error_passes_through():
    assert OD._classify_llm_error(Exception("connection refused")) == "connection refused"


# ── check_llm_gateway ────────────────────────────────────────────────────────
def _patch_resolvers(monkeypatch, *, key, base="http://litellm.internal:4000"):
    monkeypatch.setattr(llm_client, "_resolve_api_key", lambda: key)
    monkeypatch.setattr(llm_client, "_resolve_base_url", lambda: base)
    monkeypatch.setattr(llm_client, "default_model", lambda: "gpt-4.1-mini")


def test_no_key_is_a_clear_fail(monkeypatch):
    _patch_resolvers(monkeypatch, key="")
    res = OD.check_llm_gateway()
    assert not res.ok
    assert res.key_tail == "(none)"
    assert "no API key configured" in res.error


def test_rejected_key_reports_401(monkeypatch):
    _patch_resolvers(monkeypatch, key="sk-liveW-cA")

    class FakeModels:
        def list(self):
            raise Exception(
                "Error code: 401 - token_not_found_in_db: Unable to find token"
            )

    class FakeClient:
        models = FakeModels()

    monkeypatch.setattr(llm_client, "get_client", lambda: FakeClient())
    res = OD.check_llm_gateway()
    assert not res.ok
    assert res.endpoint == "http://litellm.internal:4000"
    assert res.key_tail == "...W-cA"
    assert res.error.startswith("key rejected by gateway (401)")


def test_healthy_gateway_is_ok(monkeypatch):
    _patch_resolvers(monkeypatch, key="sk-goodkey99")

    class FakeModels:
        def list(self):
            return [{"id": "gpt-4.1-mini"}]

    class FakeClient:
        models = FakeModels()

    monkeypatch.setattr(llm_client, "get_client", lambda: FakeClient())
    res = OD.check_llm_gateway()
    assert res.ok
    assert res.error is None


def test_no_base_url_labels_openai_direct(monkeypatch):
    _patch_resolvers(monkeypatch, key="sk-goodkey99", base="")

    class FakeClient:
        class models:  # noqa: N801 — trivial stub
            @staticmethod
            def list():
                return []

    monkeypatch.setattr(llm_client, "get_client", lambda: FakeClient())
    res = OD.check_llm_gateway()
    assert res.ok
    assert "OpenAI public API" in res.endpoint


# ── report integration ───────────────────────────────────────────────────────
class _AllGreenSupa:
    def select(self, table, *, columns="*", params=None, limit=100):
        if table == "hermes_ai_roles":
            return [
                {"role_name": r}
                for r in (
                    "HermesCommissionAuditor",
                    "HermesRenewalSpecialist",
                    "HermesFinanceOps",
                    "HermesOpsRouter",
                )
            ]
        return [{columns: "x"}]


def test_report_ok_reflects_a_failing_llm_probe(monkeypatch):
    monkeypatch.setattr(
        OD,
        "check_llm_gateway",
        lambda: OD.LLMCheckResult(
            ok=False,
            endpoint="http://litellm.internal:4000",
            key_tail="...W-cA",
            model_group="gpt-4.1-mini",
            error="key rejected by gateway (401)",
        ),
    )
    report = OD.run_ops_doctor(_AllGreenSupa(), check_movement=False, check_llm=True)

    assert not report.ok  # tables green, but the gateway is down → overall red
    body = "\n".join(report.format_lines())
    assert "LLM gateway:" in body
    assert "key rejected by gateway (401)" in body


def test_report_stays_green_when_llm_probe_disabled(monkeypatch):
    # Guard: opting out (tests/offline) must not silently mark things red.
    report = OD.run_ops_doctor(_AllGreenSupa(), check_movement=False, check_llm=False)
    assert report.ok
    assert report.llm is None
    assert "LLM gateway:" not in "\n".join(report.format_lines())
