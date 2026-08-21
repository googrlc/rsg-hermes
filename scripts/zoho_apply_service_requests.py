#!/usr/bin/env python3
"""Create Zoho CRM Service_Requests module + fields from the CSV pack.

Dry-run by default. ``--apply`` is the only path that writes to CRM.
Does not create Zoho Desk tickets, does not write Supabase, does not
create a Claims module.

  python scripts/zoho_apply_service_requests.py
  python scripts/zoho_apply_service_requests.py --apply
  python scripts/zoho_apply_service_requests.py --print-deluge
  python scripts/zoho_apply_service_requests.py --inspect-only

Environment:
  ZOHO_CLIENT_ID / ZOHO_CLIENT_SECRET / ZOHO_REFRESH_TOKEN
  optional ZOHO_DATA_CENTER (default com)

Prefer a sandbox token. Do not --apply against production from CI.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from hermes.zoho.apply import (
    apply_plan,
    build_plan,
    inspect_live,
)
from hermes.zoho.crm_buttons import all_deluge, button_catalog
from hermes.zoho.service_requests import MODULE_API_NAME


def _print_inventory(inv: Any) -> None:
    print("=== live CRM inspect ===")
    if not inv.auth_ok:
        print(f"AUTH FAILED: {inv.auth_error}")
        for note in inv.notes:
            print(f"  note: {note}")
        return
    names = sorted(n for n in inv.module_api_names if n)
    interesting = [
        n
        for n in names
        if any(
            key in n.lower()
            for key in ("case", "service", "polic", "renew", "claim", "ams", "account", "contact")
        )
    ]
    print(f"modules: {len(names)}")
    print("interesting: " + (", ".join(interesting) or "(none)"))
    print(f"Cases fields: {len(inv.cases_fields)}")
    print(f"Service_Requests fields: {len(inv.service_request_fields)}")
    for note in inv.notes:
        print(f"  note: {note}")


def _print_plan(actions: list[dict[str, Any]]) -> None:
    print("=== plan (idempotent) ===")
    for action in actions:
        kind = action.get("kind")
        act = action.get("action")
        name = action.get("api_name") or action.get("plural_label") or ""
        reason = action.get("reason") or action.get("data_type") or ""
        print(f"  {act:6} {kind:6} {name:24} {reason}")
        if act == "create" and action.get("closest_existing"):
            print(
                f"           closest live name: {action['closest_existing']} "
                "(will not silently use Cases)"
            )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Apply the Zoho CRM Service_Requests module pack (dry-run default)."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write module + fields to CRM. Default is dry-run.",
    )
    parser.add_argument(
        "--print-deluge",
        action="store_true",
        help="Print CRM button + workflow Deluge and exit.",
    )
    parser.add_argument(
        "--inspect-only",
        action="store_true",
        help="List live modules/fields and exit (no plan dump beyond inspect).",
    )
    args = parser.parse_args()

    if args.print_deluge:
        print(all_deluge())
        print("\n=== button catalog ===")
        for item in button_catalog():
            print(f"  {item['module']}: {item['name']} ({item['function']})")
        return 0

    inventory = None
    client = None
    try:
        from hermes_integrations.zoho_client import ZohoClient, ZohoClientError

        try:
            client = ZohoClient()
        except ZohoClientError as exc:
            print(f"Zoho client not constructed: {exc}", file=sys.stderr)
            if args.apply:
                return 1
        if client is not None:
            inventory = inspect_live(client)
            _print_inventory(inventory)
            if args.inspect_only:
                return 0 if inventory.auth_ok else 1
            if args.apply and not inventory.auth_ok:
                print(
                    "Refusing --apply because live inspect failed. "
                    "Fix ZOHO_REFRESH_TOKEN (sandbox preferred) and retry.",
                    file=sys.stderr,
                )
                return 1
    except Exception as exc:  # noqa: BLE001
        print(f"inspect skipped: {exc}", file=sys.stderr)
        if args.apply:
            return 1
        if args.inspect_only:
            return 1

    actions = build_plan(inventory)
    _print_plan(actions)

    result = apply_plan(client, actions, apply=args.apply)
    print("=== result ===")
    print(json.dumps(
        {
            "apply": result["apply"],
            "module_api_name": result["module_api_name"],
            "created_count": len(result["created"]),
            "skipped_count": len(result["skipped"]),
            "pending_count": len(result.get("pending") or []),
            "errors": result["errors"],
        },
        indent=2,
    ))
    if args.apply and result["errors"]:
        return 1
    if not args.apply:
        print("(dry-run — pass --apply to write Service_Requests in CRM)")
        print("Do not apply against production unless this token is a sandbox.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
