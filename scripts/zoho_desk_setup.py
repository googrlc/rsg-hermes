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

from hermes.desk.live import LAYOUT_IDS
from hermes.desk.setup import Phase1Plan, matching_department, plan_phase1
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
            body = client.create_field(payload, module="tickets")
        except ZohoDeskClientError as exc:
            errors.append({"kind": "field", "name": item.name, "error": str(exc)[:400]})
            continue
        field_id = str(body.get("id") or "")
        created.append(
            {
                "kind": "field",
                "name": item.name,
                "id": field_id,
                "apiName": str(body.get("apiName") or ""),
            }
        )
        layout_id = LAYOUT_IDS.get("General Service")
        patch: dict[str, Any] = {}
        if allowed:
            patch["allowedValues"] = list(allowed)
            patch["sortBy"] = "userDefined"
        if mandatory:
            patch["isMandatory"] = True
        if patch and field_id and layout_id:
            try:
                client.patch_layout_field(layout_id, field_id, patch)
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
    return {
        "org_id": client.org_id,
        "department_id": department_id,
        "created": created,
        "skipped": plan.skipped,
        "errors": errors,
    }


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
