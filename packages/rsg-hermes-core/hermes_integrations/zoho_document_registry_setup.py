"""Build Zoho v8 payloads for the Document_Registry custom module.

Runtime CRM writes live in ``zoho_document_registry.py``. This module only
plans / applies *settings* (create the module and its fields). Default is
dry-run; ``--apply`` needs ``ZohoCRM.settings.modules.CREATE`` and
``ZohoCRM.settings.fields.CREATE``.

Lookups use display_label ``Nextcloud Files`` so Account, Lead, Policy, Deal,
and Renewal each get that related list. Filed_Documents is not created here.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

from hermes_integrations.zoho_document_registry import DEFAULT_MODULE

REPO_ROOT = Path(__file__).resolve().parents[3]
FIELDS_CSV = REPO_ROOT / "docs" / "zoho" / "fields_document_registry.csv"
PICKLISTS_CSV = REPO_ROOT / "docs" / "zoho" / "picklists_hermes_vocab.csv"

SINGULAR_LABEL = "Document"
PLURAL_LABEL = "Document Registry"
DISPLAY_FIELD_LABEL = "Document Name"
RELATED_LIST_LABEL = "Nextcloud Files"

_CSV_TO_ZOHO_TYPE = {
    "Single Line": "text",
    "Picklist": "picklist",
    "Date": "date",
    "URL": "website",
    "Number": "integer",
    "Lookup (Accounts)": "lookup",
    "Lookup (Leads)": "lookup",
    "Lookup (Policies)": "lookup",
    "Lookup (Deals)": "lookup",
    "Lookup (Renewals)": "lookup",
    "Lookup (Users)": "userlookup",
}

_LOOKUP_TARGETS = {
    "Lookup (Accounts)": "Accounts",
    "Lookup (Leads)": "Leads",
    "Lookup (Policies)": "Policies",
    "Lookup (Deals)": "Deals",
    "Lookup (Renewals)": "Renewals",
}


def _repo_csv(path: Path) -> Path:
    if path.is_file():
        return path
    alt = Path.cwd() / "docs" / "zoho" / path.name
    return alt


def load_picklists(path: Path | None = None) -> dict[str, list[str]]:
    csv_path = _repo_csv(path or PICKLISTS_CSV)
    out: dict[str, list[str]] = {}
    if not csv_path.is_file():
        return out
    with csv_path.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            key = (row.get("vocab_key") or "").strip()
            value = (row.get("value") or "").strip()
            if key and value:
                out.setdefault(key, []).append(value)
    return out


def load_field_rows(path: Path | None = None) -> list[dict[str, str]]:
    csv_path = _repo_csv(path or FIELDS_CSV)
    with csv_path.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def module_create_payload(profile_ids: list[str]) -> dict[str, Any]:
    if not profile_ids:
        raise ValueError("at least one Zoho profile id is required to create a module")
    return {
        "modules": [
            {
                "plural_label": PLURAL_LABEL,
                "singular_label": SINGULAR_LABEL,
                "api_name": DEFAULT_MODULE,
                "profiles": [{"id": pid} for pid in profile_ids],
                "display_field": {
                    "field_label": DISPLAY_FIELD_LABEL,
                    "data_type": "text",
                },
            }
        ]
    }


def field_create_payload(
    row: dict[str, str],
    *,
    picklists: dict[str, list[str]] | None = None,
    lookup_module_ids: dict[str, str] | None = None,
) -> dict[str, Any] | None:
    """Zoho settings/fields body for one CSV row. None = skip (display Name)."""
    api_name = (row.get("API_Name") or "").strip()
    if api_name == "Name":
        return None  # created with the module as display_field
    label = (row.get("Display_Label") or api_name).strip()
    csv_type = (row.get("Data_Type") or "").strip()
    data_type = _CSV_TO_ZOHO_TYPE.get(csv_type)
    if not data_type:
        raise ValueError(f"unsupported Document_Registry field type {csv_type!r} for {api_name}")

    field: dict[str, Any] = {
        "field_label": label,
        "data_type": data_type,
    }
    length = (row.get("Length") or "").strip()
    if length and data_type in ("text", "website", "integer"):
        parsed = int(length.split(".")[0])
        if data_type == "integer":
            parsed = min(parsed, 9)
        field["length"] = parsed

    if data_type == "picklist":
        source = (row.get("Picklist_Source") or "").strip()
        vocab_key = source.split(":")[-1] if ":" in source else source
        values = (picklists or {}).get(vocab_key) or []
        if not values:
            raise ValueError(f"no picklist values for {api_name} ({vocab_key})")
        field["pick_list_values"] = [
            {"display_value": v, "actual_value": v} for v in values
        ]

    if csv_type in _LOOKUP_TARGETS:
        target = _LOOKUP_TARGETS[csv_type]
        lookup_mod: dict[str, Any] = {"api_name": target}
        ids = lookup_module_ids or {}
        if ids.get(target):
            lookup_mod["id"] = ids[target]
        field["lookup"] = {
            "module": lookup_mod,
            "display_label": RELATED_LIST_LABEL,
        }

    return {"fields": [field], "api_name": api_name, "csv_type": csv_type}


def plan_fields(
    *,
    existing_api_names: set[str],
    existing_modules: set[str],
    picklists: dict[str, list[str]] | None = None,
    lookup_module_ids: dict[str, str] | None = None,
    rows: list[dict[str, str]] | None = None,
) -> list[dict[str, Any]]:
    """Return create/skip actions for each CSV field."""
    picklists = picklists if picklists is not None else load_picklists()
    steps: list[dict[str, Any]] = []
    for row in rows or load_field_rows():
        payload = field_create_payload(
            row, picklists=picklists, lookup_module_ids=lookup_module_ids
        )
        api_name = (row.get("API_Name") or "").strip()
        if payload is None:
            steps.append({"action": "skip", "api_name": api_name, "reason": "display field on module"})
            continue
        if api_name in existing_api_names:
            steps.append({"action": "skip", "api_name": api_name, "reason": "already exists"})
            continue
        csv_type = payload["csv_type"]
        if csv_type in _LOOKUP_TARGETS:
            target = _LOOKUP_TARGETS[csv_type]
            # Accounts and Leads are standard; custom modules may be missing.
            if target not in ("Accounts", "Leads") and target not in existing_modules:
                steps.append(
                    {
                        "action": "skip",
                        "api_name": api_name,
                        "reason": f"{target} module is not in this org yet",
                    }
                )
                continue
        steps.append(
            {
                "action": "create_field",
                "api_name": api_name,
                "payload": {"fields": payload["fields"]},
            }
        )
    return steps
