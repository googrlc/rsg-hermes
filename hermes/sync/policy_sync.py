"""NowCerts → EspoCRM policy sync — policies ONLY.

EspoCRM owns Accounts / Contacts / Opportunities. The *only* data that flows from
NowCerts into EspoCRM is policies. This job fetches updated NowCerts policies and
upserts them into the EspoCRM ``Policy`` entity, matched to an EXISTING account.

Policies whose insured isn't already an EspoCRM account are **skipped and
reported** — never auto-created. Auto-creating accounts from insureds is exactly
the duplicate-account garbage this sync is meant to avoid (see the legacy
``run_insured_to_account_sync``, which is deliberately NOT scheduled).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from hermes.sync.field_mapper import map_nowcerts_policy_to_espo_policy

log = logging.getLogger(__name__)


@dataclass
class PolicySyncResult:
    processed: int = 0
    created: int = 0
    updated: int = 0
    skipped_no_account: int = 0
    skipped_no_number: int = 0
    errors: list[str] = field(default_factory=list)
    skipped_accounts: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    @property
    def message(self) -> str:
        return (
            f"policies processed={self.processed} created={self.created} "
            f"updated={self.updated} skipped_no_account={self.skipped_no_account} "
            f"skipped_no_number={self.skipped_no_number} errors={len(self.errors)}"
        )


def _nc_insured_name(p: dict[str, Any]) -> str:
    """Best-effort insured name from a NowCerts policy record."""
    commercial = p.get("insuredCommercialName") or p.get("commercialName")
    if commercial:
        return str(commercial).strip()
    first = str(p.get("insuredFirstName") or "").strip()
    last = str(p.get("insuredLastName") or "").strip()
    person = f"{first} {last}".strip()
    return person or str(p.get("insuredName") or "").strip()


def _resolve_account_id(
    espo: Any,
    nc_policy: dict[str, Any],
    *,
    cache: dict[str, str | None],
) -> tuple[str | None, str]:
    """Find an EXISTING EspoCRM Account for this policy's insured.

    Returns ``(account_id, insured_name)``; ``account_id`` is None when there's
    no confident match. We match on an exact account-name equality (not a fuzzy
    contains) to avoid binding a policy to the wrong duplicate in dirty data —
    a near-miss is better surfaced as a skip for human review than mis-linked.
    Never creates an account.
    """
    name = _nc_insured_name(nc_policy)
    if not name:
        return None, ""
    if name in cache:
        return cache[name], name
    hit = espo.find_one_by_field("Account", "name", name, select="id,name")
    account_id = str(hit["id"]) if hit and hit.get("id") else None
    cache[name] = account_id
    return account_id, name


def run_policy_sync(
    nc: Any,
    espo: Any,
    *,
    since: str | None = None,
    dry_run: bool = False,
    page_size: int = 100,
    limit: int | None = None,
) -> PolicySyncResult:
    """Fetch NowCerts policies (optionally since an ISO datetime) and upsert each
    into the EspoCRM Policy entity, keyed on ``policy_number``. Read-only when
    ``dry_run`` is set.
    """
    result = PolicySyncResult()
    policies = nc.fetch_policies(since=since, page_size=page_size)
    if limit:
        policies = policies[:limit]
    log.info("policy sync: %d NowCerts policies to process (dry_run=%s)", len(policies), dry_run)

    account_cache: dict[str, str | None] = {}
    for p in policies:
        result.processed += 1
        ref = p.get("number") or p.get("policyNumber") or p.get("Number") or "?"
        try:
            account_id, insured_name = _resolve_account_id(espo, p, cache=account_cache)
            payload = map_nowcerts_policy_to_espo_policy(
                p, account_id=account_id, account_name=insured_name
            )
            if payload is None:
                result.skipped_no_number += 1
                log.info("SKIP policy (no usable policy_number): insured=%r", insured_name)
                continue

            if not account_id:
                result.skipped_no_account += 1
                if insured_name and insured_name not in result.skipped_accounts:
                    result.skipped_accounts.append(insured_name)
                log.info(
                    "SKIP policy %s — no existing EspoCRM account for insured %r",
                    payload["policy_number"], insured_name,
                )
                continue

            existing = espo.find_one_by_field(
                "Policy", "policy_number", payload["policy_number"],
                select="id,policy_number",
            )
            verb = "UPDATE" if (existing and existing.get("id")) else "CREATE"
            if dry_run:
                log.info("DRY %s Policy %s → account %r", verb, payload["policy_number"], insured_name)
                if verb == "UPDATE":
                    result.updated += 1
                else:
                    result.created += 1
                continue

            if existing and existing.get("id"):
                espo.update("Policy", str(existing["id"]), payload)
                result.updated += 1
            else:
                espo.create("Policy", payload)
                result.created += 1
        except Exception as exc:  # noqa: BLE001 — one bad policy shouldn't abort the run
            result.errors.append(f"policy {ref}: {exc}")
            log.warning("policy sync error on %s: %s", ref, exc)

    log.info("policy sync done: %s", result.message)
    return result
