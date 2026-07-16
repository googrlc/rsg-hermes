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
    r["pipeline_stage"] = rconfig.PIPELINE_STAGE_CLOSED
    r["disposition"] = rconfig.DISPOSITION_RENEWED
    _patch_espo(monkeypatch, r)
    result = complete.handle(_payload())
    assert result["action"] == rconfig.DISPOSITION_RENEWED
    assert result["filed"] is True
    assert len(captured["saved"]) == 1
    assert captured["slack"][0]["channel"] == rconfig.SLACK_RSG_WINS


def test_handle_lost_files_and_posts_loss(monkeypatch, captured):
    r = _sample_renewal()
    r["pipeline_stage"] = rconfig.PIPELINE_STAGE_CLOSED
    r["disposition"] = rconfig.DISPOSITION_LOST_PRICE
    r["renewal_notes"] = "client states a competitor came in 20% lower"
    _patch_espo(monkeypatch, r)
    result = complete.handle(_payload())
    assert result["action"] == "lost"
    assert len(captured["saved"]) == 1
    assert captured["slack"][0]["channel"] == rconfig.SLACK_THE_BOSS
    assert "Price" in captured["slack"][0]["text"]


def test_won_card_is_compact_with_buttons(monkeypatch, captured):
    r = _sample_renewal()
    r["pipeline_stage"] = rconfig.PIPELINE_STAGE_CLOSED
    r["disposition"] = rconfig.DISPOSITION_RENEWED
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
    r["pipeline_stage"] = rconfig.PIPELINE_STAGE_CLOSED
    r["disposition"] = rconfig.DISPOSITION_REWRITTEN
    _patch_espo(monkeypatch, r)
    result = complete.handle(_payload())
    assert result["action"] == rconfig.DISPOSITION_REWRITTEN
    assert captured["slack"][0]["channel"] == rconfig.SLACK_RSG_WINS



# ---------------------------------------------------- legacy back-compat synthesis
# Pre-reshape records carry `stage` (Renewed - Won / Lost) + `lost_reason`
# instead of the v6 `pipeline_stage` + `disposition`. _disposition() must
# synthesize a v6 value so those records still file + post correctly.

def _legacy_renewal(stage, lost_reason=None):
    r = _sample_renewal()
    r["pipeline_stage"] = None      # legacy record has no v6 field
    r["stage"] = stage
    r["disposition"] = None
    r["lost_reason"] = lost_reason
    return r


def test_legacy_won_stage_synthesizes_renewed_and_routes_to_wins(monkeypatch, captured):
    r = _legacy_renewal(rconfig.LEGACY_PIPELINE_STAGE_WON)
    _patch_espo(monkeypatch, r)
    result = complete.handle(_payload())
    assert result["disposition"] == rconfig.DISPOSITION_RENEWED
    assert result["action"] == rconfig.DISPOSITION_RENEWED
    assert result["filed"] is True
    assert captured["slack"][0]["channel"] == rconfig.SLACK_RSG_WINS


def test_legacy_lost_with_price_reason_synthesizes_lost_price(monkeypatch, captured):
    r = _legacy_renewal(rconfig.LEGACY_PIPELINE_STAGE_LOST, lost_reason="Price")
    _patch_espo(monkeypatch, r)
    result = complete.handle(_payload())
    assert result["disposition"] == rconfig.DISPOSITION_LOST_PRICE
    assert result["action"] == "lost"
    assert captured["slack"][0]["channel"] == rconfig.SLACK_THE_BOSS
    assert "Price" in captured["slack"][0]["text"]


def test_legacy_lost_with_unresponsive_synthesizes_lost_no_response(monkeypatch, captured):
    r = _legacy_renewal(rconfig.LEGACY_PIPELINE_STAGE_LOST, lost_reason="Unresponsive")
    _patch_espo(monkeypatch, r)
    result = complete.handle(_payload())
    assert result["disposition"] == rconfig.DISPOSITION_LOST_NO_RESPONSE
    assert result["action"] == "lost"


