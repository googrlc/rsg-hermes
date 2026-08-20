"""Create Nextcloud URL fields in Zoho and put them on the Standard layout.

Used by the OAuth CLI (``scripts/ensure_zoho_document_url_fields.py``) and the
Playwright CLI (``scripts/playwright_zoho_document_url_fields.py``). Both send
the same Settings API payloads; only the transport differs (OAuth vs a logged-in
CRM browser session).
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Protocol

from hermes_integrations.zoho_document_fields import (
    DOCUMENT_URL_FIELDS,
    DOCUMENTS_SECTION,
    existing_field_index,
    missing_document_fields,
    website_create_payload,
)

CRM_HOST_RE = re.compile(r"^(?:crmplus\.|crm\.)zoho\.[a-z.]+$", re.I)
ORG_ID_RE = re.compile(r"/org(\d+)", re.I)
CSRF_COOKIE_NAMES = ("crmcsr", "crmcsrfparam", "CSRF_TOKEN")


class SettingsClient(Protocol):
    def request(
        self,
        method: str,
        path: str,
        *,
        query: dict[str, str] | None = None,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]: ...


def crm_origin_from_url(url: str) -> str:
    """Return the CRM origin (``crm.zoho.com`` or ``crmplus.zoho.com``) from a page URL."""
    parsed = urllib.parse.urlparse(str(url or "").strip())
    host = parsed.hostname or ""
    if not CRM_HOST_RE.match(host):
        raise RuntimeError(
            f"Not on a Zoho CRM host yet (got {url!r}). Log into crm.zoho.com or crmplus.zoho.com first."
        )
    scheme = parsed.scheme or "https"
    return f"{scheme}://{host}"


def crm_org_from_url(url: str) -> str:
    """Parse ``org935119573`` from a CRM Plus / CRM URL."""
    match = ORG_ID_RE.search(str(url or ""))
    if match:
        return match.group(1)
    return str(os.environ.get("ZOHO_CRM_ORG") or "").strip()


def crm_csrf_from_cookie_header(cookie_header: str) -> str:
    """Extract the CRM CSRF token Zoho expects as ``X-ZCSRF-TOKEN: crmcsrfparam=…``."""
    parts: dict[str, str] = {}
    for chunk in (cookie_header or "").split(";"):
        chunk = chunk.strip()
        if "=" not in chunk:
            continue
        name, value = chunk.split("=", 1)
        parts[name.strip()] = urllib.parse.unquote(value.strip())
    for name in CSRF_COOKIE_NAMES:
        if parts.get(name):
            return parts[name]
    return ""


def settings_url(origin: str, path: str, query: dict[str, str] | None = None, *, version: str = "v8") -> str:
    qs = f"?{urllib.parse.urlencode(query)}" if query else ""
    return f"{origin.rstrip('/')}/crm/{version}{path}{qs}"


class OAuthZohoSettingsClient:
    """Settings API (v8) using ``ZOHO_*`` OAuth env vars."""

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


def decode_settings_response(method: str, url: str, status: int, text: str) -> dict[str, Any]:
    """Parse a Settings API body; reject HTML login pages and non-2xx JSON."""
    raw = (text or "").strip()
    if status >= 400:
        raise RuntimeError(f"{method} {url} -> HTTP {status}: {raw[:2000]}")
    if not raw:
        return {}
    if raw[:1] in "<":
        raise RuntimeError(
            f"{method} {url} returned HTML (HTTP {status}). The CRM session is not logged in."
        )
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{method} {url} -> HTTP {status}: not JSON: {raw[:500]}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"{method} {url} -> HTTP {status}: expected object, got {type(payload)}")
    return payload


def list_module_api_names(client: SettingsClient) -> set[str]:
    payload = client.request("GET", "/settings/modules")
    names: set[str] = set()
    for module in payload.get("modules") or []:
        api = module.get("api_name")
        if api:
            names.add(str(api))
    return names


def list_fields(client: SettingsClient, module: str) -> list[dict[str, Any]]:
    payload = client.request("GET", "/settings/fields", query={"module": module})
    return list(payload.get("fields") or [])


def create_website_field(client: SettingsClient, module: str, spec: dict[str, str]) -> dict[str, Any]:
    body = {"fields": [website_create_payload(spec)]}
    return client.request("POST", "/settings/fields", query={"module": module}, body=body)


def standard_layout(client: SettingsClient, module: str) -> dict[str, Any] | None:
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
    client: SettingsClient,
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


def process_module(client: SettingsClient, module: str, *, apply: bool, present: set[str]) -> None:
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
