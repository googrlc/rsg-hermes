"""Momentum AMS REST/OData client — autonomous-agent read/write path.

Uses MomentumTokenManager for auto-refreshing JWT auth, so long-running cron and
worker agents never hit the 3-hour token ceiling. Paginated OData reads for the
entities the RSG agents need, plus a generic write surface (gated behind dry-run /
blast-radius enforcement, enabled per shared standard 3.2/3.3 when agents promote
past read-only).

Env (via token manager): MOMENTUM_API_URL, MOMENTUM_USERNAME, MOMENTUM_PASSWORD.
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any

import requests

from hermes.integrations.momentum_token import MomentumTokenManager

log = logging.getLogger(__name__)

DEFAULT_API_URL = "https://api.momentumamp.com"


class MomentumRESTError(Exception):
    pass


class MomentumRESTClient:
    def __init__(
        self,
        token_manager: MomentumTokenManager | None = None,
        api_url: str | None = None,
        timeout: float = 60.0,
        page_size: int = 200,
        pause: float = 0.0,
    ) -> None:
        self.token_manager = token_manager or MomentumTokenManager()
        self.api_url = (api_url or os.environ.get("MOMENTUM_API_URL", DEFAULT_API_URL)).rstrip("/")
        self.timeout = timeout
        self.page_size = page_size
        self.pause = pause

    # -- transport -----------------------------------------------------------
    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.token_manager.get_token()}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        retry_auth: bool = True,
    ) -> Any:
        url = f"{self.api_url}{path}"
        resp = requests.request(
            method, url, headers=self._headers(), params=params, json=json_body, timeout=self.timeout
        )
        if resp.status_code == 401 and retry_auth:
            log.warning("401 on %s %s — forcing re-login", method, path)
            self.token_manager.force_login()
            return self._request(method, path, params=params, json_body=json_body, retry_auth=False)
        if resp.status_code >= 400:
            raise MomentumRESTError(f"{method} {path} HTTP {resp.status_code}: {resp.text[:300]}")
        if resp.status_code == 204 or not resp.content:
            return None
        try:
            return resp.json()
        except ValueError:
            return resp.text

    # -- OData reads ---------------------------------------------------------
    def count(self, entity: str, filter_: str | None = None) -> int:
        params: dict[str, Any] = {"$count": "true", "$top": 0}
        if filter_:
            params["$filter"] = filter_
        data = self._request("GET", f"/api/{entity}", params=params)
        if isinstance(data, dict):
            return int(data.get("@odata.count", data.get("count", 0)) or 0)
        return 0

    def list(
        self,
        entity: str,
        *,
        top: int | None = None,
        skip: int = 0,
        select: str | None = None,
        filter_: str | None = None,
        order_by: str | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"$top": top or self.page_size, "$skip": skip, "$count": "true"}
        if select:
            params["$select"] = select
        if filter_:
            params["$filter"] = filter_
        if order_by:
            params["$orderby"] = order_by
        return self._request("GET", f"/api/{entity}", params=params)

    def enumerate_all(
        self,
        entity: str,
        *,
        select: str | None = None,
        filter_: str | None = None,
        order_by: str | None = None,
        max_records: int | None = None,
    ) -> list[dict[str, Any]]:
        """Page through an entire entity collection with a courtesy pause."""
        out: list[dict[str, Any]] = []
        skip = 0
        while True:
            page = self.list(entity, skip=skip, select=select, filter_=filter_, order_by=order_by)
            rows = self._rows(page)
            out.extend(rows)
            total = self._total(page)
            skip += self.page_size
            if self.pause:
                time.sleep(self.pause)
            if not rows or skip >= total or (max_records and len(out) >= max_records):
                break
        return out[:max_records] if max_records else out

    def get(self, entity: str, entity_id: str) -> dict[str, Any]:
        return self._request("GET", f"/api/{entity}/{entity_id}")

    @staticmethod
    def _rows(page: Any) -> list[dict[str, Any]]:
        if isinstance(page, dict):
            r = page.get("value")
            if isinstance(r, list):
                return r
            r = page.get("list") or page.get("results") or page.get("items")
            return r if isinstance(r, list) else []
        return []

    @staticmethod
    def _total(page: Any) -> int:
        if isinstance(page, dict):
            return int(page.get("@odata.count", page.get("count", 0)) or 0)
        return 0

    # -- writes (gated; enable when agents promote past read-only) -----------
    # def insert(self, entity, payload, *, dry_run=True): ...
    # def update(self, entity, entity_id, payload, *, dry_run=True): ...
    # Blast-radius (50/run, 1 write/sec), Supabase agent_writes mirror, and
    # dry_run gating enforced here per shared standard 3.2/3.3 when enabled.
