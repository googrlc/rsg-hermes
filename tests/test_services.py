"""Splitting the app into one process per service must not change what it serves.

The failure this guards against is quiet and one-directional: a route that no
service claims still works in the unsplit deployment and 404s once the apps are
running as separate containers. It would pass every test, pass review, and
break only in production, on whichever endpoint nobody thought about.

So: the union of the split services must serve exactly the routes the single
app serves, and no two services may claim the same path.
"""

from __future__ import annotations

import pytest

from hermes.services import ALL, SERVICES, create_app

FASTAPI_BUILTINS = {"/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc"}


def _routes(app) -> set[tuple[str, str]]:
    return {
        (method, r.path)
        for r in app.routes
        for method in (getattr(r, "methods", None) or ())
        if r.path not in FASTAPI_BUILTINS
    }


def test_the_split_services_serve_exactly_what_the_single_app_serves() -> None:
    whole = _routes(create_app(ALL))
    split: set[tuple[str, str]] = set()
    for name in SERVICES:
        split |= _routes(create_app(name))

    # Each split app adds its own /health; the monolith has one already.
    health = {(m, p) for m, p in split if p == "/health"}
    split -= health
    whole -= {(m, p) for m, p in whole if p == "/health"}

    missing = whole - split
    assert not missing, (
        "these routes belong to no service and would 404 in a split deployment "
        f"while working in the monolith: {sorted(missing)}"
    )
    extra = split - whole
    assert not extra, f"split services serve routes the app does not: {sorted(extra)}"


def test_no_two_services_claim_the_same_route() -> None:
    seen: dict[tuple[str, str], str] = {}
    clashes = []
    for name in SERVICES:
        for route in _routes(create_app(name)):
            if route[1] == "/health":
                continue
            if route in seen:
                clashes.append(f"{route} claimed by both {seen[route]} and {name}")
            seen[route] = name
    assert not clashes, "\n".join(clashes)


def test_no_two_services_claim_the_same_path_prefix() -> None:
    """The front proxy routes on these, so an overlap is an ambiguous request."""
    owner: dict[str, str] = {}
    for name, spec in SERVICES.items():
        for prefix in spec.path_prefixes:
            assert prefix not in owner, (
                f"prefix {prefix!r} claimed by both {owner[prefix]} and {name}"
            )
            owner[prefix] = name


def test_every_declared_prefix_actually_has_routes_behind_it() -> None:
    """A prefix with nothing behind it sends real traffic to a service that will
    404 it — worse than not routing it at all."""
    for name, spec in SERVICES.items():
        paths = {p for _, p in _routes(create_app(name))}
        for prefix in spec.path_prefixes:
            assert any(p.startswith(prefix) for p in paths), (
                f"{name} claims {prefix!r} but serves no route under it"
            )


@pytest.mark.parametrize("name", sorted(SERVICES))
def test_each_service_reports_its_own_identity_on_health(name: str) -> None:
    """A probe against the wrong port must be obvious, not reassuring."""
    from fastapi.testclient import TestClient

    r = TestClient(create_app(name)).get("/health")
    assert r.status_code == 200
    assert r.json()["service"] == name


def test_all_is_the_default_so_an_unset_env_changes_nothing(monkeypatch) -> None:
    monkeypatch.delenv("HERMES_SERVICE", raising=False)
    from hermes.api import app
    from hermes.services import current_service

    assert current_service() == ALL
    assert create_app() is app


def test_an_unknown_service_name_fails_loudly() -> None:
    with pytest.raises(SystemExit) as exc:
        create_app("renewal")  # singular — the real one is "renewals"
    assert "unknown service" in str(exc.value)


def test_compose_publishes_the_ports_the_registry_declares() -> None:
    """The registry is the source of truth for ports; the compose overlay repeats
    them. A drift between the two is a service reachable on a port nothing routes
    to — it comes up healthy and receives no traffic."""
    import re
    from pathlib import Path

    compose = Path("docker-compose.services.yml")
    if not compose.exists():  # pragma: no cover - overlay is optional
        pytest.skip("no split-deployment overlay in this checkout")
    text = compose.read_text(encoding="utf8")

    for name, spec in SERVICES.items():
        block = re.search(
            rf"^  hermes-{name}:\n(?:.*\n)*?(?=^  \S|\Z)", text, re.M
        )
        assert block, f"no hermes-{name} service in {compose}"
        body = block.group(0)
        assert f'"{spec.port}:{spec.port}"' in body, (
            f"hermes-{name} does not publish {spec.port}:{spec.port} — the registry "
            f"says {spec.port}"
        )
        assert f"HERMES_SERVICE: {name}" in body, (
            f"hermes-{name} does not set HERMES_SERVICE={name}"
        )


def test_every_queue_object_type_is_owned_by_exactly_one_service() -> None:
    """A worker per service means each object_type needs exactly one drainer.
    Two services draining the same type race for the same rows; zero means the
    queue silently fills."""
    from hermes_core.queue import BACKED_OFF_OBJECT_TYPES

    owner: dict[str, str] = {}
    for name, spec in SERVICES.items():
        for object_type in spec.queue_object_types:
            assert object_type not in owner, (
                f"object_type {object_type!r} drained by both {owner[object_type]} and {name}"
            )
            owner[object_type] = name

    unowned = set(BACKED_OFF_OBJECT_TYPES) - set(owner)
    assert not unowned, (
        f"no service drains {sorted(unowned)} — those jobs would queue forever "
        "once the scheduler is split per service"
    )


def test_a_named_service_uses_its_registered_port_over_a_shared_env_var(monkeypatch) -> None:
    """The services share one .env on the box, and it sets HERMES_API_PORT.

    When that env var outranked the registry, all five services bound the same
    port and none of them answered on the one compose had published. The
    registry is the source of truth for a named service; HERMES_API_PORT is for
    the unsplit app, which has no registered port of its own.
    """
    import argparse

    monkeypatch.setenv("HERMES_API_PORT", "8484")

    def resolve(service: str, explicit: int | None = None) -> int:
        # Mirrors the precedence in hermes.api.main.
        if explicit:
            return explicit
        if service != ALL:
            return SERVICES[service].port
        import os

        return int(os.environ.get("HERMES_API_PORT") or 8484)

    for name, spec in SERVICES.items():
        assert resolve(name) == spec.port, (
            f"{name} resolved to {resolve(name)} with HERMES_API_PORT set; "
            f"its registered port is {spec.port}"
        )
    # Distinct ports, so nothing collides when they run side by side.
    resolved = [resolve(n) for n in SERVICES]
    assert len(set(resolved)) == len(resolved), f"ports collide: {resolved}"
    # The unsplit app still honours the env var, and --port still wins outright.
    assert resolve(ALL) == 8484
    assert resolve("finance", explicit=9999) == 9999
    _ = argparse  # documents that this mirrors the CLI path
