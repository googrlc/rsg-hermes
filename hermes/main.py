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


def main() -> int:
    load_dotenv()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        stream=sys.stderr,
    )
    parser = argparse.ArgumentParser(description="Hermes — EspoCRM coordinator")
    parser.add_argument("command", nargs="*", help="One-shot command (omit for REPL)")
    parser.add_argument("--ping", action="store_true", help="Test API key and exit")
    parser.add_argument("--doctor", action="store_true", help="Run non-mutating CRM readiness checks")
    parser.add_argument("--kpi", action="store_true", help="Print quick entity counts")
    parser.add_argument("--audit-fields", action="store_true", help="Build schema_map.json field audit")
    parser.add_argument("--audit-schema", action="store_true", help="Build schema_map.json schema audit")
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
    # --- Hermes Operations Center commands ---
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
        "--snapshot-kpis",
        action="store_true",
        help="Record system health, finance, and renewal KPI snapshots",
    )
    args = parser.parse_args()

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

    if args.slack:
        from hermes.integrations.slack_socket import run_slack_socket

        try:
            run_slack_socket(espo=client)
        except RuntimeError as e:
            print(e, file=sys.stderr)
            return 2
        return 0

    if args.api:
        import uvicorn
        from hermes.api import app as api_app

        print(f"Starting Hermes API on port {args.api_port}...")
        uvicorn.run(api_app, host="0.0.0.0", port=args.api_port)
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
