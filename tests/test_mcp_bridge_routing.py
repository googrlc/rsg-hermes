"""The MCP bridge must send each tool to the app that owns its route.

The bridge is what Gretchen and Lamar actually talk to, so a misroute here is a
tool that answers "nothing found" instead of erroring — the worst failure mode
for a question like "who writes this risk?", because an empty carrier list reads
as a declination.

Two specific traps this pins:

1. **`/api/command-center/*` are HUB routes despite their names.**
   `command-center/renewals` is the cockpit's renewal list, served by the hub,
   NOT by the renewals service. Routing it on the word "renewals" would 404 the
   `list_renewals` tool. Matching is on full path prefixes, and the
   command-center paths are deliberately absent from the table.

2. **A shorter prefix must not swallow a longer one.** `/api/commissions` must
   not capture `/api/commission-statements`.

And the property that matters most: with no env vars set, every path still goes
to `HERMES_API_URL`, so deploying this bridge changes nothing until an app is
explicitly opted in.
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys

import pytest

BRIDGE = pathlib.Path("deploy/mcp-bridge/app.py")


def _load_bridge(monkeypatch, **env):
    """Import the bridge module fresh with a given environment.

    It reads its backends at import time, which is the behaviour under test:
    the container is configured by env and restarted, never reconfigured live.
    """
    for key in ("HERMES_FINANCE_URL", "HERMES_CASES_URL", "HERMES_INTAKE_URL",
                "HERMES_RENEWALS_URL", "HERMES_CARRIERS_URL"):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("HERMES_API_URL", "http://rsg-hermes-api:8787")
    monkeypatch.setenv("API_SERVER_KEY", "test-key")

    spec = importlib.util.spec_from_file_location("_bridge_under_test", BRIDGE)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_bridge_under_test"] = mod
    try:
        spec.loader.exec_module(mod)
    except SystemExit as exc:  # pragma: no cover - import-time config guard
        pytest.skip(f"bridge refused to import in this environment: {exc}")
    return mod


HUB = "http://rsg-hermes-api:8787"

ALL_TOOL_PATHS = [
    "/dispatch",
    "/api/command-center/renewals",
    "/api/command-center/retention",
    "/api/command-center/tasks",
    "/api/commissions",
    "/api/commission-rules",
    "/api/carrier-appetite",
    "/api/cases",
    "/api/tasks",
    "/api/intake",
    "/api/documents",
    "/api/documents/save",
    "/api/nextcloud/upload",
    "/api/ams/search-insured",
    "/api/hermes/sync-health",
]


def test_with_nothing_set_every_tool_still_reaches_the_hub(monkeypatch) -> None:
    """If this breaks, deploying the bridge silently repoints live tools."""
    b = _load_bridge(monkeypatch)
    for path in ALL_TOOL_PATHS:
        assert b._backend_for(path) == HUB, f"{path} drifted off the hub"


def test_command_center_paths_stay_on_the_hub_even_when_every_app_is_opted_in(
    monkeypatch,
) -> None:
    """The trap. These read like renewals/tasks routes and are neither."""
    b = _load_bridge(
        monkeypatch,
        HERMES_RENEWALS_URL="http://rsg-hermes-renewals:8804",
        HERMES_CASES_URL="http://rsg-hermes-cases:8802",
        HERMES_FINANCE_URL="http://rsg-hermes-finance:8801",
        HERMES_INTAKE_URL="http://rsg-hermes-intake:8803",
        HERMES_CARRIERS_URL="http://rsg-hermes-carriers:8805",
    )
    for path in ("/api/command-center/renewals",
                 "/api/command-center/retention",
                 "/api/command-center/tasks",
                 "/api/command-center/tasks/abc-123/complete"):
        assert b._backend_for(path) == HUB, (
            f"{path} was routed to an app service; it is a hub route and would 404"
        )


def test_each_app_gets_its_own_routes(monkeypatch) -> None:
    b = _load_bridge(
        monkeypatch,
        HERMES_FINANCE_URL="http://f:8801",
        HERMES_CASES_URL="http://c:8802",
        HERMES_INTAKE_URL="http://i:8803",
        HERMES_RENEWALS_URL="http://r:8804",
        HERMES_CARRIERS_URL="http://ca:8805",
    )
    assert b._backend_for("/api/commissions") == "http://f:8801"
    assert b._backend_for("/api/commission-rules") == "http://f:8801"
    assert b._backend_for("/api/cases") == "http://c:8802"
    assert b._backend_for("/api/tasks") == "http://c:8802"
    assert b._backend_for("/api/intake") == "http://i:8803"
    assert b._backend_for("/api/renewals") == "http://r:8804"
    assert b._backend_for("/api/carrier-appetite") == "http://ca:8805"
    # Untouched hub surfaces.
    assert b._backend_for("/dispatch") == HUB
    assert b._backend_for("/api/ams/search-insured") == HUB
    assert b._backend_for("/api/documents") == HUB


def test_a_shorter_prefix_does_not_swallow_a_longer_one(monkeypatch) -> None:
    b = _load_bridge(monkeypatch, HERMES_FINANCE_URL="http://f:8801")
    assert b._backend_for("/api/commission-statements") == "http://f:8801"
    assert b._backend_for("/api/commission-statements/abc/approve") == "http://f:8801"


def test_a_prefix_does_not_match_a_longer_unrelated_word(monkeypatch) -> None:
    b = _load_bridge(monkeypatch, HERMES_CASES_URL="http://c:8802")
    assert b._backend_for("/api/casesomething") == HUB


def test_sub_paths_follow_their_prefix(monkeypatch) -> None:
    b = _load_bridge(monkeypatch, HERMES_CASES_URL="http://c:8802")
    assert b._backend_for("/api/cases/abc-123/documents") == "http://c:8802"
    assert b._backend_for("/api/tasks/xyz/push-to-ams") == "http://c:8802"


def test_opting_one_app_in_leaves_the_others_alone(monkeypatch) -> None:
    b = _load_bridge(monkeypatch, HERMES_CASES_URL="http://c:8802")
    assert b._backend_for("/api/cases") == "http://c:8802"
    assert b._backend_for("/api/commissions") == HUB
    assert b._backend_for("/api/renewals") == HUB
    assert b._backend_for("/api/carrier-appetite") == HUB
