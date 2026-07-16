#!/usr/bin/env python3
"""Hermes entrypoint: REPL or one-shot CLI for a VPS or automation."""

from __future__ import annotations

import argparse
import logging
import sys

from dotenv import load_dotenv

import os

from hermes.core.auditor import SchemaAuditor, crm_readiness, quick_kpis
from hermes.core.client import EspoClient, EspoClientError
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
- Prepare an EspoCRM account update draft
- Prepare an EspoCRM opportunity update draft
- Show me exactly what you would write to EspoCRM

Policy data repair:
- Repair policy accounts dry run
- Repair policy accounts apply
- hermes --repair-policy-accounts-dry-run
- hermes --repair-policy-accounts
"""


def main() -> int:
    load_dotenv()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        stream=sys.stderr,
    )
    parser = argparse.ArgumentParser(description="Hermes — EspoCRM coordinator")
    parser.add_argument("command", nargs="*", help="One-shot command (omit for REPL)")
    parser.add_argument("--commands", action="store_true", help="Print Open WebUI command catalog and exit")
    parser.add_argument("--ping", action="store_true", help="Test API key and exit")
    parser.add_argument("--doctor", action="store_true", help="Run non-mutating CRM readiness checks")
    parser.add_argument("--kpi", action="store_true", help="Print quick entity counts")
    parser.add_argument("--audit-fields", action="store_true", help="Build schema_map.json field audit")
    parser.add_argument("--audit-schema", action="store_true", help="Build schema_map.json schema audit")
    parser.add_argument(
        "--inventory-metadata",
        action="store_true",
        help="Build live Espo metadata inventory (writable/read-only/required fields)",
    )
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
        "--renewal-sweep",
        action="store_true",
        help="Create renewal prep tasks for Identified renewals (Gretchen)",
    )
    parser.add_argument(
        "--renewal-sweep-limit",
        type=int,
        default=None,
        help="Cap renewal-sweep candidates (use 1 for a safe first live run)",
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
        "--commission-ingest",
        action="store_true",
        help="Ingest commissionable policies from crm_commissions into commission_ledger",
    )
    parser.add_argument(
        "--commission-ingest-dry-run",
        action="store_true",
        help="Preview commission ingest without writing to commission_ledger",
    )
    parser.add_argument(
        "--espo-writeback",
        action="store_true",
        help="Write EspoCRM service Cases back to the NowCerts task ledger (AMS)",
    )
    parser.add_argument(
        "--espo-writeback-dry-run",
        action="store_true",
        help="Preview Cases->NowCerts write-back without writing to NowCerts/Espo",
    )
    parser.add_argument(
        "--espo-writeback-hours",
        type=int,
        default=24,
        help="Look-back window (hours) for modified Cases in --espo-writeback",
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
        help="Reconcile a carrier statement file (csv/xlsx/pdf) against Espo commissions",
    )
    parser.add_argument(
        "--commission-reconcile-dry-run",
        action="store_true",
        help="Run reconciliation without posting Slack alert",
    )
    parser.add_argument(
        "--slack",
        action="store_true",
        help="Run Slack Socket Mode bot (SLACK_BOT_TOKEN, SLACK_APP_TOKEN)",
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
    # --- NowCerts ↔ EspoCRM Sync commands ---
    parser.add_argument(
        "--sync-nowcerts",
        action="store_true",
        help="Run NowCerts → EspoCRM sync pipeline (Insured → Account)",
    )
    parser.add_argument(
        "--sync-nowcerts-dry-run",
        action="store_true",
        help="Preview NowCerts sync without writing to EspoCRM",
    )
    parser.add_argument(
        "--sync-nowcerts-since",
        type=str,
        default=None,
        help="Only sync records changed since this ISO datetime (e.g. 2026-05-01T00:00:00)",
    )
    parser.add_argument(
        "--sync-policies",
        action="store_true",
        help="Run NowCerts → EspoCRM POLICIES-ONLY sync (upsert Policy by policy_number; never touches Accounts/Contacts)",
    )
    parser.add_argument(
        "--sync-policies-dry-run",
        action="store_true",
        help="Preview the policies-only sync without writing to EspoCRM",
    )
    parser.add_argument(
        "--sync-policies-since",
        type=str,
        default=None,
        help="Only sync policies changed since this ISO datetime (e.g. 2026-06-01T00:00:00)",
    )
    parser.add_argument(
        "--sync-policies-limit",
        type=int,
        default=None,
        help="Cap the number of policies processed (useful for a first dry-run)",
    )
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
        "--enrich-nowcerts",
        type=str,
        default=None,
        metavar="ACCOUNT_ID",
        help="Syncback: enrich the linked NowCerts insured from one ACTIVE EspoCRM account (upsert by DatabaseId)",
    )
    parser.add_argument(
        "--enrich-nowcerts-dry-run",
        action="store_true",
        help="Preview the NowCerts enrichment payload for --enrich-nowcerts without writing to the AMS",
    )
    # --- Nightly CRM Changelog ---
    parser.add_argument(
        "--changelog",
        action="store_true",
        help="Run nightly CRM changelog: post changes to Slack + log in EspoCRM",
    )
    parser.add_argument(
        "--changelog-dry-run",
        action="store_true",
        help="Render CRM changelog without posting to Slack or logging to CRM",
    )
    parser.add_argument(
        "--changelog-force",
        action="store_true",
        help="Bypass daily idempotency guard for changelog",
    )
    parser.add_argument(
        "--changelog-hours",
        type=int,
        default=None,
        help="Lookback window in hours (default: 24)",
    )
    # --- Bidirectional Sync ---
    parser.add_argument(
        "--sync-bidirectional",
        action="store_true",
        help="Run full bidirectional sync: NowCerts↔Supabase↔EspoCRM",
    )
    parser.add_argument(
        "--sync-bidirectional-dry-run",
        action="store_true",
        help="Preview bidirectional sync without writing to any system",
    )
    parser.add_argument(
        "--sync-crm-to-hub",
        action="store_true",
        help="Mirror EspoCRM changes to Supabase golden record",
    )
    parser.add_argument(
        "--sync-crm-to-hub-dry-run",
        action="store_true",
        help="Preview CRM-to-hub mirror without writing",
    )
    parser.add_argument(
        "--sync-hub-to-nowcerts",
        action="store_true",
        help="Push Supabase outbound queue to NowCerts AMS",
    )
    parser.add_argument(
        "--sync-hub-to-nowcerts-dry-run",
        action="store_true",
        help="Preview hub-to-NowCerts push without writing",
    )
    parser.add_argument(
        "--sync-hours",
        type=int,
        default=24,
        help="Lookback window in hours for sync (default: 24)",
    )
    # --- Hermes Operations Center commands ---
    # --- Email triage (Microsoft 365 first; reads inbox, routes mail) ---
    parser.add_argument(
        "--email-triage",
        action="store_true",
        help="LIVE: triage mailbox(es) — actionable mail -> intake_submissions, noise -> quarantine folder",
    )
    parser.add_argument(
        "--email-triage-dry-run",
        action="store_true",
        help="Preview email triage: classify and log per-message actions without inserting or moving anything",
    )
    parser.add_argument(
        "--email-provider",
        default="ms365",
        choices=["ms365", "gmail"],
        help="Mail provider to triage (default: ms365)",
    )
    parser.add_argument(
        "--email-mailboxes",
        default="",
        help="Comma-separated mailbox UPNs to triage (default: MS365_MAILBOXES env)",
    )
    parser.add_argument(
        "--email-since-hours",
        type=int,
        default=24,
        help="Lookback window in hours for email triage (default: 24)",
    )
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
        "--process-crm-queue",
        action="store_true",
        help="Dequeue pending CRM writes and apply to EspoCRM",
    )
    parser.add_argument(
        "--process-crm-queue-dry-run",
        action="store_true",
        help="Preview CRM queue processing without writing to EspoCRM",
    )
    parser.add_argument(
        "--run-crm-queue-worker",
        action="store_true",
        help="Continuously poll crm_write_queue every N seconds (systemd worker mode)",
    )
    parser.add_argument(
        "--crm-queue-poll-seconds",
        type=float,
        default=5.0,
        help="Poll interval for --run-crm-queue-worker (default: 5s)",
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
        "--run-outbound-drain-worker",
        action="store_true",
        help="Continuously drain outbound_sync_queue (Hermes's single scheduler; pg_cron stays disabled)",
    )
    parser.add_argument(
        "--outbound-drain-poll-seconds",
        type=float,
        default=900.0,
        help="Poll interval for --run-outbound-drain-worker (default: 900s = 15 min)",
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
    parser.add_argument(
        "--espo-db-doctor",
        action="store_true",
        help="Check the read-only direct-Postgres lane to EspoCRM (connectivity, read-only guard, pg_trgm, schema)",
    )
    parser.add_argument(
        "--repair-policy-accounts",
        action="store_true",
        help="Link Policies to Accounts by insuredMomentumId -> Account.momentum_client_id",
    )
    parser.add_argument(
        "--repair-policy-accounts-dry-run",
        action="store_true",
        help="Preview Policy account-link repairs without writing to EspoCRM",
    )
    args = parser.parse_args()

    if args.commands:
        print(COMMAND_CATALOG)
        return 0

    # --- NowCerts sync (requires NowCerts + Supabase + EspoCRM) ---
    if args.sync_nowcerts or args.sync_nowcerts_dry_run:
        from hermes.integrations.supabase_client import SupabaseClient, SupabaseClientError
        from hermes.sync.nowcerts_client import NowCertsClient, NowCertsClientError
        from hermes.sync.pipeline import run_insured_to_account_sync

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
        try:
            espo = EspoClient()
        except EspoClientError as e:
            print(f"EspoCRM connection failed: {e}", file=sys.stderr)
            return 2

        sync_result = run_insured_to_account_sync(
            nc,
            espo,
            supa,
            dry_run=args.sync_nowcerts_dry_run,
            since=args.sync_nowcerts_since,
        )
        print(sync_result.message)
        if sync_result.errors:
            print("Errors:")
            for err in sync_result.errors:
                print(f"- {err}")
        return 0 if sync_result.ok else 1

    # --- NowCerts → EspoCRM policies-only sync (no Supabase, no account writes) ---
    if args.sync_policies or args.sync_policies_dry_run:
        from hermes.sync.nowcerts_client import NowCertsClient, NowCertsClientError
        from hermes.sync.policy_sync import run_policy_sync

        try:
            nc = NowCertsClient()
        except NowCertsClientError as e:
            print(f"NowCerts connection failed: {e}", file=sys.stderr)
            return 2
        try:
            espo = EspoClient()
        except EspoClientError as e:
            print(f"EspoCRM connection failed: {e}", file=sys.stderr)
            return 2

        pol_result = run_policy_sync(
            nc,
            espo,
            since=args.sync_policies_since,
            dry_run=args.sync_policies_dry_run,
            limit=args.sync_policies_limit,
        )
        print(pol_result.message)
        if pol_result.skipped_accounts:
            print(f"\nSkipped — no EspoCRM account ({len(pol_result.skipped_accounts)} insureds):")
            for nm in pol_result.skipped_accounts[:50]:
                print(f"- {nm}")
            if len(pol_result.skipped_accounts) > 50:
                print(f"... +{len(pol_result.skipped_accounts) - 50} more")
        if pol_result.errors:
            print("\nErrors:")
            for err in pol_result.errors[:50]:
                print(f"- {err}")
        return 0 if pol_result.ok else 1

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
        )
        print(comm_result.message)
        if comm_result.errors:
            print("\nErrors:")
            for err in comm_result.errors[:50]:
                print(f"- {err}")
        return 0 if comm_result.ok else 1

    # --- Syncback: enrich one NowCerts insured from an ACTIVE account ---
    if args.enrich_nowcerts:
        import json as _json

        from hermes.sync.enrich import enrich_insured_from_account
        from hermes.sync.nowcerts_client import NowCertsClient, NowCertsClientError

        try:
            espo = EspoClient()
            nc = NowCertsClient()
        except (EspoClientError, NowCertsClientError) as e:
            print(f"connection failed: {e}", file=sys.stderr)
            return 2
        res = enrich_insured_from_account(
            espo, nc, args.enrich_nowcerts, dry_run=args.enrich_nowcerts_dry_run,
        )
        print(_json.dumps(res, indent=2, default=str))
        return 0 if res.get("ok") or res.get("action") == "skip" else 1

    # --- Bidirectional sync (requires NowCerts + Supabase + EspoCRM) ---
    _bidi = args.sync_bidirectional or args.sync_bidirectional_dry_run
    _crm_hub = args.sync_crm_to_hub or args.sync_crm_to_hub_dry_run
    _hub_nc = args.sync_hub_to_nowcerts or args.sync_hub_to_nowcerts_dry_run
    if _bidi or _crm_hub or _hub_nc:
        from hermes.integrations.supabase_client import SupabaseClient, SupabaseClientError

        try:
            supa = SupabaseClient()
        except SupabaseClientError as e:
            print(f"Supabase connection failed: {e}", file=sys.stderr)
            return 2
        try:
            espo = EspoClient()
        except EspoClientError as e:
            print(f"EspoCRM connection failed: {e}", file=sys.stderr)
            return 2

        if _crm_hub:
            from hermes.sync.bidirectional import run_crm_to_hub
            bidi_result = run_crm_to_hub(
                espo, supa,
                dry_run=args.sync_crm_to_hub_dry_run,
                since_hours=args.sync_hours,
            )
        elif _hub_nc:
            from hermes.sync.bidirectional import run_hub_to_nowcerts
            from hermes.sync.nowcerts_client import NowCertsClient, NowCertsClientError
            try:
                nc = NowCertsClient()
            except NowCertsClientError as e:
                print(f"NowCerts connection failed: {e}", file=sys.stderr)
                return 2
            bidi_result = run_hub_to_nowcerts(
                nc, supa,
                dry_run=args.sync_hub_to_nowcerts_dry_run,
            )
        else:
            from hermes.sync.bidirectional import run_bidirectional
            from hermes.sync.nowcerts_client import NowCertsClient, NowCertsClientError
            try:
                nc = NowCertsClient()
            except NowCertsClientError as e:
                print(f"NowCerts connection failed: {e}", file=sys.stderr)
                return 2
            bidi_result = run_bidirectional(
                nc, espo, supa,
                dry_run=args.sync_bidirectional_dry_run,
                since_hours=args.sync_hours,
            )
        print(bidi_result.message)
        if bidi_result.errors:
            print("Errors:")
            for err in bidi_result.errors:
                print(f"- {err}")
        return 0 if bidi_result.ok else 1

    # --- Email triage (requires the provider client + Supabase) ---
    if args.email_triage or args.email_triage_dry_run:
        from hermes.integrations.supabase_client import SupabaseClient, SupabaseClientError

        dry_run = args.email_triage_dry_run
        provider = args.email_provider
        default_mailboxes_env = (
            "GMAIL_MAILBOXES" if provider == "gmail" else "MS365_MAILBOXES"
        )
        mailboxes = [
            m.strip()
            for m in (args.email_mailboxes or os.environ.get(default_mailboxes_env, "")).split(",")
            if m.strip()
        ]
        if not mailboxes:
            print(
                f"No mailboxes to triage. Pass --email-mailboxes or set {default_mailboxes_env}.",
                file=sys.stderr,
            )
            return 2

        try:
            supa = SupabaseClient()
        except SupabaseClientError as e:
            print(f"Supabase connection failed: {e}", file=sys.stderr)
            return 2

        if provider == "ms365":
            from hermes.integrations.ms365_client import MS365Client, MS365ClientError
            from hermes.sync.email_triage import run_ms365_triage

            try:
                ms = MS365Client()
            except MS365ClientError as e:
                print(f"Microsoft 365 connection failed: {e}", file=sys.stderr)
                return 2
            triage_result = run_ms365_triage(
                ms, supa,
                mailboxes=mailboxes,
                since_hours=args.email_since_hours,
                dry_run=dry_run,
            )
        elif provider == "gmail":
            from hermes.integrations.gmail_client import GmailClient, GmailClientError
            from hermes.sync.email_triage import run_gmail_triage

            try:
                gm = GmailClient()
            except GmailClientError as e:
                print(f"Gmail connection failed: {e}", file=sys.stderr)
                return 2
            triage_result = run_gmail_triage(
                gm, supa,
                mailboxes=mailboxes,
                since_hours=args.email_since_hours,
                dry_run=dry_run,
            )
        else:
            print(f"Unsupported email provider: {provider}", file=sys.stderr)
            return 2

        print(triage_result.message)
        if triage_result.errors:
            print("Errors:")
            for err in triage_result.errors:
                print(f"- {err}")
        return 0 if triage_result.ok else 1

    # --- Document library (Supabase + Supermemory; no EspoCRM required) ---
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

    # --- Supabase-only commands (no EspoCRM credentials required) ---
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

    if args.espo_db_doctor:
        from hermes.integrations import espo_db

        if not espo_db.is_configured():
            print(
                "Read lane not configured. Set ESPO_DB_HOST, ESPO_DB_NAME, ESPO_DB_USER "
                "(and ESPO_DB_PASSWORD) to enable direct-Postgres reads.",
                file=sys.stderr,
            )
            return 2
        try:
            db = espo_db.EspoDb()
            health = db.check_health()
        except espo_db.EspoDbError as e:
            print(f"Read lane error: {e}", file=sys.stderr)
            return 2
        finally:
            try:
                db.close()  # type: ignore[possibly-undefined]
            except Exception:
                pass
        print("\n".join(health.format_lines()))
        return 0 if health.ok else 1

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

    # --- Nightly CRM Changelog (requires EspoCRM + Slack) ---
    if args.changelog or args.changelog_dry_run:
        from hermes.jobs import nightly_changelog

        try:
            espo = EspoClient()
        except EspoClientError as e:
            print(f"EspoCRM connection failed: {e}", file=sys.stderr)
            return 2

        result = nightly_changelog.run(
            espo,
            dry_run=args.changelog_dry_run,
            force=getattr(args, "changelog_force", False),
            lookback_hours=args.changelog_hours,
        )
        print(result.message)
        if result.warnings:
            print("Warnings:")
            for w in result.warnings:
                print(f"- {w}")
        return 0 if result.ok else 1

    # --- API server (manages its own clients lazily) ---
    if args.api:
        import uvicorn
        from hermes.api import app as api_app

        print(f"Starting Hermes API on port {args.api_port}...")
        uvicorn.run(api_app, host="0.0.0.0", port=args.api_port)
        return 0

    # --- Commands requiring EspoCRM ---
    try:
        client = EspoClient()
    except EspoClientError as e:
        print(e, file=sys.stderr)
        return 2

    if args.audit_fields or args.audit_schema:
        schema = SchemaAuditor(client).run_field_audit()
        print(f"Schema audit wrote {os.environ.get('HERMES_SCHEMA_MAP', 'schema_map.json')}")
        print(schema)
        return 0
    if args.inventory_metadata:
        schema = SchemaAuditor(client).run_live_metadata_inventory()
        print(f"Metadata inventory wrote {os.environ.get('HERMES_SCHEMA_MAP', 'schema_map.json')}")
        print(f"Entity count: {schema.get('entity_count', 0)}")
        return 0

    if args.slack:
        from hermes.integrations.slack_socket import run_slack_socket

        try:
            run_slack_socket(espo=client)
        except RuntimeError as e:
            print(e, file=sys.stderr)
            return 2
        return 0

    if args.ping:
        print(client.ping())
        return 0

    if args.doctor:
        report = crm_readiness(client)
        print("\n".join(report.format_lines()))
        return 0 if report.ok else 1

    if args.kpi:
        for r in quick_kpis(client):
            print(f"{r.label}: {r.value}" + (f" — {r.detail}" if r.detail else ""))
        return 0

    if args.repair_policy_accounts or args.repair_policy_accounts_dry_run:
        from hermes.commands.policy_repair import run_policy_account_repair

        result = run_policy_account_repair(
            client,
            dry_run=args.repair_policy_accounts_dry_run or not args.repair_policy_accounts,
        )
        print(result.format_message())
        return 0 if result.ok else 1

    if args.revenue_sentinel or args.revenue_sentinel_dry_run:
        from hermes.jobs import revenue_sentinel

        result = revenue_sentinel.run(
            client,
            dry_run=args.revenue_sentinel_dry_run,
            force=args.revenue_sentinel_force,
        )
        print(result.message)
        if result.warnings:
            print("Warnings:")
            for warning in result.warnings:
                print(f"- {warning}")
        return 0 if result.ok else 1

    if args.renewal_sweep:
        from hermes.renewals.sweep import run as renewal_sweep

        result = renewal_sweep(limit=args.renewal_sweep_limit)
        print(f"Renewal sweep: {result['created']} task(s) created of "
              f"{result['candidates']} candidate(s)")
        return 0

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
            client,
            dry_run=args.commission_audit_dry_run,
            force=args.commission_audit_force,
        )
        print(result.message)
        if result.warnings:
            print("Warnings:")
            for warning in result.warnings:
                print(f"- {warning}")
        return 0 if result.ok else 1

    if args.commission_ingest or args.commission_ingest_dry_run:
        from hermes.jobs.commission_ingest import run_ingest

        result = run_ingest(dry_run=args.commission_ingest_dry_run)
        print(result.message)
        if result.errors:
            print("Errors:")
            for err in result.errors[:10]:
                print(f"- {err}")
        return 0 if result.ok else 1

    if args.espo_writeback or args.espo_writeback_dry_run:
        from hermes.jobs.espo_to_nowcerts_writeback import run_writeback
        from hermes.jobs.espo_account_writeback import run_account_writeback

        dry = args.espo_writeback_dry_run
        hours = args.espo_writeback_hours
        r_tasks = run_writeback(dry_run=dry, since_hours=hours)
        r_accts = run_account_writeback(dry_run=dry, since_hours=hours)
        print(r_tasks.message)
        print(r_accts.message)
        errors = r_tasks.errors + r_accts.errors
        if errors:
            print("Errors:")
            for err in errors[:10]:
                print(f"- {err}")
        return 0 if (r_tasks.ok and r_accts.ok) else 1

    if args.eom_scorecard or args.eom_scorecard_dry_run:
        from hermes.jobs import revenue_integrity

        result = revenue_integrity.run_eom_scorecard(
            client,
            dry_run=args.eom_scorecard_dry_run,
            force=args.eom_scorecard_force,
        )
        print(result.message)
        if result.warnings:
            print("Warnings:")
            for warning in result.warnings:
                print(f"- {warning}")
        return 0 if result.ok else 1

    if args.process_crm_queue or args.process_crm_queue_dry_run:
        from hermes.integrations.supabase_client import SupabaseClient, SupabaseClientError
        from hermes.operations.crm_queue_worker import process_queue

        try:
            supa = SupabaseClient()
        except SupabaseClientError as e:
            print(f"Supabase connection failed: {e}", file=sys.stderr)
            return 2
        result = process_queue(
            supa,
            client,
            dry_run=args.process_crm_queue_dry_run,
        )
        print(result.message)
        if result.errors:
            print("Errors:")
            for err in result.errors:
                print(f"- {err}")
        return 0 if result.ok else 1

    if args.run_crm_queue_worker:
        from hermes.integrations.supabase_client import SupabaseClient, SupabaseClientError
        from hermes.operations.crm_queue_worker import run_worker_loop

        try:
            supa = SupabaseClient()
        except SupabaseClientError as e:
            print(f"Supabase connection failed: {e}", file=sys.stderr)
            return 2
        print(f"Starting CRM queue worker loop every {args.crm_queue_poll_seconds}s...")
        run_worker_loop(
            supa,
            client,
            poll_seconds=args.crm_queue_poll_seconds,
        )
        return 0

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

    if args.run_outbound_drain_worker:
        from hermes.integrations.supabase_client import SupabaseClient, SupabaseClientError
        from hermes.sync.pipeline import run_outbound_drain_loop

        try:
            supa = SupabaseClient()
        except SupabaseClientError as e:
            print(f"Supabase connection failed: {e}", file=sys.stderr)
            return 2
        print(
            f"Starting outbound drain loop every {args.outbound_drain_poll_seconds}s "
            "(pg_cron outbound path is retired)..."
        )
        run_outbound_drain_loop(
            supa,
            client,
            poll_seconds=args.outbound_drain_poll_seconds,
        )
        return 0

    if args.commission_reconcile_file:
        from hermes.jobs import commission_reconciliation

        result = commission_reconciliation.run_reconciliation(
            client,
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
        result = dispatcher.dispatch(client, line)
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
        result = dispatcher.dispatch(client, line)
        print(result.message)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
