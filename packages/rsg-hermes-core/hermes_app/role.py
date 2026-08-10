"""Hermes process role — who may hold NowCerts credentials.

Orthogonal to ``HERMES_SERVICE`` (which app routes this process serves).
``HERMES_ROLE`` gates credentials and which module set is allowed to mount.

Roles
-----
write_in
    The single NowCerts core. Owns sync-in and the AMS write queue. Requires
    NowCerts credentials. Default for ``all`` / ``hub``.

finance_readout
    Finance surface only. Must NOT hold NowCerts credentials. Default for
    ``finance``.

mirror_reader
    CRM / intake / renewals / carriers instances that READ the mirror and
    ENQUEUE writes through the write_in core. Must NOT hold NowCerts
    credentials. Default for ``intake`` / ``renewals`` / ``carriers``.
"""

from __future__ import annotations

import os
from typing import Iterable

ROLE_WRITE_IN = "write_in"
ROLE_FINANCE_READOUT = "finance_readout"
ROLE_MIRROR_READER = "mirror_reader"

ROLES = frozenset({ROLE_WRITE_IN, ROLE_FINANCE_READOUT, ROLE_MIRROR_READER})

_SERVICE_DEFAULT_ROLE: dict[str, str] = {
    "all": ROLE_WRITE_IN,
    "hub": ROLE_WRITE_IN,
    "finance": ROLE_FINANCE_READOUT,
    "intake": ROLE_MIRROR_READER,
    "renewals": ROLE_MIRROR_READER,
    "carriers": ROLE_MIRROR_READER,
}

_ROLE_ALLOWED_SERVICES: dict[str, frozenset[str]] = {
    ROLE_WRITE_IN: frozenset({"all", "hub"}),
    ROLE_FINANCE_READOUT: frozenset({"finance"}),
    ROLE_MIRROR_READER: frozenset({"intake", "renewals", "carriers"}),
}

ROLE_MODULES: dict[str, tuple[str, ...]] = {
    ROLE_WRITE_IN: (
        "hermes.api",
        "hermes.routers.finance",
        "hermes.routers.carriers",
        "hermes.routers.renewals",
        "hermes.routers.intake",
        "hermes.command_center.api_routes",
        "hermes.command_center.extract_api",
    ),
    ROLE_FINANCE_READOUT: ("hermes.routers.finance",),
    ROLE_MIRROR_READER: (
        "hermes.routers.intake",
        "hermes.routers.renewals",
        "hermes.routers.carriers",
        "hermes.command_center.api_routes",
        "hermes.command_center.extract_api",
    ),
}

NOWCERTS_CRED_ENVS = (
    "NOWCERTS_API_KEY",
    "NOWCERTS_USERNAME",
    "NOWCERTS_PASSWORD",
)


def nowcerts_creds_present(env: dict[str, str] | None = None) -> bool:
    """True when any NowCerts credential env is non-empty."""
    src = env if env is not None else os.environ
    if (src.get("NOWCERTS_API_KEY") or "").strip():
        return True
    user = (src.get("NOWCERTS_USERNAME") or "").strip()
    password = (src.get("NOWCERTS_PASSWORD") or "").strip()
    return bool(user and password)


def default_role_for_service(service: str) -> str:
    return _SERVICE_DEFAULT_ROLE.get((service or "all").strip().lower(), ROLE_WRITE_IN)


def current_role(service: str | None = None, env: dict[str, str] | None = None) -> str:
    """Resolved HERMES_ROLE. Explicit env wins; otherwise service default."""
    src = env if env is not None else os.environ
    explicit = (src.get("HERMES_ROLE") or "").strip().lower()
    if explicit:
        if explicit not in ROLES:
            raise SystemExit(
                f"unknown HERMES_ROLE {explicit!r}; expected one of: "
                + ", ".join(sorted(ROLES))
            )
        return explicit
    from hermes_app.service import ALL, current_service

    svc = (service or current_service() or ALL).strip().lower()
    return default_role_for_service(svc)


def assert_role_service_combo(role: str, service: str) -> None:
    svc = (service or "all").strip().lower()
    allowed = _ROLE_ALLOWED_SERVICES.get(role, frozenset())
    if svc not in allowed:
        raise SystemExit(
            f"HERMES_ROLE={role} cannot serve HERMES_SERVICE={svc}; "
            f"allowed services: {', '.join(sorted(allowed))}"
        )


def assert_role_credentials(role: str, env: dict[str, str] | None = None) -> None:
    has = nowcerts_creds_present(env)
    if role == ROLE_WRITE_IN and not has:
        raise SystemExit(
            "HERMES_ROLE=write_in requires NowCerts credentials "
            "(NOWCERTS_API_KEY, or NOWCERTS_USERNAME + NOWCERTS_PASSWORD)"
        )
    if role in (ROLE_FINANCE_READOUT, ROLE_MIRROR_READER) and has:
        raise SystemExit(
            f"HERMES_ROLE={role} must not hold NowCerts credentials "
            f"(unset {', '.join(NOWCERTS_CRED_ENVS)}). "
            "Only the write_in NowCerts core may hold them."
        )


def assert_role_config(
    service: str | None = None,
    *,
    env: dict[str, str] | None = None,
    enforce_credentials: bool = True,
) -> str:
    from hermes_app.service import ALL, current_service

    svc = (service or (env or os.environ).get("HERMES_SERVICE") or current_service() or ALL)
    svc = str(svc).strip().lower()
    role = current_role(svc, env=env)
    assert_role_service_combo(role, svc)
    if enforce_credentials:
        assert_role_credentials(role, env=env)
    return role


def modules_for(role: str, service: str | None = None) -> tuple[str, ...]:
    base = ROLE_MODULES.get(role, ())
    if role != ROLE_MIRROR_READER:
        return base
    svc = (service or "").strip().lower()
    by_service: dict[str, tuple[str, ...]] = {
        "intake": (
            "hermes.routers.intake",
            "hermes.command_center.api_routes",
            "hermes.command_center.extract_api",
        ),
        "renewals": ("hermes.routers.renewals",),
        "carriers": ("hermes.routers.carriers",),
    }
    return by_service.get(svc, base)


def modules_loaded_names(modules: Iterable[str]) -> list[str]:
    return [m.rsplit(".", 1)[-1] for m in modules]


def inferred_db_user(role: str, env: dict[str, str] | None = None) -> str:
    src = env if env is not None else os.environ
    explicit = (src.get("HERMES_DB_USER") or "").strip()
    if explicit:
        return explicit
    database_url = (src.get("DATABASE_URL") or "").strip()
    if database_url and "://" in database_url:
        try:
            after_scheme = database_url.split("://", 1)[1]
            userinfo = after_scheme.split("@", 1)[0]
            user = userinfo.split(":", 1)[0]
            if user:
                return user
        except Exception:
            pass
    return {
        ROLE_WRITE_IN: "hermes_write",
        ROLE_FINANCE_READOUT: "hermes_finance",
        ROLE_MIRROR_READER: "hermes_mirror_reader",
    }.get(role, "unknown")
