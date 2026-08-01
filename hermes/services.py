"""One process per app.

Every route in this codebase used to be served by a single uvicorn worker. That
made the six apps a shared fate: a slow renewal call stalled finance, a bad
deploy took all of them down, and a traceback in the log could have come from
any of them. Splitting the handlers off the event loop (see
tests/test_event_loop_not_blocked.py) fixed an app freezing *itself*. This is
what stops one app freezing the others.

A service is a name, the routers it serves, and a port. `create_app("finance")`
returns a FastAPI app carrying only the finance routes; running that under its
own uvicorn gives finance its own event loop, its own threadpool, its own
restart, and its own log stream.

`create_app("all")` returns the existing `hermes.api.app` unchanged, and that
is the default — a deploy that sets no HERMES_SERVICE behaves exactly as it
does today. Splitting is opt-in per container.

Talking to each other
---------------------
Services share one image and one database, so a service that needs another
app's *logic* imports it, as it always has — no HTTP hop, no serialization. A
service needs `base_url()` only to reach another app's HTTP surface, which is
the case when the apps eventually live in separate images or repos. The
registry carries the ports so that day is a config change, not a redesign.

Routing
-------
`path_prefixes` is the contract with whatever sits in front (nginx, or the
portal's proxy). Each prefix belongs to exactly one service — asserted in
tests/test_services.py, along with the guarantee that the union of the split
services serves exactly the routes the single app serves. A route that belongs
to no service would 404 in a split deployment while working fine in the
monolith, which is the kind of difference that only shows up in production.
"""

from __future__ import annotations

from fastapi import FastAPI
from hermes_app.service import (
    ALL,
    ServiceSpec,
    base_url as _base_url,
    build_app,
    current_service,
)

__all__ = ["ALL", "SERVICES", "ServiceSpec", "base_url", "create_app", "current_service"]


SERVICES: dict[str, ServiceSpec] = {
    "finance": ServiceSpec(
        name="finance",
        description="Commission rules, ledger, analytics, overrides, statement gate",
        router_modules=("hermes.routers.finance",),
        path_prefixes=("/api/commissions", "/api/commission-rules", "/api/commission-statements"),
        port=8801,
    ),
    "cases": ServiceSpec(
        name="cases",
        description="Cases, the tasks under them, case documents, and the AMS push queue",
        router_modules=("hermes.routers.cases",),
        path_prefixes=(
            "/api/cases", "/api/case-templates", "/api/casework", "/api/tasks", "/api/queue",
        ),
        port=8802,
        queue_object_types=("case", "task"),
    ),
    "intake": ServiceSpec(
        name="intake",
        description="Lead capture, the intake desk and queue, agency-intake drafting",
        router_modules=(
            "hermes.routers.intake",
            "hermes.command_center.api_routes",
            "hermes.command_center.extract_api",
        ),
        path_prefixes=(
            "/api/intake", "/api/leads", "/api/pipeline", "/agency-intake", "/api/extract",
        ),
        port=8803,
        queue_object_types=("intake", "intake_ams", "intake_crm"),
    ),
    "renewals": ServiceSpec(
        name="renewals",
        description="The renewal worklist and the corrections applied on top of it",
        router_modules=("hermes.routers.renewals",),
        path_prefixes=("/api/renewals",),
        port=8804,
        queue_object_types=("renewal",),
    ),
    "carriers": ServiceSpec(
        name="carriers",
        description="Carrier appetite read (see rsg-carrierhub for the other one)",
        router_modules=("hermes.routers.carriers",),
        path_prefixes=("/api/carrier-appetite", "/api/carriers"),
        port=8805,
    ),
    "hub": ServiceSpec(
        name="hub",
        description="The CRM core: clients, opportunities, quotes, policies, documents, agent",
        router_modules=("hermes.api",),
        path_prefixes=(),  # the default backend — everything not claimed above
        port=8787,
        queue_object_types=("quote", "opportunity_writeback"),
    ),
}


def base_url(service: str) -> str:
    """Where to reach another service over HTTP, by name."""
    return _base_url(SERVICES[service])


def create_app(service: str | None = None) -> FastAPI:
    """Build the app for one service, or the whole thing.

    "all" returns `hermes.api.app` itself rather than a reconstruction, so the
    unsplit deployment runs the exact object it runs today.
    """
    service = (service or current_service()).strip().lower()

    if service == ALL:
        from hermes.api import app

        return app

    if service not in SERVICES:
        raise SystemExit(
            f"unknown service {service!r}; expected one of: {ALL}, "
            + ", ".join(sorted(SERVICES))
        )

    spec = SERVICES[service]

    # The hub is `hermes.api`, which builds its own app with everything mounted.
    # Serving hub alone means its own router only — not the apps it also mounts
    # for the unsplit case.
    if service == "hub":
        from hermes.api import router as hub_router

        from hermes_app.service import bare_app

        app = bare_app(spec)
        app.include_router(hub_router)
        return app

    return build_app(spec)
