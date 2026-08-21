#!/usr/bin/env python3
"""Print (and optionally verify) Zoho CRM setup for the Data Quality Investigator button.

Hermes relay path (recommended):
  Zoho button → POST /api/webhooks/zoho/dqi-investigation
  Hermes → Cursor automation webhook

Usage:
  python scripts/zoho_setup_dqi_integration.py
  python scripts/zoho_setup_dqi_integration.py --check-zoho
  python scripts/zoho_setup_dqi_integration.py --smoke-hermes https://hermes-gretch.tail1cbc83.ts.net:8444

Environment (Hermes box /opt/app/.env):
  SERVICE_WEBHOOK_SECRET
  CURSOR_AUTOMATION_WEBHOOK_URL
  CURSOR_AUTOMATION_WEBHOOK_KEY
  ZOHO_CLIENT_ID / ZOHO_CLIENT_SECRET / ZOHO_REFRESH_TOKEN

Zoho CRM Variables to create manually (Text type):
  hermes_dqi_webhook_base    → HERMES public base URL (no trailing path)
  hermes_dqi_webhook_secret  → same as SERVICE_WEBHOOK_SECRET
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request


def _check_env() -> list[str]:
    missing = []
    for key in (
        "SERVICE_WEBHOOK_SECRET",
        "CURSOR_AUTOMATION_WEBHOOK_URL",
        "CURSOR_AUTOMATION_WEBHOOK_KEY",
    ):
        if not (os.environ.get(key) or "").strip():
            missing.append(key)
    return missing


def _check_zoho_modules() -> None:
    from hermes_integrations.zoho_client import ZohoClientError, get_client, reset_client

    reset_client()
    zoho = get_client()
    body = zoho._get("settings/modules")
    modules = body.get("modules") or []
    names = sorted(m.get("api_name") or "" for m in modules if isinstance(m, dict))
    for want in ("Renewals", "Policies"):
        mark = "OK" if want in names else "MISSING"
        print(f"  [{mark}] module {want}")
    if "Renewals" not in names:
        print("  Renewals module not found — check Setup → APIs → Modules for API name.")


def _smoke_hermes(base_url: str, secret: str, policy_number: str) -> None:
    url = base_url.rstrip("/") + "/api/webhooks/zoho/dqi-investigation"
    payload = json.dumps({"policy_number": policy_number, "source": "setup_smoke"}).encode()
    req = urllib.request.Request(
        url,
        data=payload,
        method="POST",
        headers={
            "Authorization": f"Bearer {secret}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=45) as resp:
            body = resp.read().decode()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode()[:500]
        raise SystemExit(f"Hermes smoke failed HTTP {exc.code}: {detail}") from exc
    print(f"  Hermes relay OK ({url})")
    print(f"  Response: {body[:300]}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check-zoho", action="store_true", help="Verify Zoho OAuth + modules")
    parser.add_argument(
        "--smoke-hermes",
        metavar="BASE_URL",
        help="POST a smoke policy_number through the Hermes relay",
    )
    parser.add_argument("--policy-number", default="990414352")
    args = parser.parse_args()

    print("=== Hermes env (/opt/app/.env) ===")
    missing = _check_env()
    if missing:
        print("  Missing:", ", ".join(missing))
    else:
        print("  OK — SERVICE_WEBHOOK_SECRET + Cursor webhook vars present")

    secret = (os.environ.get("SERVICE_WEBHOOK_SECRET") or "").strip()
    base = (os.environ.get("HERMES_PUBLIC_BASE_URL") or "https://hermes-gretch.tail1cbc83.ts.net:8444").strip()

    print("\n=== Tailscale Funnel (Zoho must reach Hermes over HTTPS) ===")
    print("  On hermes-gretch:")
    print("    tailscale funnel --bg 8444")
    print(f"  Then set hermes_dqi_webhook_base CRM variable to public base, e.g. {base}")

    print("\n=== Zoho CRM Variables (Text — create in Setup → CRM Variables) ===")
    print("  hermes_dqi_webhook_base   → public Hermes URL (no /api/... suffix)")
    print("  hermes_dqi_webhook_secret → SERVICE_WEBHOOK_SECRET value")

    print("\n=== Deluge function + button ===")
    print("  Function: docs/zoho/deluge/trigger_policy_investigation.deluge")
    print("  Name: automation.trigger_cursor_policy_investigation")
    print("  Arg: renewalId ← Record Id (merge-field picker)")
    print("  Button: Renewals layout → Policy verification")

    print("\n=== Full guide ===")
    print("  docs/integrations/zoho-data-quality-investigator-webhook.md")

    if args.check_zoho:
        print("\n=== Zoho module check ===")
        try:
            from hermes_integrations.zoho_client import ZohoClientError

            _check_zoho_modules()
        except ZohoClientError as exc:
            print(f"  Zoho error: {exc}")
            return 1

    if args.smoke_hermes:
        if not secret:
            print("\nSERVICE_WEBHOOK_SECRET required for smoke test", file=sys.stderr)
            return 1
        print("\n=== Hermes relay smoke ===")
        _smoke_hermes(args.smoke_hermes, secret, args.policy_number)

    return 0 if not missing else 1


if __name__ == "__main__":
    raise SystemExit(main())
