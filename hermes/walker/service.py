"""Walker service layer -- all business logic for the on-demand renewal API.

No scheduler, no timers. Every call is request-driven. Classification reuses
the existing renewal_classifier.classify_risk and cadence.classify_segment as
library calls, not scheduled jobs.
"""
from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any

from hermes.integrations.supabase_client import SupabaseClient
from hermes.operations.renewal_classifier import (
    classify_risk,
    _parse_date,
    _as_float,
    _best_commission_by_policy,
)
from hermes.renewals.cadence import classify_segment
from hermes.renewals import config as renewal_config

log = logging.getLogger(__name__)

# EspoCRM Opportunity custom fields (the retain layer).
# 8 fields -- the 2 timer fields (cNextTouchCode, cNextTouchDate) are deliberately
# skipped: nothing fires on a clock in the Walker model.
OPP_C_RENEWAL_SEGMENT = "cRenewalSegment"
OPP_C_RENEWAL_OWNER = "cRenewalOwner"
OPP_C_COMPLEXITY_FLAGS = "cComplexityFlags"
OPP_C_RENEWAL_DECISION = "cRenewalDecision"
OPP_C_TOUCH_LOG = "cTouchLog"
OPP_C_HANDOFF_NOTES = "cHandoffNotes"
OPP_C_DAY1_SENT_AT = "cDay1SentAt"
OPP_C_LAST_CLIENT_CONTACT_DATE = "cLastClientContactDate"

RENEWAL_OPP_FIELDS = (
    OPP_C_RENEWAL_SEGMENT,
    OPP_C_RENEWAL_OWNER,
    OPP_C_COMPLEXITY_FLAGS,
    OPP_C_RENEWAL_DECISION,
    OPP_C_TOUCH_LOG,
    OPP_C_HANDOFF_NOTES,
    OPP_C_DAY1_SENT_AT,
    OPP_C_LAST_CLIENT_CONTACT_DATE,
)


