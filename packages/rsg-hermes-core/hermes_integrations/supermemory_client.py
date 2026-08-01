"""Supermemory client — Hermes "Holographic Memory" document store.

Thin REST wrapper (matches the other integrations: env creds, requests
Session, custom error). Documents are stored as Supermemory memories tagged
with container tags that encode the folder model:

  client space:   ["hermes-docs", "client:<account-slug>", "type:<doc_type>"]
  internal space: ["hermes-docs", "internal", "folder:<folder-slug>", "type:<doc_type>"]

API (v3):
  POST   /v3/documents            add        -> {id, status}
  POST   /v3/documents/list       list       -> {memories: [...]}
  POST   /v3/search               semantic   -> {results: [...]}
  DELETE /v3/documents/{id}       delete
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any

import requests

log = logging.getLogger(__name__)

DOCS_ROOT_TAG = "hermes-docs"


class SupermemoryClientError(Exception):
    """Raised on auth failures or non-success Supermemory responses."""


def slug(value: str) -> str:
    """Lowercase, hyphenated slug for use inside a container tag."""
    s = re.sub(r"[^a-z0-9]+", "-", (value or "").lower()).strip("-")
    return s or "untitled"


def client_tags(account_name: str, doc_type: str) -> list[str]:
    return [DOCS_ROOT_TAG, f"client:{slug(account_name)}", f"type:{doc_type}"]


def internal_tags(folder: str, doc_type: str) -> list[str]:
    return [DOCS_ROOT_TAG, "internal", f"folder:{slug(folder)}", f"type:{doc_type}"]


def scope_tag(scope: str) -> str:
    """Container tag isolating one instance's private memory (e.g. 'scope:hermes-gretch')."""
    return f"scope:{slug(scope)}"


class SupermemoryClient:
    """REST client for Supermemory's document/memory API."""

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: float = 30.0,
        scope: str | None = None,
    ) -> None:
        self.api_key = api_key or os.environ.get("SUPERMEMORY_API_KEY", "")
        self.base_url = (
            base_url or os.environ.get("SUPERMEMORY_BASE_URL", "https://api.supermemory.ai")
        ).rstrip("/")
        self.timeout = timeout
        # When a scope is set, every write is additionally tagged with it and every
        # read is constrained to it — this is what keeps Gretchen's instance memory
        # from bleeding into Lamar's (and vice versa). Left unset, the client behaves
        # exactly as before (shared agency document library, no scoping).
        self.scope = (scope or "").strip() or None
        self._scope_tag = scope_tag(self.scope) if self.scope else None
        if not self.api_key:
            raise SupermemoryClientError(
                "SUPERMEMORY_API_KEY must be set (env or constructor)."
            )
        self.session = requests.Session()

    def _with_scope(self, container_tags: list[str] | None) -> list[str] | None:
        """Add this instance's scope tag to a tag list (no-op when unscoped)."""
        if not self._scope_tag:
            return container_tags
        tags = list(container_tags or [])
        if self._scope_tag not in tags:
            tags.append(self._scope_tag)
        return tags

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
        url = f"{self.base_url}{path}"
        resp = self.session.request(
            method, url, headers=self._headers(), json=payload, timeout=self.timeout
        )
        if not resp.ok:
            raise SupermemoryClientError(
                f"Supermemory {method} {path} failed {resp.status_code}: {resp.text[:400]}"
            )
        return resp.json() if resp.content else {}

    # ── Writes ────────────────────────────────────────────────────────────

    def add_document(
        self,
        content: str,
        *,
        container_tags: list[str],
        metadata: dict[str, Any] | None = None,
        custom_id: str | None = None,
    ) -> dict[str, Any]:
        """Add a document. Returns {id, status} (status is usually 'queued').

        Medicare-lane PHI backstop: when the container tags mark this as
        Medicare-lane memory, content and string metadata are run through
        ``redact_phi`` so a stray MBI / SSN / eligibility detail can't be stored
        (rule 3c). The allowlist builder ``phi.build_medicare_memory`` is the
        primary control; this is defense in depth.
        """
        from hermes_core import phi

        medicare = phi.is_medicare_context(container_tags)
        if medicare:
            content = phi.redact_phi(content)

        payload: dict[str, Any] = {"content": content, "containerTags": self._with_scope(container_tags)}
        if metadata:
            # Supermemory metadata values must be scalar (str/number/bool).
            scrubbed = {
                k: v for k, v in metadata.items() if isinstance(v, (str, int, float, bool))
            }
            if medicare:
                scrubbed = {k: (phi.redact_phi(v) if isinstance(v, str) else v)
                            for k, v in scrubbed.items()}
            payload["metadata"] = scrubbed
        if custom_id:
            payload["customId"] = custom_id
        return self._request("POST", "/v3/documents", payload)

    def delete_document(self, document_id: str) -> dict[str, Any]:
        return self._request("DELETE", f"/v3/documents/{document_id}")

    # ── Reads ─────────────────────────────────────────────────────────────

    def list_documents(
        self, *, container_tags: list[str] | None = None, limit: int = 50
    ) -> list[dict[str, Any]]:
        payload: dict[str, Any] = {"limit": limit}
        scoped = self._with_scope(container_tags)
        if scoped:
            payload["containerTags"] = scoped
        body = self._request("POST", "/v3/documents/list", payload)
        return body.get("memories", []) if isinstance(body, dict) else []

    def search(self, query: str, *, limit: int = 10) -> list[dict[str, Any]]:
        payload: dict[str, Any] = {"q": query, "limit": limit}
        if self._scope_tag:
            payload["containerTags"] = [self._scope_tag]
        body = self._request("POST", "/v3/search", payload)
        return body.get("results", []) if isinstance(body, dict) else []
