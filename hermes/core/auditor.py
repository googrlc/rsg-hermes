"""Data quality checks and lightweight KPI helpers over EspoCRM."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from hermes.core.client import EspoClient, EspoClientError


@dataclass
class KPIReport:
    label: str
    value: Any
    detail: str | None = None


@dataclass
class CRMReadinessCheck:
    name: str
    ok: bool
    critical: bool
    message: str
    detail: str | None = None


@dataclass
class CRMReadinessReport:
    checks: list[CRMReadinessCheck]

    @property
    def failed_critical(self) -> int:
        return sum(1 for check in self.checks if check.critical and not check.ok)

    @property
    def ok(self) -> bool:
        return self.failed_critical == 0

    def format_lines(self) -> list[str]:
        lines = ["Hermes CRM readiness"]
        for check in self.checks:
            marker = "OK" if check.ok else "FAIL"
            suffix = f" — {check.detail}" if check.detail else ""
            lines.append(f"{marker}: {check.message}{suffix}")
        if self.ok:
            lines.append("READY: Hermes can authenticate, read core CRM data, and load metadata.")
        else:
            lines.append(f"NOT READY: {self.failed_critical} critical check(s) failed.")
        return lines


def count_entity(client: EspoClient, entity: str, where: list[dict[str, Any]] | None = None) -> int:
    """Return total from a list request (Espo returns `total` on collection GET)."""
    params: dict[str, Any] = {"maxSize": 1}
    if where:
        params["where"] = where
    body = client.get(entity, params=params)
    if isinstance(body, dict) and "total" in body:
        return int(body["total"])
    raise EspoClientError(f"Unexpected list response for {entity}")


def missing_required_fields(
    client: EspoClient,
    entity: str,
    field: str,
    *,
    max_scan: int = 200,
) -> list[dict[str, Any]]:
    """Return records where `field` is empty (best-effort for coordinator QA)."""
    body = client.get(
        entity,
        params={
            "maxSize": max_scan,
            "where": [
                {"type": "isNull", "attribute": field},
            ],
        },
    )
    if not isinstance(body, dict):
        return []
    rows = body.get("list") or []
    return [r for r in rows if isinstance(r, dict)]


def quick_kpis(client: EspoClient) -> list[KPIReport]:
    """Starter KPIs; extend with your entity names and filters."""
    reports: list[KPIReport] = []
    for entity in ("Account", "Contact", "Opportunity"):
        try:
            n = count_entity(client, entity)
            reports.append(KPIReport(entity, n))
        except EspoClientError as e:
            reports.append(KPIReport(entity, None, str(e)))
    return reports


def _readiness_user_name(body: Any) -> str:
    if isinstance(body, dict):
        user = body.get("user")
        if isinstance(user, dict):
            return str(user.get("userName") or user.get("name") or user.get("id") or "unknown")
        return str(body.get("userName") or body.get("name") or body.get("id") or "unknown")
    return "unknown"


def crm_readiness(client: EspoClient, entities: tuple[str, ...] = ("Account", "Contact", "Opportunity")) -> CRMReadinessReport:
    """Run a non-mutating readiness gate for CRM officer work.

    This checks the real paths Hermes needs for reports and safe data operations:
    API auth, core entity list reads, and metadata availability. It intentionally
    does not create/update records; write commands remain single-attempt to avoid
    duplicates when a network failure happens after EspoCRM receives the payload.
    """
    checks: list[CRMReadinessCheck] = []

    try:
        ping = client.ping()
        user_name = _readiness_user_name(ping)
        checks.append(
            CRMReadinessCheck(
                name="auth",
                ok=True,
                critical=True,
                message=f"Authenticated as {user_name}",
            )
        )
    except EspoClientError as e:
        checks.append(
            CRMReadinessCheck(
                name="auth",
                ok=False,
                critical=True,
                message=f"Authentication failed: {e}",
            )
        )

    for entity in entities:
        try:
            total = count_entity(client, entity)
            checks.append(
                CRMReadinessCheck(
                    name=f"read_{entity}",
                    ok=True,
                    critical=True,
                    message=f"{entity} read ok ({total} records)",
                )
            )
        except EspoClientError as e:
            checks.append(
                CRMReadinessCheck(
                    name=f"read_{entity}",
                    ok=False,
                    critical=True,
                    message=f"{entity} read failed: {e}",
                )
            )

    try:
        metadata = client.get_metadata()
        entity_defs = SchemaAuditor._entity_defs(metadata if isinstance(metadata, dict) else {})
        entity_count = len(entity_defs)
        checks.append(
            CRMReadinessCheck(
                name="metadata",
                ok=entity_count > 0,
                critical=True,
                message="Metadata loaded" if entity_count > 0 else "Metadata returned no entity definitions",
                detail=f"{entity_count} entities",
            )
        )
    except EspoClientError as e:
        checks.append(
            CRMReadinessCheck(
                name="metadata",
                ok=False,
                critical=True,
                message=f"Metadata failed: {e}",
            )
        )

    return CRMReadinessReport(checks)


class SchemaAuditor:
    """Best-effort schema map builder for Hermes command tuning."""

    DEFAULT_HIGHLIGHT_FIELDS = ("agentOfAgencyCode", "carrierCode", "totalPremium")

    def __init__(self, client: EspoClient, output_path: str | Path | None = None) -> None:
        self.client = client
        self.output_path = Path(output_path or os.environ.get("HERMES_SCHEMA_MAP", "schema_map.json"))

    def _metadata(self) -> dict[str, Any]:
        scopes: dict[str, Any] | None = None
        try:
            raw_scopes = self.client.get_metadata("scopes")
            if isinstance(raw_scopes, dict):
                scopes = raw_scopes
        except EspoClientError:
            pass
        metadata = self.client.get_metadata()
        if not isinstance(metadata, dict):
            metadata = {}
        if scopes is not None:
            metadata.setdefault("scopes", scopes)
        return metadata

    @staticmethod
    def _entity_defs(metadata: dict[str, Any]) -> dict[str, Any]:
        entity_defs = metadata.get("entityDefs")
        if isinstance(entity_defs, dict):
            return entity_defs
        scopes = metadata.get("scopes")
        return scopes if isinstance(scopes, dict) else {}

    def run_field_audit(self) -> dict[str, Any]:
        metadata = self._metadata()
        entity_defs = self._entity_defs(metadata)
        highlight_fields = list(self.DEFAULT_HIGHLIGHT_FIELDS)
        field_locations: dict[str, list[str]] = {field: [] for field in highlight_fields}

        for entity_name, entity_def in entity_defs.items():
            if not isinstance(entity_def, dict):
                continue
            fields = entity_def.get("fields")
            if not isinstance(fields, dict):
                continue
            for field in highlight_fields:
                if field in fields:
                    field_locations[field].append(str(entity_name))

        schema_map = {
            "rsg_highlight_fields": highlight_fields,
            "rsg_field_locations": field_locations,
            "entity_count": len(entity_defs),
        }
        self.output_path.write_text(json.dumps(schema_map, indent=2, sort_keys=True) + "\n")
        return schema_map

    def run_live_metadata_inventory(self) -> dict[str, Any]:
        """Export entity/field writability inventory from live Espo metadata."""
        metadata = self._metadata()
        entity_defs = self._entity_defs(metadata)
        inventory: dict[str, Any] = {}
        for entity_name, entity_def in entity_defs.items():
            if not isinstance(entity_def, dict):
                continue
            fields = entity_def.get("fields")
            if not isinstance(fields, dict):
                continue
            writable: list[str] = []
            read_only: list[str] = []
            required: list[str] = []
            for field_name, field_def in fields.items():
                if not isinstance(field_def, dict):
                    continue
                if field_def.get("required"):
                    required.append(str(field_name))
                if field_def.get("readOnly"):
                    read_only.append(str(field_name))
                else:
                    writable.append(str(field_name))
            inventory[str(entity_name)] = {
                "writable_fields": sorted(writable),
                "read_only_fields": sorted(read_only),
                "required_fields": sorted(required),
                "field_count": len(fields),
            }
        report = {
            "entity_count": len(inventory),
            "entities": inventory,
        }
        self.output_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
        return report
