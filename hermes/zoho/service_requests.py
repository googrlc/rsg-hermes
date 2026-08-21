"""Zoho CRM Service_Requests pack — constants, CSV, payloads, Catalyst map.

Zoho CRM is the system of record. This module does not talk to Zoho Desk and
does not write a Supabase service-desk table.
"""

from __future__ import annotations

import csv
import re
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
DOCS_ZOHO = REPO_ROOT / "docs" / "zoho"
FIELDS_CSV = DOCS_ZOHO / "fields_service_requests.csv"
CATALYST_MAP_CSV = DOCS_ZOHO / "catalyst_field_map.csv"
VOCAB_CSV = DOCS_ZOHO / "picklists_hermes_vocab.csv"

MODULE_API_NAME = "Service_Requests"
MODULE_SINGULAR = "Service Request"
MODULE_PLURAL = "Service Requests"

# Catalyst currently stores work on standard Cases. This pack does not
# silently reuse that module.
CASES_MODULE = "Cases"
POLICIES_MODULE = "Policies"
RENEWALS_MODULE = "Renewals"
CLAIMS_MODULE = "Claims"

REQUEST_TYPES: tuple[str, ...] = (
    "Certificate Request",
    "Policy Change",
    "Add Vehicle",
    "Remove Vehicle",
    "Add Driver",
    "Remove Driver",
    "Billing Question",
    "Claims Question",
    "Coverage Question",
    "Renewal Service",
    "Cancellation",
    "Reinstatement",
    "ID Card Request",
    "Mortgagee Change",
    "Document Request",
    "Other",
)

STATUSES: tuple[str, ...] = ("New", "In Progress", "Waiting", "Completed")
PRIORITIES: tuple[str, ...] = ("Low", "Standard", "High")
TEAMS: tuple[str, ...] = ("Personal Lines", "Commercial", "Unassigned")

OPEN_STATUSES: frozenset[str] = frozenset({"New", "In Progress", "In progress"})
WAITING_STATUSES: frozenset[str] = frozenset({"Waiting"})
COMPLETED_STATUSES: frozenset[str] = frozenset({"Completed"})

# Fields Zoho creates with the module — never POST these.
SYSTEM_API_NAMES: frozenset[str] = frozenset(
    {"Name", "Owner", "Created_Time", "Modified_Time", "id", "Created_By", "Modified_By"}
)

# Catalyst insurance-shaped fields that may already exist on Cases.
CASES_INSURANCE_FIELDS: tuple[str, ...] = (
    "Desk_Stage",
    "Request_Type",
    "Policy_Number",
    "Client_Name",
    "Account_Name",
    "Owner_Name",
    "Due_Date",
    "Service_Time",
    "Completion_Time",
    "Age_Days",
    "Completed_At",
    "Closed_Time",
    "Next_Step",
    "Contact_Name",
    "Subject",
    "Description",
    "Overdue",
)

CSV_HEADERS: tuple[str, ...] = (
    "Module",
    "Display_Label",
    "API_Name",
    "Data_Type",
    "Length",
    "Mandatory",
    "Unique",
    "External_ID",
    "Default_Value",
    "Picklist_Source",
    "Sync_Direction",
    "Hermes_Column",
    "Notes",
)

FORMULA_SERVICE_TIME = (
    "If(IsEmpty(${Closed_Date}), Datecomp(Now(), ${Open_Date}), "
    "Datecomp(${Closed_Date}, ${Open_Date})) / 60"
)
FORMULA_COMPLETION_TIME = (
    "If(IsEmpty(${Closed_Date}), '', Datecomp(${Closed_Date}, ${Open_Date}) / 60)"
)
FORMULA_AGE_DAYS = (
    "If(IsEmpty(${Open_Date}), 0, Datecomp(If(IsEmpty(${Closed_Date}), Now(), "
    "${Closed_Date}), ${Open_Date}) / 1440)"
)
FORMULA_OVERDUE = (
    "And(Not(IsEmpty(${Due_Date})), ${Due_Date} < Now(), ${Status} != 'Completed')"
)

