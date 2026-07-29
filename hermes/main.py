#!/usr/bin/env python3
"""Hermes entrypoint: REPL or one-shot CLI for a VPS or automation."""

from __future__ import annotations

import argparse
import logging
import sys

from dotenv import load_dotenv

import os

from hermes.core.dispatcher import Dispatcher

COMMAND_CATALOG = """Hermes command catalog

Approval tokens:
- APPROVE CRM ONLY
- APPROVE SUPABASE ONLY
- APPROVE TASKS ONLY
- APPROVE ALL
- REVISE
- CANCEL

Property research:
- Research [address] for property underwriting
- Find county, parcel, owner, tax values, year built, square footage, and source links for [address]
- Run title-like pre-check for [address]
- Check public recorder clues for [address]
- Estimate rebuild cost range for [address] using available property facts

Business research:
- Research this business for underwriting: [business name] [address] [website]
- Enrich this lead: [business name] [address] [phone/email]
- Find NAICS, SIC, GL class, WC class, business description, and risk flags for [business name]

Document extraction:
- Read this dec page and extract policy data
- Summarize this policy document
- Compare this quote to the current policy
- Extract all vehicles and drivers from this document
- Review this loss run and flag underwriting issues

Transcript workflow:
- Summarize this call transcript
- Turn this call transcript into CRM notes and tasks
- Extract client promises, RSG promises, deadlines, and follow-up items from this transcript

Medicare:
- Review this Medicare client intake
- Prepare Medicare checklist for this client
- Check RSG Medicare tables for carrier options for this client

Life:
- Review this life insurance intake
- Prepare a preliminary life underwriting summary
- Check carrier table-rating data for this life case

Commissions:
- Calculate expected commission for this policy
- Check commission rule for [carrier] [LOB] [new/renewal]
- Compare expected vs posted commission for this policy

CRM draft commands:
- Stage intake: [paste the account summary]
- New commercial prospect: [paste the account summary]
- Show me the proposed NowCerts changes for policy [number]

Policy data repair:
- Repair policy accounts dry run
- Repair policy accounts apply
"""


