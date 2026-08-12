#!/usr/bin/env python3
"""Move Zoho CRM layout fields to Unused Items via the REST API.

Zoho MCP exposes read-only layout metadata (`ZohoCRM_getLayouts`). Layout updates
require direct API calls with OAuth credentials.

Uses PATCH (not PUT) per Zoho CRM v8 docs:
https://www.zoho.com/crm/developer/docs/api/v8/update-custom-layout.html

Environment variables (required):
  ZOHO_CLIENT_ID
  ZOHO_CLIENT_SECRET
  ZOHO_REFRESH_TOKEN

Optional:
  ZOHO_ACCOUNTS_URL   default https://accounts.zoho.com
  ZOHO_API_DOMAIN     default https://www.zohoapis.com
  ZOHO_API_VERSION    default v8

Usage:
  python scripts/zoho_update_layouts.py --dry-run          # default
  python scripts/zoho_update_layouts.py --apply
  python scripts/zoho_update_layouts.py --apply --module Accounts
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from typing import Any

# module_api_name -> (layout_id, field api_names to move to Unused Items)
LAYOUTS: dict[str, tuple[str, tuple[str, ...]]] = {
    "Accounts": (
        "7529682000000091029",
        (
            "Account_Type",
            "Annual_Revenue",
            "Employees",
            "Industry",
            "Account_Number",
            "Account_Site",
            "SIC_Code",
            "Rating",
            "Ticker_Symbol",
            "Ownership",
            "Fax",
            "Website",
        ),
    ),
    "Deals": (
        "7529682000000091023",
        (
            "Type",
            "Lead_Source",
            "Reason_For_Loss__s",
            "Campaign_Source",
            "Contact_Name",
            "Expected_Revenue",
        ),
    ),
    "Cases": (
        "7529682000000091027",
        (
            "Type",
            "Status",
            "Case_Origin",
            "Case_Reason",
            "Product_Name",
            "No_of_comments",
            "Solution",
            "Add_Comment",
        ),
    ),
}


class ZohoClient:
    def __init__(self) -> None:
        self.client_id = os.environ["ZOHO_CLIENT_ID"]
        self.client_secret = os.environ["ZOHO_CLIENT_SECRET"]
        self.refresh_token = os.environ["ZOHO_REFRESH_TOKEN"]
        self.accounts_url = os.environ.get("ZOHO_ACCOUNTS_URL", "https://accounts.zoho.com").rstrip("/")
        self.api_domain = os.environ.get("ZOHO_API_DOMAIN", "https://www.zohoapis.com").rstrip("/")
        self.api_version = os.environ.get("ZOHO_API_VERSION", "v8")
        self._access_token: str | None = None

    def _refresh_access_token(self) -> str:
        body = urllib.parse.urlencode(
            {
                "grant_type": "refresh_token",
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "refresh_token": self.refresh_token,
            }
        ).encode()
        req = urllib.request.Request(
            f"{self.accounts_url}/oauth/v2/token",
            data=body,
            method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            payload = json.loads(resp.read().decode())
        if "access_token" not in payload:
            raise RuntimeError(f"Token refresh failed: {payload}")
        self._access_token = payload["access_token"]
        return self._access_token

    @property
    def access_token(self) -> str:
        if not self._access_token:
            return self._refresh_access_token()
        return self._access_token

    def request(
        self,
        method: str,
        path: str,
        *,
        query: dict[str, str] | None = None,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        qs = f"?{urllib.parse.urlencode(query)}" if query else ""
        url = f"{self.api_domain}/crm/{self.api_version}{path}{qs}"
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(
            url,
            data=data,
            method=method,
            headers={
                "Authorization": f"Zoho-oauthtoken {self.access_token}",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                raw = resp.read().decode()
                return json.loads(raw) if raw.strip() else {}
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")
            raise RuntimeError(f"{method} {url} -> HTTP {exc.code}: {detail}") from exc

    def get_layout(self, module: str, layout_id: str) -> dict[str, Any]:
        payload = self.request("GET", f"/settings/layouts/{layout_id}", query={"module": module})
        layouts = payload.get("layouts") or []
        if not layouts:
            raise RuntimeError(f"No layout returned for {module} layout_id={layout_id}: {payload}")
        return layouts[0]

    def patch_layout(self, module: str, layout_id: str, body: dict[str, Any]) -> dict[str, Any]:
        return self.request(
            "PATCH",
            f"/settings/layouts/{layout_id}",
            query={"module": module},
            body=body,
        )


def find_fields_in_layout(layout: dict[str, Any], api_names: set[str]) -> dict[str, dict[str, str]]:
    """Return api_name -> {id, section_id, section_name} for fields currently in sections."""
    found: dict[str, dict[str, str]] = {}
    for section in layout.get("sections") or []:
        if section.get("type") != "used":
            continue
        section_id = str(section.get("id") or "")
        section_name = str(section.get("name") or section.get("display_label") or "")
        for field in section.get("fields") or []:
            api = field.get("api_name")
            if api in api_names and field.get("type", "used") == "used":
                found[str(api)] = {
                    "id": str(field.get("id")),
                    "section_id": section_id,
                    "section_name": section_name,
                }
    return found


def build_unused_payload(found: dict[str, dict[str, str]]) -> dict[str, Any]:
    """Group field _delete markers by section for a minimal PATCH body."""
    by_section: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for meta in found.values():
        by_section[meta["section_id"]].append(
            {
                "id": meta["id"],
                "_delete": {"permanent": False},
            }
        )
    sections = [{"id": section_id, "fields": fields} for section_id, fields in by_section.items()]
    return {"layouts": [{"sections": sections}]}


def process_module(client: ZohoClient, module: str, layout_id: str, remove: tuple[str, ...], apply: bool) -> None:
    api_names = set(remove)
    print(f"\n=== {module} (layout {layout_id}) ===")
    layout = client.get_layout(module, layout_id)
    print(f"Layout name: {layout.get('name')}")

    found = find_fields_in_layout(layout, api_names)
    missing = sorted(api_names - set(found))
    if missing:
        print(f"Already unused or not on layout: {', '.join(missing)}")

    if not found:
        print("Nothing to move.")
        return

    for api, meta in sorted(found.items()):
        print(f"  move to Unused: {api} (field_id={meta['id']}, section={meta['section_name']})")

    payload = build_unused_payload(found)
    print("\nPATCH payload:")
    print(json.dumps(payload, indent=2))

    if not apply:
        print("(dry-run — pass --apply to send PATCH)")
        return

    result = client.patch_layout(module, layout_id, payload)
    print("PATCH response:")
    print(json.dumps(result, indent=2))


def main() -> int:
    parser = argparse.ArgumentParser(description="Move Zoho CRM layout fields to Unused Items.")
    parser.add_argument("--apply", action="store_true", help="Send PATCH requests (default is dry-run).")
    parser.add_argument("--module", action="append", dest="modules", help="Limit to module(s), e.g. Accounts.")
    args = parser.parse_args()

    for var in ("ZOHO_CLIENT_ID", "ZOHO_CLIENT_SECRET", "ZOHO_REFRESH_TOKEN"):
        if var not in os.environ:
            print(f"Missing env var {var}. See script docstring for OAuth setup.", file=sys.stderr)
            return 1

    selected = args.modules or list(LAYOUTS.keys())
    unknown = [m for m in selected if m not in LAYOUTS]
    if unknown:
        print(f"Unknown module(s): {unknown}. Known: {', '.join(LAYOUTS)}", file=sys.stderr)
        return 1

    client = ZohoClient()
    for module in selected:
        layout_id, remove = LAYOUTS[module]
        process_module(client, module, layout_id, remove, apply=args.apply)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