class WalkerService:
    """On-demand renewal data + CRM writes. One instance per request is fine."""

    def __init__(self, supa=None, espo=None, nowcerts=None) -> None:
        self._supa = supa
        self._espo = espo
        self._nowcerts = nowcerts

    # -- lazy singletons --------------------------------------------------

    @property
    def supa(self):
        if self._supa is None:
            from hermes.integrations.supabase_client import SupabaseClient
            self._supa = SupabaseClient()
        return self._supa

    @property
    def espo(self):
        if self._espo is None:
            from hermes.core.client import EspoClient
            self._espo = EspoClient()
        return self._espo

    @property
    def nowcerts(self):
        if self._nowcerts is None:
            from hermes.sync.nowcerts_client import NowCertsClient
            self._nowcerts = NowCertsClient()
        return self._nowcerts

    # -- freshness stamp --------------------------------------------------

    def _last_refresh_stamp(self) -> str:
        """Return the most recent updated_at from the canonical mirror."""
        try:
            rows = self.supa.select(
                "project_85_renewals",
                columns="updated_at",
                params={"order": "updated_at.desc", "limit": "1"},
                limit=1,
            )
            if rows and rows[0].get("updated_at"):
                return str(rows[0]["updated_at"])
        except Exception as exc:
            log.warning("Could not read freshness stamp: %s", exc)
        return "unknown"


    def _resolve_opportunity_id(self, record_id: str) -> str:
        """Resolve a record ID to an EspoCRM Opportunity ID.

        Accepts:
        - An EspoCRM Opportunity ID (direct lookup)
        - A Supabase project_85_renewals UUID (lookup by id)
        - A policy number (contains match on policy_number in Supabase)
        Creates an Opportunity if none exists.
        """
        # 1. Try direct EspoCRM Opportunity lookup
        try:
            opp = self.espo.get(f"Opportunity/{record_id}", params={"select": "id,name"})
            if isinstance(opp, dict) and opp.get("id"):
                return opp["id"]
        except Exception:
            pass

        # 2. Try Supabase lookup — by id (exact) then by policy_number (contains)
        renewal_row = None
        try:
            rows = self.supa.select(
                "project_85_renewals",
                columns="id,client_name,expiration_date,premium_current,policy_number",
                params={"id": f"eq.{record_id}"},
                limit=1,
            )
            if rows:
                renewal_row = rows[0]
        except Exception:
            pass

        if not renewal_row:
            try:
                rows = self.supa.select(
                    "project_85_renewals",
                    columns="id,client_name,expiration_date,premium_current,policy_number",
                    params={"policy_number": f"ilike.%{record_id}%"},
                    limit=1,
                )
                if rows:
                    renewal_row = rows[0]
            except Exception:
                pass

        if renewal_row:
            account_name = renewal_row.get("client_name")
            if account_name and account_name != "Unknown client":
                # 3a. Exact match on Opportunity name
                try:
                    opp = self.espo.find_one_by_field(
                        "Opportunity", "name", account_name,
                        select="id,name",
                    )
                    if opp and opp.get("id"):
                        return opp["id"]
                except Exception:
                    pass
                # 3b. Contains match on Opportunity name
                try:
                    body = self.espo.get("Opportunity", params={
                        "maxSize": 1,
                        "select": "id,name",
                        "where": [{"type": "contains", "attribute": "name", "value": account_name}],
                    })
                    if isinstance(body, dict):
                        items = body.get("list", []) if isinstance(body.get("list"), list) else []
                        if items and items[0].get("id"):
                            return items[0]["id"]
                except Exception:
                    pass
                # 3c. No Opportunity found — create one
                try:
                    opp = self.espo.create("Opportunity", {
                        "name": account_name,
                        "amount": _as_float(renewal_row.get("premium_current")) or 0,
                        "stage": "Renewal",
                    })
                    if isinstance(opp, dict) and opp.get("id"):
                        log.info("Walker: created Opportunity %s for '%s'",
                                 opp["id"], account_name)
                        return opp["id"]
                except Exception as exc:
                    log.warning("Walker: could not create Opportunity: %s", exc)

        raise ValueError(
            f"Could not resolve or create an Opportunity for record ID '{record_id}'. "
            f"The renewal may have 'Unknown client' or no matching record in the mirror."
        )

    # -- READS ------------------------------------------------------------

    def get_queue(self, days: int = 60) -> dict[str, Any]:
        """Renewals inside N days, classified at request time."""
        today = date.today()
        horizon = (today + timedelta(days=days)).isoformat()

        renewals = self.supa.select(
            "project_85_renewals",
            columns=(
                "id,policy_number,client_name,expiration_date,premium_current,"
                "premium_renewal,risk_status,last_contact_date,increase_percentage,"
                "updated_at"
            ),
            params={"expiration_date": f"lte.{horizon}", "order": "expiration_date.asc"},
            limit=500,
        )
        commissions = self.supa.select(
            "crm_commissions",
            columns="policy_number,policy_status,premium,expiration_date",
            limit=2000,
        )
        by_policy = _best_commission_by_policy(commissions)

        # Account-level premium for segment classification.
        account_premium: dict[str, float] = {}
        for r in renewals:
            name = (r.get("client_name") or "").strip().lower()
            prem = _as_float(r.get("premium_current")) or 0.0
            account_premium[name] = account_premium.get(name, 0.0) + prem

        items = []
        for r in renewals:
            comm = by_policy.get(r.get("policy_number"))
            policy_status = comm.get("policy_status") if comm else None
            exp = r.get("expiration_date")
            exp_date = _parse_date(exp)
            days_until = (exp_date - today).days if exp_date else None

            risk = classify_risk(
                policy_status=policy_status,
                expiration_date=exp,
                today=today,
                increase_percentage=_as_float(r.get("increase_percentage")),
            )

            segment = classify_segment(
                {"line_of_business": "", "expiration_date": exp},
                account_active_premium=account_premium.get(
                    (r.get("client_name") or "").strip().lower()
                ),
            )

            items.append({
                "id": r.get("id"),
                "client": r.get("client_name"),
                "policy_number": r.get("policy_number"),
                "line": None,
                "carrier": None,
                "premium": _as_float(r.get("premium_current")),
                "days_out": days_until,
                "segment": segment,
                "risk": risk,
                "owner": None,
                "flags": [],
                "last_touch": r.get("last_contact_date"),
            })

        return {
            "data_as_of": self._last_refresh_stamp(),
            "days_window": days,
            "count": len(items),
            "items": items,
        }

    def get_renewal_detail(self, renewal_id: str) -> dict[str, Any]:
        """Single-client truth pulled LIVE from NowCerts through Hermes.

        Accepts either an EspoCRM Renewal ID or a Supabase project_85_renewals ID.
        """
        renewal = {}
        account_name = ""

        # Try EspoCRM Renewal entity first
        try:
            renewal = self.espo.get(f"{renewal_config.RENEWAL_ENTITY}/{renewal_id}")
            if not isinstance(renewal, dict):
                renewal = {}
        except Exception:
            pass

        # Fall back to Supabase renewal lookup
        if not renewal:
            try:
                rows = self.supa.select(
                    "project_85_renewals",
                    columns="id,client_name,expiration_date,premium_current,policy_number,risk_status",
                    params={"id": f"eq.{renewal_id}"},
                    limit=1,
                )
                if rows:
                    r = rows[0]
                    renewal = {
                        "id": r.get("id"),
                        "name": r.get("client_name"),
                        "accountName": r.get("client_name"),
                        "expiration_date": r.get("expiration_date"),
                        "current_premium": r.get("premium_current"),
                        "policy_number": r.get("policy_number"),
                    }
            except Exception:
                pass

        if not renewal:
            raise ValueError(f"Renewal {renewal_id} not found in EspoCRM or Supabase")

        account_name = renewal.get("accountName") or renewal.get("name") or ""
        nowcerts_data: dict[str, Any] = {}
        try:
            insureds = self.nowcerts.fetch_insureds(max_pages=5)
            matched = _match_nowcerts_insured(insureds, account_name)
            if matched:
                policies = self.nowcerts.fetch_policies(max_pages=5)
                insured_id = matched.get("databaseId") or matched.get("DatabaseId")
                client_policies = [
                    p for p in policies
                    if _policy_belongs_to(p, insured_id, account_name)
                ] if policies else []
                nowcerts_data = {
                    "insured": _clean_insured(matched),
                    "policies": [_clean_policy(p) for p in client_policies],
                    "account_total_premium": _account_total(client_policies),
                }
        except Exception as exc:
            log.warning("NowCerts live read failed for %s: %s", renewal_id, exc)
            nowcerts_data = {"error": str(exc)}

        opportunity = {}
        try:
            opp = self.espo.find_one_by_field(
                "Opportunity", "name", account_name,
                select=",".join(["id", "name", "stage", "amount"] + list(RENEWAL_OPP_FIELDS)),
            )
            if opp:
                opportunity = opp
        except Exception as exc:
            log.warning("EspoCRM Opportunity read failed: %s", exc)

        return {
            "data_as_of": datetime.now(timezone.utc).isoformat() + " (live)",
            "renewal_id": renewal_id,
            "client": account_name,
            "renewal": _clean_renewal(renewal),
            "nowcerts": nowcerts_data,
            "opportunity": opportunity,
        }

    def search(self, query: str) -> dict[str, Any]:
        """Lookup by name or policy number. Finds name variants via ilike."""
        q = query.strip()
        if not q:
            return {"data_as_of": self._last_refresh_stamp(), "query": q, "results": []}

        by_policy = self.supa.select(
            "project_85_renewals",
            columns="id,policy_number,client_name,expiration_date,premium_current,risk_status",
            params={"policy_number": f"eq.{q}"},
            limit=20,
        )
        by_name = self.supa.select(
            "project_85_renewals",
            columns="id,policy_number,client_name,expiration_date,premium_current,risk_status",
            params={"client_name": f"ilike.%{q}%"},
            limit=20,
        )

        seen: set[str] = set()
        results = []
        for r in by_policy + by_name:
            rid = r.get("id") or r.get("policy_number")
            if rid in seen:
                continue
            seen.add(rid)
            results.append({
                "id": r.get("id"),
                "client": r.get("client_name"),
                "policy_number": r.get("policy_number"),
                "expiration_date": r.get("expiration_date"),
                "premium": _as_float(r.get("premium_current")),
                "risk_status": r.get("risk_status"),
            })

        return {
            "data_as_of": self._last_refresh_stamp(),
            "query": q,
            "count": len(results),
            "results": results,
        }

    def get_quiet_lapse(self) -> dict[str, Any]:
        """Expired terms with NO successor term -- the silent-churn catcher."""
        today = date.today().isoformat()
        renewals = self.supa.select(
            "project_85_renewals",
            columns="id,policy_number,client_name,expiration_date,premium_current,risk_status",
            params={"expiration_date": f"lt.{today}", "order": "expiration_date.desc"},
            limit=500,
        )
        commissions = self.supa.select(
            "crm_commissions",
            columns="policy_number,policy_status,expiration_date",
            limit=2000,
        )

        lapses = []
        for r in renewals:
            has_successor = False
            for c in commissions:
                if c.get("policy_number") != r.get("policy_number"):
                    continue
                c_exp = _parse_date(c.get("expiration_date"))
                r_exp = _parse_date(r.get("expiration_date"))
                if c_exp and r_exp and c_exp > r_exp:
                    status = (c.get("policy_status") or "").lower()
                    if status not in ("expired", "cancelled", "canceled", "non-renewed"):
                        has_successor = True
                        break
            if not has_successor:
                lapses.append({
                    "id": r.get("id"),
                    "client": r.get("client_name"),
                    "policy_number": r.get("policy_number"),
                    "expired": r.get("expiration_date"),
                    "premium": _as_float(r.get("premium_current")),
                    "risk_status": r.get("risk_status"),
                })

        return {
            "data_as_of": self._last_refresh_stamp(),
            "count": len(lapses),
            "items": lapses,
        }

    def get_scoreboard(self) -> dict[str, Any]:
        """Retention %, renewed/lost premium."""
        renewals = self.supa.select(
            "project_85_renewals",
            columns="id,policy_number,client_name,expiration_date,premium_current,risk_status",
            limit=2000,
        )

        renewed_premium = 0.0
        lost_premium = 0.0
        active_premium = 0.0

        for r in renewals:
            risk = r.get("risk_status") or ""
            prem = _as_float(r.get("premium_current")) or 0.0
            if risk == "RENEWED":
                renewed_premium += prem
            elif risk == "LAPSED":
                lost_premium += prem
            elif risk in ("SAFE", "AT_RISK", "CRITICAL"):
                active_premium += prem

        decided = renewed_premium + lost_premium
        retention_pct = round((renewed_premium / decided * 100), 1) if decided > 0 else None

        return {
            "data_as_of": self._last_refresh_stamp(),
            "total_renewals": len(renewals),
            "retention_pct": retention_pct,
            "renewed_premium": round(renewed_premium, 2),
            "lost_premium": round(lost_premium, 2),
            "active_premium": round(active_premium, 2),
        }

    # -- WRITES (all land on the EspoCRM Opportunity) ---------------------

    def post_touch(self, record_id: str, body: dict[str, Any]) -> dict[str, Any]:
        """Log a touch on the Opportunity. Accepts renewal ID or Opportunity ID."""
        opportunity_id = self._resolve_opportunity_id(record_id)
        now = datetime.now(timezone.utc).isoformat()
        entry = {
            "timestamp": now,
            "actor": body.get("actor", "unknown"),
            "channel": body.get("channel", "manual"),
            "note": body.get("note", ""),
        }
        opp = self.espo.get(
            f"Opportunity/{opportunity_id}",
            params={"select": f"id,{OPP_C_TOUCH_LOG},{OPP_C_LAST_CLIENT_CONTACT_DATE}"},
        )
        current_log = _parse_touch_log(
            opp.get(OPP_C_TOUCH_LOG) if isinstance(opp, dict) else None
        )
        current_log.append(entry)

        payload = {
            OPP_C_TOUCH_LOG: _format_touch_log(current_log),
            OPP_C_LAST_CLIENT_CONTACT_DATE: now[:10],
        }
        self.espo.update("Opportunity", opportunity_id, payload)
        return {"ok": True, "opportunity_id": opportunity_id, "touch_logged": now}

    def patch_worksheet(self, record_id: str, body: dict[str, Any]) -> dict[str, Any]:
        """Update worksheet state. Accepts renewal ID or Opportunity ID."""
        opportunity_id = self._resolve_opportunity_id(record_id)
        allowed = body.get("fields", {})
        safe = {
            k: v for k, v in allowed.items()
            if k.startswith("c") or k in ("stage", "amount", "description")
        }
        if not safe:
            raise ValueError("No valid fields to update")
        self.espo.update("Opportunity", opportunity_id, safe)
        return {"ok": True, "opportunity_id": opportunity_id, "updated_fields": list(safe.keys())}

    def post_flag(self, record_id: str, body: dict[str, Any]) -> dict[str, Any]:
        """Add a complexity flag. Accepts renewal ID or Opportunity ID."""
        opportunity_id = self._resolve_opportunity_id(record_id)
        flag_text = body.get("flag", "")
        if not flag_text:
            raise ValueError("flag text required")
        opp = self.espo.get(
            f"Opportunity/{opportunity_id}",
            params={"select": f"id,{OPP_C_COMPLEXITY_FLAGS}"},
        )
        current = (
            opp.get(OPP_C_COMPLEXITY_FLAGS) or "" if isinstance(opp, dict) else ""
        )
        flags = [f.strip() for f in current.split("|") if f.strip()]
        if flag_text not in flags:
            flags.append(flag_text)
        self.espo.update("Opportunity", opportunity_id, {OPP_C_COMPLEXITY_FLAGS: "|".join(flags)})
        return {"ok": True, "opportunity_id": opportunity_id, "flags": flags}

    def post_handoff(self, record_id: str, body: dict[str, Any]) -> dict[str, Any]:
        """Set handoff notes. Accepts renewal ID or Opportunity ID."""
        opportunity_id = self._resolve_opportunity_id(record_id)
        note = body.get("note", "")
        if not note:
            raise ValueError("handoff note required")
        self.espo.update("Opportunity", opportunity_id, {OPP_C_HANDOFF_NOTES: note})
        return {"ok": True, "opportunity_id": opportunity_id}

    def post_outcome(self, record_id: str, body: dict[str, Any]) -> dict[str, Any]:
        """Set the renewal decision + pipeline stage. Accepts renewal ID or Opportunity ID."""
        opportunity_id = self._resolve_opportunity_id(record_id)
        decision = body.get("decision", "")
        stage = body.get("stage")
        payload: dict[str, Any] = {OPP_C_RENEWAL_DECISION: decision}
        if stage:
            payload["stage"] = stage
        self.espo.update("Opportunity", opportunity_id, payload)
        return {"ok": True, "opportunity_id": opportunity_id, "decision": decision, "stage": stage}


