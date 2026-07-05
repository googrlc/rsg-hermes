"""`hermes agent` + `hermes rollback` CLI subcommands.

  hermes agent run <name> [--state <state>] [--trigger <trigger>] [--max-records N]
  hermes agent list
  hermes rollback --run-id <ULID> [--write-id <id>]

Both resolve to the shared AgentRunner lifecycle (hermes/agents/base.py).
"""

from __future__ import annotations

import argparse
import logging
import sys
from typing import Any

log = logging.getLogger(__name__)


def _build_supabase() -> Any | None:
    try:
        from hermes.integrations.supabase_client import SupabaseClient, SupabaseClientError

        return SupabaseClient()
    except Exception as exc:  # missing creds -> run audit-only (no Supabase logging)
        log.warning("Supabase unavailable, running without audit mirror: %s", exc)
        return None


def _build_notifier() -> Any:
    try:
        from hermes.integrations.slack_notifier import SlackNotifier  # type: ignore

        return SlackNotifier()
    except Exception:
        from hermes.agents.base import NullNotifier

        return NullNotifier()


def _agent_run(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="hermes agent run", description="Run an RSG agent")
    parser.add_argument("name", help="Agent name (see `hermes agent list`)")
    parser.add_argument("--state", default=None, help="Override lifecycle state")
    parser.add_argument("--trigger", default="on-demand", help="cron|webhook|on-demand")
    parser.add_argument("--dry-run", action="store_true", help="Force dry-run regardless of state")
    parser.add_argument(
        "--max-records",
        type=int,
        default=None,
        help="Cap records read (audit agents only)",
    )
    args = parser.parse_args(argv)

    from hermes.agents import get_agent_class

    cls = get_agent_class(args.name)
    if cls is None:
        print(f"Unknown agent: {args.name}", file=sys.stderr)
        print(f"Available: {', '.join(_available()) or '(none registered)'}", file=sys.stderr)
        return 2

    supa = _build_supabase()
    notifier = _build_notifier()
    kwargs: dict[str, Any] = {
        "supa": supa,
        "state": args.state,
        "trigger": args.trigger,
        "notifier": notifier,
    }
    if args.dry_run:
        kwargs["dry_run"] = True
    if args.max_records is not None and "max_records" in cls.__init__.__code__.co_varnames:
        kwargs["max_records"] = args.max_records

    agent = cls(**kwargs)
    result = agent.run()
    print(result.message)
    return 0 if result.ok else 1


def _available() -> list[str]:
    from hermes.agents import available_agents

    return available_agents()


def _agent_list() -> int:
    names = _available()
    print("Registered agents:")
    for name in names:
        print(f"  - {name}")
    if not names:
        print("  (none registered)")
    return 0


def run_agent_cli(argv: list[str]) -> int:
    """Dispatch `hermes agent [run|list] ...`."""
    if not argv:
        print("usage: hermes agent <run|list> ...", file=sys.stderr)
        return 2
    sub = argv[0]
    rest = argv[1:]
    if sub == "list":
        return _agent_list()
    if sub == "run":
        return _agent_run(rest)
    print(f"unknown `agent` subcommand: {sub}", file=sys.stderr)
    return 2


def run_rollback_cli(argv: list[str]) -> int:
    """Dispatch `hermes rollback --run-id <ULID>`."""
    parser = argparse.ArgumentParser(prog="hermes rollback", description="Roll back an agent run")
    parser.add_argument("--run-id", required=True, help="ULID of the agent run to roll back")
    parser.add_argument("--write-id", type=int, default=None, help="Roll back a single write row id")
    args = parser.parse_args(argv)

    supa = _build_supabase()
    if supa is None:
        print("Rollback requires Supabase access (agent_writes log).", file=sys.stderr)
        return 2

    from hermes.agents.base import AgentRunner  # for the generic revert path

    runner = AgentRunner(supa=supa, run_id=args.run_id)

    if args.write_id is not None:
        outcome = runner.rollback_write(args.write_id)
        print(f"rollback write {args.write_id}: {outcome}")
        return 0 if outcome.get("ok") else 1

    # Roll back every executed write within the 7-day window for this run.
    try:
        writes = supa.select(
            "agent_writes",
            columns="id,tool_name,target_system,target_entity,target_id,payload,response",
            params={"run_id": f"eq.{args.run_id}", "status": "eq.executed", "limit": "200"},
        )
    except Exception as exc:
        print(f"Failed to load writes for run {args.run_id}: {exc}", file=sys.stderr)
        return 1

    if not writes:
        print(f"No executed writes found for run {args.run_id}.")
        return 0

    rolled = 0
    for row in writes:
        outcome = runner.rollback_write(int(row["id"]))
        if outcome.get("ok"):
            rolled += 1
        else:
            print(f"  write {row['id']} ({row.get('tool_name')}): {outcome}")
    print(f"Rolled back {rolled}/{len(writes)} writes for run {args.run_id}.")
    print(
        "NOTE: row statuses marked rolled_back in Supabase. Concrete agents must "
        "implement do_write reversals (soft-delete tags) to undo the AMS change.",
        file=sys.stderr,
    )
    return 0
