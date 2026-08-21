"""Every tool the bridge advertises must actually be wired to a handler.

The failure this prevents is one we watched happen from the other side: asking a
live MCP door for a tool it does not have returns

    {"result": {"content": [{"text": "Unknown tool: x"}], "isError": true}}

— an HTTP 200, no JSON-RPC error, and the failure text sitting where the answer
should be. A caller that is not looking for that shape reads "Unknown tool" as
the result and carries on.

So a name in the tool list with no handler behind it does not fail loudly. It
answers, plausibly, with nothing. These tests keep the two sides in step.

The AMS write tools get extra scrutiny because they move client policy data:
they must require a named approver, and the executor that performs the write
must preview by default.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

BRIDGE = pathlib.Path("deploy/mcp-bridge/app.py")
SRC = BRIDGE.read_text(encoding="utf8")
TREE = ast.parse(SRC)


def _declared_tools() -> list[dict]:
    """The tool dicts from the bridge's tool list, read statically."""
    tools = []
    for node in ast.walk(TREE):
        if not isinstance(node, ast.Dict):
            continue
        keys = [k.value for k in node.keys if isinstance(k, ast.Constant)]
        if "name" in keys and "inputSchema" in keys:
            entry = {}
            for k, v in zip(node.keys, node.values):
                if not isinstance(k, ast.Constant):
                    continue
                if k.value in ("name", "description") and isinstance(v, ast.Constant):
                    entry[k.value] = v.value
                elif k.value == "description":  # concatenated string parts
                    try:
                        entry["description"] = ast.literal_eval(v)
                    except Exception:
                        entry["description"] = ""
                elif k.value == "inputSchema":
                    try:
                        entry["inputSchema"] = ast.literal_eval(v)
                    except Exception:
                        entry["inputSchema"] = {}
            if entry.get("name"):
                tools.append(entry)
    return tools


def _handler_names() -> set[str]:
    """Keys of the _HANDLERS dict."""
    for node in ast.walk(TREE):
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id == "_HANDLERS" and isinstance(node.value, ast.Dict):
                    return {k.value for k in node.value.keys if isinstance(k, ast.Constant)}
    return set()


TOOLS = _declared_tools()
HANDLERS = _handler_names()
AMS_WRITE_TOOLS = {"ams_create_insured", "ams_upsert_policy", "ams_push_task",
                   "ams_push_case", "ams_drain_casework"}


def test_the_bridge_declares_tools_and_handlers_at_all() -> None:
    assert TOOLS, "no tools parsed out of the bridge"
    assert HANDLERS, "no _HANDLERS dict parsed out of the bridge"


@pytest.mark.parametrize("tool", [t["name"] for t in TOOLS])
def test_every_declared_tool_has_a_handler(tool: str) -> None:
    """Otherwise it answers 'Unknown tool' inside a 200, which reads as data."""
    assert tool in HANDLERS, (
        f"{tool} is advertised but has no handler — calling it returns "
        '{"isError": true, "content":[{"text":"Unknown tool"}]} with HTTP 200'
    )


def test_every_handler_is_declared() -> None:
    """A handler nobody can reach is dead weight, and usually a rename half-done."""
    orphans = HANDLERS - {t["name"] for t in TOOLS}
    assert not orphans, f"handlers with no tool declaration: {sorted(orphans)}"


@pytest.mark.parametrize("tool", sorted(AMS_WRITE_TOOLS - {"ams_drain_casework"}))
def test_an_ams_write_requires_a_named_approver(tool: str) -> None:
    """These move client policy data. 'Who approved this' must not be optional."""
    spec = next((t for t in TOOLS if t["name"] == tool), None)
    assert spec, f"{tool} is not declared"
    schema = spec.get("inputSchema") or {}
    props, required = schema.get("properties", {}), schema.get("required", [])
    approver = {"approved_by", "confirm"} & set(props)
    assert approver, f"{tool} takes no approver or confirmation field"
    if "approved_by" in props:
        assert "approved_by" in required, (
            f"{tool} has approved_by but does not require it — an unnamed AMS write"
        )


def test_the_executor_previews_by_default() -> None:
    """ams_drain_casework is what actually writes. Defaulting to a real write
    would make 'run the casework executor' push to the AMS with no preview."""
    spec = next((t for t in TOOLS if t["name"] == "ams_drain_casework"), None)
    assert spec, "ams_drain_casework is not declared"
    desc = (spec.get("description") or "").lower()
    assert "dry_run" in desc and "default" in desc, (
        "the description must tell the agent that dry_run defaults to true"
    )
    assert "dry_run" in (spec.get("inputSchema") or {}).get("properties", {})


def test_the_push_tools_explain_the_two_refusals_a_caller_will_hit() -> None:
    """A 400 the agent cannot interpret becomes 'it didn't work'. Both refusals
    are recoverable, and the description is where the agent learns how."""
    for name in ("ams_push_task", "ams_push_case"):
        desc = (next(t for t in TOOLS if t["name"] == name).get("description") or "").lower()
        assert "due date" in desc, f"{name} does not mention the due-date refusal"
        assert "agency_crm_users" in desc, f"{name} does not say approved_by must be a real identity"


def test_push_tools_stage_rather_than_claiming_to_write() -> None:
    """They enqueue; ams_drain_casework writes. A description implying the write
    already happened invites 'done' when nothing has reached the AMS."""
    for name in ("ams_push_task", "ams_push_case"):
        desc = (next(t for t in TOOLS if t["name"] == name).get("description") or "").lower()
        assert "stage" in desc or "queue" in desc, f"{name} does not say it only stages"


def test_the_bridge_still_carries_the_tools_production_was_running() -> None:
    """The repo's copy of this file drifted BEHIND the box for a long time.

    The deployed bridge had seven tools that had been added straight onto the box
    and never committed — add_deck_card, case_progress, list_cases,
    list_deck_boards, list_intake_queue, list_nextcloud_folder,
    ensure_nextcloud_folders — plus the SSE plumbing. Three merged PRs edited
    this file while it was not the artifact that runs, so none of them reached
    production, and deploying the repo over the box would have deleted seven
    working tools.

    This list is what production was serving when the two were reconciled. It is
    not a wish list: dropping any of these is a capability the agent loses.
    """
    names = {t["name"] for t in TOOLS}
    was_live = {
        "add_deck_card", "ams_create_insured", "ams_search_insured",
        "ams_upsert_policy", "carrier_appetite", "case_progress",
        "commission_rules", "complete_task", "create_case", "create_client",
        "create_task", "draft_intake", "ensure_nextcloud_folders",
        "file_to_nextcloud", "hermes_dispatch", "list_cases", "list_commissions",
        "document_registry_search", "document_registry_upload",
        "list_deck_boards", "list_documents", "list_intake_queue",
        "list_nextcloud_folder", "list_renewals", "list_tasks", "ping",
        "retention_scan", "save_document", "sync_health",
    }
    missing = was_live - names
    assert not missing, (
        f"these were live on the box and are gone from the repo: {sorted(missing)}"
    )


def test_the_sse_plumbing_survives() -> None:
    """The deployed bridge answers streamable-HTTP clients. The repo copy had no
    such handling, so replacing the box with it would have broken every caller
    that negotiates text/event-stream."""
    assert "_wants_sse" in SRC, "SSE negotiation is missing from the bridge"
