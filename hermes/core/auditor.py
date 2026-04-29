"""Data quality checks and lightweight KPI helpers over EspoCRM."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from hermes.core.client import EspoClient, EspoClientError


@dataclass
class KPIReport:
    label: str
    value: Any
    detail: str | None = None


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
