"""Tests for scripts/gen_tool_map.py."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "gen_tool_map.py"
DOC = REPO_ROOT / "docs" / "hermes-tool-map.md"


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def test_gen_tool_map_offline_updates_doc():
    result = _run("--offline")
    assert result.returncode == 0
    assert "updated:" in result.stdout or "up to date" in result.stdout
    text = DOC.read_text(encoding="utf-8")
    assert "<!-- LIVE_RUNTIME_TOOLS_BEGIN -->" in text
    assert "`renewals_overview`" in text
    assert "`list_skills`" in text


def test_gen_tool_map_check_passes_after_regen():
    _run("--offline")
    result = _run("--offline", "--check")
    assert result.returncode == 0
    assert "up to date" in result.stdout


def test_skills_endpoint_matches_offline_catalog(client):
    """HTTP catalog should match imported catalog when the API is up."""
    from hermes.agent.skills_catalog import catalog

    resp = client.get("/api/command-center/skills")
    assert resp.status_code == 200
    live = {t["name"] for t in resp.json()["runtime_tools"]}
    offline = {t["name"] for t in catalog()["runtime_tools"]}
    assert live == offline


@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    from hermes.api import app

    return TestClient(app)