def test_legacy_lost_unmapped_reason_defaults_to_do_not_renew(monkeypatch, captured):
    r = _legacy_renewal(rconfig.LEGACY_PIPELINE_STAGE_LOST, lost_reason="Something unmapped")
    _patch_espo(monkeypatch, r)
    result = complete.handle(_payload())
    assert result["disposition"] == rconfig.DISPOSITION_DO_NOT_RENEW
    assert result["action"] == "lost"
    assert captured["slack"][0]["channel"] == rconfig.SLACK_THE_BOSS


def test_legacy_lost_no_reason_defaults_to_do_not_renew(monkeypatch, captured):
    r = _legacy_renewal(rconfig.LEGACY_PIPELINE_STAGE_LOST, lost_reason=None)
    _patch_espo(monkeypatch, r)
    result = complete.handle(_payload())
    assert result["disposition"] == rconfig.DISPOSITION_DO_NOT_RENEW


def test_explicit_disposition_takes_priority_over_legacy_stage(monkeypatch, captured):
    # stage says Won but an explicit v6 disposition is set -> explicit wins
    r = _legacy_renewal(rconfig.LEGACY_PIPELINE_STAGE_WON)
    r["disposition"] = rconfig.DISPOSITION_REWRITTEN
    _patch_espo(monkeypatch, r)
    result = complete.handle(_payload())
    assert result["disposition"] == rconfig.DISPOSITION_REWRITTEN
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


# ============================================================ renewal_worksheet
# Unit tests for hermes/commands/renewal_worksheet.py
# All tests use synthetic identifiers only — no real client names or policy
# numbers appear in any assertion string.

from hermes.commands import renewal_worksheet as rw_mod
from hermes.core.dispatcher import Dispatcher, DispatchResult


def _make_dispatcher():
    """Instantiate Dispatcher without a live Supabase connection."""
    from unittest.mock import MagicMock
    d = Dispatcher(use_openai=False)
    d.supa = MagicMock()
    return d


def _policy_row(
    *,
    account="Test Corp LLC",
    policy_number="TST-0001",
    line_of_business="Commercial Auto",
    carrier="Test Carrier",
    status="Active",
    exp="2027-01-01",
    acct_id="acct-1",
    policy_id="pol-1",
):
    return {
        "id": policy_id,
        "name": f"{account} - {line_of_business} Renewal",
        "accountName": account,
        "accountId": acct_id,
        "policyNumber": policy_number,
        "lineOfBusiness": line_of_business,
        "carrier": carrier,
        "status": status,
        "expirationDate": exp,
        "premiumAmount": 5000,
    }


# ---------------------------------------------------------------- parse_request

def test_parse_request_policy_number():
    result = rw_mod.parse_request("prepare renewal worksheet for policy TST-0001")
    assert result["policy_number"] == "TST-0001"


def test_parse_request_policy_number_normalised():
    result = rw_mod.parse_request("prepare renewal worksheet for policy  tst-0001 ")
    assert result["policy_number"] == "TST-0001"


def test_parse_request_client_name():
    result = rw_mod.parse_request("prepare a renewal worksheet for Test Corp LLC")
    assert result["client_name"] == "Test Corp LLC"
    assert result["policy_number"] is None


def test_parse_request_build_variant():
    result = rw_mod.parse_request("build renewal worksheet for Test Corp LLC")
    assert result["client_name"] == "Test Corp LLC"


def test_parse_request_generate_variant():
    result = rw_mod.parse_request("generate a renewal worksheet for Test Corp LLC")
    assert result["client_name"] == "Test Corp LLC"


def test_parse_request_create_variant():
    result = rw_mod.parse_request("create renewal worksheet for Test Corp LLC")
    assert result["client_name"] == "Test Corp LLC"


# ---------------------------------------------------------------- normalise

def test_normalise_strips_and_uppercases():
    assert rw_mod._normalise_policy_number("  tst-0001  ") == "TST-0001"


def test_normalise_collapses_internal_spaces():
    assert rw_mod._normalise_policy_number("TST  0001") == "TST 0001"


# ---------------------------------------------------------------- route precedence

