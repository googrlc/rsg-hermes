"""Momentum AMS REST token manager — auto-refreshing JWT access for autonomous agents.

Solves the 3-hour token TTL: agents authenticate with a permanent API-user
username + password, receive an accessToken (JWT) + refreshToken, and this manager
keeps a live token on hand by refreshing before expiry and re-logging in on any
failure. No manual key rotation required.

Env: MOMENTUM_API_URL (default https://api.momentumamp.com),
     MOMENTUM_USERNAME, MOMENTUM_PASSWORD.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime, timezone
from typing import Any

import requests

log = logging.getLogger(__name__)

DEFAULT_API_URL = "https://api.momentumamp.com"
# Safety net: force a fresh password re-login if nothing has succeeded in this window.
# Well under the observed 3h ceiling so autonomous cron/worker agents never hit it.
FORCED_RELOGIN_SECONDS = 2 * 60 * 60      # 2h
REFRESH_HEADROOM_SECONDS = 10 * 60        # refresh 10 min before token expiry
DEFAULT_TOKEN_LIFETIME = 30 * 60           # fallback if expiresIn absent/unparseable


class MomentumAuthError(Exception):
    """Raised when login or token refresh fails."""


class MomentumTokenManager:
    """Thread-safe JWT manager: login -> refresh -> force-relogin lifecycle."""

    def __init__(
        self,
        api_url: str | None = None,
        username: str | None = None,
        password: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        self.api_url = (api_url or os.environ.get("MOMENTUM_API_URL", DEFAULT_API_URL)).rstrip("/")
        self.username = username or os.environ.get("MOMENTUM_USERNAME", "")
        self.password = password or os.environ.get("MOMENTUM_PASSWORD", "")
        if not self.username or not self.password:
            raise MomentumAuthError("MOMENTUM_USERNAME and MOMENTUM_PASSWORD must be set.")
        self.timeout = timeout
        self._lock = threading.Lock()
        self._access_token: str | None = None
        self._refresh_token: str | None = None
        self._expires_at: float = 0.0
        self._last_success: float = 0.0

    # -- auth flows ----------------------------------------------------------
    def _login(self) -> None:
        resp = requests.post(
            f"{self.api_url}/api/token",
            json={"username": self.username, "password": self.password},
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            timeout=self.timeout,
        )
        if resp.status_code >= 400:
            raise MomentumAuthError(f"login HTTP {resp.status_code}: {resp.text[:200]}")
        self._apply_token(resp.json())
        log.info(
            "Momentum login ok; token expires %s",
            datetime.fromtimestamp(self._expires_at, tz=timezone.utc).isoformat(),
        )

    def _refresh(self) -> bool:
        if not (self._refresh_token and self._access_token):
            return False
        try:
            resp = requests.post(
                f"{self.api_url}/api/token/refresh",
                json={"accessToken": self._access_token, "refreshToken": self._refresh_token},
                headers={"Content-Type": "application/json", "Accept": "application/json"},
                timeout=self.timeout,
            )
            if resp.status_code >= 400:
                log.warning("refresh HTTP %s: %s", resp.status_code, resp.text[:160])
                return False
            dto = resp.json()
            if not dto.get("accessToken"):
                return False
            self._apply_token(dto)
            log.info(
                "Momentum token refreshed; expires %s",
                datetime.fromtimestamp(self._expires_at, tz=timezone.utc).isoformat(),
            )
            return True
        except Exception as e:  # network/parse errors -> fall back to login
            log.warning("refresh exception: %s", e)
            return False

    def _apply_token(self, dto: dict[str, Any]) -> None:
        self._access_token = dto.get("accessToken")
        self._refresh_token = dto.get("refreshToken") or self._refresh_token
        if not self._access_token:
            raise MomentumAuthError(f"token response missing accessToken: {str(dto)[:200]}")
        self._expires_at = self._parse_expiry(dto.get("expiresIn"))
        self._last_success = time.time()

    @staticmethod
    def _parse_expiry(expires_in: Any) -> float:
        if not expires_in:
            return time.time() + DEFAULT_TOKEN_LIFETIME
        try:
            dt = datetime.fromisoformat(str(expires_in).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.timestamp()
        except Exception:
            return time.time() + DEFAULT_TOKEN_LIFETIME

    # -- public API ----------------------------------------------------------
    def get_token(self) -> str:
        """Return a live JWT, refreshing or re-logging in as needed."""
        with self._lock:
            now = time.time()
            forced = (now - self._last_success) >= FORCED_RELOGIN_SECONDS
            near_expiry = self._access_token is None or now >= (self._expires_at - REFRESH_HEADROOM_SECONDS)
            if forced or self._access_token is None:
                self._login()
            elif near_expiry:
                if not self._refresh():
                    self._login()
            return self._access_token  # type: ignore[return-value]

    def force_login(self) -> None:
        """Discard cached tokens and authenticate from scratch (used on 401)."""
        with self._lock:
            self._login()
