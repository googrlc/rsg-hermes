#!/usr/bin/env python3
"""Install the CRM Account → Desk Create Service Request button.

Dry-run by default. Tries, in order:

1. CRM custom buttons API (needs ZohoCRM.settings.custom_buttons.*)
2. CRM custom links API (needs ZohoCRM.settings.custom_links.CREATE)
3. Print the Deluge + Setup steps so L can paste the true button

The Deluge function is the real button (creates a Desk ticket). A custom
link only opens the Desk new-ticket form.

  python scripts/zoho_crm_account_desk_button.py
  python scripts/zoho_crm_account_desk_button.py --apply
  python scripts/zoho_crm_account_desk_button.py --print-deluge
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from hermes.desk.crm_button import (
    BUTTON_DESCRIPTION,
    BUTTON_NAME,
    CUSTOM_LINK_NAME,
    MODULE,
    POSITIONS,
    button_setup_steps,
    custom_link_url,
    render_deluge,
)
from hermes_integrations.zoho_client import ZohoClient, ZohoClientError

import requests


def _summarize(body: Any, *, limit: int = 800) -> str:
    try:
        return json.dumps(body)[:limit]
    except TypeError:
        return str(body)[:limit]


def _crm_call(
    client: ZohoClient,
    method: str,
    url: str,
    *,
    params: dict[str, Any] | None = None,
    json_body: Any = None,
) -> Any:
    """One-shot CRM call. Do not re-auth on OAUTH_SCOPE_MISMATCH — that burns the token."""
    token = client._ensure_token()
    headers = {
        "Authorization": f"Zoho-oauthtoken {token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    resp = requests.request(
        method,
        url,
        headers=headers,
        params=params,
        json=json_body,
        timeout=client.timeout,
    )
    if not resp.ok:
        raise ZohoClientError(f"Zoho {method} {url} failed {resp.status_code}: {resp.text[:500]}")
    if not resp.content:
        return {}
    return resp.json()


def _profile_ids(client: ZohoClient) -> list[dict[str, str]]:
    body = _crm_call(client, "GET", "https://www.zohoapis.com/crm/v8/settings/profiles")
    rows = body.get("profiles") if isinstance(body, dict) else None
    if not isinstance(rows, list):
        return []
    out: list[dict[str, str]] = []
    for row in rows:
        if not isinstance(row, dict) or not row.get("id"):
            continue
        if row.get("type") == "portal_profile":
            continue
        out.append({"id": str(row["id"])})
    return out


def _existing_links(client: ZohoClient) -> list[dict[str, Any]]:
    body = _crm_call(
        client,
        "GET",
        "https://www.zohoapis.com/crm/v8/settings/custom_links",
        params={"module": MODULE},
    )
    rows = body.get("custom_links") if isinstance(body, dict) else None
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def _existing_buttons(client: ZohoClient) -> Any:
    return _crm_call(
        client,
        "GET",
        "https://www.zohoapis.com/crm/v8/settings/custom_buttons",
        params={"module": MODULE},
    )


def _create_link(client: ZohoClient, profiles: list[dict[str, str]]) -> Any:
    payload = {
        "custom_links": [
            {
                "name": CUSTOM_LINK_NAME,
                "description": BUTTON_DESCRIPTION,
                "url": custom_link_url(),
                "url_encoding": "UTF-8",
                "profiles": profiles,
            }
        ]
    }
    return _crm_call(
        client,
        "POST",
        "https://www.zohoapis.com/crm/v8/settings/custom_links",
        params={"module": MODULE},
        json_body=payload,
    )


def _button_payloads(profiles: list[dict[str, str]]) -> list[dict[str, Any]]:
    """Best-effort custom-button bodies. CRM may reject unknown keys."""
    buttons = []
    for position in ("view", "list_view_each_record"):
        buttons.append(
            {
                "name": BUTTON_NAME,
                "description": BUTTON_DESCRIPTION,
                "profiles": profiles,
                "position": position,
                "action": {
                    "type": "invoke_url",
                    "url": custom_link_url(),
                },
            }
        )
    return buttons


def _create_buttons(client: ZohoClient, profiles: list[dict[str, str]]) -> Any:
    return _crm_call(
        client,
        "POST",
        "https://www.zohoapis.com/crm/v8/settings/custom_buttons",
        params={"module": MODULE},
        json_body={"custom_buttons": _button_payloads(profiles)},
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Create CRM link/button when the token allows it.")
    parser.add_argument("--print-deluge", action="store_true", help="Print the button function and exit.")
    args = parser.parse_args()

    if args.print_deluge:
        print(render_deluge())
        return 0

    print(f"Button: {BUTTON_NAME}")
    print(f"Module: {MODULE}")
    print("Locations:")
    for position in POSITIONS:
        print(f"  - {position}")
    print("Setup steps:")
    for step in button_setup_steps():
        print(f"  - {step}")

    try:
        client = ZohoClient()
    except ZohoClientError as exc:
        print(f"CRM client unavailable: {exc}", file=sys.stderr)
        print("Paste the Deluge from --print-deluge into CRM Setup.")
        return 1

    results: dict[str, Any] = {"apply": args.apply}

    # Profiles and custom links first. Custom-buttons 401 is a scope miss —
    # hitting it first used to re-auth in a loop and lock the refresh token.
    profiles: list[dict[str, str]] = []
    try:
        profiles = _profile_ids(client)
        print(f"Internal CRM profiles: {len(profiles)}")
    except ZohoClientError as exc:
        results["profiles_error"] = str(exc)[:400]
        print("GET profiles failed:", results["profiles_error"])

    try:
        results["links"] = _existing_links(client)
        names = [row.get("name") for row in results["links"]]
        print("Existing custom links:", names)
    except ZohoClientError as exc:
        results["links_error"] = str(exc)[:400]
        print("GET custom_links failed:", results["links_error"])

    try:
        results["functions"] = _crm_call(
            client,
            "GET",
            "https://www.zohoapis.com/crm/v2/settings/functions",
            params={"type": "org"},
        )
        print("GET functions:", _summarize(results["functions"]))
    except ZohoClientError as exc:
        results["functions_error"] = str(exc)[:400]
        print("GET functions failed:", results["functions_error"])

    try:
        results["buttons_get"] = _existing_buttons(client)
        print("GET custom_buttons:", _summarize(results["buttons_get"]))
    except ZohoClientError as exc:
        results["buttons_get_error"] = str(exc)[:400]
        print("GET custom_buttons failed:", results["buttons_get_error"])

    if not args.apply:
        print("Dry-run. Pass --apply to create the custom link/button.")
        print("--- Deluge ---")
        print(render_deluge())
        return 0

    if not profiles:
        print("Cannot create a link/button without profile ids. Paste the Deluge in Setup.")
        print(render_deluge())
        return 1

    existing_names = {row.get("name") for row in results.get("links") or []}
    if CUSTOM_LINK_NAME in existing_names:
        print(f"Custom link {CUSTOM_LINK_NAME!r} already exists on {MODULE}.")
    else:
        try:
            created_link = _create_link(client, profiles)
            results["link_create"] = created_link
            print("Created custom link:", _summarize(created_link))
        except ZohoClientError as exc:
            results["link_create_error"] = str(exc)[:500]
            print("POST custom_links failed:", results["link_create_error"])

    try:
        created_buttons = _create_buttons(client, profiles)
        results["button_create"] = created_buttons
        print("Created custom buttons:", _summarize(created_buttons))
    except ZohoClientError as exc:
        results["button_create_error"] = str(exc)[:500]
        print("POST custom_buttons failed:", results["button_create_error"])
        print("Paste the Deluge function in CRM Setup — that is the real per-Account button.")
        print(render_deluge())

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