# Keyword → Request_Type for the email button. First match wins.
_TYPE_HINTS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("certificate", "coi", "cert of insurance", "additional insured"), "Certificate Request"),
    (("id card", "insurance card", "proof of insurance"), "ID Card Request"),
    (("mortgagee", "lienholder", "loss payee"), "Mortgagee Change"),
    (("reinstate",), "Reinstatement"),
    (("cancel", "cancellation"), "Cancellation"),
    (("renewal", "x-date", "xdate"), "Renewal Service"),
    (("add vehicle", "new vehicle", "add a vehicle"), "Add Vehicle"),
    (("remove vehicle", "delete vehicle", "drop vehicle"), "Remove Vehicle"),
    (("add driver", "new driver"), "Add Driver"),
    (("remove driver", "delete driver", "drop driver"), "Remove Driver"),
    (("policy change", "endorsement", "coverage change"), "Policy Change"),
    (("billing", "invoice", "payment", "premium finance"), "Billing Question"),
    (("claim", "fnol", "loss run"), "Claims Question"),
    (("coverage question", "am i covered", "what's covered"), "Coverage Question"),
    (("document", "policy copy", "dec page", "declarations"), "Document Request"),
)


def load_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def load_field_pack(path: Path | None = None) -> list[dict[str, str]]:
    rows = load_csv(path or FIELDS_CSV)
    if not rows:
        raise ValueError(f"empty field pack: {path or FIELDS_CSV}")
    missing = [h for h in CSV_HEADERS if h not in rows[0]]
    if missing:
        raise ValueError(f"{path or FIELDS_CSV} missing columns: {missing}")
    return rows


def vocab_values(list_key: str, path: Path | None = None) -> list[str]:
    rows = load_csv(path or VOCAB_CSV)
    values = [r["value"] for r in rows if r.get("vocab_key") == list_key]
    if not values:
        raise ValueError(f"no picklist values for {list_key}")
    return values


def logical_api_name(api_name: str) -> str:
    """Strip org suffixes Zoho may append (__c / __s / _1)."""
    name = (api_name or "").strip()
    name = re.sub(r"(__c|__s)$", "", name, flags=re.I)
    return name


def names_match(live_api: str, logical: str) -> bool:
    a = logical_api_name(live_api).lower()
    b = logical_api_name(logical).lower()
    return a == b or a.replace("_", "") == b.replace("_", "")


def picklist_for_row(row: dict[str, str]) -> list[str] | None:
    source = (row.get("Picklist_Source") or "").strip()
    if not source:
        return None
    if ":" in source:
        _, key = source.split(":", 1)
        return vocab_values(key.strip())
    return None


def field_create_payload(row: dict[str, str]) -> dict[str, Any] | None:
    """Zoho CRM v8 settings/fields body for one pack row, or None to skip."""
    api = (row.get("API_Name") or "").strip()
    data_type = (row.get("Data_Type") or "").strip()
    if api in SYSTEM_API_NAMES or (row.get("Sync_Direction") or "").strip() == "System":
        return None
    if data_type.startswith("Lookup"):
        target = lookup_target_module(row) or ""
        payload: dict[str, Any] = {
            "field_label": row["Display_Label"],
            "data_type": "lookup",
            "lookup": {
                "module": {"api_name": target},
                "display_label": MODULE_PLURAL,
            },
        }
        if (row.get("Mandatory") or "").upper() == "Y":
            payload["required"] = True
        return payload

    zoho_type, extra = _zoho_data_type(data_type, row)
    payload = {
        "field_label": row["Display_Label"],
        "data_type": zoho_type,
    }
    if extra:
        payload.update(extra)
    length = (row.get("Length") or "").strip()
    if length and zoho_type in {"text", "textarea", "integer", "bigint"}:
        try:
            payload["length"] = int(length.split(".")[0])
        except ValueError:
            pass
    if (row.get("Mandatory") or "").upper() == "Y":
        payload["required"] = True
    if (row.get("Unique") or "").upper() == "Y" and zoho_type in {"text", "autonumber"}:
        payload["unique"] = {"case_sensitive": False}
    default = (row.get("Default_Value") or "").strip()
    if default and zoho_type == "picklist":
        payload["default_value"] = {"value": default}
    values = picklist_for_row(row)
    if values:
        payload["pick_list_values"] = [{"display_value": v} for v in values]
    return payload


def _zoho_data_type(data_type: str, row: dict[str, str]) -> tuple[str, dict[str, Any]]:
    dt = data_type.lower()
    if dt == "single line":
        return "text", {}
    if dt == "multi line":
        return "textarea", {}
    if dt == "picklist":
        return "picklist", {}
    if dt == "datetime":
        return "datetime", {}
    if dt == "date":
        return "date", {}
    if dt == "checkbox":
        return "boolean", {}
    if dt == "auto number":
        start = 1001
        prefix = "SR-"
        default = (row.get("Default_Value") or "").strip()
        m = re.match(r"([A-Za-z0-9_-]+?)(\d+)$", default)
        if m:
            prefix, start = m.group(1), int(m.group(2))
        return "autonumber", {"auto_number": {"prefix": prefix, "start_number": start}}
    if dt.startswith("formula"):
        api = row.get("API_Name") or ""
        expression, return_type = formula_for(api)
        return "formula", {
            "formula": {"expression": expression, "return_type": return_type},
        }
    raise ValueError(f"unsupported Data_Type {data_type!r} for {row.get('API_Name')}")


