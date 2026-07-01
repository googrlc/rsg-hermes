"""Tests for hermes.renewals — card rendering, completion routing, webhook auth.

No network: EspoClient, save_document, and SlackNotifier are monkeypatched.
"""
import pytest

from hermes.renewals import card, complete, sweep, worksheet
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
        "renewal_proposed_premium": 8895,
        "renewal_premium": 9140,
        "premium_change": 8.5,
        "expiration_date": "2026-07-14",
        "urgency": "High",
        "pipeline_stage": "Quote Requested",
        "disposition": None,
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
    r["pipeline_stage"] = rconfig.PIPELINE_STAGE_WON
    r["disposition"] = rconfig.DISPOSITION_WON
    _patch_espo(monkeypatch, r)
    result = complete.handle(_payload())
    assert result["action"] == "won"
    assert result["filed"] is True
    assert len(captured["saved"]) == 1
    assert captured["slack"][0]["channel"] == rconfig.SLACK_RSG_WINS


def test_handle_lost_files_and_posts_loss(monkeypatch, captured):
    r = _sample_renewal()
    r["pipeline_stage"] = rconfig.PIPELINE_STAGE_LOST
    r["disposition"] = rconfig.DISPOSITION_LOST
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
    r["pipeline_stage"] = rconfig.PIPELINE_STAGE_WON
    r["disposition"] = rconfig.DISPOSITION_WON
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


def _all_boxes(value: bool) -> dict:
    """A renewal dict with every CHECKBOX_FIELDS key set to value."""
    return {f: value for f in rconfig.CHECKBOX_FIELDS}


def test_checkbox_fields_nonempty():
    assert rconfig.CHECKBOX_FIELDS, "CHECKBOX_FIELDS must not be empty"


def test_each_checkbox_field_flows_into_merge():
    # every config checkbox must be read by merge_fields() — a rename in one
    # place but not the other fails loudly.
    base = worksheet.merge_fields(_all_boxes(False))
    for field in rconfig.CHECKBOX_FIELDS:
        toggled = _all_boxes(False)
        toggled[field] = True
        assert worksheet.merge_fields(toggled) != base, (
            f"{field} in CHECKBOX_FIELDS is not consumed by merge_fields()"
        )


def test_each_checkbox_field_renders_in_worksheet():
    base = worksheet.build_worksheet_content(_all_boxes(False))
    for field in rconfig.CHECKBOX_FIELDS:
        toggled = _all_boxes(False)
        toggled[field] = True
        assert worksheet.build_worksheet_content(toggled) != base, (
            f"{field} in CHECKBOX_FIELDS does not affect the filed worksheet doc"
        )


def test_worksheet_links_back_to_records(monkeypatch):
    monkeypatch.setattr(rconfig, "ESPO_BASE_URL", "https://espo.example.com")
    r = _sample_renewal()
    r["accountId"] = "ACC1"
    doc = complete._worksheet_doc(r)
    assert "## Links" in doc
    assert "https://espo.example.com/#Account/view/ACC1" in doc      # client record
    assert "https://espo.example.com/#Renewal/view/r1" in doc        # renewal record


def test_acknowledge_is_idempotent():
    # build a real won card, then acknowledge it twice
    blocks = complete._completion_blocks(
        _sample_renewal(), header="✅ won", task_url="http://t", worksheet_url="http://w")
    # first ack: button removed, footer added
    acked = complete.apply_acknowledgement(blocks, "U123")
    assert acked is not None
    flat = repr(acked)
    assert "Acknowledged by <@U123>" in flat
    action_ids = [e.get("action_id") for b in acked
                  if b.get("type") == "actions" for e in b.get("elements", [])]
    assert not any(a and a.startswith("renewal_ack_") for a in action_ids)  # ack button gone
    assert any(a == "renewal_open_worksheet" for a in action_ids)           # link buttons kept
    # second ack on the already-acked card: no-op
    assert complete.apply_acknowledgement(acked, "U999") is None


def test_handle_in_flight_does_not_file(monkeypatch, captured):
    r = _sample_renewal()
    r["pipeline_stage"] = rconfig.PIPELINE_STAGE_QUOTE_REQUESTED
    _patch_espo(monkeypatch, r)
    result = complete.handle(_payload())
    assert result["action"] == "in_flight"
    assert captured["saved"] == []
    assert captured["slack"] == []


