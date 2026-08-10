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


def _attach_healthz(app: FastAPI, *, service: str, modules: tuple[str, ...]) -> None:
    """Register GET /healthz with role / credential / db_user reporting.

    Never includes the NowCerts key — only a boolean. mirror_lag_seconds is
    filled for write_in only (best-effort; null when the DB is unreachable).
    """
    from hermes_app.role import (
        ROLE_WRITE_IN,
        current_role,
        inferred_db_user,
        modules_loaded_names,
        nowcerts_creds_present,
    )

    role = current_role(service)
    loaded = modules_loaded_names(modules)
    app.state.hermes_role = role
    app.state.hermes_modules = list(loaded)
    app.state.hermes_service = service

    @app.get("/healthz")
    def healthz() -> dict[str, object]:
        payload: dict[str, object] = {
            "role": role,
            "service": service,
            "modules_loaded": loaded,
            "nowcerts": nowcerts_creds_present(),
            "db_user": inferred_db_user(role),
            "mirror_lag_seconds": None,
        }
        if role == ROLE_WRITE_IN:
            payload["mirror_lag_seconds"] = _mirror_lag_seconds()
        return payload


def _mirror_lag_seconds() -> float | None:
    """Seconds since the last completed outbound_sync_queue job, if known."""
    from datetime import datetime, timezone

    try:
        from hermes_app import deps

        recent = deps.get_supa().select(
            "outbound_sync_queue",
            columns="updated_at",
            params={"status": "eq.completed", "order": "updated_at.desc"},
            limit=1,
        )
        if not recent or not recent[0].get("updated_at"):
            return None
        raw = str(recent[0]["updated_at"]).replace("Z", "+00:00")
        ts = datetime.fromisoformat(raw)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return max(0.0, (datetime.now(timezone.utc) - ts).total_seconds())
    except Exception:  # noqa: BLE001 — healthz must never 500
        return None


def bare_app(spec: ServiceSpec, *, modules: tuple[str, ...] | None = None) -> FastAPI:
    """An app carrying nothing but its identity and health endpoints."""
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

    _attach_healthz(
        app,
        service=spec.name,
        modules=modules if modules is not None else spec.router_modules,
    )
    return app


def build_app(spec: ServiceSpec) -> FastAPI:
    """Compose a service from the routers its spec names."""
    import importlib

    from hermes_app.role import current_role, modules_for

    role = current_role(spec.name)
    allowed = set(modules_for(role, spec.name))
    to_mount = tuple(m for m in spec.router_modules if m in allowed)

    app = bare_app(spec, modules=to_mount or spec.router_modules)
    for module_path in to_mount:
        mod = importlib.import_module(module_path)
        for attr in ("router", "dashboard_router"):
            r = getattr(mod, attr, None)
            if r is not None:
                app.include_router(r)
    return app
