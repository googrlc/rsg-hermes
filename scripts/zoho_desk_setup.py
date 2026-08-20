#!/usr/bin/env python3
"""Apply RSG Zoho Desk Phase 1 (department, shared fields, teams).

Dry-run by default. Does not create Blueprints or workflows — those stay in
the Desk admin UI after fields exist.

Environment (OAuth with Desk scopes):
  ZOHO_CLIENT_ID / ZOHO_CLIENT_SECRET / ZOHO_REFRESH_TOKEN
  or ZOHO_DESK_* overrides
  ZOHO_DESK_ORG_ID  optional; discovered from GET /organizations when omitted

Usage:
  python scripts/zoho_desk_setup.py              # dry-run
  python scripts/zoho_desk_setup.py --apply
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from hermes.desk.fields import (
    AUTO_DRIVER_FIELDS,
    BILLING_FIELDS,
    CANCELLATION_FIELDS,
    CERTIFICATE_FIELDS,
    POLICY_CHANGE_FIELDS,
    SHARED_FIELDS,
)
from hermes.desk.live import LAYOUT_IDS
from hermes.desk.setup import Phase1Plan, matching_department, plan_phase1
from hermes.desk.spec import CATEGORIES
from hermes.desk.spec import DEPARTMENT
from hermes_integrations.zoho_desk_client import ZohoDeskClient, ZohoDeskClientError


def _pick_org(orgs: list[dict[str, Any]]) -> dict[str, Any]:
    if not orgs:
        raise ZohoDeskClientError("Desk returned no organizations for this token")
    for row in orgs:
        if str(row.get("isDefault")).lower() in {"true", "1"}:
            return row
        if row.get("isAdminInOrg") is True:
            return row
    return orgs[0]


def _agent_ids(agents: list[dict[str, Any]]) -> list[str]:
    ids: list[str] = []
    for row in agents:
        if row.get("isConfirmed") is False:
            continue
        rid = row.get("id")
        if rid:
            ids.append(str(rid))
    if ids:
        return ids
    return [str(row["id"]) for row in agents if row.get("id")]


def build_plan(client: ZohoDeskClient) -> Phase1Plan:
    if not client.org_id:
        org = _pick_org(client.list_organizations())
        client.org_id = str(org.get("id") or "")
        if not client.org_id:
            raise ZohoDeskClientError("Desk organization listing returned no id")
    departments = client.list_departments()
    fields = client.list_organization_fields("tickets")
    existing = matching_department(departments, DEPARTMENT)
    dept_id = str(existing["id"]) if existing and existing.get("id") else None
    teams = client.list_teams(department_id=dept_id) if dept_id else client.list_teams()
    return plan_phase1(
        departments=departments,
        fields=fields,
        teams=teams,
        agent_ids=_agent_ids(client.list_agents()),
    )


DEFAULT_LAYOUT_ID = LAYOUT_IDS["General Service"]
CLONE_TARGETS = {
    "shared": [lid for name, lid in LAYOUT_IDS.items() if name != "General Service"],
    "certificate": [LAYOUT_IDS["Certificate Request"]],
    "auto_driver": [LAYOUT_IDS["Auto or Driver Change"]],
    "policy_change": [LAYOUT_IDS["General Policy Change"]],
    "billing": [LAYOUT_IDS["Billing and Cancellation"]],
    "cancellation": [LAYOUT_IDS["Billing and Cancellation"]],
}


def _create_ticket_field(client: ZohoDeskClient, payload: dict[str, Any]) -> tuple[dict[str, Any], list[str] | None]:
    try:
        return client.create_field(payload, module="tickets"), None
    except ZohoDeskClientError as exc:
        if "LICENSE_ACCESS_LIMITED" in str(exc) and payload.get("type") == "Boolean":
            fallback = dict(payload)
            fallback["type"] = "Picklist"
            fallback.pop("isEncryptedField", None)
            body = client.create_field(fallback, module="tickets")
            return body, ["Yes", "No"]
        raise


def apply_plan(client: ZohoDeskClient, plan: Phase1Plan) -> dict[str, Any]:
    created: list[dict[str, str]] = []
    errors: list[dict[str, str]] = []
    department_id = plan.existing_department_id
    if plan.create_department:
        body = client.create_department(plan.create_department.payload)
        department_id = str(body.get("id") or department_id or "")
        created.append({"kind": "department", "name": plan.department_name, "id": department_id})
    for item in plan.fields:
        payload = dict(item.payload)
        allowed = payload.pop("_allowedValues", None)
        mandatory = payload.pop("_isMandatory", None)
        try:
            body, fallback_values = _create_ticket_field(client, payload)
        except ZohoDeskClientError as exc:
            errors.append({"kind": "field", "name": item.name, "error": str(exc)[:400]})
            continue
        if fallback_values:
            allowed = list(fallback_values)
            created.append(
                {
                    "kind": "field",
                    "name": item.name,
                    "id": str(body.get("id") or ""),
                    "apiName": str(body.get("apiName") or ""),
                    "note": "Boolean limit 20; created as Yes/No picklist",
                }
            )
        else:
            created.append(
                {
                    "kind": "field",
                    "name": item.name,
                    "id": str(body.get("id") or ""),
                    "apiName": str(body.get("apiName") or ""),
                }
            )
        field_id = str(body.get("id") or "")
        patch: dict[str, Any] = {}
        if allowed:
            patch["allowedValues"] = list(allowed)
            patch["sortBy"] = "userDefined"
        if mandatory:
            patch["isMandatory"] = True
        if patch and field_id:
            try:
                client.patch_layout_field(DEFAULT_LAYOUT_ID, field_id, patch)
            except ZohoDeskClientError as exc:
                errors.append(
                    {"kind": "field-layout", "name": item.name, "error": str(exc)[:400]}
                )
    for item in plan.teams:
        payload = dict(item.payload)
        if department_id:
            payload["departmentId"] = department_id
        try:
            body = client.create_team(payload)
        except ZohoDeskClientError as exc:
            errors.append({"kind": "team", "name": item.name, "error": str(exc)[:400]})
            continue
        created.append({"kind": "team", "name": item.name, "id": str(body.get("id") or "")})

    clone_errors = _clone_fields_to_layouts(client)
    errors.extend(clone_errors)
    _patch_request_category(client)
    return {
        "org_id": client.org_id,
        "department_id": department_id,
        "created": created,
        "skipped": plan.skipped,
        "errors": errors,
    }


def _label_to_field_id(client: ZohoDeskClient) -> dict[str, str]:
    out: dict[str, str] = {}
    for row in client.list_organization_fields("tickets"):
        if not row.get("isCustomField"):
            continue
        label = str(row.get("displayLabel") or "").strip().lower()
        fid = str(row.get("id") or "")
        if label and fid:
            out[label] = fid
    return out


def _clone_fields_to_layouts(client: ZohoDeskClient) -> list[dict[str, str]]:
    errors: list[dict[str, str]] = []
    ids = _label_to_field_id(client)
    groups = (
        ("shared", SHARED_FIELDS),
        ("certificate", CERTIFICATE_FIELDS),
        ("auto_driver", AUTO_DRIVER_FIELDS),
        ("policy_change", POLICY_CHANGE_FIELDS),
        ("billing", BILLING_FIELDS),
        ("cancellation", CANCELLATION_FIELDS),
    )
    seen: set[str] = set()
    for group, fields in groups:
        targets = [tid for tid in CLONE_TARGETS[group] if tid != DEFAULT_LAYOUT_ID]
        if not targets:
            continue
        for spec in fields:
            label = spec.label.strip().lower()
            fid = ids.get(label)
            if not fid or fid in seen:
                continue
            seen.add(fid)
            for target in targets:
                try:
                    client.patch_layout_field(DEFAULT_LAYOUT_ID, fid, {"isMandatory": bool(spec.mandatory)})
                    client.clone_field_to_layouts(DEFAULT_LAYOUT_ID, fid, [target])
                except ZohoDeskClientError as exc:
                    if "FieldAlreadyExists" in str(exc):
                        continue
                    errors.append(
                        {"kind": "clone", "name": f"{spec.label} -> {target}", "error": str(exc)[:400]}
                    )
    return errors


def _patch_request_category(client: ZohoDeskClient) -> None:
    ids = _label_to_field_id(client)
    fid = ids.get("request category")
    if not fid:
        return
    client.patch_layout_field(
        DEFAULT_LAYOUT_ID,
        fid,
        {
            "allowedValues": list(CATEGORIES),
            "defaultValue": "General Service",
            "sortBy": "userDefined",
            "isMandatory": True,
        },
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Create missing Desk objects (default is dry-run)")
    args = parser.parse_args(argv)
    try:
        client = ZohoDeskClient()
        plan = build_plan(client)
    except ZohoDeskClientError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    summary = {
        "mode": "apply" if args.apply else "dry-run",
        "org_id": client.org_id,
        "department": plan.department_name,
        "would_create": [
            {"kind": item.kind, "name": item.name} for item in plan.actions
        ],
        "skipped": plan.skipped,
    }
    if not args.apply:
        print(json.dumps(summary, indent=2))
        return 0
    result = apply_plan(client, plan)
    summary["result"] = result
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