def test_handle_rewritten_routes_to_wins(monkeypatch, captured):
    r = _sample_renewal()
    r["pipeline_stage"] = rconfig.PIPELINE_STAGE_WON
    r["disposition"] = rconfig.DISPOSITION_REWRITTEN
    _patch_espo(monkeypatch, r)
    result = complete.handle(_payload())
    assert result["action"] == rconfig.DISPOSITION_REWRITTEN
    assert captured["slack"][0]["channel"] == rconfig.SLACK_RSG_WINS


def test_build_worksheet_content_uses_nested_worksheet_fields():
    renewal = _sample_renewal()
    renewal["renewalWorksheet"] = {
        "lob_variant": "commercial_auto",
        "vehicle_count": 7,
        "garaging_zip": "30303",
        "completion_type": "full_review",
        "notes": "All units confirmed",
    }
    doc = worksheet.build_worksheet_content(renewal)
    assert "LOB variant: commercial_auto" in doc
    assert "Vehicle count: 7" in doc
    assert "Garaging zip: 30303" in doc
    assert "completion_type" not in doc


def test_handle_ignores_non_renewal_parent(captured):
    result = complete.handle(_payload(parent_type="Account"))
    assert "skipped" in result
    assert captured["saved"] == []


def test_handle_ignores_non_completion_event(captured):
    result = complete.handle(_payload(event="service.task_started"))
    assert "skipped" in result
    assert captured["saved"] == []


# --------------------------------------------------------------- sweep notify

@pytest.fixture
def captured_sweep(monkeypatch):
    """Capture SlackNotifier posts at the point the sweep imports it (lazy import
    from hermes.integrations.slack_notifier inside _notify)."""
    posts = []

    class FakeSlack:
        def __init__(self, channel=None):
            self.channel = channel

        def post_message(self, *, text, blocks=None):
            posts.append({"channel": self.channel, "text": text, "blocks": blocks})

    import hermes.integrations.slack_notifier as sn
    monkeypatch.setattr(sn, "SlackNotifier", FakeSlack)
    monkeypatch.setattr(rconfig, "ESPO_BASE_URL", "https://espo.example.com")
    return posts


def test_sweep_notify_posts_one_card_per_renewal(captured_sweep):
    r = _sample_renewal()
    sweep._notify([(r, {"id": "task1"})])

    # digest line + one card
    assert len(captured_sweep) == 2
    assert "1 renewal task(s) ready" in captured_sweep[0]["text"]

    card_post = captured_sweep[1]
    assert card_post["channel"] == rconfig.SLACK_GRETCHEN_TASKS
    blocks = card_post["blocks"]
    flat = repr(blocks)
    # correct client + LOB mapping (not the renewal name, not "—")
    assert "Martinez Landscaping LLC" in flat
    assert "Commercial Auto" in flat
    assert "Client:" in flat and "Line of business:" in flat and "Renewal date:" in flat
    # actionable: worksheet (-> renewal record), open task (-> task), acknowledge
    action_ids = [e.get("action_id") for b in blocks
                  if b.get("type") == "actions" for e in b.get("elements", [])]
    assert "renewal_open_worksheet" in action_ids
    assert "renewal_open_task" in action_ids
    assert any(a and a.startswith("renewal_ack_") for a in action_ids)
    # task button deep-links the created task; worksheet links the renewal record
    urls = [e.get("url") for b in blocks if b.get("type") == "actions"
            for e in b.get("elements", []) if e.get("url")]
    assert "https://espo.example.com/#Task/view/task1" in urls
    assert "https://espo.example.com/#Renewal/view/r1" in urls


def test_sweep_notify_noop_when_nothing_created(captured_sweep):
    sweep._notify([])
    assert captured_sweep == []


def test_client_name_never_uses_renewal_name_whole():
    # the screenshot bug: accountName empty -> must NOT show "X - LOB Renewal" whole
    bad = {"id": "r9", "name": "Dream Chaser Trucking - Other Renewal"}
    assert complete._client_name(bad) == "Dream Chaser Trucking"
