"""Policy data-quality investigation — cross-system diff for one policy.

Compares live NowCerts (AMS system of record), the Supabase canonical mirror,
renewal_candidates, project_85_renewals, and portal_overrides for a single
policy identity. Read-only; corrections are recommended, never auto-applied.

Used by GET /api/hermes/investigate-policy and the data-quality-investigator
Cursor agent skill.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from typing import TYPE_CHECKING, Any

from hermes_core.canonical import EXCLUDE_STATUSES, CURRENT_STATUSES, normalize_status

if TYPE_CHECKING:
    from hermes_integrations.nowcerts_client import NowCertsClient
    from hermes_integrations.supabase_client import SupabaseClient

log = logging.getLogger(__name__)

# Investigation verdicts
VERDICT_NOT_FOUND = "not_found"
VERDICT_AMBIGUOUS = "ambiguous"
VERDICT_NO_MISMATCH = "no_mismatch"
VERDICT_STALE_MIRROR = "outcome_a_stale_mirror"
VERDICT_AMS_WRONG = "outcome_b_ams_wrong"
VERDICT_INSURED_INACTIVE = "insured_inactive"

_TERMINAL = EXCLUDE_STATUSES | frozenset({"Renewed", "Rewritten"})


@dataclass
class RecommendedAction:
    action: str
    description: str
    command: str | None = None
    requires_approval: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PolicyInvestigationReport:
    policy_number: str
    client_name: str | None
    line_of_business: str | None
    generated_at: str
    verdict: str
    summary: str
    ams: dict[str, Any] = field(default_factory=dict)
    mirror_policies: list[dict[str, Any]] = field(default_factory=list)
    insured: dict[str, Any] | None = None
    renewal_candidates: list[dict[str, Any]] = field(default_factory=list)
    project_85_renewals: list[dict[str, Any]] = field(default_factory=list)
    portal_overrides: list[dict[str, Any]] = field(default_factory=list)
    sync_mappings: list[dict[str, Any]] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)
    recommended_actions: list[RecommendedAction] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "policy_number": self.policy_number,
            "client_name": self.client_name,
            "line_of_business": self.line_of_business,
            "generated_at": self.generated_at,
            "verdict": self.verdict,
            "summary": self.summary,
            "ams": self.ams,
            "mirror_policies": self.mirror_policies,
            "insured": self.insured,
            "renewal_candidates": self.renewal_candidates,
            "project_85_renewals": self.project_85_renewals,
            "portal_overrides": self.portal_overrides,
            "sync_mappings": self.sync_mappings,
            "issues": self.issues,
            "recommended_actions": [a.to_dict() for a in self.recommended_actions],
        }

    def format_lines(self) -> list[str]:
        lines = [
            f"Policy Investigation: {self.policy_number}",
            "=" * 48,
            f"Generated: {self.generated_at}",
            f"Verdict:   {self.verdict}",
            "",
            self.summary,
        ]
        if self.issues:
            lines.append("")
            lines.append("Issues:")
            for issue in self.issues:
                lines.append(f"  - {issue}")
        if self.recommended_actions:
            lines.append("")
            lines.append("Recommended actions:")
            for act in self.recommended_actions:
                gate = " [approval required]" if act.requires_approval else ""
                cmd = f" ({act.command})" if act.command else ""
                lines.append(f"  - {act.action}{gate}{cmd}: {act.description}")
        return lines


def _iso_date(value: Any) -> str | None:
    if not value:
        return None
    return str(value)[:10]


def _nc_status(raw: dict[str, Any]) -> str:
    raw_status = raw.get("policyStatus") or raw.get("status") or ""
    return normalize_status(raw_status) or str(raw_status).strip() or "Unknown"


def _nc_premium(raw: dict[str, Any]) -> float | None:
    for key in ("premium", "policyPremium", "premiumAmount"):
        if raw.get(key) is not None:
            try:
                return float(raw.get(key))
            except (TypeError, ValueError):
                return None
    return None


def _summarize_ams(raw: dict[str, Any] | None, *, ambiguous: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    if ambiguous:
        return {
            "ambiguous": True,
            "match_count": len(ambiguous),
            "matches": [
                {
                    "policy_guid": m.get("databaseId") or m.get("id"),
                    "status": _nc_status(m),
                    "effective_date": _iso_date(m.get("effectiveDate")),
                    "expiration_date": _iso_date(m.get("expirationDate")),
                    "premium": _nc_premium(m),
                    "carrier": m.get("carrierName") or m.get("carrier"),
                    "line_of_business": m.get("lineOfBusiness") or m.get("line_of_business"),
                }
                for m in ambiguous
            ],
        }
    if not raw:
        return {"found": False}
    insured_guid = raw.get("insuredDatabaseId") or raw.get("InsuredDatabaseId")
    return {
        "found": True,
        "policy_guid": raw.get("databaseId") or raw.get("id"),
        "insured_guid": str(insured_guid) if insured_guid else None,
        "status": _nc_status(raw),
        "status_raw": raw.get("policyStatus") or raw.get("status"),
        "active": raw.get("active"),
        "effective_date": _iso_date(raw.get("effectiveDate")),
        "expiration_date": _iso_date(raw.get("expirationDate")),
        "premium": _nc_premium(raw),
        "carrier": raw.get("carrierName") or raw.get("carrier"),
        "line_of_business": raw.get("lineOfBusiness") or raw.get("line_of_business"),
        "client_name": (
            raw.get("insuredCommercialName")
            or raw.get("insuredName")
            or raw.get("commercialName")
        ),
    }


def _mirror_row_summary(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "policy_guid": row.get("policy_guid"),
        "status": row.get("status"),
        "active": row.get("active"),
        "sync_owner": row.get("sync_owner"),
        "effective_date": _iso_date(row.get("effective_date")),
        "expiration_date": _iso_date(row.get("expiration_date")),
        "premium_amount": row.get("premium_amount"),
        "carrier": row.get("carrier"),
        "lines_of_business": row.get("lines_of_business"),
    }


def _candidate_summary(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row.get("id"),
        "renewal_event_date": _iso_date(row.get("renewal_event_date")),
        "expiration_date": _iso_date(row.get("expiration_date")),
        "normalized_status": row.get("normalized_status"),
        "policy_active": row.get("policy_active"),
        "insured_active": row.get("insured_active"),
        "eligibility_state": row.get("eligibility_state"),
        "eligibility_reason": row.get("eligibility_reason"),
        "in_working_queue": row.get("in_working_queue"),
        "premium_current": row.get("premium_current"),
    }


def _classify(
    *,
    ams_summary: dict[str, Any],
    mirror_rows: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
    project_85: list[dict[str, Any]],
    insured_active_live: bool | None,
) -> tuple[str, str, list[str], list[RecommendedAction]]:
    issues: list[str] = []
    actions: list[RecommendedAction] = []

    if ams_summary.get("ambiguous"):
        count = ams_summary.get("match_count", 0)
        return (
            VERDICT_AMBIGUOUS,
            f"NowCerts returned {count} policies for this number — escalate before correcting.",
            [f"AMS has {count} PolicyDetail rows for the same policy number"],
            [RecommendedAction(
                action="manual_review",
                description="Review all AMS term rows in Momentum and pick the current term before any write.",
                requires_approval=True,
            )],
        )

    ams_found = ams_summary.get("found", False)
    ams_status = ams_summary.get("status") or ""
    ams_terminal = ams_status in _TERMINAL

    mirror_active = [r for r in mirror_rows if r.get("active")]
    mirror_cancelled = [r for r in mirror_rows if normalize_status(r.get("status")) == "Cancelled"]
    ghost_active_candidates = [
        c for c in candidates
        if c.get("normalized_status") in CURRENT_STATUSES and c.get("policy_active")
    ]
    on_project_85 = bool(project_85)

    if not ams_found and not mirror_rows and not candidates:
        return (
            VERDICT_NOT_FOUND,
            "Policy not found in AMS, mirror, or renewal engine.",
            ["No records in any Hermes data source"],
            [],
        )

    if mirror_active and ams_terminal:
        owners = sorted({str(r.get("sync_owner") or "?") for r in mirror_active})
        issues.append(
            f"Mirror has {len(mirror_active)} active row(s) ({', '.join(owners)}) "
            f"but AMS status is {ams_status}"
        )
    if ghost_active_candidates and ams_terminal:
        issues.append(
            f"{len(ghost_active_candidates)} renewal_candidate row(s) still show Active policy status"
        )
    if on_project_85 and ams_terminal:
        issues.append("Policy is on project_85_renewals but AMS is terminal")

    if insured_active_live is False:
        issues.append("Live AMS reports insured inactive")

    # Outcome A — AMS terminal, mirror/worklist stale
    if ams_terminal and (mirror_active or on_project_85 or ghost_active_candidates):
        actions.extend([
            RecommendedAction(
                action="sync_canonical_book",
                command="hermes --sync-canonical-book",
                description="Reconcile the Supabase mirror from NowCerts (retire stale rsg-import rows).",
            ),
            RecommendedAction(
                action="renewal_refresh",
                command="hermes --renewal-refresh",
                description="Rebuild renewal_candidates and re-project project_85_renewals.",
            ),
        ])
        if on_project_85 or ghost_active_candidates:
            actions.append(RecommendedAction(
                action="dismiss_renewal",
                command="POST /api/renewals/{id}/override",
                description="Dismiss the renewal worklist entry if it persists after refresh.",
                requires_approval=True,
            ))
        actions.append(RecommendedAction(
            action="update_zoho",
            description="Update Zoho CRM policy/deal status to match AMS (manual until Zoho sync is wired).",
            requires_approval=True,
        ))
        return (
            VERDICT_STALE_MIRROR,
            f"AMS shows {ams_status}; mirror or worklist is stale. Run book sync + renewal refresh.",
            issues,
            actions,
        )

    # Outcome B — AMS still active but mirror/candidates say cancelled
    mirror_all_terminal = mirror_rows and all(not r.get("active") for r in mirror_rows)
    candidates_cancelled = candidates and all(
        normalize_status(c.get("normalized_status")) in _TERMINAL for c in candidates
    )
    if ams_status in CURRENT_STATUSES and (mirror_all_terminal or candidates_cancelled):
        actions.append(RecommendedAction(
            action="push_to_ams",
            command="POST /api/ams/policy (confirm=true after approval)",
            description="AMS still shows Active but mirror says terminal — verify in Momentum, then gated AMS writeback.",
            requires_approval=True,
        ))
        return (
            VERDICT_AMS_WRONG,
            f"AMS shows {ams_status} but mirror/candidates are terminal. Verify AMS before any write.",
            issues,
            actions,
        )

    if insured_active_live is False and mirror_active:
        actions.extend([
            RecommendedAction(
                action="sync_canonical_book",
                command="hermes --sync-canonical-book",
                description="Refresh mirror after insured deactivation in AMS.",
            ),
            RecommendedAction(
                action="renewal_refresh",
                command="hermes --renewal-refresh",
                description="Rebuild renewal candidates after book sync.",
            ),
        ])
        return (
            VERDICT_INSURED_INACTIVE,
            "Insured is inactive in live AMS but mirror still has active policies.",
            issues,
            actions,
        )

    if issues:
        return (
            VERDICT_NO_MISMATCH,
            "Minor inconsistencies detected; review issues before acting.",
            issues,
            actions,
        )

    return (
        VERDICT_NO_MISMATCH,
        "AMS, mirror, and renewal engine agree — no correction needed.",
        issues,
        actions,
    )


def investigate_policy(
    policy_number: str,
    *,
    client_name: str | None = None,
    line_of_business: str | None = None,
    nowcerts: "NowCertsClient",
    supa: "SupabaseClient",
) -> PolicyInvestigationReport:
    """Cross-system investigation for one policy number. Read-only."""
    pn = str(policy_number or "").strip()
    if not pn:
        raise ValueError("policy_number is required")

    now_iso = datetime.now(timezone.utc).isoformat()
    report = PolicyInvestigationReport(
        policy_number=pn,
        client_name=client_name,
        line_of_business=line_of_business,
        generated_at=now_iso,
        verdict=VERDICT_NOT_FOUND,
        summary="",
    )

    # --- Live AMS ---
    ams_raw: dict[str, Any] | None = None
    ams_ambiguous: list[dict[str, Any]] | None = None
    try:
        found = nowcerts.find_policy_by_number(pn)
        if isinstance(found, dict) and found.get("_ambiguous"):
            ams_ambiguous = found.get("matches") or []
        else:
            ams_raw = found
    except Exception as exc:  # noqa: BLE001
        log.exception("AMS policy lookup failed for %s", pn)
        report.issues.append(f"AMS lookup failed: {exc}")

    report.ams = _summarize_ams(ams_raw, ambiguous=ams_ambiguous)
    if not client_name and report.ams.get("client_name"):
        report.client_name = report.ams["client_name"]

    insured_guid = report.ams.get("insured_guid")

    # --- Mirror ---
    try:
        mirror_rows = supa.select(
            "canonical_policies",
            columns=(
                "policy_guid,nowcerts_insured_guid,policy_number,lines_of_business,"
                "carrier,status,active,effective_date,expiration_date,premium_amount,"
                "sync_owner,renewed_policy"
            ),
            params={"policy_number": f"eq.{pn}", "order": "expiration_date.desc"},
            limit=50,
        )
    except Exception as exc:  # noqa: BLE001
        log.exception("mirror lookup failed for %s", pn)
        mirror_rows = []
        report.issues.append(f"Mirror lookup failed: {exc}")

    report.mirror_policies = [_mirror_row_summary(r) for r in mirror_rows]
    if not insured_guid and mirror_rows:
        insured_guid = mirror_rows[0].get("nowcerts_insured_guid")

    # --- Insured ---
    if insured_guid:
        try:
            clients = supa.select(
                "canonical_clients",
                columns=(
                    "nowcerts_insured_guid,insured_name,first_name,last_name,"
                    "email,active,updated_at,nowcerts_synced_at"
                ),
                params={"nowcerts_insured_guid": f"eq.{insured_guid}"},
                limit=1,
            )
            report.insured = clients[0] if clients else None
        except Exception as exc:  # noqa: BLE001
            log.exception("insured lookup failed for %s", insured_guid)
            report.issues.append(f"Insured lookup failed: {exc}")
        if not client_name and report.insured:
            report.client_name = report.insured.get("insured_name")

    # --- Renewal engine ---
    cand_params: dict[str, str] = {"policy_number": f"eq.{pn}", "order": "renewal_event_date.desc"}
    try:
        candidates = supa.select(
            "renewal_candidates",
            columns=(
                "id,policy_number,client_name,line_of_business,renewal_event_date,"
                "expiration_date,normalized_status,policy_active,insured_active,"
                "eligibility_state,eligibility_reason,in_working_queue,premium_current"
            ),
            params=cand_params,
            limit=50,
        )
    except Exception as exc:  # noqa: BLE001
        candidates = []
        report.issues.append(f"renewal_candidates lookup failed: {exc}")

    if client_name and not candidates:
        try:
            candidates = supa.select(
                "renewal_candidates",
                columns=(
                    "id,policy_number,client_name,line_of_business,renewal_event_date,"
                    "expiration_date,normalized_status,policy_active,insured_active,"
                    "eligibility_state,eligibility_reason,in_working_queue,premium_current"
                ),
                params={"client_name": f"ilike.*{client_name}*", "policy_number": f"eq.{pn}"},
                limit=50,
            )
        except Exception:  # noqa: BLE001
            pass

    report.renewal_candidates = [_candidate_summary(c) for c in candidates]

    try:
        p85 = supa.select(
            "project_85_renewals",
            columns="id,policy_number,client_name,expiration_date,premium_current,risk_status,updated_at",
            params={"policy_number": f"eq.{pn}"},
            limit=10,
        )
    except Exception as exc:  # noqa: BLE001
        p85 = []
        report.issues.append(f"project_85_renewals lookup failed: {exc}")
    report.project_85_renewals = p85

    try:
        overrides = supa.select(
            "portal_overrides",
            columns="id,entity_type,entity_key,field_name,field_value,status,created_at,created_by",
            params={"entity_key": f"eq.{pn}", "order": "created_at.desc"},
            limit=20,
        )
    except Exception as exc:  # noqa: BLE001
        overrides = []
        report.issues.append(f"portal_overrides lookup failed: {exc}")
    report.portal_overrides = overrides

    if insured_guid:
        try:
            mappings = supa.select(
                "sync_mappings",
                columns="id,object_type,espocrm_id,nowcerts_id,source_system,active,last_synced_at",
                params={"nowcerts_id": f"eq.{insured_guid}"},
                limit=10,
            )
            report.sync_mappings = mappings
        except Exception:  # noqa: BLE001
            pass

    # --- Live insured flag ---
    insured_active_live: bool | None = None
    if insured_guid and not ams_ambiguous:
        try:
            insured_active_live = nowcerts.is_insured_active(str(insured_guid))
        except Exception:  # noqa: BLE001
            insured_active_live = None

    verdict, summary, issues, actions = _classify(
        ams_summary=report.ams,
        mirror_rows=mirror_rows,
        candidates=candidates,
        project_85=p85,
        insured_active_live=insured_active_live,
    )
    report.verdict = verdict
    report.summary = summary
    report.issues.extend(issues)
    report.recommended_actions = actions

    if line_of_business:
        report.line_of_business = line_of_business
    elif report.ams.get("line_of_business"):
        report.line_of_business = report.ams["line_of_business"]
    elif mirror_rows:
        report.line_of_business = mirror_rows[0].get("lines_of_business")

    return report
