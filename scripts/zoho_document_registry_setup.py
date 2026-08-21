#!/usr/bin/env python3
"""Create the Zoho CRM Document_Registry custom module and its fields.

Default is dry-run (prints the plan). ``--apply`` calls Zoho CRM v8 settings
APIs and needs OAuth scopes:

  ZohoCRM.settings.modules.CREATE
  ZohoCRM.settings.fields.CREATE
  ZohoCRM.settings.modules.READ
  ZohoCRM.settings.fields.READ
  ZohoCRM.settings.profiles.READ  (or settings.ALL)

If OAuth cannot create fields, ship the CSV + this script and run ``--apply``
in the org when credentials have settings scopes. Do not invent a second
catalog (Filed_Documents).

    python scripts/zoho_document_registry_setup.py
    python scripts/zoho_document_registry_setup.py --apply

Env:
  ZOHO_CLIENT_ID
  ZOHO_CLIENT_SECRET
  ZOHO_REFRESH_TOKEN
  ZOHO_ACCOUNTS_URL   default https://accounts.zoho.com
  ZOHO_API_DOMAIN     default https://www.zohoapis.com
  ZOHO_API_VERSION    default v8
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[1]
_CORE = _ROOT / "packages" / "rsg-hermes-core"
if str(_CORE) not in sys.path:
    sys.path.insert(0, str(_CORE))

try:
    from dotenv import load_dotenv

    load_dotenv(_ROOT / ".env")
except ImportError:
    pass

from hermes_integrations.zoho_document_registry import DEFAULT_MODULE
from hermes_integrations.zoho_document_registry_setup import (
    load_picklists,
    module_create_payload,
    plan_fields,
)


class ZohoSettingsClient:
    def __init__(self) -> None:
        self.client_id = os.environ.get("ZOHO_CLIENT_ID", "")
        self.client_secret = os.environ.get("ZOHO_CLIENT_SECRET", "")
        self.refresh_token = os.environ.get("ZOHO_REFRESH_TOKEN", "")
        if not (self.client_id and self.client_secret and self.refresh_token):
            raise SystemExit(
                "ZOHO_CLIENT_ID, ZOHO_CLIENT_SECRET, and ZOHO_REFRESH_TOKEN must be set"
            )
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

    def request(
        self,
        method: str,
        path: str,
        *,
        query: dict[str, str] | None = None,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not self._access_token:
            self._refresh_access_token()
        qs = f"?{urllib.parse.urlencode(query)}" if query else ""
        url = f"{self.api_domain}/crm/{self.api_version}{path}{qs}"
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(
            url,
            data=data,
            method=method,
            headers={
                "Authorization": f"Zoho-oauthtoken {self._access_token}",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                raw = resp.read().decode()
                return json.loads(raw) if raw.strip() else {}
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")
            if exc.code == 401:
                self._refresh_access_token()
                req.add_header("Authorization", f"Zoho-oauthtoken {self._access_token}")
                try:
                    with urllib.request.urlopen(req, timeout=120) as resp:
                        raw = resp.read().decode()
                        return json.loads(raw) if raw.strip() else {}
                except urllib.error.HTTPError as retry_exc:
                    retry_detail = retry_exc.read().decode(errors="replace")
                    raise RuntimeError(
                        f"{method} {url} -> HTTP {retry_exc.code}: {retry_detail}"
                    ) from retry_exc
            raise RuntimeError(f"{method} {url} -> HTTP {exc.code}: {detail}") from exc


def _module_names(client: ZohoSettingsClient) -> dict[str, str]:
    body = client.request("GET", "/settings/modules")
    out: dict[str, str] = {}
    for mod in body.get("modules") or []:
        if isinstance(mod, dict) and mod.get("api_name"):
            out[str(mod["api_name"])] = str(mod.get("id") or "")
    return out


def _field_names(client: ZohoSettingsClient, module: str) -> set[str]:
    body = client.request("GET", "/settings/fields", query={"module": module})
    names: set[str] = set()
    for field in body.get("fields") or []:
        if isinstance(field, dict) and field.get("api_name"):
            names.add(str(field["api_name"]))
    return names


def _profile_ids(client: ZohoSettingsClient) -> list[str]:
    body = client.request("GET", "/settings/profiles")
    ids: list[str] = []
    for profile in body.get("profiles") or []:
        if isinstance(profile, dict) and profile.get("id"):
            ids.append(str(profile["id"]))
    if not ids:
        raise RuntimeError("no Zoho profiles returned — cannot create a custom module")
    return ids


def collect_plan(client: ZohoSettingsClient) -> dict[str, Any]:
    modules = _module_names(client)
    existing = DEFAULT_MODULE in modules
    field_names: set[str] = set()
    if existing:
        field_names = _field_names(client, DEFAULT_MODULE)
    picklists = load_picklists()
    field_plan = plan_fields(
        existing_api_names=field_names,
        existing_modules=set(modules),
        picklists=picklists,
        lookup_module_ids={k: v for k, v in modules.items() if v},
    )
    return {
        "module": DEFAULT_MODULE,
        "module_exists": existing,
        "filed_documents_exists": "Filed_Documents" in modules,
        "profiles": _profile_ids(client),
        "existing_fields": sorted(field_names),
        "field_plan": field_plan,
    }


def apply_plan(client: ZohoSettingsClient, plan: dict[str, Any]) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    if not plan["module_exists"]:
        payload = module_create_payload(plan["profiles"])
        created = client.request("POST", "/settings/modules", body=payload)
        results.append({"action": "create_module", "response": created})
        plan["module_exists"] = True
    for step in plan["field_plan"]:
        if step["action"] != "create_field":
            results.append(step)
            continue
        resp = client.request(
            "POST",
            "/settings/fields",
            query={"module": DEFAULT_MODULE},
            body=step["payload"],
        )
        results.append({"action": "create_field", "api_name": step["api_name"], "response": resp})
    return {"ok": True, "results": results}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help="Create the module/fields (default is dry-run).")
    ap.add_argument("--json", action="store_true", help="Print JSON instead of a text report.")
    args = ap.parse_args()

    try:
        client = ZohoSettingsClient()
        plan = collect_plan(client)
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001
        print(f"plan failed: {exc}", file=sys.stderr)
        return 2

    if args.json and not args.apply:
        print(json.dumps(plan, indent=2, default=str))
        return 0

    print(f"Module {plan['module']}: {'exists' if plan['module_exists'] else 'MISSING'}")
    if plan.get("filed_documents_exists"):
        print(
            "WARNING: Filed_Documents also exists in this org. "
            "Document_Registry is the one catalog — hide Filed_Documents; do not file into both."
        )
    print(f"Profiles available: {len(plan['profiles'])}")
    for step in plan["field_plan"]:
        extra = step.get("reason") or step.get("api_name")
        print(f"  {step['action']}: {step.get('api_name')} ({extra})")

    if not args.apply:
        print("\n(dry-run — pass --apply to create the module and missing fields)")
        print("After create: related list Nextcloud Files on Account/Lead/Policy/Deal/Renewal.")
        print("Create layout: do not require Nextcloud_File_URL if using the temp-attachment drop.")
        print("Hermes API create path still refuses a CRM row without that URL.")
        return 0

    try:
        out = apply_plan(client, plan)
    except Exception as exc:  # noqa: BLE001
        print(f"apply failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(out, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
