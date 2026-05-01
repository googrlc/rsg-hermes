#!/usr/bin/env python3
"""Hermes entrypoint: REPL or one-shot CLI for a VPS or automation."""

from __future__ import annotations

import argparse
import sys

from dotenv import load_dotenv

import os

from hermes.core.auditor import SchemaAuditor, crm_readiness, quick_kpis
from hermes.core.client import EspoClient, EspoClientError
from hermes.core.dispatcher import Dispatcher


def main() -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Hermes — EspoCRM coordinator")
    parser.add_argument("command", nargs="*", help="One-shot command (omit for REPL)")
    parser.add_argument("--ping", action="store_true", help="Test API key and exit")
    parser.add_argument("--doctor", action="store_true", help="Run non-mutating CRM readiness checks")
    parser.add_argument("--kpi", action="store_true", help="Print quick entity counts")
    parser.add_argument("--audit-fields", action="store_true", help="Build schema_map.json field audit")
    parser.add_argument("--audit-schema", action="store_true", help="Build schema_map.json schema audit")
    parser.add_argument(
        "--slack",
        action="store_true",
        help="Run Slack Socket Mode bot (SLACK_BOT_TOKEN, SLACK_APP_TOKEN)",
    )
    args = parser.parse_args()

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
