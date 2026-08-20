#!/usr/bin/env python3
"""Set up Nextcloud Team Folders for the RSG agency document store.

Default is a read-only status + plan. Admin writes require ``--apply`` and a
Nextcloud admin app password (the Hermes filing user is only a subadmin).

    python scripts/nextcloud_team_folders_setup.py
    python scripts/nextcloud_team_folders_setup.py --apply

Env (existing Hermes keys, plus optional admin override):
    NEXTCLOUD_URL
    NEXTCLOUD_USER / NEXTCLOUD_USERNAME
    NEXTCLOUD_APP_PASSWORD
    NEXTCLOUD_ADMIN_USER              # e.g. root
    NEXTCLOUD_ADMIN_APP_PASSWORD      # app password for that admin
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

# Repo checkout: put rsg-hermes-core on the path without requiring an install.
_ROOT = Path(__file__).resolve().parents[1]
_CORE = _ROOT / "packages" / "rsg-hermes-core"
if str(_CORE) not in sys.path:
    sys.path.insert(0, str(_CORE))

try:
    from dotenv import load_dotenv
    load_dotenv(_ROOT / ".env")
except ImportError:
    pass


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--apply",
        action="store_true",
        help="Perform admin writes (enable app, create Team Folder, MKCOL lanes).",
    )
    ap.add_argument(
        "--json",
        action="store_true",
        help="Print machine-readable JSON instead of the text report.",
    )
    ap.add_argument(
        "--quota-gb",
        type=int,
        default=500,
        help="Team Folder quota in GiB (default 500). Use 0 for unlimited.",
    )
    ap.add_argument(
        "--skip-optional-groups",
        action="store_true",
        help="Do not create Commercial Lines / Personal Lines / Management groups.",
    )
    args = ap.parse_args()

    from hermes_integrations.nextcloud_team_folders import (
        DEFAULT_QUOTA_BYTES,
        UNLIMITED_QUOTA,
        apply_setup,
        collect_status,
        format_status,
        plan_from_status,
    )

    try:
        status = collect_status()
    except Exception as exc:  # noqa: BLE001
        print(f"status failed: {exc}", file=sys.stderr)
        return 2

    plan = plan_from_status(status)
    if not args.apply:
        if args.json:
            print(json.dumps({"status": status, "plan": plan}, indent=2, default=str))
        else:
            print(format_status(status, plan=plan))
            print()
            print("Dry-run only. Re-run with --apply as a Nextcloud admin to execute.")
            if not status.get("is_admin"):
                print(
                    "This user cannot enable Team Folders. Use NEXTCLOUD_ADMIN_USER=root "
                    "plus an app password, or enable Apps → Team folders in the web UI."
                )
        return 0

    quota = UNLIMITED_QUOTA if args.quota_gb <= 0 else args.quota_gb * (1024**3)
    if args.quota_gb == 500:
        quota = DEFAULT_QUOTA_BYTES
    try:
        result = apply_setup(
            quota_bytes=quota,
            create_optional_groups=not args.skip_optional_groups,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"apply failed: {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(result, indent=2, default=str))
    else:
        print(json.dumps(result, indent=2, default=str))
        print()
        print("Set NEXTCLOUD_BASE_PATH=Agency Documents on the Hermes API box after this.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