# == helpers ================================================================

def _match_nowcerts_insured(insureds: list[dict], account_name: str) -> dict | None:
    """Find the NowCerts insured matching the EspoCRM account name."""
    target = (account_name or "").strip().lower()
    if not target:
        return None
    for ins in insureds:
        for key in ("commercialName", "CommercialName", "firstName", "FirstName"):
            val = ins.get(key)
            if val and str(val).strip().lower() == target:
                return ins
        full = " ".join(
            str(ins.get(k, ""))
            for k in ("commercialName", "CommercialName", "firstName", "FirstName", "lastName", "LastName")
        ).lower()
        if target in full:
            return ins
    return None


def _policy_belongs_to(policy: dict, insured_id: str | None, account_name: str) -> bool:
    """Check if a NowCerts policy belongs to a given insured."""
    if insured_id:
        pid = policy.get("insuredDatabaseId") or policy.get("InsuredDatabaseId") or policy.get("databaseId")
        if pid and str(pid) == str(insured_id):
            return True
    name = (account_name or "").lower()
    pname = (policy.get("insuredName") or policy.get("InsuredName") or "").lower()
    return bool(name) and name in pname


def _clean_insured(ins: dict) -> dict:
    """Extract the fields the Walker needs from a NowCerts insured record."""
    return {
        "database_id": ins.get("databaseId") or ins.get("DatabaseId"),
        "commercial_name": ins.get("commercialName") or ins.get("CommercialName"),
        "first_name": ins.get("firstName") or ins.get("FirstName"),
        "last_name": ins.get("lastName") or ins.get("LastName"),
        "email": ins.get("email") or ins.get("Email"),
        "phone": ins.get("phone") or ins.get("Phone"),
    }


