"""The repo must not be bind-mounted into the Hermes containers (#234).

`.:/app` mounted the git working tree into every container, so anything a
container wrote landed in the repo. It caused two incidents on 2026-07-26 (a
`docker cp` restore overwrote the host tree below its committed HEAD; a bad `-v`
created a directory named `hermes/operations/staleness.py` inside the repo) and
left bridge artifacts owned by dnsmasq:1000.

The mount was only safe to drop once job-idempotency state moved to the named
hermes-state volume. These tests lock the cutover in so nobody re-adds the mount
without also re-introducing the write-into-the-repo surface.
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
COMPOSE = REPO_ROOT / "docker-compose.yml"
CRONTAB = REPO_ROOT / "deploy" / "cron" / "hermes.crontab"

STATE_OVERRIDES = {
    "HERMES_SENTINEL_STATE_FILE",
    "HERMES_COMMISSION_AUDIT_STATE_FILE",
    "HERMES_EOM_SCORECARD_STATE_FILE",
}


def _load_compose() -> dict:
    import yaml

    return yaml.safe_load(COMPOSE.read_text())


@pytest.fixture(scope="module")
def compose() -> dict:
    return _load_compose()


def test_compose_file_is_present_and_parseable():
    assert COMPOSE.exists(), "docker-compose.yml is missing"
    assert _load_compose()["services"], "docker-compose.yml has no services"


def test_no_service_bind_mounts_the_repo_tree(compose):
    """No `.:/app` (or any host bind of the repo) may shadow the image's code."""
    for name, cfg in compose["services"].items():
        vols = cfg.get("volumes") or []
        for v in vols:
            spec = v.split(":")[0] if isinstance(v, str) else v.get("source", "")
            assert spec not in (".", "./"), (
                f"{name} still bind-mounts the repo tree ({v}) — re-adding it "
                "puts container writes back into the git working tree (#234)."
            )


def test_every_service_mounts_the_state_volume(compose):
    """Job-idempotency state must survive container recreates now that /app is
    not mounted — losing it re-posts the daily briefing / re-runs audits."""
    for name, cfg in compose["services"].items():
        vols = cfg.get("volumes") or []
        assert any(
            (isinstance(v, str) and v.startswith("hermes-state:"))
            for v in vols
        ), f"{name} does not mount the hermes-state volume — its state is ephemeral"


def test_state_file_env_overrides_point_into_the_state_volume(compose):
    """The .env defaults are relative (.hermes/*.json), which resolved into the
    bind-mounted tree. Without the mount they would resolve to a read-only path
    in the image and silently reset every recreate. The compose overrides must
    point at /var/lib/hermes."""
    for name, cfg in compose["services"].items():
        env = cfg.get("environment") or {}
        # environment can be a list ("KEY=VALUE") or a mapping.
        if isinstance(env, list):
            env = dict(line.split("=", 1) for line in env if "=" in line)
        for key in STATE_OVERRIDES:
            if key in env:
                assert str(env[key]).startswith("/var/lib/hermes/"), (
                    f"{name}.{key}={env[key]} does not point into the hermes-state "
                    "volume — without the bind mount this state is ephemeral"
                )


def test_cron_uses_compose_run_not_up_or_exec():
    """Cron must use `docker compose run --rm hermes ...` (the image), not `up`/
    `exec` against a long-lived container — deploys are build-only now, so only
    the image carries current code. Active (uncommented) entries only."""
    assert CRONTAB.exists()
    # Active = uncommented, non-empty, and actually a job line (skip env lines
    # like `CRON_TZ=America/New_York` that the crontab sets at the top).
    active = [
        line for line in CRONTAB.read_text().splitlines()
        if line.strip()
        and not line.lstrip().startswith("#")
        and "docker compose" in line
    ]
    assert active, "no active docker compose cron entries found — did the crontab format change?"
    for line in active:
        assert "docker compose run --rm" in line, (
            f"cron entry does not use `docker compose run --rm`:\n  {line}\n"
            "With the bind mount gone, only the image has current code."
        )
        assert "docker compose up" not in line and "docker compose exec" not in line, (
            f"cron entry uses up/exec, which won't see a rebuilt image:\n  {line}"
        )
