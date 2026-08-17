#!/usr/bin/env python3
"""Inventory SharePoint sites before RSG-Knowledge consolidation.

Requires MS365_* (and optional SHAREPOINT_SITE_URL) in the environment — same
credentials as sharepoint_mcp.py. Run from repo root with venv active:

    source .venv/bin/activate
    python scripts/sharepoint_site_inventory.py
    python scripts/sharepoint_site_inventory.py --query RSG --deep
    python scripts/sharepoint_site_inventory.py --output docs/sharepoint-site-inventory.md

Phase A of docs/sharepoint-knowledge-consolidation.md: inventory every candidate
site *before* creating or populating RSG-Knowledge.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = REPO_ROOT / "docs" / "sharepoint-site-inventory.md"
CORE_PATH = REPO_ROOT / "packages" / "rsg-hermes-core"
if str(CORE_PATH) not in sys.path:
    sys.path.insert(0, str(CORE_PATH))


def _gather(query: str, *, deep: bool, limit: int) -> list[dict]:
    from hermes_integrations.ms365_client import MS365ClientError
    from hermes_integrations.sharepoint_client import SharePointClient

    client = SharePointClient()
    sites = client.list_sites(query, limit=limit)
    rows: list[dict] = []
    for site in sites:
        row = {
            "displayName": site.get("displayName") or site.get("name") or "",
            "name": site.get("name") or "",
            "webUrl": site.get("webUrl") or "",
            "id": site.get("id") or "",
            "libraries": [],
            "root_items_sample": [],
        }
        if deep and row["webUrl"]:
            try:
                resolved = client.get_site(row["webUrl"])
                row["id"] = resolved.get("id") or row["id"]
                client._default_site = resolved  # noqa: SLF001 — scope list_drives
                for drive in client.list_drives(resolved["id"]):
                    lib = {
                        "name": drive.get("name"),
                        "id": drive.get("id"),
                        "webUrl": drive.get("webUrl"),
                    }
                    row["libraries"].append(lib)
                    if drive.get("id") == client.default_drive()["id"]:
                        try:
                            items = client.list_folder("/", drive_id=drive["id"])[:15]
                            row["root_items_sample"] = [
                                {
                                    "name": i.get("name"),
                                    "folder": bool(i.get("folder")),
                                    "size": i.get("size"),
                                }
                                for i in items
                            ]
                        except MS365ClientError:
                            pass
            except MS365ClientError as exc:
                row["inventory_error"] = str(exc)
        rows.append(row)
    return rows


def _render_markdown(rows: list[dict], *, query: str, deep: bool) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        "# SharePoint site inventory",
        "",
        f"Generated: **{now}** · search query: `{query}` · deep: `{deep}`",
        "",
        "> **Do not create RSG-Knowledge until this inventory is reviewed.**",
        "> Map each row to keep / merge / archive / delete before building the target site.",
        "",
        "See [`sharepoint-knowledge-consolidation.md`](sharepoint-knowledge-consolidation.md).",
        "",
        "## Sites",
        "",
        "| Display name | URL | Site ID | Libraries | Root sample | Decision | Target folder |",
        "|---|---|---|---|---|---|---|",
    ]
    for row in rows:
        libs = ", ".join(
            str(l.get("name") or "?") for l in row.get("libraries") or []
        ) or "—"
        sample_names = [
            s.get("name") for s in (row.get("root_items_sample") or [])[:5]
        ]
        sample = ", ".join(n for n in sample_names if n) or "—"
        if row.get("inventory_error"):
            sample = f"error: {row['inventory_error'][:40]}"
        url = row.get("webUrl") or ""
        lines.append(
            "| {name} | {url} | `{id}` | {libs} | {sample} | TBD | TBD |".format(
                name=(row.get("displayName") or "—").replace("|", "\\|"),
                url=url.replace("|", "\\|") if url else "—",
                id=(row.get("id") or "")[:36] + "…" if row.get("id") else "—",
                libs=libs.replace("|", "\\|"),
                sample=sample.replace("|", "\\|")[:80],
            )
        )
    lines.extend(
        [
            "",
            "## Decision key",
            "",
            "| Decision | Meaning |",
            "|---|---|",
            "| **keep** | Already canonical; may become RSG-Knowledge itself |",
            "| **merge** | Copy content into RSG-Knowledge, then stub/archive source |",
            "| **archive** | Copy to `99-archive/YYYY-MM-site-name/`, then read-only source |",
            "| **delete** | Empty or superseded; archive zip only if required |",
            "| **exclude** | Not agency knowledge (project site, client site, Teams junk) |",
            "",
            "## Next steps",
            "",
            "1. Fill **Decision** and **Target folder** columns above.",
            "2. Create **RSG-Knowledge** only after the map is approved.",
            "3. Copy (never cut) files per the map; log in `00-meta/migration-log.md`.",
            "4. Set `SHAREPOINT_SITE_URL` to the final site when migration is complete.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--query",
        default="*",
        help="Graph site search query (default: * for broad inventory)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=100,
        help="Max sites to return (default 100)",
    )
    parser.add_argument(
        "--deep",
        action="store_true",
        help="List libraries and root folder sample per site (slower)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"Markdown output path (default: {DEFAULT_OUTPUT.relative_to(REPO_ROOT)})",
    )
    parser.add_argument(
        "--json",
        type=Path,
        default=None,
        help="Optional JSON dump path",
    )
    args = parser.parse_args()

    try:
        rows = _gather(args.query, deep=args.deep, limit=args.limit)
    except Exception as exc:  # noqa: BLE001
        print(
            "SharePoint inventory failed. Set MS365_TENANT_ID, MS365_CLIENT_ID, "
            "MS365_CLIENT_SECRET in the environment (Cursor MCP env or shell).\n"
            f"Error: {exc}",
            file=sys.stderr,
        )
        return 1

    md = _render_markdown(rows, query=args.query, deep=args.deep)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(md, encoding="utf-8")
    print(f"Wrote {len(rows)} site(s) → {args.output}")

    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(rows, indent=2), encoding="utf-8")
        print(f"Wrote JSON → {args.json}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