def _clean_policy(p: dict) -> dict:
    """Extract the fields the Walker needs from a NowCerts policy record."""
    return {
        "number": p.get("number") or p.get("Number"),
        "line_of_business": p.get("lineOfBusiness") or p.get("LineOfBusiness"),
        "carrier": p.get("companyName") or p.get("CompanyName") or p.get("carrier"),
        "effective_date": p.get("effectiveDate") or p.get("EffectiveDate"),
        "expiration_date": p.get("expirationDate") or p.get("ExpirationDate"),
        "premium": _as_float(p.get("premium") or p.get("Premium")),
        "status": p.get("policyStatus") or p.get("PolicyStatus") or p.get("status"),
    }


def _account_total(policies: list[dict]) -> float:
    """Sum active policy premiums for the account."""
    total = 0.0
    for p in policies:
        status = str(p.get("status") or p.get("policyStatus") or "").lower()
        if status in ("expired", "cancelled", "canceled", "non-renewed"):
            continue
        total += _as_float(p.get("premium")) or 0.0
    return round(total, 2)


def _clean_renewal(renewal: dict) -> dict:
    """Extract the key fields from an EspoCRM Renewal record."""
    return {
        "id": renewal.get("id"),
        "name": renewal.get("name"),
        "account_id": renewal.get("accountId"),
        "account_name": renewal.get("accountName"),
        "carrier": renewal.get("carrier"),
        "line_of_business": renewal.get("line_of_business"),
        "expiration_date": renewal.get("expiration_date"),
        "current_premium": _as_float(renewal.get("current_premium")),
        "renewal_premium": _as_float(renewal.get("renewal_premium")),
        "pipeline_stage": renewal.get("pipeline_stage") or renewal.get("stage"),
        "disposition": renewal.get("disposition"),
    }


def _parse_touch_log(raw: Any) -> list[dict]:
    """Parse the cTouchLog field (JSON string or list) into a list of dicts."""
    if isinstance(raw, list):
        return raw
    if isinstance(raw, str) and raw:
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, list) else []
        except (json.JSONDecodeError, ValueError):
            return []
    return []


def _format_touch_log(log: list[dict]) -> str:
    """Serialize the touch log back to a JSON string for EspoCRM."""
    return json.dumps(log, default=str)