def formula_for(api_name: str) -> tuple[str, str]:
    logical = logical_api_name(api_name)
    if logical == "Service_Time":
        return FORMULA_SERVICE_TIME, "double"
    if logical == "Completion_Time":
        return FORMULA_COMPLETION_TIME, "double"
    if logical == "Age_Days":
        return FORMULA_AGE_DAYS, "double"
    if logical == "Overdue":
        return FORMULA_OVERDUE, "boolean"
    raise ValueError(f"no formula registered for {api_name}")


def lookup_target_module(row: dict[str, str]) -> str | None:
    data_type = (row.get("Data_Type") or "").strip()
    if not data_type.startswith("Lookup"):
        return None
    return data_type[data_type.find("(") + 1 : data_type.rfind(")")].strip()


def existing_field(live_fields: list[dict[str, Any]], logical: str, label: str) -> dict[str, Any] | None:
    want_label = (label or "").strip().lower()
    for field in live_fields:
        api = str(field.get("api_name") or "")
        flabel = str(field.get("field_label") or field.get("display_label") or "").strip().lower()
        if names_match(api, logical) or (want_label and flabel == want_label):
            return field
    return None


def module_exists(live_modules: list[dict[str, Any]], api_name: str) -> dict[str, Any] | None:
    for mod in live_modules:
        if names_match(str(mod.get("api_name") or ""), api_name):
            return mod
        plural = str(mod.get("plural_label") or "").strip().lower()
        if plural == MODULE_PLURAL.lower() and names_match(api_name, MODULE_API_NAME):
            return mod
    return None


