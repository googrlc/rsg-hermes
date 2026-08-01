"""Tests for the Nextcloud Deck client and its API surface.

Two behaviours here are not obvious and were both learned against the live
instance: POST /cards silently ignores `duedate`, and board/list names are not
typed the way they are stored ("To Do" vs "To do").
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from hermes_integrations.nextcloud_deck import DeckClient, DeckError

ENV = {
    "NEXTCLOUD_URL": "https://nc.example",
    "NEXTCLOUD_USERNAME": "hermes",
    "NEXTCLOUD_APP_PASSWORD": "pw",
}

BOARDS = [{"id": 2, "title": "Welcome to Nextcloud Deck!", "archived": False}]
STACKS = [
    {"id": 5, "title": "Custom lists - click to rename!", "cards": []},
    {"id": 6, "title": "To Do", "cards": [{"id": 11, "title": "Existing", "duedate": None}]},
]


def _client(responses):
    """responses: list of (status, json) played back in call order."""
    sess = MagicMock()
    made = []

    def _request(method, url, **kw):
        made.append((method, url, kw.get("json")))
        status, body = responses.pop(0)
        r = MagicMock()
        r.ok = 200 <= status < 300
        r.status_code = status
        r.content = b"{}" if body is not None else b""
        r.json.return_value = body
        r.text = str(body)
        return r

    sess.request.side_effect = _request
    with patch.dict("os.environ", ENV, clear=True):
        c = DeckClient(session=sess)
    return c, made


def test_lists_boards_with_their_stacks():
    c, _ = _client([(200, BOARDS)])
    assert c.list_boards() == [{"id": 2, "title": "Welcome to Nextcloud Deck!", "archived": False}]


def test_resolve_matches_names_case_insensitively():
    """The default board's list is stored "To Do"; nobody types it that way."""
    c, _ = _client([(200, BOARDS), (200, STACKS)])
    assert c.resolve("welcome to nextcloud deck!", "to do") == (2, 6)


def test_an_unknown_board_says_which_ones_exist():
    c, _ = _client([(200, BOARDS)])
    with pytest.raises(DeckError, match="Welcome to Nextcloud Deck!"):
        c.resolve("Nope", "To Do")


def test_an_unknown_list_says_which_ones_exist():
    c, _ = _client([(200, BOARDS), (200, STACKS)])
    with pytest.raises(DeckError, match="Custom lists"):
        c.resolve("Welcome to Nextcloud Deck!", "Backlog")


def test_a_due_date_needs_a_second_call_because_create_drops_it():
    """POST /cards returns duedate: null however you ask. Regression cover for
    the card looking created while the date silently went missing."""
    created = {"id": 12, "title": "Call Crystal Oneil", "duedate": None}
    updated = {"id": 12, "title": "Call Crystal Oneil", "duedate": "2026-07-28T17:00:00+00:00"}
    c, made = _client([(200, BOARDS), (200, STACKS), (200, STACKS), (200, created), (200, updated)])
    out = c.create_card(
        board="Welcome to Nextcloud Deck!", stack="To Do",
        title="Call Crystal Oneil", duedate="2026-07-28T17:00:00+00:00",
    )
    assert out["created"] is True
    assert out["card"]["duedate"] == "2026-07-28T17:00:00+00:00"
    methods = [m for m, _u, _b in made]
    assert methods[-2:] == ["POST", "PUT"], "create then set the date"


def test_no_second_call_when_no_due_date_is_wanted():
    created = {"id": 13, "title": "No date", "duedate": None}
    c, made = _client([(200, BOARDS), (200, STACKS), (200, STACKS), (200, created)])
    c.create_card(board="Welcome to Nextcloud Deck!", stack="To Do", title="No date")
    assert [m for m, _u, _b in made].count("PUT") == 0


def test_creating_the_same_card_twice_is_a_no_op():
    """A scheduled job that runs twice must not leave two identical cards."""
    c, made = _client([(200, BOARDS), (200, STACKS), (200, STACKS)])
    out = c.create_card(board="Welcome to Nextcloud Deck!", stack="To Do", title="Existing")
    assert out["created"] is False and out["card"]["id"] == 11
    assert "POST" not in [m for m, _u, _b in made]


def test_skip_if_exists_false_creates_a_duplicate_on_purpose():
    created = {"id": 14, "title": "Existing", "duedate": None}
    c, made = _client([(200, BOARDS), (200, STACKS), (200, created)])
    out = c.create_card(
        board="Welcome to Nextcloud Deck!", stack="To Do", title="Existing", skip_if_exists=False
    )
    assert out["created"] is True
    assert "POST" in [m for m, _u, _b in made]


def test_every_request_carries_the_ocs_header():
    """Without it Nextcloud answers with the login page instead of JSON."""
    c, _ = _client([(200, BOARDS)])
    c.list_boards()
    assert c.session.request.call_args.kwargs["headers"]["OCS-APIRequest"] == "true"


def test_a_non_json_response_is_reported_as_such():
    sess = MagicMock()
    r = MagicMock(ok=True, status_code=200, content=b"<html>login</html>")
    r.json.side_effect = ValueError("no json")
    sess.request.return_value = r
    with patch.dict("os.environ", ENV, clear=True):
        c = DeckClient(session=sess)
    with pytest.raises(DeckError, match="non-JSON"):
        c.list_boards()


def test_missing_credentials_is_a_clear_error():
    with patch.dict("os.environ", {}, clear=True):
        c = DeckClient()
    assert not c.is_configured()
    with pytest.raises(DeckError, match="not configured"):
        c.list_boards()
