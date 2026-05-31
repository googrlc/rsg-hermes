"""Tests for hermes.renewals — card rendering, completion routing, webhook auth.

No network: EspoClient, save_document, and SlackNotifier are monkeypatched.
"""
import pytest

from hermes.renewals import card, complete
from hermes.renewals import config as rconfig


def _sample_renewal():
    return {
        "id": "r1",
        "name": "Martinez Landscaping LLC - Commercial Auto Renewal",
        "accountName": "Martinez Landscaping LLC",
        "accountId": "a1",
        "carrier": "Progressive Commercial",
        "line_of_business": "Commercial Auto",
        "current_premium": 8420,
        "renewal_premium": 9140,
        "premium_change": 8.5,
        "expiration_date": "2026-07-14",
        "urgency": "High",
        "lost_reason": None,
        "renewal_notes": None,
    }


# ---------------------------------------------------------------- card

def test_card_contains_key_sections():
    out = card.build_card(_sample_renewal())
    assert "Martinez Landscaping LLC" in out          # account name
    assert "Decision guide" in out                    # the premium guide
    assert "Renewal Notes" in out                     # client-states instruction
    assert "$8,420" in out                            # expiring premium, formatted


def test_card_handles_missing_fields():
    out = card.build_card({"id": "r2"})               # almost-empty record
    assert "Decision guide" in out
    assert "—" in out                                 # graceful blanks, no crash


# ------------------------------------------------------------ verify_secret

@pytest.fixture
def with_secret(monkeypatch):
    # complete.config is the same module object as rconfig
    monkeypatch.setattr(rconfig, "SERVICE_WEBHOOK_SECRET", "topsecret")
    return "topsecret"


def test_verify_secret_accepts_correct(with_secret):
    assert complete.verify_secret("topsecret") is True


def test_verify_secret_rejects_wrong(with_secret):
    assert complete.verify_secret("nope") is False


def test_verify_secret_rejects_missing(with_secret):
    assert complete.verify_secret(None) is False
    assert complete.verify_secret("") is False


def test_verify_secret_rejects_when_unset(monkeypatch):
    monkeypatch.setattr(rconfig, "SERVICE_WEBHOOK_SECRET", "")
    assert complete.verify_secret("anything") is False


# ------------------------------------------------------------ handle() routing

class _FakeEspo:
    def __init__(self, renewal):
        self._renewal = renewal

    def get(self, path):
        return self._renewal


@pytest.fixture
def captured(monkeypatch):
    calls = {"saved": [], "slack": []}

    def fake_save_document(**kwargs):
        calls["saved"].append(kwargs)
        return {"id": "doc1"}

    class FakeSlack:
        def __init__(self, channel=None):
            self.channel = channel

        def post_message(self, *, text, blocks=None):
            calls["slack"].append({"channel": self.channel, "text": text, "blocks": blocks})

    monkeypatch.setattr(complete, "save_document", fake_save_document)
    monkeypatch.setattr(complete, "SlackNotifier", FakeSlack)
    return calls


def _payload(parent_type="Renewal", event="service.task_completed"):
    return {
        "eventType": event,
        "task": {"parentType": parent_type, "parentId": "r1", "status": "Completed"},
    }


def _patch_espo(monkeypatch, renewal):
    monkeypatch.setattr(complete, "EspoClient", lambda *a, **k: _FakeEspo(renewal))


def test_handle_won_files_and_posts_win(monkeypatch, captured):
    r = _sample_renewal()
    r["stage"] = rconfig.STAGE_WON
    _patch_espo(monkeypatch, r)
    result = complete.handle(_payload())
    assert result["action"] == "won"
    assert result["filed"] is True
    assert len(captured["saved"]) == 1
    assert captured["slack"][0]["channel"] == rconfig.SLACK_RSG_WINS


def test_handle_lost_files_and_posts_loss(monkeypatch, captured):
    r = _sample_renewal()
    r["stage"] = rconfig.STAGE_LOST
    r["lost_reason"] = "Price"
    r["renewal_notes"] = "client states a competitor came in 20% lower"
    _patch_espo(monkeypatch, r)
    result = complete.handle(_payload())
    assert result["action"] == "lost"
    assert len(captured["saved"]) == 1
    assert captured["slack"][0]["channel"] == rconfig.SLACK_THE_BOSS
    assert "Price" in captured["slack"][0]["text"]


def test_won_card_is_compact_with_buttons(monkeypatch, captured):
    r = _sample_renewal()
    r["stage"] = rconfig.STAGE_WON
    _patch_espo(monkeypatch, r)
    complete.handle({"eventType": "service.task_completed",
                     "task": {"parentType": "Renewal", "parentId": "r1",
                              "id": "task1", "status": "Completed"}})
    blocks = captured["slack"][0]["blocks"]
    assert blocks, "won message must use Block Kit blocks"
    flat = repr(blocks)
    # compact fields, not a full description
    assert "Client:" in flat and "Line of business:" in flat and "Renewal date:" in flat
    assert "premium" not in flat.lower()  # no full premium dump in the card
    # acknowledge button present with a renewal_ack_ action id
    action_ids = [e.get("action_id") for b in blocks
                  if b.get("type") == "actions" for e in b.get("elements", [])]
    assert any(a and a.startswith("renewal_ack_") for a in action_ids)


def test_handle_in_flight_does_not_file(monkeypatch, captured):
    r = _sample_renewal()
    r["stage"] = rconfig.STAGE_QUOTE_REQUESTED
    _patch_espo(monkeypatch, r)
    result = complete.handle(_payload())
    assert result["action"] == "in_flight"
    assert captured["saved"] == []
    assert captured["slack"] == []


def test_handle_ignores_non_renewal_parent(captured):
    result = complete.handle(_payload(parent_type="Account"))
    assert "skipped" in result
    assert captured["saved"] == []


def test_handle_ignores_non_completion_event(captured):
    result = complete.handle(_payload(event="service.task_started"))
    assert "skipped" in result
    assert captured["saved"] == []
