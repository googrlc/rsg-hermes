"""Proactive case creation — open a case when work has quietly slipped.

The scans already find the problems (renewals coming due, cases past their date,
tasks going stale); until now they only reported them, so acting on a finding
still depended on somebody reading a message and remembering. This gives findings
somewhere to land.

Guardrails, because the failure mode here is well evidenced. The agency's CRM
previously acquired five identical auto-generated cases against real clients, and
they had to be deleted by hand. At the time of writing there are 73 renewals
inside 120 days with no case — a detector that opened one for each would repeat
that mistake at fifteen times the scale, and the result is noise nobody trusts.

So:

* **Dry-run by default.** Writing requires an explicit commit.
* **A hard per-run cap.** Default 10. Hitting the cap is reported, never silent —
  a truncated run that looks complete is worse than one that says it stopped.
* **A tight default horizon.** 30 days, not the full 120 the data supports.
* **Urgency ordering**, so a capped run does the soonest work rather than
  whatever the database happened to return first.
* **Idempotency delegated** to the existing renewal-event identity key, so a
  second run cannot double-open a case the first one created.

Only renewals open cases. A stalled case or an overdue task already HAS a case —
opening another would be noise, so those are reported for a human to act on.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover
    from hermes.sync.supabase_client import SupabaseClient

log = logging.getLogger(__name__)

DEFAULT_HORIZON_DAYS = 30
DEFAULT_LIMIT = 10
STALLED_CASE_DAYS = 14
OVERDUE_TASK_GRACE_DAYS = 3

CASES_TABLE = "agency_crm_cases"
TASKS_TABLE = "agency_crm_tasks"
CANDIDATES_TABLE = "renewal_candidates"


def _today() -> date:
    return datetime.utcnow().date()


# ── Detectors ────────────────────────────────────────────────────────────────

def find_renewals_without_case(
    supa: "SupabaseClient", *, horizon_days: int = DEFAULT_HORIZON_DAYS,
) -> list[dict[str, Any]]:
    """Eligible renewals inside the horizon that nobody has opened a case for.

    Includes recently expired events (back 30 days): a renewal that sailed past
    its date with no case is the most urgent kind, not the least, and excluding
    it would hide exactly the failure this is meant to catch.
    """
    today = _today()
    rows = supa.select(
        CANDIDATES_TABLE, columns="*",
        params={
            "expiration_date": f"gte.{today - timedelta(days=30)}",
            "order": "expiration_date.asc",
        },
        limit=500,
    )
    horizon = today + timedelta(days=horizon_days)

    # One read of open cases beats a query per candidate.
    open_cases = supa.select(
        CASES_TABLE, columns="insured_name,insured_database_id,status,case_type",
        params={"status": "eq.open"}, limit=1000,
    )
    covered_names = {
        (c.get("insured_name") or "").strip().lower()
        for c in open_cases if c.get("case_type") == "renewal"
    }
    covered_ids = {
        str(c.get("insured_database_id")) for c in open_cases
        if c.get("insured_database_id") and c.get("case_type") == "renewal"
    }

    out: list[dict[str, Any]] = []
    for r in rows:
        exp = r.get("expiration_date")
        if not exp:
            continue
        try:
            exp_date = date.fromisoformat(str(exp)[:10])
        except ValueError:
            continue
        if exp_date > horizon:
            continue
        # Ineligible events are excluded upstream by the eligibility engine;
        # respect that rather than second-guessing it here.
        if (r.get("eligibility_state") or "eligible") != "eligible":
            continue
        name = (r.get("client_name") or "").strip().lower()
        if name in covered_names or str(r.get("insured_id")) in covered_ids:
            continue
        if not (r.get("insured_id") and r.get("policy_lineage_id") and r.get("renewal_event_date")):
            continue  # cannot build the identity key; not safely idempotent

        out.append({
            "signal": "renewal_without_case",
            "days_until": (exp_date - _today()).days,
            "client_name": r.get("client_name"),
            "insured_id": r.get("insured_id"),
            "policy_lineage_id": r.get("policy_lineage_id"),
            "renewal_event_date": r.get("renewal_event_date"),
            "policy_number": r.get("policy_number"),
            "nowcerts_policy_guid": r.get("nowcerts_policy_guid"),
            "line_of_business": r.get("line_of_business"),
            "segment": r.get("segment"),
            "expiration_date": str(exp)[:10],
        })

    out.sort(key=lambda f: f["days_until"])  # soonest (and most overdue) first
    return out


def find_stalled_cases(
    supa: "SupabaseClient", *, idle_days: int = STALLED_CASE_DAYS,
) -> list[dict[str, Any]]:
    """Open cases past their due date with nothing completed recently.

    Reported, not acted on: a case already exists, so the useful signal is
    "this one stopped moving", not "open another".
    """
    cases = supa.select(CASES_TABLE, columns="*", params={"status": "eq.open"}, limit=500)
    cutoff = datetime.utcnow() - timedelta(days=idle_days)
    findings: list[dict[str, Any]] = []

    for c in cases:
        due = c.get("due_at")
        overdue = bool(due and str(due)[:10] < _today().isoformat())
        tasks = supa.select(
            TASKS_TABLE, columns="status,completed_at,updated_at",
            params={"case_id": f"eq.{c.get('id')}"}, limit=200,
        )
        stamps = [t.get("completed_at") or t.get("updated_at") for t in tasks]
        stamps = [s for s in stamps if s]
        last = max(stamps) if stamps else c.get("created_at")
        idle = bool(last and str(last)[:19] < cutoff.isoformat()[:19])
        if not (overdue or idle):
            continue
        open_tasks = [t for t in tasks if t.get("status") not in ("completed", "cancelled")]
        findings.append({
            "signal": "stalled_case",
            "case_id": c.get("id"),
            "case_number": c.get("case_number"),
            "case_type": c.get("case_type"),
            "insured_name": c.get("insured_name"),
            "overdue": overdue,
            "idle_since": last,
            "open_tasks": len(open_tasks),
        })
    return findings


def find_overdue_tasks(
    supa: "SupabaseClient", *, grace_days: int = OVERDUE_TASK_GRACE_DAYS,
) -> list[dict[str, Any]]:
    """Open tasks past due beyond the grace period."""
    cutoff = (datetime.utcnow() - timedelta(days=grace_days)).isoformat()
    rows = supa.select(
        TASKS_TABLE, columns="*",
        params={"due_at": f"lt.{cutoff}", "order": "due_at.asc"}, limit=200,
    )
    return [{
        "signal": "overdue_task",
        "task_id": t.get("id"),
        "title": t.get("title"),
        "due_at": t.get("due_at"),
        "case_id": t.get("case_id"),
        "assigned_to_email": t.get("assigned_to_email"),
    } for t in rows if t.get("status") not in ("completed", "cancelled")]


# ── Action ───────────────────────────────────────────────────────────────────

def scan(
    supa: "SupabaseClient",
    *,
    horizon_days: int = DEFAULT_HORIZON_DAYS,
    limit: int = DEFAULT_LIMIT,
    commit: bool = False,
    owner_email: str | None = None,
) -> dict[str, Any]:
    """Find slipped work and, when committing, open the renewal cases for it.

    Returns what it found, what it opened, and — importantly — what it did not
    get to because of the cap.
    """
    from hermes.renewals import cases as RC

    renewals = find_renewals_without_case(supa, horizon_days=horizon_days)
    stalled = find_stalled_cases(supa)
    overdue = find_overdue_tasks(supa)

    selected = renewals[:limit]
    deferred = renewals[limit:]

    opened: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []

    if commit:
        for f in selected:
            try:
                case, created = RC.create_case(
                    supa,
                    insured_id=str(f["insured_id"]),
                    policy_lineage_id=str(f["policy_lineage_id"]),
                    renewal_event_date=str(f["renewal_event_date"]),
                    policy_number=f.get("policy_number"),
                    nowcerts_policy_guid=f.get("nowcerts_policy_guid"),
                    client_name=f.get("client_name"),
                    line_of_business=f.get("line_of_business"),
                    segment=f.get("segment"),
                    owner_email=owner_email,
                )
                opened.append({
                    "client_name": f.get("client_name"),
                    "case_number": case.get("case_number"),
                    "created": created,          # False = already existed
                    "days_until": f["days_until"],
                })
            except Exception as exc:  # noqa: BLE001
                # One bad candidate must not abort the run; the rest are still
                # worth opening.
                log.exception("proactive case failed for %s", f.get("client_name"))
                failed.append({"client_name": f.get("client_name"), "error": str(exc)})

    return {
        "committed": commit,
        "horizon_days": horizon_days,
        "limit": limit,
        "found": {
            "renewals_without_case": len(renewals),
            "stalled_cases": len(stalled),
            "overdue_tasks": len(overdue),
        },
        "opened": opened,
        "opened_count": len([o for o in opened if o.get("created")]),
        "already_existed": len([o for o in opened if not o.get("created")]),
        "failed": failed,
        # Never let a capped run read as a complete one.
        "deferred_count": len(deferred),
        "deferred": [{"client_name": d.get("client_name"), "days_until": d["days_until"]}
                     for d in deferred[:20]],
        "candidates": selected if not commit else [],
        "stalled_cases": stalled,
        "overdue_tasks": overdue,
    }


def format_report(result: dict[str, Any]) -> str:
    """Plain-language summary for the console or a chat post."""
    f = result["found"]
    mode = "COMMITTED" if result["committed"] else "DRY RUN — nothing written"
    lines = [
        f"Proactive case scan ({mode})",
        f"  horizon {result['horizon_days']}d · cap {result['limit']} per run",
        "",
        f"  renewals with no case : {f['renewals_without_case']}",
        f"  stalled cases         : {f['stalled_cases']}",
        f"  overdue tasks         : {f['overdue_tasks']}",
    ]
    if result["committed"]:
        lines += ["", f"  opened {result['opened_count']} case(s)"
                      + (f", {result['already_existed']} already existed"
                         if result["already_existed"] else "")]
        for o in result["opened"]:
            if o.get("created"):
                lines.append(f"    · {o['client_name']} ({o['case_number']}) — {o['days_until']}d")
        if result["failed"]:
            lines.append(f"  {len(result['failed'])} failed:")
            for x in result["failed"]:
                lines.append(f"    ! {x['client_name']}: {x['error']}")
    else:
        for c in result.get("candidates", [])[:20]:
            lines.append(f"    would open · {c['client_name']} — {c['days_until']}d")

    if result["deferred_count"]:
        lines += ["", f"  {result['deferred_count']} more past the cap — re-run to continue."]
    return "\n".join(lines)
