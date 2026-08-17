#!/usr/bin/env python3
"""Backfill agency memory retrieval rows from completed intake_submissions.

Re-runs ``_insert_retrieval_rows`` for submissions whose ``draft_summary`` still
has facts/notes but retrieval was never written (or was lost). Safe to run
multiple times — may duplicate facts if the same labels already exist.

Requires Supabase creds (same as ``hermes --ops-doctor``).

    source .venv/bin/activate
    python scripts/backfill_agency_memory.py --dry-run --limit 20
    python scripts/backfill_agency_memory.py --limit 50
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
CORE = REPO_ROOT / "packages" / "rsg-hermes-core"
if str(CORE) not in sys.path:
    sys.path.insert(0, str(CORE))
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

log = logging.getLogger("backfill_agency_memory")


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Backfill client_facts from intake_submissions")
    p.add_argument("--dry-run", action="store_true", help="List candidates only")
    p.add_argument("--limit", type=int, default=100, help="Max submissions to process")
    p.add_argument(
        "--status",
        default="complete",
        help="intake_submissions.status filter (default: complete)",
    )
    p.add_argument("--submission-id", help="Process one submission by id")
    p.add_argument("-v", "--verbose", action="store_true")
    return p.parse_args()


def _fact_count(summary: dict) -> int:
    facts = summary.get("facts") or []
    return sum(1 for f in facts if isinstance(f, dict))


def main() -> int:
    args = _parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(message)s",
    )

    from hermes_integrations.supabase_client import SupabaseClient
    from hermes.operations.agency_intake_approval import _insert_retrieval_rows

    supa = SupabaseClient()

    params: dict[str, str] = {"order": "created_at.desc"}
    if args.submission_id:
        params["id"] = f"eq.{args.submission_id}"
    else:
        params["status"] = f"eq.{args.status}"

    rows = supa.select(
        "intake_submissions",
        columns="id,status,draft_summary,created_at",
        params=params,
        limit=args.limit,
    )
    if not rows:
        log.info("No intake_submissions matched")
        return 0

    processed = 0
    skipped = 0
    for row in rows:
        sid = str(row.get("id") or "")
        summary = row.get("draft_summary") or {}
        if not isinstance(summary, dict):
            log.warning("submission %s: draft_summary not a dict — skip", sid)
            skipped += 1
            continue
        n_facts = _fact_count(summary)
        has_note = bool((summary.get("note") or {}).get("body"))
        if n_facts == 0 and not has_note:
            log.debug("submission %s: no facts or note — skip", sid)
            skipped += 1
            continue
        account = (summary.get("account") or {}).get("account_name") or "(no account)"
        log.info(
            "candidate %s  status=%s  facts=%d  note=%s  account=%s",
            sid,
            row.get("status"),
            n_facts,
            "yes" if has_note else "no",
            account,
        )
        if args.dry_run:
            processed += 1
            continue
        ids = _insert_retrieval_rows(supa, summary)
        log.info(
            "submission %s inserted entities=%d facts=%d notes=%d",
            sid,
            len(ids.get("client_entities", [])),
            len(ids.get("client_facts", [])),
            len(ids.get("client_notes", [])),
        )
        processed += 1

    log.info(
        "done: %d candidates, %d skipped, dry_run=%s",
        processed,
        skipped,
        args.dry_run,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
