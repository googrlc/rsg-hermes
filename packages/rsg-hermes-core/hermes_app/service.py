"""What it takes to be one RSG Hermes service.

The registry of *which* services exist belongs to whoever is composing them —
in the unsplit repo that is `hermes/services.py`; in an app repo it is that
app declaring itself. What is shared is the shape: a spec, the env convention
for naming the running service, how to reach a sibling over HTTP, and the
factory that turns routers into an app with a health endpoint.

Every app repo needs this identically, so it lives here rather than being
copied into each one.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from fastapi import FastAPI

ALL = "all"


@dataclass(frozen=True)
class ServiceSpec:
    name: str
    description: str
    # Import paths of modules exposing a `router`. Resolved lazily so building
    # one service does not import another's dependencies.
    router_modules: tuple[str, ...]
    # URL prefixes this service owns, for the front proxy. Longest match wins,
    # so "/api/intakes" vs "/api/intake" must both be listed where they differ.
    path_prefixes: tuple[str, ...]
    port: int
    # Queue object_types this service's worker drains (hermes_core.queue).
    # Empty means the service has no background work of its own.
    queue_object_types: tuple[str, ...] = field(default_factory=tuple)


def current_service() -> str:
    """Which service this process is. Defaults to the whole app."""
    return (os.environ.get("HERMES_SERVICE") or ALL).strip().lower()


def base_url(spec: ServiceSpec) -> str:
    """Where to reach another service over HTTP.

    Only needed to call another app's HTTP surface; while the apps share an
    image, importing the logic is usually right. Overridable per service
    (HERMES_SERVICE_URL_FINANCE=...) for when they stop being neighbours.
    """
    override = os.environ.get(f"HERMES_SERVICE_URL_{spec.name.upper()}")
    if override:
        return override.rstrip("/")
    host = os.environ.get("HERMES_SERVICE_HOST", f"rsg-hermes-{spec.name}")
    return f"http://{host}:{spec.port}"


def bare_app(spec: ServiceSpec) -> FastAPI:
    """An app carrying nothing but its identity and a health endpoint."""
    app = FastAPI(
        title=f"Hermes — {spec.name}",
        description=spec.description,
        version="0.1.0",
    )

    @app.get("/health")
    def health() -> dict[str, object]:
        """Per-service health. Names the service so a probe against the wrong
        port is obvious rather than reassuring."""
        return {"ok": True, "service": spec.name, "prefixes": list(spec.path_prefixes)}

    return app


def build_app(spec: ServiceSpec) -> FastAPI:
    """Compose a service from the routers its spec names."""
    import importlib

    app = bare_app(spec)
    for module_path in spec.router_modules:
        mod = importlib.import_module(module_path)
        for attr in ("router", "dashboard_router"):
            r = getattr(mod, attr, None)
            if r is not None:
                app.include_router(r)
    return app
