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

If the refresh token lacks ``ZohoCRM.settings.ALL``, use the Playwright
script instead (log into CRM in a browser, then the same Settings calls run
inside that session):

  PYTHONPATH=packages/rsg-hermes-core:. \\
    python scripts/playwright_zoho_document_url_fields.py --apply

Usage:
  PYTHONPATH=packages/rsg-hermes-core:. python scripts/ensure_zoho_document_url_fields.py
  PYTHONPATH=packages/rsg-hermes-core:. python scripts/ensure_zoho_document_url_fields.py --apply
  PYTHONPATH=packages/rsg-hermes-core:. python scripts/ensure_zoho_document_url_fields.py --apply --module Accounts
"""

from __future__ import annotations

import argparse
import os
import sys

from hermes_integrations.zoho_document_fields import DOCUMENT_URL_FIELDS
from hermes_integrations.zoho_settings_ensure import (
    OAuthZohoSettingsClient,
    list_module_api_names,
    process_module,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Create Zoho Nextcloud URL fields and place them on layouts.")
    parser.add_argument("--apply", action="store_true", help="Create fields and patch layouts (default is dry-run).")
    parser.add_argument("--module", action="append", dest="modules", help="Limit to module API name(s).")
    args = parser.parse_args()

    for var in ("ZOHO_CLIENT_ID", "ZOHO_CLIENT_SECRET", "ZOHO_REFRESH_TOKEN"):
        if var not in os.environ:
            print(f"Missing env var {var}.", file=sys.stderr)
            print(
                "No OAuth? Log into CRM with Playwright instead:\n"
                "  PYTHONPATH=packages/rsg-hermes-core:. "
                "python scripts/playwright_zoho_document_url_fields.py --apply",
                file=sys.stderr,
            )
            return 1

    selected = args.modules or list(DOCUMENT_URL_FIELDS)
    unknown = [m for m in selected if m not in DOCUMENT_URL_FIELDS]
    if unknown:
        print(f"Unknown module(s): {unknown}. Known: {', '.join(DOCUMENT_URL_FIELDS)}", file=sys.stderr)
        return 1

    client = OAuthZohoSettingsClient()
    present = list_module_api_names(client)
    for module in selected:
        process_module(client, module, apply=args.apply, present=present)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
