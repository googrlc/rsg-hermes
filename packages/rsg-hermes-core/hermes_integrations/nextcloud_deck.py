"""Nextcloud Deck — shared kanban boards, over the Deck REST API.

Deck is where RSG's cross-team work lives (boards -> stacks -> cards). This is
the write surface Hermes needs so a scheduled run can put a card on a board
without a human driving the browser.

Config (env; shares the credentials the document store already uses):
    NEXTCLOUD_URL           https://host
    NEXTCLOUD_USER          account (NEXTCLOUD_USERNAME also accepted)
    NEXTCLOUD_APP_PASSWORD  Nextcloud app password

API notes, both learned the hard way against the live instance:

* Every request needs ``OCS-APIRequest: true`` or Nextcloud answers with the
  login page rather than JSON.
* **POST /cards ignores duedate.** Creating a card with a due date is two calls:
  create, then PUT the card back with ``duedate`` set. A single POST silently
  returns a card with ``duedate: null`` — no error, and the caller looks
  successful while the date is missing. ``create_card`` does both.
"""

from __future__ import annotations

import os
from typing import Any

import requests

DECK_API = "/index.php/apps/deck/api/v1.0"
DEFAULT_TIMEOUT = 30.0


class DeckError(RuntimeError):
    pass


class DeckClient:
    def __init__(
        self,
        *,
        url: str | None = None,
        user: str | None = None,
        app_password: str | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        session: "requests.Session | None" = None,
    ) -> None:
        self.url = (url if url is not None else os.environ.get("NEXTCLOUD_URL", "")).strip().rstrip("/")
        env_user = os.environ.get("NEXTCLOUD_USER") or os.environ.get("NEXTCLOUD_USERNAME", "")
        self.user = (user if user is not None else env_user).strip()
        self.app_password = (
            app_password if app_password is not None else os.environ.get("NEXTCLOUD_APP_PASSWORD", "")
        ).strip()
        self.timeout = timeout
        self._session = session

    def is_configured(self) -> bool:
        return bool(self.url and self.user and self.app_password)

    def _require_configured(self) -> None:
        if not self.is_configured():
            raise DeckError(
                "Nextcloud Deck is not configured — set NEXTCLOUD_URL, NEXTCLOUD_USER "
                "and NEXTCLOUD_APP_PASSWORD."
            )

    @property
    def session(self) -> "requests.Session":
        if self._session is None:
            self._session = requests.Session()
            self._session.auth = (self.user, self.app_password)
        return self._session

    def _call(self, method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
        self._require_configured()
        resp = self.session.request(
            method,
            f"{self.url}{DECK_API}{path}",
            headers={"OCS-APIRequest": "true", "Accept": "application/json"},
            json=payload,
            timeout=self.timeout,
        )
        if not resp.ok:
            raise DeckError(f"Deck {method} {path} failed {resp.status_code}: {resp.text[:300]}")
        if not resp.content:
            return None
        try:
            return resp.json()
        except ValueError as exc:
            # Almost always the login page, i.e. a credential problem.
            raise DeckError(f"Deck {method} {path}: non-JSON response") from exc

    # -- reads --------------------------------------------------------------

    def list_boards(self) -> list[dict[str, Any]]:
        return [
            {"id": b["id"], "title": b.get("title"), "archived": bool(b.get("archived"))}
            for b in (self._call("GET", "/boards") or [])
        ]

    def list_stacks(self, board_id: int) -> list[dict[str, Any]]:
        return [
            {
                "id": s["id"],
                "title": s.get("title"),
                "board_id": board_id,
                "cards": [
                    {"id": c["id"], "title": c.get("title"), "duedate": c.get("duedate")}
                    for c in (s.get("cards") or [])
                ],
            }
            for s in (self._call("GET", f"/boards/{board_id}/stacks") or [])
        ]

    # -- resolution ---------------------------------------------------------

    def resolve(self, board: str, stack: str) -> tuple[int, int]:
        """Board and stack *titles* -> ids, matched case-insensitively.

        Callers hold names, not ids, and the names are not typed consistently —
        the default board's list is "To Do", which nobody writes that way.
        """
        boards = self.list_boards()
        b = next((x for x in boards if (x["title"] or "").strip().lower() == board.strip().lower()), None)
        if b is None:
            raise DeckError(
                f"No Deck board named {board!r}. Boards: "
                + ", ".join(repr(x["title"]) for x in boards)
            )
        stacks = self.list_stacks(b["id"])
        s = next((x for x in stacks if (x["title"] or "").strip().lower() == stack.strip().lower()), None)
        if s is None:
            raise DeckError(
                f"No list named {stack!r} on board {b['title']!r}. Lists: "
                + ", ".join(repr(x["title"]) for x in stacks)
            )
        return b["id"], s["id"]

    # -- write --------------------------------------------------------------

    def create_card(
        self,
        *,
        board: str,
        stack: str,
        title: str,
        description: str | None = None,
        duedate: str | None = None,
        skip_if_exists: bool = True,
    ) -> dict[str, Any]:
        """Put a card on a board. Idempotent by title within the stack by default.

        A scheduled job that runs twice should not leave two identical cards, and
        Deck has no natural key to lean on, so the title within the stack is it.
        """
        board_id, stack_id = self.resolve(board, stack)

        if skip_if_exists:
            for s in self.list_stacks(board_id):
                if s["id"] != stack_id:
                    continue
                for c in s["cards"]:
                    if (c["title"] or "").strip().lower() == title.strip().lower():
                        return {"created": False, "card": c, "board_id": board_id, "stack_id": stack_id}

        card = self._call(
            "POST",
            f"/boards/{board_id}/stacks/{stack_id}/cards",
            {"title": title, "type": "plain", "order": 0, "description": description or ""},
        )
        if not card or "id" not in card:
            raise DeckError("Deck create returned no card id")

        # The create call drops duedate on the floor; set it with a follow-up PUT.
        if duedate:
            card = self._call(
                "PUT",
                f"/boards/{board_id}/stacks/{stack_id}/cards/{card['id']}",
                {
                    "title": title,
                    "type": "plain",
                    "owner": self.user,
                    "description": description or "",
                    "duedate": duedate,
                },
            ) or card

        return {
            "created": True,
            "card": {"id": card.get("id"), "title": card.get("title"), "duedate": card.get("duedate")},
            "board_id": board_id,
            "stack_id": stack_id,
        }
