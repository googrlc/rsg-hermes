"""Idempotent Zoho CRM apply plan for Service_Requests.

Dry-run by default. ``--apply`` is the only write path. Never talks to Desk.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from hermes.zoho.service_requests import (
    CASES_INSURANCE_FIELDS,
    CASES_MODULE,
    CLAIMS_MODULE,
    MODULE_API_NAME,
    MODULE_PLURAL,
    MODULE_SINGULAR,
    POLICIES_MODULE,
    RENEWALS_MODULE,
    field_create_payload,
    load_field_pack,
    module_exists,
    names_match,
    plan_actions,
)

SETTINGS_API_VERSION = "v8"


class ApplyError(RuntimeError):
    """Raised when live CRM inspection or a write fails."""


@dataclass
class LiveInventory:
    modules: list[dict[str, Any]]
    cases_fields: list[dict[str, Any]]
    service_request_fields: list[dict[str, Any]]
    auth_ok: bool
    auth_error: str | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def module_api_names(self) -> list[str]:
        return [str(m.get("api_name") or "") for m in self.modules if m.get("api_name")]

    def has(self, api_name: str) -> bool:
        return module_exists(self.modules, api_name) is not None


def settings_url(client: Any, path: str) -> str:
    dc = getattr(client, "data_center", "com") or "com"
    return f"https://www.zohoapis.{dc}/crm/{SETTINGS_API_VERSION}{path}"


def list_modules(client: Any) -> list[dict[str, Any]]:
    body = client._get(settings_url(client, "/settings/modules"))
    rows = body.get("modules") if isinstance(body, dict) else None
    if not isinstance(rows, list):
        return []
    return [r for r in rows if isinstance(r, dict)]


def list_fields(client: Any, module: str) -> list[dict[str, Any]]:
    body = client._get(
        settings_url(client, "/settings/fields"),
        params={"module": module},
    )
    rows = body.get("fields") if isinstance(body, dict) else None
    if not isinstance(rows, list):
        return []
    return [r for r in rows if isinstance(r, dict)]


def inspect_live(client: Any) -> LiveInventory:
    """List modules + Cases / Service_Requests fields. Auth errors are captured."""
    try:
        modules = list_modules(client)
    except Exception as exc:  # noqa: BLE001 — surface to the operator
        return LiveInventory(
            modules=[],
            cases_fields=[],
            service_request_fields=[],
            auth_ok=False,
            auth_error=str(exc)[:500],
            notes=["Live inspect failed. Dry-run of the pack still prints create payloads."],
        )

    notes: list[str] = []
    cases_fields: list[dict[str, Any]] = []
    sr_fields: list[dict[str, Any]] = []
    if module_exists(modules, CASES_MODULE):
        try:
            cases_fields = list_fields(client, CASES_MODULE)
        except Exception as exc:  # noqa: BLE001
            notes.append(f"Cases fields: {exc}"[:300])
        present = {
            logical_api_name_from_live(f)
            for f in cases_fields
        }
        insurance = [name for name in CASES_INSURANCE_FIELDS if name in present]
        if insurance:
            notes.append(
                "Cases already has insurance-shaped fields "
                + ", ".join(insurance)
                + ". Still creating Service_Requests as specified. "
                "Catalyst currently points at Cases — retarget; do not dual-write Desk."
            )
        else:
            notes.append(
                "Cases exists live but the Catalyst insurance fields were not all "
                "present under those API names (suffixes possible). Still creating Service_Requests."
            )
    else:
        notes.append("Cases module not returned by settings/modules.")

    sr = module_exists(modules, MODULE_API_NAME)
    if sr:
        notes.append(
            f"Service_Requests already exists as {sr.get('api_name')}. Field create is idempotent."
        )
        try:
            sr_fields = list_fields(client, str(sr.get("api_name") or MODULE_API_NAME))
        except Exception as exc:  # noqa: BLE001
            notes.append(f"Service_Requests fields: {exc}"[:300])
    else:
        closest = None
        for mod in modules:
            api = str(mod.get("api_name") or "")
            if "service" in api.lower() and "request" in api.lower():
                closest = api
                break
        if closest:
            notes.append(
                f"No module named {MODULE_API_NAME}. Closest live name: {closest}. "
                "Will try to create Service_Requests; if Zoho rejects the name, stop and document it — "
                "do not silently use Cases."
            )
        else:
            notes.append(f"{MODULE_API_NAME} does not exist live; plan is to create it.")

    for name, label in (
        (POLICIES_MODULE, "Policies"),
        (RENEWALS_MODULE, "Renewals"),
        ("Renewal_Events", "Renewal_Events"),
        ("AMS_Write_Queue", "AMS_Write_Queue"),
        (CLAIMS_MODULE, "Claims"),
    ):
        if module_exists(modules, name):
            notes.append(f"{label} exists live.")
        else:
            notes.append(
                f"{label} was not in settings/modules "
                f"({'do not create Claims' if name == CLAIMS_MODULE else 'lookup will be skipped until that pack is applied'})."
            )

    return LiveInventory(
        modules=modules,
        cases_fields=cases_fields,
        service_request_fields=sr_fields,
        auth_ok=True,
        notes=notes,
    )


def logical_api_name_from_live(field: dict[str, Any]) -> str:
    from hermes.zoho.service_requests import logical_api_name

    return logical_api_name(str(field.get("api_name") or ""))


def build_plan(inventory: LiveInventory | None = None) -> list[dict[str, Any]]:
    pack = load_field_pack()
    live_modules = inventory.modules if inventory else []
    live_fields = inventory.service_request_fields if inventory else []
    return plan_actions(pack, live_modules=live_modules, live_fields=live_fields)


def module_create_payload() -> dict[str, Any]:
    return {
        "modules": [
            {
                "plural_label": MODULE_PLURAL,
                "singular_label": MODULE_SINGULAR,
            }
        ]
    }


def apply_plan(client: Any, actions: list[dict[str, Any]], *, apply: bool) -> dict[str, Any]:
    """Create module + fields. ``apply=False`` returns the plan only."""
    created: list[dict[str, Any]] = []
    skipped = [a for a in actions if a.get("action") == "skip"]
    errors: list[dict[str, str]] = []
    module_api = MODULE_API_NAME

    if not apply:
        return {
            "apply": False,
            "module_api_name": module_api,
            "created": created,
            "skipped": skipped,
            "pending": [a for a in actions if a.get("action") == "create"],
            "errors": errors,
        }

    for action in actions:
        if action.get("action") != "create":
            continue
        try:
            if action["kind"] == "module":
                body = client._post(
                    settings_url(client, "/settings/modules"),
                    module_create_payload(),
                )
                created.append({"kind": "module", "response": _summarize(body)})
                live_name = _created_module_api_name(body)
                if live_name:
                    module_api = live_name
                    if not names_match(live_name, MODULE_API_NAME):
                        errors.append(
                            {
                                "kind": "module",
                                "error": (
                                    f"Zoho created {live_name!r} instead of {MODULE_API_NAME!r}. "
                                    "Do not silently treat Cases as Service_Requests. Update the pack "
                                    "to the live API name."
                                ),
                            }
                        )
            elif action["kind"] == "field":
                payload = action.get("payload") or field_create_payload(
                    {"API_Name": action.get("api_name"), "Display_Label": action.get("api_name"),
                     "Data_Type": action.get("data_type") or "Single Line",
                     "Length": "", "Mandatory": "N", "Unique": "N",
                     "Default_Value": "", "Picklist_Source": "", "Sync_Direction": ""}
                )
                if not payload:
                    continue
                # Overdue formula may be rejected — fall back to checkbox.
                body = _create_field(client, module_api, payload)
                created.append(
                    {
                        "kind": "field",
                        "api_name": action.get("api_name"),
                        "response": _summarize(body),
                    }
                )
        except Exception as exc:  # noqa: BLE001
            errors.append(
                {
                    "kind": str(action.get("kind")),
                    "api_name": str(action.get("api_name") or MODULE_API_NAME),
                    "error": str(exc)[:500],
                }
            )
    return {
        "apply": True,
        "module_api_name": module_api,
        "created": created,
        "skipped": skipped,
        "pending": [],
        "errors": errors,
    }


def _create_field(client: Any, module_api: str, payload: dict[str, Any]) -> Any:
    try:
        return client._post(
            settings_url(client, "/settings/fields") + f"?module={module_api}",
            {"fields": [payload]},
        )
    except Exception as exc:
        if payload.get("data_type") == "formula" and (
            payload.get("formula") or {}
        ).get("return_type") == "boolean":
            fallback = {
                "field_label": payload.get("field_label"),
                "data_type": "boolean",
            }
            return client._post(
                settings_url(client, "/settings/fields") + f"?module={module_api}",
                {"fields": [fallback]},
            )
        raise exc


def _created_module_api_name(body: Any) -> str | None:
    if not isinstance(body, dict):
        return None
    data = body.get("modules") or body.get("data")
    if isinstance(data, list) and data and isinstance(data[0], dict):
        details = data[0].get("details") or data[0]
        api = details.get("api_name") or data[0].get("api_name")
        if api:
            return str(api)
    return None


def _summarize(body: Any, *, limit: int = 400) -> Any:
    if isinstance(body, dict):
        slim = {k: body[k] for k in list(body)[:8]}
        text = str(slim)
        return text[:limit]
    return str(body)[:limit]