def plan_actions(
    pack: list[dict[str, str]],
    *,
    live_modules: list[dict[str, Any]] | None = None,
    live_fields: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Idempotent create/skip plan. No network."""
    live_modules = live_modules or []
    live_fields = live_fields or []
    existing_module = module_exists(live_modules, MODULE_API_NAME)
    actions: list[dict[str, Any]] = []
    if existing_module:
        actions.append(
            {
                "kind": "module",
                "action": "skip",
                "api_name": existing_module.get("api_name") or MODULE_API_NAME,
                "id": existing_module.get("id"),
                "reason": "module already exists",
            }
        )
    else:
        closest = _closest_module_name(live_modules)
        actions.append(
            {
                "kind": "module",
                "action": "create",
                "api_name": MODULE_API_NAME,
                "singular_label": MODULE_SINGULAR,
                "plural_label": MODULE_PLURAL,
                "closest_existing": closest,
            }
        )

    for row in pack:
        api = (row.get("API_Name") or "").strip()
        label = (row.get("Display_Label") or "").strip()
        if field_create_payload(row) is None:
            actions.append(
                {
                    "kind": "field",
                    "action": "skip",
                    "api_name": api,
                    "reason": "system field — do not create",
                }
            )
            continue
        target = lookup_target_module(row)
        if target and not module_exists(live_modules, target) and live_modules:
            actions.append(
                {
                    "kind": "field",
                    "action": "skip",
                    "api_name": api,
                    "reason": f"lookup target {target} not present live",
                }
            )
            continue
        found = existing_field(live_fields, api, label)
        if found:
            actions.append(
                {
                    "kind": "field",
                    "action": "skip",
                    "api_name": api,
                    "live_api_name": found.get("api_name") or api,
                    "id": found.get("id"),
                    "reason": "field already exists",
                }
            )
            continue
        payload = field_create_payload(row)
        actions.append(
            {
                "kind": "field",
                "action": "create",
                "api_name": api,
                "data_type": row.get("Data_Type"),
                "payload": payload,
            }
        )
    return actions


def _closest_module_name(live_modules: list[dict[str, Any]]) -> str | None:
    want = logical_api_name(MODULE_API_NAME).lower().replace("_", "")
    for mod in live_modules:
        api = logical_api_name(str(mod.get("api_name") or "")).lower().replace("_", "")
        if api == want or "servicerequest" in api:
            return str(mod.get("api_name"))
    return None


def suggest_request_type(subject: str = "", body: str = "") -> str:
    blob = f"{subject} {body}".lower()
    for needles, label in _TYPE_HINTS:
        if any(n in blob for n in needles):
            return label
    return "Other"


def status_to_desk_stage(status: str | None) -> str:
    """Catalyst bundle compares Desk_Stage to 'In progress' (lowercase p)."""
    raw = (status or "").strip()
    if raw.lower() == "in progress":
        return "In progress"
    return raw or "New"


def _lookup_name(value: Any) -> str:
    if isinstance(value, dict):
        return str(
            value.get("name")
            or value.get("Account_Name")
            or value.get("Full_Name")
            or value.get("Last_Name")
            or ""
        )
    if value is None:
        return ""
    return str(value)


def record_get(record: dict[str, Any], *logical_names: str) -> Any:
    """Read a field from a Zoho record, allowing __c / __s suffixes."""
    for logical in logical_names:
        if logical in record and record[logical] not in (None, ""):
            return record[logical]
        for key, value in record.items():
            if names_match(str(key), logical) and value not in (None, ""):
                return value
    return None


def catalyst_row(record: dict[str, Any]) -> dict[str, Any]:
    """Shape a Service_Requests record the way the Catalyst worklist already reads Cases."""
    status = record_get(record, "Status", "Desk_Stage") or "New"
    owner = record_get(record, "Owner")
    account = record_get(record, "Account_Name")
    contact = record_get(record, "Contact_Name")
    client = record_get(record, "Client_Name") or _lookup_name(account)
    closed = record_get(record, "Closed_Date", "Closed_Time", "Completed_At")
    overdue = record_get(record, "Overdue")
    if isinstance(overdue, str):
        overdue = overdue.strip().lower() in {"true", "1", "yes"}
    request_type = record_get(record, "Request_Type") or ""
    return {
        "id": str(record.get("id") or ""),
        "module": MODULE_API_NAME,
        "Subject": record_get(record, "Subject", "Name") or "",
        "Description": record_get(record, "Description") or "",
        "Request_Type": request_type,
        "Request_Type_Label": request_type,
        "Desk_Stage": status_to_desk_stage(str(status)),
        "Status": str(status),
        "Priority": record_get(record, "Priority") or "Standard",
        "Policy_Number": record_get(record, "Policy_Number") or "",
        "Client_Name": client,
        "Account_Name": _lookup_name(account),
        "Owner_Name": _lookup_name(owner),
        "Due_Date": record_get(record, "Due_Date") or "",
        "Service_Time": record_get(record, "Service_Time"),
        "Completion_Time": record_get(record, "Completion_Time"),
        "Age_Days": record_get(record, "Age_Days"),
        "Overdue": bool(overdue),
        "Completed_At": closed or "",
        "Closed_Time": closed or "",
        "Next_Step": record_get(record, "Next_Step") or "",
        "Contact_Name": _lookup_name(contact),
        "Carrier": record_get(record, "Carrier") or "",
        "Line_Of_Business": record_get(record, "Line_Of_Business", "Line_of_Business") or "",
        "Open_Date": record_get(record, "Open_Date") or "",
        "Last_Activity": record_get(record, "Last_Activity", "Modified_Time") or "",
    }


def matches_view(row: dict[str, Any], *, view: str, stage: str = "", window_days: int | None = None) -> bool:
    stage_val = (row.get("Desk_Stage") or row.get("Status") or "").strip()
    status_l = stage_val.lower()
    view_l = (view or "desk").strip().lower()
    if view_l in {"waiting"}:
        return status_l == "waiting"
    if view_l in {"completed"}:
        return status_l == "completed"
    if view_l in {"overdue"}:
        return bool(row.get("Overdue")) and status_l != "completed"
    # default open worklist (desk / open / "")
    if stage:
        return status_to_desk_stage(stage_val).lower() == status_to_desk_stage(stage).lower()
    return status_l in {"new", "in progress"}


def matches_query(row: dict[str, Any], q: str = "", type_filter: str = "") -> bool:
    if type_filter and str(row.get("Request_Type") or "") != type_filter:
        return False
    needle = (q or "").strip().lower()
    if not needle:
        return True
    blob = " ".join(
        str(row.get(k) or "")
        for k in ("Subject", "Client_Name", "Policy_Number", "Description", "Request_Type")
    ).lower()
    return needle in blob


def load_catalyst_map(path: Path | None = None) -> list[dict[str, str]]:
    return load_csv(path or CATALYST_MAP_CSV)
