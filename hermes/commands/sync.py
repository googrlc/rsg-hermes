"""Dispatcher-routed NowCerts sync commands.

Allows triggering the NowCerts → EspoCRM sync pipeline and querying
sync status from Slack or Open WebUI (any chat interface).
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Any

from hermes.core.dispatcher import DispatchResult

if TYPE_CHECKING:
    from hermes.core.client import EspoClient
    from hermes.integrations.supabase_client import SupabaseClient

log = logging.getLogger(__name__)

_DRY_RUN_RE = re.compile(r"\bdry[\s-]?run\b", re.I)
_STATUS_RE = re.compile(r"\b(status|last\s+run|history|runs)\b", re.I)
_CONFLICTS_RE = re.compile(r"\bconflicts?\b", re.I)
_ERRORS_RE = re.compile(r"\berrors?\b", re.I)
_SINCE_RE = re.compile(r"\bsince\s+(\S+)", re.I)

# Bidirectional-variant routing. These must NOT match a plain
# "sync nowcerts [dry-run|since …]" command, which falls through to the
# default NowCerts → EspoCRM pipeline.
_BIDI_RE = re.compile(r"\b(bidirectional|bi-directional|both\s+ways?|two[\s-]way|round[\s-]?trip|full[\s-]?sync)\b", re.I)
_CRM_TO_HUB_RE = re.compile(r"\b(crm|espo)\b.*\bhub\b", re.I)
_HUB_TO_NC_RE = re.compile(r"\b(hub|push)\b.*\b(nowcerts|nc)\b", re.I)


def handle(
    client: "EspoClient",
    text: str,
    *,
    supa: "SupabaseClient | None" = None,
) -> DispatchResult:
    """Route sync sub-commands: run, dry-run, status, conflicts, errors."""
    if supa is None:
        return DispatchResult(False, "Supabase is not configured — sync commands require Supabase.")

    if _STATUS_RE.search(text):
        return _sync_status(supa)
    if _CONFLICTS_RE.search(text):
        return _sync_conflicts(supa)
    if _ERRORS_RE.search(text):
        return _sync_errors(supa)

    dry_run = bool(_DRY_RUN_RE.search(text))

    # Bidirectional sync commands
    if _BIDI_RE.search(text):
        return _trigger_bidirectional(client, supa, dry_run=dry_run)
    if _CRM_TO_HUB_RE.search(text):
        return _trigger_crm_to_hub(client, supa, dry_run=dry_run)
    if _HUB_TO_NC_RE.search(text):
        return _trigger_hub_to_nowcerts(supa, dry_run=dry_run)

    # Default: NowCerts → EspoCRM
    since_match = _SINCE_RE.search(text)
    since = since_match.group(1) if since_match else None
    return _trigger_sync(client, supa, dry_run=dry_run, since=since)


def _trigger_sync(
    client: "EspoClient",
    supa: "SupabaseClient",
    *,
    dry_run: bool,
    since: str | None,
) -> DispatchResult:
    """Run the NowCerts → EspoCRM pipeline."""
    try:
        from hermes.sync.nowcerts_client import NowCertsClient, NowCertsClientError
        from hermes.sync.pipeline import run_insured_to_account_sync
    except ImportError as exc:
        return DispatchResult(False, f"Sync module not available: {exc}")

    try:
        nc = NowCertsClient()
    except Exception as exc:
        return DispatchResult(False, f"NowCerts connection failed: {exc}")

    result = run_insured_to_account_sync(
        nc, client, supa, dry_run=dry_run, since=since,
    )

    lines = [result.message]
    if result.errors:
        lines.append(f"Errors ({len(result.errors)}):")
        for err in result.errors[:5]:
            lines.append(f"  • {err}")
        if len(result.errors) > 5:
            lines.append(f"  … and {len(result.errors) - 5} more")

    return DispatchResult(result.ok, "\n".join(lines))


def _sync_status(supa: "SupabaseClient") -> DispatchResult:
    """Return the last 5 sync runs."""
    try:
        runs = supa.select(
            "sync_runs",
            columns="id,workflow_name,status,records_processed,records_created,records_updated,records_failed,finished_at",
            params={"order": "created_at.desc"},
            limit=5,
        )
    except Exception as exc:
        return DispatchResult(False, f"Failed to query sync_runs: {exc}")

    if not runs:
        return DispatchResult(True, "No sync runs found yet.")

    lines = ["*Recent sync runs:*"]
    for r in runs:
        status = r.get("status", "?")
        wf = r.get("workflow_name", "?")
        processed = r.get("records_processed", 0)
        created = r.get("records_created", 0)
        updated = r.get("records_updated", 0)
        failed = r.get("records_failed", 0)
        finished = r.get("finished_at", "in progress")
        emoji = "✅" if status == "success" else "⚠️" if status == "partial" else "❌" if status == "failed" else "🔄"
        lines.append(
            f"{emoji} *{wf}* — {status} | "
            f"processed:{processed} created:{created} updated:{updated} failed:{failed} | "
            f"{finished}"
        )

    return DispatchResult(True, "\n".join(lines))


def _sync_conflicts(supa: "SupabaseClient") -> DispatchResult:
    """Return recent unresolved sync conflicts."""
    try:
        conflicts = supa.select(
            "sync_conflicts",
            columns="id,field_name,nowcerts_value,espocrm_value,resolution,created_at",
            params={"resolution": "eq.pending", "order": "created_at.desc"},
            limit=10,
        )
    except Exception as exc:
        return DispatchResult(False, f"Failed to query sync_conflicts: {exc}")

    if not conflicts:
        return DispatchResult(True, "No unresolved sync conflicts.")

    lines = [f"*{len(conflicts)} unresolved conflict(s):*"]
    for c in conflicts:
        field = c.get("field_name", "?")
        src = str(c.get("nowcerts_value", ""))[:40]
        dst = str(c.get("espocrm_value", ""))[:40]
        lines.append(f"  • *{field}*: NowCerts=`{src}` vs EspoCRM=`{dst}`")

    return DispatchResult(True, "\n".join(lines))


def _trigger_crm_to_hub(
    client: "EspoClient",
    supa: "SupabaseClient",
    *,
    dry_run: bool,
) -> DispatchResult:
    """Mirror EspoCRM → Supabase golden record."""
    try:
        from hermes.sync.bidirectional import run_crm_to_hub
    except ImportError as exc:
        return DispatchResult(False, f"Bidirectional sync module not available: {exc}")

    result = run_crm_to_hub(client, supa, dry_run=dry_run)
    lines = [result.message]
    if result.errors:
        lines.append(f"Errors ({len(result.errors)}):")
        for err in result.errors[:5]:
            lines.append(f"  • {err}")
    return DispatchResult(result.ok, "\n".join(lines))


def _trigger_hub_to_nowcerts(
    supa: "SupabaseClient",
    *,
    dry_run: bool,
) -> DispatchResult:
    """Push Supabase → NowCerts."""
    try:
        from hermes.sync.bidirectional import run_hub_to_nowcerts
        from hermes.sync.nowcerts_client import NowCertsClient
    except ImportError as exc:
        return DispatchResult(False, f"Bidirectional sync module not available: {exc}")

    try:
        nc = NowCertsClient()
    except Exception as exc:
        return DispatchResult(False, f"NowCerts connection failed: {exc}")

    result = run_hub_to_nowcerts(nc, supa, dry_run=dry_run)
    lines = [result.message]
    if result.errors:
        lines.append(f"Errors ({len(result.errors)}):")
        for err in result.errors[:5]:
            lines.append(f"  • {err}")
    return DispatchResult(result.ok, "\n".join(lines))


def _trigger_bidirectional(
    client: "EspoClient",
    supa: "SupabaseClient",
    *,
    dry_run: bool,
) -> DispatchResult:
    """Run all three sync directions."""
    try:
        from hermes.sync.bidirectional import run_bidirectional
        from hermes.sync.nowcerts_client import NowCertsClient
    except ImportError as exc:
        return DispatchResult(False, f"Bidirectional sync module not available: {exc}")

    try:
        nc = NowCertsClient()
    except Exception as exc:
        return DispatchResult(False, f"NowCerts connection failed: {exc}")

    result = run_bidirectional(nc, client, supa, dry_run=dry_run)
    lines = [result.message]
    if result.errors:
        lines.append(f"Errors ({len(result.errors)}):")
        for err in result.errors[:5]:
            lines.append(f"  • {err}")
        if len(result.errors) > 5:
            lines.append(f"  … and {len(result.errors) - 5} more")
    return DispatchResult(result.ok, "\n".join(lines))


def _sync_errors(supa: "SupabaseClient") -> DispatchResult:
    """Return recent sync errors."""
    try:
        errors = supa.select(
            "sync_errors",
            columns="id,error_type,error_message,object_type,object_id,created_at",
            params={"order": "created_at.desc"},
            limit=10,
        )
    except Exception as exc:
        return DispatchResult(False, f"Failed to query sync_errors: {exc}")

    if not errors:
        return DispatchResult(True, "No recent sync errors.")

    lines = [f"*{len(errors)} recent error(s):*"]
    for e in errors:
        etype = e.get("error_type", "unknown")
        msg = str(e.get("error_message", ""))[:80]
        obj = f"{e.get('object_type', '?')}:{e.get('object_id', '?')}"
        lines.append(f"  • [{etype}] {obj} — {msg}")

    return DispatchResult(True, "\n".join(lines))