def main() -> int:
    load_dotenv()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        stream=sys.stderr,
    )
    parser = argparse.ArgumentParser(description="Hermes — agency CRM coordinator")
    parser.add_argument("command", nargs="*", help="One-shot command (omit for REPL)")
    parser.add_argument("--commands", action="store_true", help="Print the Hermes command catalog and exit")
    parser.add_argument("--ping", action="store_true", help="Check that Hermes itself is up and exit")
    parser.add_argument("--kpi", action="store_true", help="Print canonical book counts (clients, policies, premium)")
    parser.add_argument(
        "--revenue-sentinel",
        action="store_true",
        help="Run Project 85 daily revenue guardrail briefing once",
    )
    parser.add_argument(
        "--revenue-sentinel-dry-run",
        action="store_true",
        help="Render sentinel briefing without posting to Slack",
    )
    parser.add_argument(
        "--revenue-sentinel-force",
        action="store_true",
        help="Bypass daily idempotency guard and post even if already sent",
    )
    parser.add_argument(
        "--revenue-sentinel-health",
        action="store_true",
        help="Check sentinel freshness/config status without posting",
    )
    parser.add_argument(
        "--renewal-refresh",
        action="store_true",
        help="Rebuild renewal_candidates from the live book (eligibility engine) + project eligible to project_85_renewals",
    )
    parser.add_argument(
        "--renewal-refresh-dry-run",
        action="store_true",
        help="Preview renewal eligibility (eligible/needs_verification/excluded counts) without writing",
    )
    parser.add_argument(
        "--agency-snapshot",
        action="store_true",
        help="Compute and write today's agency_snapshots row (book size + trailing-12mo retention) "
             "from the live book. Idempotent per day — replaces the same-day row.",
    )
    parser.add_argument(
        "--agency-snapshot-dry-run",
        action="store_true",
        help="Preview the agency snapshot (computed numbers, no write)",
    )
    parser.add_argument(
        "--renewal-classify",
        action="store_true",
        help="Re-grade urgency (risk_status) over eligible renewal_candidates (Command Center)",
    )
    parser.add_argument(
        "--renewal-classify-dry-run",
        action="store_true",
        help="Preview renewal urgency re-grade without writing",
    )
    parser.add_argument(
        "--renewal-executor",
        action="store_true",
        help="Process approved renewal jobs from outbound_sync_queue (Job Contract v2)",
    )
    parser.add_argument(
        "--renewal-executor-limit",
        type=int,
        default=1,
        help="Max approved renewal jobs to process this run (contract: claim one)",
    )
    parser.add_argument(
        "--renewal-executor-dry-run",
        action="store_true",
        help="Preview renewal jobs (validate+read+compare) without claiming or writing",
    )
    parser.add_argument(
        "--run-renewal-executor-worker",
        action="store_true",
        help="Continuously process approved renewal jobs every N seconds (opt-in)",
    )
    parser.add_argument(
        "--renewal-executor-poll-seconds",
        type=float,
        default=300.0,
        help="Poll interval for --run-renewal-executor-worker (default: 300s)",
    )
    parser.add_argument(
        "--intake-executor",
        action="store_true",
        help="Process approved new-business intake jobs (create_insured) from outbound_sync_queue",
    )
    parser.add_argument(
        "--intake-executor-limit",
        type=int,
        default=1,
        help="Max approved intake jobs to process this run",
    )
    parser.add_argument(
        "--intake-executor-dry-run",
        action="store_true",
        help="Preview intake insured payloads (field keys) without claiming or writing — verify casing",
    )
    parser.add_argument(
        "--quote-executor",
        action="store_true",
        help="Process approved quote jobs (opportunity → NowCerts Policy·IsQuote) from outbound_sync_queue",
    )
    parser.add_argument(
        "--quote-executor-limit",
        type=int,
        default=1,
        help="Max approved quote jobs to process this run",
    )
    parser.add_argument(
        "--quote-executor-dry-run",
        action="store_true",
        help="Preview quote Policy/Insert payloads without claiming or writing to NowCerts",
    )
    parser.add_argument(
        "--opportunity-writeback-executor",
        action="store_true",
        help="Process approved opportunity terminal-writeback jobs (CRM Bound/Won or Lost → NowCerts) from outbound_sync_queue",
    )
    parser.add_argument(
        "--opportunity-writeback-limit",
        type=int,
        default=1,
        help="Max approved opportunity-writeback jobs to process this run",
    )
    parser.add_argument(
        "--opportunity-writeback-dry-run",
        action="store_true",
        help="Preview opportunity-writeback jobs without claiming or writing to NowCerts",
    )
    parser.add_argument(
        "--opportunity-writeback-opportunity-id",
        default=None,
        help="Read-only diagnostic: resolve one NowCerts opportunity by id and print "
             "the exact writeback payload (assignedTo shape, insuredDatabaseId presence) "
             "regardless of queue state. Forces dry-run — never writes.",
    )
    parser.add_argument(
        "--casework-executor",
        action="store_true",
        help="Process approved case/task jobs (agency_crm case/task → NowCerts task) from outbound_sync_queue",
    )
    parser.add_argument(
        "--casework-executor-limit",
        type=int,
        default=1,
        help="Max approved case/task jobs to process this run",
    )
    parser.add_argument(
        "--casework-executor-dry-run",
        action="store_true",
        help="Preview case/task NowCerts task payloads without claiming or writing",
    )
    parser.add_argument(
        "--proactive-cases",
        action="store_true",
        help="Find slipped work (renewals with no case, stalled cases, overdue tasks). "
             "Reports only unless --proactive-cases-commit is also given.",
    )
    parser.add_argument(
        "--proactive-cases-commit",
        action="store_true",
        help="Actually open the renewal cases the scan found (default is report-only)",
    )
    parser.add_argument(
        "--proactive-cases-horizon",
        type=int,
        default=30,
        help="Days ahead to look for uncovered renewals (default 30)",
    )
    parser.add_argument(
        "--proactive-cases-limit",
        type=int,
        default=10,
        help="Max cases to open in one run (default 10). Anything past the cap is reported.",
    )
    parser.add_argument(
        "--run-scheduler",
        action="store_true",
        help="Run the executor scheduler loop (intake+renewal every N s, locked). Requires SCHEDULER_ENABLED.",
    )
    parser.add_argument(
        "--scheduler-interval",
        type=int,
        default=int(os.environ.get("SCHEDULER_INTERVAL_SECONDS", "300")),
        help="Scheduler cadence in seconds (default 300 = 5 min)",
    )
    parser.add_argument(
        "--scheduler-batch",
        type=int,
        default=int(os.environ.get("SCHEDULER_BATCH", "10")),
        help="Max jobs each executor processes per cycle (default 10)",
    )
    parser.add_argument(
        "--scheduler-health",
        action="store_true",
        help="Print scheduler health (NowCerts queue depths + lock state) and exit",
    )
    parser.add_argument(
        "--commission-audit",
        action="store_true",
        help="Run Revenue Integrity commission blind-spot audit once",
    )
    parser.add_argument(
        "--commission-audit-dry-run",
        action="store_true",
        help="Render commission audit briefing without posting to Slack",
    )
    parser.add_argument(
        "--commission-audit-force",
        action="store_true",
        help="Bypass daily idempotency guard for commission audit",
    )
    parser.add_argument(
        "--eom-scorecard",
        action="store_true",
        help="Post end-of-month revenue scorecard for previous month",
    )
    parser.add_argument(
        "--eom-scorecard-dry-run",
        action="store_true",
        help="Render EOM scorecard without posting",
    )
    parser.add_argument(
        "--eom-scorecard-force",
        action="store_true",
        help="Bypass monthly idempotency guard for EOM scorecard",
    )
    parser.add_argument(
        "--commission-reconcile-file",
        type=str,
        help="Reconcile a carrier statement file (csv/xlsx/pdf) against commission_ledger",
    )
    parser.add_argument(
        "--commission-reconcile-dry-run",
        action="store_true",
        help="Run reconciliation without posting Slack alert",
    )
    parser.add_argument(
        "--api",
        action="store_true",
        help="Run Hermes REST API server (default port 8484)",
    )
    parser.add_argument(
        "--api-port",
        type=int,
        default=8484,
        help="Port for the REST API server (default: 8484)",
    )
    # --- NowCerts → Supabase sync commands ---
    parser.add_argument(
        "--sync-canonical-book",
        action="store_true",
        help="Run NowCerts → Supabase canonical book sync (refreshes canonical_clients/canonical_policies/"
             "nowcerts_insured_mirror that --renewal-refresh reads). Additive, preserves renewed_policy lineage.",
    )
    parser.add_argument(
        "--sync-canonical-book-dry-run",
        action="store_true",
        help="Preview the canonical book sync (counts + no writes to Supabase)",
    )
    parser.add_argument(
        "--sync-canonical-book-since",
        type=str,
        default=None,
        help="Incremental: only pull NowCerts records changed since this ISO datetime "
             "(omit for the full nightly reconciliation that also collapses duplicates)",
    )
    parser.add_argument(
        "--sync-canonical-book-limit",
        type=int,
        default=None,
        help="Cap records processed per entity (useful for a first dry-run)",
    )
    parser.add_argument(
        "--sync-quotes",
        action="store_true",
        help="Run NowCerts quotes → Supabase opportunities pipeline sync (Policy rows with isQuote=true; "
             "idempotent per client+LOB, never resets a human-advanced stage)",
    )
    parser.add_argument(
        "--sync-quotes-dry-run",
        action="store_true",
        help="Preview the quotes → opportunities sync (counts + no writes)",
    )
    parser.add_argument(
        "--sync-quotes-since",
        type=str,
        default=None,
        help="Only sync quotes changed since this ISO datetime (e.g. 2026-06-01T00:00:00)",
    )
    parser.add_argument(
        "--sync-quotes-limit",
        type=int,
        default=None,
        help="Cap the number of quotes processed (useful for a first dry-run)",
    )
    parser.add_argument(
        "--repair-quote-board",
        action="store_true",
        help="Correct type/premium/owner/close-date on existing opportunities from "
             "canonical_quotes. Previews by default; add --apply to write",
    )
    parser.add_argument(
        "--repair-quote-board-apply",
        action="store_true",
        help="Actually write the corrections found by --repair-quote-board (backs up first)",
    )
    parser.add_argument(
        "--dedupe-opportunities",
        action="store_true",
        help="Merge duplicate deals (one client+LOB, a CRM row and a quote-sync twin) "
             "into the CRM row. Previews by default; add --apply to write",
    )
    parser.add_argument(
        "--dedupe-opportunities-apply",
        action="store_true",
        help="Actually merge and delete the duplicate deals found by --dedupe-opportunities",
    )
    parser.add_argument(
        "--sync-opportunities",
        action="store_true",
        help="Mirror NowCerts Opportunities (OpportunitiesList) → Supabase opportunities pipeline. "
             "Stages stored verbatim; idempotent per NowCerts opportunity id.",
    )
    parser.add_argument(
        "--sync-opportunities-dry-run",
        action="store_true",
        help="Preview the NowCerts opportunity → pipeline sync (counts + no writes)",
    )
    parser.add_argument(
        "--sync-opportunities-limit",
        type=int,
        default=None,
        help="Cap the number of opportunities processed (useful for a first dry-run)",
    )
    parser.add_argument(
        "--sync-commissions",
        action="store_true",
        help="Seed commission_ledger EXPECTED values from canonical_policies (NowCerts agency commission "
             "+ rule fallback). Preserves statement-sourced actuals/reconciliation. Keyed per policy_number.",
    )
    parser.add_argument(
        "--sync-commissions-dry-run",
        action="store_true",
        help="Preview the commission expected-value seed (counts + no writes)",
    )
    parser.add_argument(
        "--sync-commissions-limit",
        type=int,
        default=None,
        help="Cap the number of policies processed (useful for a first dry-run)",
    )
    parser.add_argument(
        "--sync-commissions-since",
        type=str,
        default=None,
        help="Earliest policy effective_date to ledger (YYYY-MM-DD; default 2026-01-01). "
             "Excludes future-effective + older-than-since business.",
    )
    # --- Hermes Operations Center commands ---
    # --- Document library (Supermemory + index; freeform internal refs) ---
    parser.add_argument(
        "--doc-add",
        action="store_true",
        help="Save a document to the Hermes library (Supermemory + index)",
    )
    parser.add_argument("--doc-title", help="Document title (with --doc-add)")
    parser.add_argument(
        "--doc-type", default="reference",
        choices=["proposal", "note", "renewal", "comparison", "appetite", "reference", "other"],
        help="Document type (default: reference)",
    )
    parser.add_argument("--doc-account", help="Client account name (client folder)")
    parser.add_argument("--doc-folder", help="Internal freeform folder name (default: General)")
    parser.add_argument("--doc-file", help="Read document content from this file (else stdin)")
    parser.add_argument(
        "--doc-folders", action="store_true", help="List the document library folder tree"
    )
    parser.add_argument(
        "--ops-doctor",
        action="store_true",
        help="Check Supabase connectivity and Hermes table health",
    )
    parser.add_argument(
        "--run-intake-worker",
        action="store_true",
        help="Continuously poll intake_submissions every N seconds (Phase 3 rsg-intake worker)",
    )
    parser.add_argument(
        "--intake-poll-seconds",
        type=float,
        default=5.0,
        help="Poll interval for --run-intake-worker (default: 5s)",
    )
    parser.add_argument(
        "--snapshot-kpis",
        action="store_true",
        help="Record system health, finance, and renewal KPI snapshots",
    )
    parser.add_argument(
        "--curate-skills",
        action="store_true",
        help="Report-only age audit of .claude/skills (flags stale/review candidates; never deletes)",
    )
    args = parser.parse_args()

    if args.commands:
        print(COMMAND_CATALOG)
        return 0


    # --- NowCerts → Supabase canonical book sync (feeds --renewal-refresh) ---
    if args.sync_canonical_book or args.sync_canonical_book_dry_run:
        from hermes.integrations.supabase_client import SupabaseClient, SupabaseClientError
        from hermes.sync.canonical_book_sync import run_canonical_book_sync
        from hermes.sync.nowcerts_client import NowCertsClient, NowCertsClientError

        try:
            supa = SupabaseClient()
        except SupabaseClientError as e:
            print(f"Supabase connection failed: {e}", file=sys.stderr)
            return 2
        try:
            nc = NowCertsClient()
        except NowCertsClientError as e:
            print(f"NowCerts connection failed: {e}", file=sys.stderr)
            return 2

        book_result = run_canonical_book_sync(
            nc,
            supa,
            since=args.sync_canonical_book_since,
            dry_run=args.sync_canonical_book_dry_run,
            limit=args.sync_canonical_book_limit,
        )
        print(book_result.message)
        if book_result.errors:
            print("\nErrors:")
            for err in book_result.errors[:50]:
                print(f"- {err}")
        return 0 if book_result.ok else 1

    # --- NowCerts quotes → Supabase opportunities pipeline sync ---
    if args.repair_quote_board or args.repair_quote_board_apply:
        from hermes.integrations.supabase_client import SupabaseClient, SupabaseClientError
        from hermes.sync.nowcerts_client import NowCertsClient
        from hermes.sync.quote_board_repair import run_repair

        try:
            supa = SupabaseClient()
        except SupabaseClientError as e:
            print(f"Supabase connection failed: {e}", file=sys.stderr)
            return 2
        # The live register outranks the canonical_quotes snapshot, which has not
        # been refreshed since 2026-07-21. Unreachable AMS falls back and says so.
        try:
            nc = NowCertsClient()
        except Exception as e:  # noqa: BLE001
            print(f"NowCerts unavailable ({e}); repairing from the snapshot", file=sys.stderr)
            nc = None
        apply = bool(args.repair_quote_board_apply)
        res = run_repair(supa, nc, apply=apply)
        print("Quote board repair — " + ("APPLYING" if apply else "PREVIEW (nothing written)"))
        for fix in res.fixes[:40]:
            print(f"  {fix.describe()}")
        if len(res.fixes) > 40:
            print(f"  …and {len(res.fixes) - 40} more")
        for c in res.closed[:40]:
            print(f"  NOT OPEN (bound/declined/expired — belongs off the board) {c}")
        if len(res.closed) > 40:
            print(f"  …and {len(res.closed) - 40} more not open")
        for u in res.unmatched:
            print(f"  UNMATCHED (no quote in the register) {u}")
        for e in res.errors:
            print(f"  ERROR {e}")
        if res.backup_path:
            print(f"  backup: {res.backup_path}")
        print(res.message)
        return 0 if not res.errors else 1

    if args.dedupe_opportunities or args.dedupe_opportunities_apply:
        from hermes.integrations.supabase_client import SupabaseClient, SupabaseClientError
        from hermes.sync.opportunity_dedupe import run_dedupe

        try:
            supa = SupabaseClient()
        except SupabaseClientError as e:
            print(f"Supabase connection failed: {e}", file=sys.stderr)
            return 2
        apply = bool(args.dedupe_opportunities_apply)
        res = run_dedupe(supa, apply=apply)
        head = "MERGING" if apply else "PREVIEW (nothing written — add --dedupe-opportunities-apply)"
        print(f"Opportunity dedupe — {head}")
        for pair in res.pairs:
            print(f"  {pair.describe()}")
        for s in res.skipped:
            print(f"  SKIP {s}")
        for e in res.errors:
            print(f"  ERROR {e}")
        print(res.message)
        return 0 if not res.errors else 1

    if args.sync_quotes or args.sync_quotes_dry_run:
        from hermes.integrations.supabase_client import SupabaseClient, SupabaseClientError
        from hermes.sync.nowcerts_client import NowCertsClient, NowCertsClientError
        from hermes.sync.quote_sync import run_quote_sync

        try:
            supa = SupabaseClient()
        except SupabaseClientError as e:
            print(f"Supabase connection failed: {e}", file=sys.stderr)
            return 2
        try:
            nc = NowCertsClient()
        except NowCertsClientError as e:
            print(f"NowCerts connection failed: {e}", file=sys.stderr)
            return 2

        quote_result = run_quote_sync(
            nc,
            supa,
            since=args.sync_quotes_since,
            dry_run=args.sync_quotes_dry_run,
            limit=args.sync_quotes_limit,
        )
        print(quote_result.message)
        if quote_result.errors:
            print("\nErrors:")
            for err in quote_result.errors[:50]:
                print(f"- {err}")
        return 0 if quote_result.ok else 1

    # --- NowCerts Opportunities → opportunities pipeline mirror ---
    if args.sync_opportunities or args.sync_opportunities_dry_run:
        from hermes.integrations.supabase_client import SupabaseClient, SupabaseClientError
        from hermes.sync.nowcerts_client import NowCertsClient, NowCertsClientError
        from hermes.sync.opportunity_sync import run_opportunity_sync

        try:
            supa = SupabaseClient()
        except SupabaseClientError as e:
            print(f"Supabase connection failed: {e}", file=sys.stderr)
            return 2
        try:
            nc = NowCertsClient()
        except NowCertsClientError as e:
            print(f"NowCerts connection failed: {e}", file=sys.stderr)
            return 2

        opp_result = run_opportunity_sync(
            nc, supa,
            dry_run=args.sync_opportunities_dry_run,
            limit=args.sync_opportunities_limit,
        )
        print(opp_result.message)
        for pv in opp_result.previews[:30]:
            print(f"  {pv['action'].upper()} {pv['client']!r} · {pv['lob']} · {pv['stage']}")
        if opp_result.errors:
            print("\nErrors:")
            for err in opp_result.errors[:50]:
                print(f"- {err}")
        return 0 if opp_result.ok else 1

    # --- Canonical book → commission_ledger expected-value seeding ---
    if args.sync_commissions or args.sync_commissions_dry_run:
        from hermes.integrations.supabase_client import SupabaseClient, SupabaseClientError
        from hermes.sync.commission_sync import run_commission_sync

        try:
            supa = SupabaseClient()
        except SupabaseClientError as e:
            print(f"Supabase connection failed: {e}", file=sys.stderr)
            return 2

        comm_result = run_commission_sync(
            supa,
            dry_run=args.sync_commissions_dry_run,
            limit=args.sync_commissions_limit,
            since=args.sync_commissions_since,
        )
        print(comm_result.message)
        if comm_result.errors:
            print("\nErrors:")
            for err in comm_result.errors[:50]:
                print(f"- {err}")
        return 0 if comm_result.ok else 1

    # --- Syncback: enrich one NowCerts insured from an ACTIVE account ---
    # --- Email triage (requires the provider client + Supabase) ---
    if args.doc_folders:
        from hermes.documents.store import list_folders
        for f in list_folders():
            print(f"[{f['space']:8}] {f['name']}  ({f['document_count']})")
        return 0

    if args.doc_add:
        from hermes.documents.store import save_document, DocumentStoreError
        if not args.doc_title:
            print("--doc-add requires --doc-title", file=sys.stderr)
            return 2
        if args.doc_file:
            with open(args.doc_file, encoding="utf-8") as fh:
                content = fh.read()
        else:
            content = sys.stdin.read()
        if not content.strip():
            print("No content provided (use --doc-file or pipe via stdin)", file=sys.stderr)
            return 2
        try:
            row = save_document(
                title=args.doc_title,
                content=content,
                doc_type=args.doc_type,
                account_name=args.doc_account,
                folder=args.doc_folder,
                source="manual",
            )
        except DocumentStoreError as e:
            print(f"Document save failed: {e}", file=sys.stderr)
            return 2
        where = row["account_name"] if row["space"] == "client" else row["folder"]
        print(f"Saved '{row['title']}' to [{row['space']}] {where} (id={row['id']})")
        return 0

    # --- Supabase-only commands ---
    if args.ops_doctor:
        from hermes.integrations.supabase_client import SupabaseClient, SupabaseClientError
        from hermes.operations.ops_doctor import run_ops_doctor

        try:
            supa = SupabaseClient()
        except SupabaseClientError as e:
            print(f"Supabase connection failed: {e}", file=sys.stderr)
            return 2
        report = run_ops_doctor(supa)
        print("\n".join(report.format_lines()))
        return 0 if report.ok else 1

    if args.curate_skills:
        from hermes.jobs import skill_curator

        report = skill_curator.run()
        print("\n".join(report.format_lines()))
        return 0 if report.ok else 1

    if args.snapshot_kpis:
        from hermes.integrations.supabase_client import SupabaseClient, SupabaseClientError
        from hermes.operations.kpi_writer import (
            snapshot_finance,
            snapshot_renewals,
            snapshot_system_health,
        )

        try:
            supa = SupabaseClient()
        except SupabaseClientError as e:
            print(f"Supabase connection failed: {e}", file=sys.stderr)
            return 2
        results = snapshot_system_health(supa)
        results.extend(snapshot_finance(supa))
        results.extend(snapshot_renewals(supa))
        print(f"Recorded {len(results)} KPI data points.")
        return 0


    # --- API server (manages its own clients lazily) ---
    if args.api:
        import uvicorn
        from hermes.api import app as api_app

        print(f"Starting Hermes API on port {args.api_port}...")
        uvicorn.run(api_app, host="0.0.0.0", port=args.api_port)
        return 0

    if args.ping:
        print("Hermes is online.")
        return 0

    if args.kpi:
        from hermes.integrations.supabase_client import SupabaseClient, SupabaseClientError

        try:
            supa = SupabaseClient()
        except SupabaseClientError as e:
            print(e, file=sys.stderr)
            return 2
        rows = supa.select("agency_snapshots", params={"order": "snapshot_date.desc"}, limit=1)
        if not rows:
            print("No agency snapshot on file yet — run --snapshot-kpis first.", file=sys.stderr)
            return 1
        s = rows[0]
        print(f"As of {s.get('snapshot_date')}:")
        for label, key in (("Clients", "client_count"), ("Policies", "policy_count")):
            print(f"  {label}: {s.get(key) if s.get(key) is not None else 'n/a'}")
        premium = s.get("active_premium")
        retention = s.get("retention_rate")
        print(f"  Active premium: ${float(premium):,.0f}" if premium is not None else "  Active premium: n/a")
        print(f"  Retention: {retention}%" if retention is not None else "  Retention: n/a")
        return 0

    if args.revenue_sentinel or args.revenue_sentinel_dry_run:
        from hermes.jobs import revenue_sentinel

        result = revenue_sentinel.run(
            dry_run=args.revenue_sentinel_dry_run,
            force=args.revenue_sentinel_force,
        )
        print(result.message)
        if result.warnings:
            print("Warnings:")
            for warning in result.warnings:
                print(f"- {warning}")
        return 0 if result.ok else 1

    if args.renewal_refresh or args.renewal_refresh_dry_run:
        from hermes.renewals.candidate_refresh import run_refresh

        summary = run_refresh(dry_run=args.renewal_refresh_dry_run)
        print(
            f"Renewal refresh ({'dry-run' if summary['dry_run'] else 'live'}): "
            f"{summary['policies']} policies -> {summary['candidates']} events "
            f"(eligible={summary['eligible']}, needs_verification={summary['needs_verification']}, "
            f"excluded={summary['excluded']}, working_queue={summary['in_working_queue']})"
        )
        if summary.get("projected") is not None:
            print(f"  projected {summary['projected']} eligible -> project_85_renewals; "
                  f"pruned {summary.get('pruned', 0)} stale")
        for s in summary.get("sample_eligible", []):
            print(f"  {s['branch']}: {s['policy_number']} {s['event_date']} seg={s['segment']} "
                  f"queue={s['in_working_queue']} risk={s['risk_status']}")
        return 0

    if args.agency_snapshot or args.agency_snapshot_dry_run:
        from hermes.integrations.supabase_client import SupabaseClient, SupabaseClientError
        from hermes.jobs.agency_snapshot import format_summary, run_snapshot

        try:
            supa = SupabaseClient()
        except SupabaseClientError as e:
            print(f"Supabase connection failed: {e}", file=sys.stderr)
            return 2
        summary = run_snapshot(supa=supa, dry_run=args.agency_snapshot_dry_run)
        print(format_summary(summary))
        return 0

    if args.renewal_classify or args.renewal_classify_dry_run:
        from hermes.integrations.supabase_client import SupabaseClient
        from hermes.operations.renewal_classifier import refresh_renewals

        summary = refresh_renewals(
            SupabaseClient(),
            dry_run=args.renewal_classify_dry_run,
        )
        verb = "Would change" if summary["dry_run"] else "Changed"
        print(
            f"Renewal urgency re-grade ({'dry-run' if summary['dry_run'] else 'live'}): "
            f"{summary['total']} eligible renewals, {verb} {summary['changed']}."
        )
        for status, stats in summary["by_risk"].items():
            print(f"  {status}: {stats}")
        return 0

    if args.renewal_executor or args.renewal_executor_dry_run:
        from hermes.renewals.executor import run_executor

        summary = run_executor(
            limit=args.renewal_executor_limit,
            dry_run=args.renewal_executor_dry_run,
        )
        mode = "dry-run" if args.renewal_executor_dry_run else "live"
        print(
            f"Renewal executor ({mode}): claimed={summary['claimed']} "
            f"completed={summary['completed']} failed={summary['failed']} "
            f"blocked={summary['blocked']}"
        )
        for pv in summary.get("previews", []):
            detail = pv.get("reason") or pv.get("intended_change")
            print(f"  {pv['verdict']}: {pv['action']} policy={pv['policy_number']} — {detail}")
        return 0 if summary["failed"] == 0 and summary["blocked"] == 0 else 1

    if args.run_renewal_executor_worker:
        from hermes.renewals.executor import run_worker_loop

        run_worker_loop(
            poll_seconds=args.renewal_executor_poll_seconds,
            limit=args.renewal_executor_limit,
        )
        return 0

    if args.intake_executor or args.intake_executor_dry_run:
        from hermes.intake.executor import run_intake_executor

        summary = run_intake_executor(
            limit=args.intake_executor_limit,
            dry_run=args.intake_executor_dry_run,
        )
        mode = "dry-run" if args.intake_executor_dry_run else "live"
        print(
            f"Intake executor ({mode}): claimed={summary['claimed']} "
            f"completed={summary['completed']} failed={summary['failed']}"
        )
        for pv in summary.get("previews", []):
            ins = pv.get("insured", {})
            who = ins.get("CommercialName") or f"{ins.get('FirstName', '')} {ins.get('LastName', '')}".strip()
            print(
                f"  PREVIEW insured={who!r} type={ins.get('type')} "
                f"insuredType={ins.get('insuredType')} keys={sorted(ins.keys())}"
            )
        return 0 if summary["failed"] == 0 else 1

    if args.quote_executor or args.quote_executor_dry_run:
        from hermes.quotes.executor import run_quote_executor

        summary = run_quote_executor(
            limit=args.quote_executor_limit,
            dry_run=args.quote_executor_dry_run,
        )
        mode = "dry-run" if args.quote_executor_dry_run else "live"
        print(
            f"Quote executor ({mode}): claimed={summary['claimed']} "
            f"completed={summary['completed']} failed={summary['failed']}"
        )
        for pv in summary.get("previews", []):
            pol = pv.get("policy", {})
            print(f"  PREVIEW opp={pv.get('opportunity_id')} policy_keys={sorted(pol.keys())} "
                  f"insured={pol.get('InsuredDatabaseId')} lob={pol.get('LineOfBusinessName')} "
                  f"carrier={pol.get('CarrierName')} premium={pol.get('Premium')} IsQuote={pol.get('IsQuote')}")
        return 0 if summary["failed"] == 0 else 1

    if (
        args.opportunity_writeback_executor
        or args.opportunity_writeback_dry_run
        or args.opportunity_writeback_opportunity_id
    ):
        from hermes.sync.opportunity_writeback import run_opportunity_writeback_executor

        summary = run_opportunity_writeback_executor(
            limit=args.opportunity_writeback_limit,
            dry_run=args.opportunity_writeback_dry_run,
            opportunity_id=args.opportunity_writeback_opportunity_id,
        )
        mode = "dry-run" if (args.opportunity_writeback_dry_run or args.opportunity_writeback_opportunity_id) else "live"
        print(
            f"Opportunity writeback ({mode}): claimed={summary['claimed']} "
            f"completed={summary['completed']} failed={summary['failed']}"
        )
        for pv in summary.get("previews", []):
            if pv.get("found") is False:
                print(f"  PREVIEW opp={pv.get('opportunity')} → {pv.get('target_stage')} NOT FOUND in AMS")
                continue
            print(
                f"  PREVIEW opp={pv.get('opportunity')} → {pv.get('target_stage')} "
                f"assignedTo={pv.get('assigned_to_raw')!r} (type={pv.get('assigned_to_type')}) "
                f"insuredDatabaseId_present={pv.get('insured_database_id_present')}"
            )
            print(f"    resolved payload: {pv.get('resolved_payload')}")
        return 0 if summary["failed"] == 0 else 1

    if args.casework_executor or args.casework_executor_dry_run:
        from hermes.casework.executor import run_casework_executor

        summary = run_casework_executor(
            limit=args.casework_executor_limit,
            dry_run=args.casework_executor_dry_run,
        )
        mode = "dry-run" if args.casework_executor_dry_run else "live"
        print(
            f"Casework executor ({mode}): claimed={summary['claimed']} "
            f"completed={summary['completed']} failed={summary['failed']}"
        )
        for pv in summary.get("previews", []):
            t = pv.get("task", {})
            print(f"  PREVIEW {pv.get('object_type')} {pv.get('target')} title={t.get('title')!r} "
                  f"insured={t.get('insured_database_id')} category={t.get('category_name')}")
        return 0 if summary["failed"] == 0 else 1

    if args.proactive_cases or args.proactive_cases_commit:
        from hermes.casework import sentinel
        from hermes.sync.supabase_client import SupabaseClient

        result = sentinel.scan(
            SupabaseClient(),
            horizon_days=args.proactive_cases_horizon,
            limit=args.proactive_cases_limit,
            commit=args.proactive_cases_commit,
        )
        print(sentinel.format_report(result))
        return 0 if not result["failed"] else 1

    if args.scheduler_health:
        from hermes.integrations.supabase_client import SupabaseClient
        from hermes.scheduler.runner import scheduler_health

        import json as _json
        print(_json.dumps(scheduler_health(SupabaseClient()), indent=2, default=str))
        return 0

    if args.run_scheduler:
        enabled = os.environ.get("SCHEDULER_ENABLED", "").strip().lower() in ("1", "true", "yes", "on")
        if not enabled:
            print("Scheduler is DISABLED (set SCHEDULER_ENABLED=true to enable). Exiting.")
            return 0
        from hermes.scheduler.runner import run_scheduler_loop

        run_scheduler_loop(interval_seconds=args.scheduler_interval, batch=args.scheduler_batch)
        return 0

    if args.revenue_sentinel_health:
        from hermes.jobs import revenue_sentinel

        status = revenue_sentinel.health_status()
        print(status.summary)
        for key, value in status.details.items():
            print(f"{key}: {value}")
        return 0 if status.ok else 1

    if args.commission_audit or args.commission_audit_dry_run:
        from hermes.jobs import revenue_integrity

        result = revenue_integrity.run_commission_audit(
            dry_run=args.commission_audit_dry_run,
            force=args.commission_audit_force,
        )
        print(result.message)
        if result.warnings:
            print("Warnings:")
            for warning in result.warnings:
                print(f"- {warning}")
        return 0 if result.ok else 1

    if args.eom_scorecard or args.eom_scorecard_dry_run:
        from hermes.jobs import revenue_integrity

        result = revenue_integrity.run_eom_scorecard(
            dry_run=args.eom_scorecard_dry_run,
            force=args.eom_scorecard_force,
        )
        print(result.message)
        if result.warnings:
            print("Warnings:")
            for warning in result.warnings:
                print(f"- {warning}")
        return 0 if result.ok else 1

    if args.run_intake_worker:
        from hermes.integrations.supabase_client import SupabaseClient, SupabaseClientError
        from hermes.operations.intake_worker import run_intake_worker_loop

        try:
            supa = SupabaseClient()
        except SupabaseClientError as e:
            print(f"Supabase connection failed: {e}", file=sys.stderr)
            return 2
        print(f"Starting intake worker loop every {args.intake_poll_seconds}s...")
        run_intake_worker_loop(supa, poll_seconds=args.intake_poll_seconds)
        return 0

    if args.commission_reconcile_file:
        from hermes.integrations.supabase_client import SupabaseClient, SupabaseClientError
        from hermes.jobs import commission_reconciliation

        try:
            supa = SupabaseClient()
        except SupabaseClientError as e:
            print(f"Supabase connection failed: {e}", file=sys.stderr)
            return 2
        result = commission_reconciliation.run_reconciliation(
            supa,
            statement_path=args.commission_reconcile_file,
            dry_run=args.commission_reconcile_dry_run,
        )
        print(result.message)
        if result.warnings:
            print("Warnings:")
            for warning in result.warnings:
                print(f"- {warning}")
        return 0 if result.ok else 1

    use_openai = bool(os.environ.get("OPENAI_API_KEY") or os.environ.get("HERMES_OPENAI_API_KEY"))
    dispatcher = Dispatcher(use_openai=use_openai)
    if args.command:
        line = " ".join(args.command)
        result = dispatcher.dispatch(line)
        print(result.message)
        return 0 if result.ok else 1

    print("Hermes REPL (empty line to exit). Commands: add … | what/find … | cross-sell …")
    while True:
        try:
            line = input("hermes> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not line:
            break
        result = dispatcher.dispatch(line)
        print(result.message)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
