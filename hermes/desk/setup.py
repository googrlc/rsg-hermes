"""Build an idempotent Zoho Desk Phase-1 apply plan from the RSG spec.

The CLI ``scripts/zoho_desk_setup.py`` executes this plan. Field types follow
Desk's create-field vocabulary (PickList, Date Time, Website).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from hermes.desk.fields import ALL_FIELDS, Field
from hermes.desk.spec import DEPARTMENT, DEPARTMENT_ALIASES, TEAMS

DESK_TYPES = {
    "Text": "Text",
    "Email": "Email",
    "Phone": "Phone",
    "Picklist": "PickList",
    "Date": "Date",
    "DateTime": "Date Time",
    "Boolean": "Boolean",
    "Number": "Number",
    "Decimal": "Decimal",
    "URL": "Website",
    "Textarea": "Textarea",
}


@dataclass(frozen=True)
class PlannedCreate:
    kind: str
    name: str
    payload: dict[str, Any]
    reason: str = "missing"


@dataclass
class Phase1Plan:
    department_name: str = DEPARTMENT
    create_department: PlannedCreate | None = None
    existing_department_id: str | None = None
    fields: list[PlannedCreate] = field(default_factory=list)
    teams: list[PlannedCreate] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)

    @property
    def actions(self) -> list[PlannedCreate]:
        out: list[PlannedCreate] = []
        if self.create_department:
            out.append(self.create_department)
        out.extend(self.fields)
        out.extend(self.teams)
        return out


def field_payload(spec: Field) -> dict[str, Any]:
    desk_type = DESK_TYPES[spec.data_type]
    payload: dict[str, Any] = {
        "displayLabel": spec.label,
        "type": desk_type,
        "isMandatory": spec.mandatory,
        "showToHelpCenter": False,
        "isEncryptedField": spec.sensitive,
    }
    if spec.length and desk_type in {"Text", "Number", "Decimal", "Email", "Phone", "Website"}:
        payload["maxLength"] = str(spec.length)
    if spec.picklist_values:
        payload["allowedValues"] = [{"value": value} for value in spec.picklist_values]
    if spec.notes:
        payload["toolTip"] = spec.notes
        payload["toolTipType"] = "icon"
    return payload


def matching_department(
    departments: list[dict[str, Any]],
    department_name: str = DEPARTMENT,
) -> dict[str, Any] | None:
    """Return the live department, including the RSG alias for Agency Service."""
    aliases = {department_name.strip().lower()}
    aliases.update(alias.strip().lower() for alias in DEPARTMENT_ALIASES)
    for row in departments:
        for key in ("name", "displayLabel"):
            value = str(row.get(key) or "").strip().lower()
            if value in aliases:
                return row
    return None


def plan_phase1(
    *,
    departments: list[dict[str, Any]],
    fields: list[dict[str, Any]],
    teams: list[dict[str, Any]],
    agent_ids: list[str],
    department_name: str = DEPARTMENT,
) -> Phase1Plan:
    plan = Phase1Plan(department_name=department_name)
    existing = matching_department(departments, department_name)
    if existing and existing.get("id"):
        plan.existing_department_id = str(existing["id"])
        live_name = str(existing.get("name") or department_name)
        plan.skipped.append(f"department:{live_name}")
    else:
        if not agent_ids:
            raise ValueError(
                "Cannot create department Agency Service without at least one Desk agent id"
            )
        plan.create_department = PlannedCreate(
            kind="department",
            name=department_name,
            payload={
                "name": department_name,
                "nameInCustomerPortal": department_name,
                "description": "RSG service cases. Queues are ticket fields and teams, not extra departments.",
                "isVisibleInCustomerPortal": True,
                "isAssignToTeamEnabled": True,
                "associatedAgentIds": agent_ids,
            },
        )

    existing_labels = {
        str(row.get("displayLabel") or row.get("name") or "").strip().lower()
        for row in fields
    }
    seen: set[str] = set()
    for spec in ALL_FIELDS:
        key = spec.label.strip().lower()
        if key in seen:
            continue
        seen.add(key)
        if key in existing_labels:
            plan.skipped.append(f"field:{spec.label}")
            continue
        plan.fields.append(
            PlannedCreate(kind="field", name=spec.label, payload=field_payload(spec))
        )

    existing_teams = {str(row.get("name") or "").strip().lower() for row in teams}
    for name in TEAMS:
        if name.strip().lower() in existing_teams:
            plan.skipped.append(f"team:{name}")
            continue
        payload: dict[str, Any] = {
            "name": name,
            "description": f"RSG Desk team: {name}",
        }
        if plan.existing_department_id:
            payload["departmentId"] = plan.existing_department_id
        plan.teams.append(PlannedCreate(kind="team", name=name, payload=payload))
    return plan