def test_worksheet_route_precedes_renewal_sentinel():
    """'prepare a renewal worksheet for ...' must NOT route to revenue.handle."""
    from unittest.mock import MagicMock, patch
    from hermes.core.dispatcher import DispatchResult

    d = _make_dispatcher()
    mock_client = MagicMock()
    mock_client.get.return_value = {
        "list": [_policy_row(account="Test Corp LLC", policy_number="TST-0001")]
    }

    # Patch the worksheet handler so we can confirm it is the one called.
    with patch("hermes.commands.renewal_worksheet.handle") as rw_handle:
        rw_handle.return_value = DispatchResult(True, "worksheet ran for Test Corp LLC")
        result = d.dispatch(mock_client, "prepare a renewal worksheet for Test Corp LLC")
        rw_handle.assert_called_once()

    assert result.ok
    assert "Test Corp LLC" in result.message


def test_renewal_audit_still_routes_to_revenue():
    """Existing 'renewal audit' command must not be intercepted by the worksheet route."""
    from unittest.mock import MagicMock, patch
    from hermes.core.dispatcher import DispatchResult

    d = _make_dispatcher()
    mock_client = MagicMock()
    # revenue.handle queries Policy and Task entities for the audit
    mock_client.get.return_value = {"list": []}

    with patch("hermes.commands.renewal_worksheet.handle") as rw_handle:
        rw_handle.return_value = DispatchResult(True, "worksheet ran")
        result = d.dispatch(mock_client, "renewal audit")
        rw_handle.assert_not_called()

    # result.ok is True and comes from revenue.handle (not the worksheet handler)
    assert result.ok


# ---------------------------------------------------------------- NowCerts test doubles

def _nc_detail(*, policy_number="TST-0001", carrier="Test Carrier",
               lob="Commercial Auto", exp="2027-01-01", eff="2026-01-01",
               premium=5000, status="Active", guid="pol-guid-1",
               insured_guid="ins-guid-1"):
    """A NowCerts PolicyDetail record shaped like find_policy_by_number returns."""
    return {
        "databaseId": guid,
        "insuredDatabaseId": insured_guid,
        "policyNumber": policy_number,
        "carrierName": carrier,
        "lineOfBusiness": lob,
        "effectiveDate": eff,
        "expirationDate": exp,
        "premium": premium,
        "policyStatus": status,
    }


def _mock_nowcerts(detail):
    """A NowCerts client whose find_policy_by_number returns *detail* only for an
    exact number match — mirrors the real OData exact filter (no fuzzy match)."""
    from unittest.mock import MagicMock
    nc = MagicMock()
    want = None if detail is None else detail.get("policyNumber")

    def _find(number):
        if isinstance(detail, dict) and detail.get("_ambiguous"):
            return detail
        return detail if (detail is not None and number == want) else None

    nc.find_policy_by_number.side_effect = _find
    nc.is_insured_active.return_value = True
    return nc


def _candidate(*, policy_number="TST-0001", client_name="Test Corp LLC",
               lob="Commercial Auto", exp="2027-01-01", guid="pol-guid-1",
               state="eligible", risk="AT_RISK"):
    return {
        "client_name": client_name,
        "policy_number": policy_number,
        "line_of_business": lob,
        "expiration_date": exp,
        "risk_status": risk,
        "eligibility_state": state,
        "nowcerts_policy_guid": guid,
    }


def _supa_returning(rows):
    from unittest.mock import MagicMock
    supa = MagicMock()
    supa.select.return_value = rows
    return supa


# ---------------------------------------------------------------- exact policy match

def test_exact_policy_match_returns_worksheet():
    nc = _mock_nowcerts(_nc_detail(policy_number="TST-0001"))
    supa = _supa_returning([_candidate(policy_number="TST-0001")])
    result = rw_mod.handle(None, "prepare renewal worksheet for policy TST-0001", supa=supa, nowcerts=nc)
    assert result.ok
    assert "TST-0001" in result.message
    assert result.data["source"] == "nowcerts"


