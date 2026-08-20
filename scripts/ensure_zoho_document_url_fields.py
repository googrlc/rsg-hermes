#!/usr/bin/env python3
"""Create Zoho URL fields that point at Nextcloud, and put them on the layout.

The document lives once in Nextcloud. Zoho stores a clickable https link.
This script creates the website/URL fields from
``hermes_integrations.zoho_document_fields`` and places them in a Documents
section on each module's standard layout.

Custom modules Claims and Certificates are skipped until they exist in the
org (create them under Setup → Modules, then re-run).

Environment (required for --apply):
  ZOHO_CLIENT_ID
  ZOHO_CLIENT_SECRET
  ZOHO_REFRESH_TOKEN

Optional:
  ZOHO_ACCOUNTS_URL   default https://accounts.zoho.com
  ZOHO_API_DOMAIN     default https://www.zohoapis.com
  ZOHO_API_VERSION    default v8

Usage:
  PYTHONPATH=packages/rsg-hermes-core:. python scripts/ensure_zoho_document_url_fields.py
  PYTHONPATH=packages/rsg-hermes-core:. python scripts/ensure_zoho_document_url_fields.py --apply
  PYTHONPATH=packages/rsg-hermes-core:. python scripts/ensure_zoho_document_url_fields.py --apply --module Accounts
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from hermes_integrations.zoho_document_fields import (
    DOCUMENT_URL_FIELDS,
    DOCUMENTS_SECTION,
    missing_document_fields,
    website_create_payload,
)


class ZohoSettingsClient:
    """Settings API (v8) for fields, modules, and layouts."""

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


def list_module_api_names(client: ZohoSettingsClient) -> set[str]:
    payload = client.request("GET", "/settings/modules")
    names: set[str] = set()
    for module in payload.get("modules") or []:
        api = module.get("api_name")
        if api:
            names.add(str(api))
    return names


def list_fields(client: ZohoSettingsClient, module: str) -> list[dict[str, Any]]:
    payload = client.request("GET", "/settings/fields", query={"module": module})
    return list(payload.get("fields") or [])


def create_website_field(client: ZohoSettingsClient, module: str, spec: dict[str, str]) -> dict[str, Any]:
    body = {"fields": [website_create_payload(spec)]}
    return client.request("POST", "/settings/fields", query={"module": module}, body=body)


def standard_layout(client: ZohoSettingsClient, module: str) -> dict[str, Any] | None:
    payload = client.request("GET", "/settings/layouts", query={"module": module})
    layouts = list(payload.get("layouts") or [])
    if not layouts:
        return None
    for layout in layouts:
        name = str(layout.get("name") or "").lower()
        if name in ("standard", "standard__s"):
            return layout
    return layouts[0]


def documents_section(layout: dict[str, Any]) -> dict[str, Any] | None:
    for section in layout.get("sections") or []:
        label = str(section.get("display_label") or section.get("name") or "")
        if label.strip().lower() == DOCUMENTS_SECTION.lower():
            return section
    return None


def field_ids_for_specs(fields: list[dict[str, Any]], specs: tuple[dict[str, str], ...]) -> list[str]:
    from hermes_integrations.zoho_document_fields import existing_field_index

    index = existing_field_index(fields)
    ids: list[str] = []
    for spec in specs:
        row = index.get(spec["api_name"]) or index.get(spec["field_label"].strip().lower())
        fid = str((row or {}).get("id") or "")
        if fid:
            ids.append(fid)
    return ids


def layout_has_field(layout: dict[str, Any], field_id: str) -> bool:
    for section in layout.get("sections") or []:
        if section.get("type") and section.get("type") != "used":
            continue
        for field in section.get("fields") or []:
            if str(field.get("id") or "") == field_id and field.get("type", "used") == "used":
                return True
    return False


def place_fields_on_layout(
    client: ZohoSettingsClient,
    module: str,
    layout: dict[str, Any],
    field_ids: list[str],
    *,
    apply: bool,
) -> None:
    layout_id = str(layout.get("id") or "")
    if not layout_id:
        print("  no layout id; skip placement")
        return
    missing = [fid for fid in field_ids if not layout_has_field(layout, fid)]
    if not missing:
        print("  layout already shows the URL fields")
        return

    section = documents_section(layout)
    fields_payload = [{"id": fid} for fid in missing]
    if section and section.get("id"):
        body = {
            "layouts": [
                {
                    "sections": [
                        {"id": str(section["id"]), "fields": fields_payload},
                    ]
                }
            ]
        }
        print(f"  add {len(missing)} field(s) to existing '{DOCUMENTS_SECTION}' section")
    else:
        body = {
            "layouts": [
                {
                    "sections": [
                        {"display_label": DOCUMENTS_SECTION, "fields": fields_payload},
                    ]
                }
            ]
        }
        print(f"  create '{DOCUMENTS_SECTION}' section with {len(missing)} field(s)")

    print("  PATCH payload:")
    print("  " + json.dumps(body))
    if not apply:
        print("  (dry-run — pass --apply to send PATCH)")
        return
    result = client.request(
        "PATCH",
        f"/settings/layouts/{layout_id}",
        query={"module": module},
        body=body,
    )
    print("  PATCH response:")
    print("  " + json.dumps(result)[:1000])


def process_module(client: ZohoSettingsClient, module: str, *, apply: bool, present: set[str]) -> None:
    print(f"\n=== {module} ===")
    if module not in present:
        print(
            f"  module not in this Zoho org. Create it under Setup → Customization → Modules "
            f"(singular/plural from docs/zoho/modules_custom.csv), then re-run."
        )
        return

    fields = list_fields(client, module)
    missing = missing_document_fields(module, fields)
    if missing:
        for spec in missing:
            print(f"  create URL field: {spec['field_label']} ({spec['api_name']})")
            if apply:
                result = create_website_field(client, module, spec)
                print(f"  POST response: {json.dumps(result)[:800]}")
            else:
                print("  (dry-run — pass --apply to POST /settings/fields)")
        if apply:
            fields = list_fields(client, module)
    else:
        print("  URL fields already exist")

    specs = DOCUMENT_URL_FIELDS[module]
    ids = field_ids_for_specs(fields, specs)
    if not ids:
        print("  could not resolve field ids after create — check API names in Zoho")
        return
    layout = standard_layout(client, module)
    if not layout:
        print("  no layout returned; fields exist but are not on a form")
        return
    print(f"  layout: {layout.get('name')} ({layout.get('id')})")
    place_fields_on_layout(client, module, layout, ids, apply=apply)


def main() -> int:
    parser = argparse.ArgumentParser(description="Create Zoho Nextcloud URL fields and place them on layouts.")
    parser.add_argument("--apply", action="store_true", help="Create fields and patch layouts (default is dry-run).")
    parser.add_argument("--module", action="append", dest="modules", help="Limit to module API name(s).")
    args = parser.parse_args()

    for var in ("ZOHO_CLIENT_ID", "ZOHO_CLIENT_SECRET", "ZOHO_REFRESH_TOKEN"):
        if var not in os.environ:
            print(f"Missing env var {var}.", file=sys.stderr)
            return 1

    selected = args.modules or list(DOCUMENT_URL_FIELDS)
    unknown = [m for m in selected if m not in DOCUMENT_URL_FIELDS]
    if unknown:
        print(f"Unknown module(s): {unknown}. Known: {', '.join(DOCUMENT_URL_FIELDS)}", file=sys.stderr)
        return 1

    client = ZohoSettingsClient()
    present = list_module_api_names(client)
    for module in selected:
        process_module(client, module, apply=args.apply, present=present)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
