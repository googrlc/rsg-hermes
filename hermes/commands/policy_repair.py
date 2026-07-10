"""Repair Policy records missing Account links from Momentum insured IDs."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from hermes.core.dispatcher import DispatchResult
from hermes.core.field_utils import get_first_available

if TYPE_CHECKING:
    from hermes.core.client import EspoClient


PAGE_SIZE = 200


@dataclass
class PolicyAccountRepair:
    policy_id: str
    policy_name: str
    insured_momentum_id: str
    account_id: str
    account_name: str
    policy_number: str


@dataclass
class PolicyAccountRepairResult:
    dry_run: bool
    candidates: list[PolicyAccountRepair] = field(default_factory=list)
    updated: list[PolicyAccountRepair] = field(default_factory=list)
    unmatched: list[dict[str, str]] = field(default_factory=list)
    ambiguous: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    @property
    def candidate_count(self) -> int:
        return len(self.candidates)

    @property
    def updated_count(self) -> int:
        return len(self.updated)

    @property
    def unmatched_count(self) -> int:
        return len(self.unmatched)

    @property
    def ambiguous_count(self) -> int:
        return len(self.ambiguous)

    def format_message(self) -> str:
        mode = "DRY RUN" if self.dry_run else "APPLIED"
        lines = [
            f"Policy Account Repair ({mode})",
            f"Candidates: {self.candidate_count}",
            f"Updated: {self.updated_count}",
            f"Unmatched insuredMomentumId: {self.unmatched_count}",
            f"Ambiguous account matches: {self.ambiguous_count}",
        ]

        preview = self.updated if self.updated else self.candidates
        if preview:
            lines.append("")
            lines.append("Preview:")
            for item in preview[:5]:
                lines.append(
                    f"- Policy {item.policy_id} ({item.policy_name or 'unnamed'}) "
                    f"-> Account {item.account_id} ({item.account_name})"
                )

        if self.errors:
            lines.append("")
            lines.append("Errors:")
            for error in self.errors[:10]:
                lines.append(f"- {error}")

        return "\n".join(lines)


def _collection_rows(
    client: "EspoClient",
    entity: str,
    *,
    select: str | None = None,
    where: list[dict[str, Any]] | None = None,
    page_size: int = PAGE_SIZE,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    offset = 0
    total: int | None = None

    while True:
        params: dict[str, Any] = {"maxSize": min(page_size, PAGE_SIZE), "offset": offset}
        if select:
            params["select"] = select
        if where:
            params["where"] = where

        body = client.get(entity, params=params)
        page_rows = body.get("list", []) if isinstance(body, dict) else []
        if not isinstance(page_rows, list) or page_rows == []:
            break

        rows.extend([row for row in page_rows if isinstance(row, dict)])

        if isinstance(body, dict) and total is None and body.get("total") is not None:
            try:
                total = int(body["total"])
            except (TypeError, ValueError):
                total = None

        offset += len(page_rows)
        if total is not None and offset >= total:
            break
        if len(page_rows) < min(page_size, PAGE_SIZE):
            break

    return rows


def _lookup_account_by_momentum_id(client: "EspoClient", insured_momentum_id: str) -> list[dict[str, Any]]:
    body = client.get(
        "Account",
        params={
            "maxSize": 2,
            "select": "id,name,momentum_client_id",
            "where": [
                {
                    "type": "equals",
                    "attribute": "momentum_client_id",
                    "value": insured_momentum_id,
                }
            ],
        },
    )
    rows = body.get("list", []) if isinstance(body, dict) else []
    return [row for row in rows if isinstance(row, dict)]


def run_policy_account_repair(client: "EspoClient", *, dry_run: bool = True) -> PolicyAccountRepairResult:
    result = PolicyAccountRepairResult(dry_run=dry_run)
    policies = _collection_rows(
        client,
        "Policy",
        select="id,name,insuredMomentumId,accountId,accountName,policy_number,policyNumber",
    )

    for policy in policies:
        policy_id = str(policy.get("id") or "").strip()
        if not policy_id or policy.get("accountId"):
            continue

        insured_momentum_id = str(policy.get("insuredMomentumId") or "").strip()
        if not insured_momentum_id:
            continue

        accounts = _lookup_account_by_momentum_id(client, insured_momentum_id)
        if len(accounts) == 0:
            result.unmatched.append({
                "policy_id": policy_id,
                "policy_name": str(policy.get("name") or ""),
                "insuredMomentumId": insured_momentum_id,
            })
            continue
        if len(accounts) > 1:
            result.ambiguous.append({
                "policy_id": policy_id,
                "policy_name": str(policy.get("name") or ""),
                "insuredMomentumId": insured_momentum_id,
                "account_ids": [str(account.get("id") or "") for account in accounts],
            })
            continue

        account = accounts[0]
        account_id = str(account.get("id") or "").strip()
        account_name = str(account.get("name") or "").strip()
        if not account_id:
            result.unmatched.append({
                "policy_id": policy_id,
                "policy_name": str(policy.get("name") or ""),
                "insuredMomentumId": insured_momentum_id,
            })
            continue

        repair = PolicyAccountRepair(
            policy_id=policy_id,
            policy_name=str(policy.get("name") or ""),
            insured_momentum_id=insured_momentum_id,
            account_id=account_id,
            account_name=account_name,
            policy_number=str(get_first_available(policy, "policy_number", "policyNumber") or ""),
        )
        result.candidates.append(repair)

        if dry_run:
            continue

        try:
            client.update(
                "Policy",
                policy_id,
                {
                    "accountId": account_id,
                    "accountName": account_name,
                },
            )
            result.updated.append(repair)
        except Exception as exc:
            result.errors.append(f"Policy {policy_id}: {exc}")

    return result


def _is_apply(text: str) -> bool:
    if re.search(r"\b(dry[-\s]?run|preview|report|show)\b", text, re.I):
        return False
    return bool(re.search(r"\b(apply|execute|write|repair\s+now)\b", text, re.I))


def handle(client: "EspoClient", text: str) -> DispatchResult:
    result = run_policy_account_repair(client, dry_run=not _is_apply(text))
    return DispatchResult(
        result.ok,
        result.format_message(),
        {
            "dry_run": result.dry_run,
            "candidate_count": result.candidate_count,
            "updated_count": result.updated_count,
            "unmatched_count": result.unmatched_count,
            "ambiguous_count": result.ambiguous_count,
            "errors": result.errors,
        },
    )