def test_exact_policy_no_fuzzy_cross_match():
    """Policy TST-0001 must not match TST-00010 (exact OData filter, no prefix match)."""
    nc = _mock_nowcerts(_nc_detail(policy_number="TST-00010"))
    supa = _supa_returning([])
    result = rw_mod.handle(None, "prepare renewal worksheet for policy TST-0001", supa=supa, nowcerts=nc)
    assert not result.ok
    assert result.data["reconciliation_needed"] is True


# ---------------------------------------------------------------- missing policy

def test_missing_policy_returns_reconciliation_needed():
    nc = _mock_nowcerts(None)
    supa = _supa_returning([])
    result = rw_mod.handle(None, "prepare renewal worksheet for policy NOTEXIST-999", supa=supa, nowcerts=nc)
    assert not result.ok
    assert result.data["reconciliation_needed"] is True
    assert "NOTEXIST-999" in result.message


def test_missing_client_returns_reconciliation_needed():
    nc = _mock_nowcerts(None)
    supa = _supa_returning([])  # no candidate rows for the name
    result = rw_mod.handle(None, "prepare renewal worksheet for Unknown Client XYZ", supa=supa, nowcerts=nc)
    assert not result.ok
    assert result.data["reconciliation_needed"] is True


# ---------------------------------------------------------------- ambiguity

def test_ambiguous_policy_number_returns_matches():
    """Duplicate policy numbers in NowCerts → no worksheet, surface matches, escalate."""
    ambiguous = {"_ambiguous": True, "matches": [_nc_detail(guid="a"), _nc_detail(guid="b")]}
    nc = _mock_nowcerts(ambiguous)
    supa = _supa_returning([])
    result = rw_mod.handle(None, "prepare renewal worksheet for policy DUP-001", supa=supa, nowcerts=nc)
    assert not result.ok
    assert result.data["ambiguous"] is True
    assert len(result.data["matches"]) == 2


def test_ambiguous_client_name_returns_candidates():
    """Multiple candidates matching a client name → no worksheet, list candidates."""
    nc = _mock_nowcerts(None)
    supa = _supa_returning([
        _candidate(policy_number="POL-001", lob="Commercial Auto"),
        _candidate(policy_number="POL-002", lob="General Liability"),
    ])
    result = rw_mod.handle(None, "prepare renewal worksheet for Test Corp LLC", supa=supa, nowcerts=nc)
    assert not result.ok
    assert result.data["ambiguous"] is True
    assert len(result.data["candidates"]) == 2


def test_ambiguous_does_not_create_records():
    """Ambiguous responses must never silently choose a record — ok=False, no worksheet key."""
    nc = _mock_nowcerts(None)
    supa = _supa_returning([
        _candidate(policy_number="POL-001"),
        _candidate(policy_number="POL-002"),
    ])
    result = rw_mod.handle(None, "prepare renewal worksheet for Test Corp LLC", supa=supa, nowcerts=nc)
    assert not result.ok
    assert "worksheet" not in (result.data or {})


# ---------------------------------------------------------------- idempotency

def test_repeated_identical_request_returns_same_result():
    """Calling handle() twice with the same input returns identical results."""
    supa = _supa_returning([_candidate(policy_number="TST-0001")])
    r1 = rw_mod.handle(None, "prepare renewal worksheet for policy TST-0001",
                       supa=supa, nowcerts=_mock_nowcerts(_nc_detail(policy_number="TST-0001")))
    r2 = rw_mod.handle(None, "prepare renewal worksheet for policy TST-0001",
                       supa=supa, nowcerts=_mock_nowcerts(_nc_detail(policy_number="TST-0001")))
    assert r1.ok == r2.ok
    assert r1.message == r2.message


# ---------------------------------------------------------------- excluded filter

def test_excluded_candidates_absent_from_client_lookup():
    """A client with only excluded (cancelled/expired) candidates → reconciliation-needed.

    The DB query filters eligibility_state!=excluded, so the mock returns no rows.
    """
    nc = _mock_nowcerts(None)
    supa = _supa_returning([])  # excluded rows filtered out at the query
    result = rw_mod.handle(None, "prepare renewal worksheet for Test Corp LLC", supa=supa, nowcerts=nc)
    assert not result.ok
    assert result.data["reconciliation_needed"] is True
