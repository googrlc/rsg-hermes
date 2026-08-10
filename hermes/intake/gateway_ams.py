"""Call the cptintake write gateway for AMS-owned insured create/adopt.

The portal routes /api/nowcerts/* and /api/proposals to the gateway. Hermes
write_in uses the same door so intake approval earns a NowCerts GUID before CRM
rows are keyed on it.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import requests

log = logging.getLogger(__name__)

ENV_GATEWAY_URL = "INTAKE_GATEWAY_URL"
ENV_AMS_FIRST = "HERMES_INTAKE_AMS_FIRST"
ENV_STAGE_LEGACY = "HERMES_INTAKE_STAGES_AMS_INSURED"


def gateway_base(env: dict[str, str] | None = None) -> str:
    src = env if env is not None else os.environ
    return (src.get(ENV_GATEWAY_URL) or "").strip().rstrip("/")


def ams_first_enabled(env: dict[str, str] | None = None) -> bool:
    """Default ON. Explicit 0/false/no/off disables (emergency kill-switch)."""
    src = env if env is not None else os.environ
    raw = (src.get(ENV_AMS_FIRST) or "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


def create_or_adopt_insured(
    account: dict[str, Any],
    *,
    approved_by: str,
    session: requests.Session | None = None,
    env: dict[str, str] | None = None,
    timeout: float = 60.0,
) -> dict[str, Any]:
    """Best-effort AMS create-or-adopt via the gateway insured search + commit contract.

    When the gateway is not configured, returns ``{skipped: True}``. Callers must
    still open CRM rows; they key on a GUID only when this returns one.
    """
    base = gateway_base(env)
    if not base:
        return {"ok": False, "skipped": True, "reason": "INTAKE_GATEWAY_URL unset"}
    if not ams_first_enabled(env):
        return {"ok": False, "skipped": True, "reason": "HERMES_INTAKE_AMS_FIRST disabled"}

    name = account.get("account_name") or account.get("commercial_name") or account.get("name")
    if not name:
        return {"ok": False, "skipped": False, "reason": "missing account name"}

    http = session or requests.Session()
    # 1) Pre-create existence check (name search; email/DOB adopt is gateway-side on commit).
    try:
        search = http.get(
            f"{base}/api/nowcerts/insureds/search",
            params={"q": str(name)[:120]},
            timeout=timeout,
        )
        matches = []
        if search.ok:
            body = search.json() if search.content else {}
            matches = body.get("matches") or []
    except Exception as exc:  # noqa: BLE001
        log.warning("gateway insured search failed: %s", exc)
        matches = []

    email = (account.get("email") or account.get("eMail") or "").strip().lower()
    for m in matches:
        m_email = str(m.get("email") or "").strip().lower()
        if m.get("database_id") and email and m_email and m_email == email:
            return {
                "ok": True,
                "adopted": True,
                "insured_database_id": m["database_id"],
                "reason": "name+email",
                "approved_by": approved_by,
            }
        # Name-only exact display match → adopt when no conflicting email on either side.
        if (
            m.get("database_id")
            and str(m.get("display_name") or "").strip().lower() == str(name).strip().lower()
            and not email
            and not m_email
        ):
            return {
                "ok": True,
                "adopted": True,
                "insured_database_id": m["database_id"],
                "reason": "name_exact",
                "approved_by": approved_by,
            }

    # 2) No adoptable match — leave create to the gateway proposal/commit path.
    # Hermes records the intent; the portal/gateway owns the live Momentum write.
    return {
        "ok": True,
        "adopted": False,
        "insured_database_id": None,
        "reason": "no_adoptable_match",
        "pending_gateway_create": True,
        "search_matches": len(matches),
        "approved_by": approved_by,
    }
