"""Espo -> NowCerts account channel — new-client stub (on Opportunity Closed
Won) and fill-blank field corrections.

Governance ([[rsg-ams-source-of-truth-governance]]): NowCerts is the source of
truth. Two additive account channels:

* **New-client stub** — when an Opportunity is Closed Won and its Account has no
  ``momentum_client_id`` yet, dedup-check the AMS (email/FEIN/name) and either
  link the existing insured or create a MINIMAL stub via InsertNoOverride, then
  write the insured GUID back to ``Account.momentum_client_id``. Rich data is
  authored in the AMS afterward.
* **Fill-blank** — for a linked Account, fill fields that are BLANK on the
  NowCerts insured from EspoCRM. Read-first + DatabaseId-keyed: only ever sends
  fields empty in the AMS, so it can never overwrite the source of truth
  (InsertNoOverride *does* overwrite fields you send — hence read-first).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from hermes.core.client import EspoClient, EspoClientError
from hermes.sync.nowcerts_client import NowCertsClient, NowCertsClientError

log = logging.getLogger(__name__)

WON_STAGE = "Closed Won"

_OPP_SELECT = "id,name,stage,accountId,accountName,modifiedAt"
_ACCT_SELECT = ",".join([
    "id", "name", "momentum_client_id", "fein", "emailAddress", "phoneNumber",
    "billingAddressStreet", "billingAddressCity", "billingAddressState",
    "billingAddressPostalCode",
])

# Espo Account field -> (NowCerts read field [camelCase], write field [PascalCase]).
# Conservative safe set only. Fill-blank sends the write field only when the read
# field is empty on the insured.
_FILL_MAP: dict[str, tuple[str, str]] = {
    "fein": ("fein", "FEIN"),
    "emailAddress": ("eMail", "EMail"),
    "phoneNumber": ("phone", "Phone"),
    "billingAddressStreet": ("addressLine1", "AddressLine1"),
    "billingAddressCity": ("city", "City"),
    "billingAddressState": ("state", "State"),
    "billingAddressPostalCode": ("zipCode", "ZipCode"),
}


@dataclass
class AccountSyncResult:
    won_scanned: int = 0
    stubbed: int = 0            # new insured created
    linked_existing: int = 0    # matched an existing insured
    already_linked: int = 0     # account already had a GUID
    filled: int = 0             # accounts that got blank fields filled
    fields_filled: int = 0      # total blank fields written
    failed: int = 0
    errors: list[str] = field(default_factory=list)
    dry_run: bool = False

    @property
    def ok(self) -> bool:
        return self.failed == 0

    @property
    def message(self) -> str:
        prefix = "[DRY RUN] " if self.dry_run else ""
        return (
            f"{prefix}Account write-back: won={self.won_scanned}, "
            f"stubbed={self.stubbed}, linked={self.linked_existing}, "
            f"already-linked={self.already_linked}, "
            f"fill-blank={self.filled} accts/{self.fields_filled} fields, "
            f"failed={self.failed}."
        )


def _cutoff(since_hours: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=since_hours)).strftime("%Y-%m-%d %H:%M:%S")


def _extract_insured_id(resp: Any) -> str | None:
    if not isinstance(resp, dict):
        return None
    candidates = [resp]
    data = resp.get("data")
    if isinstance(data, dict):
        candidates.insert(0, data)
    for obj in candidates:
        for key in ("insuredDatabaseId", "database_id", "databaseId", "DatabaseId", "id"):
            val = obj.get(key)
            if val:
                return str(val)
    return None


def _account_stub(acct: dict[str, Any]) -> dict[str, Any]:
    """Minimal insured stub — name + identity keys only (rich data added in AMS)."""
    stub: dict[str, Any] = {"CommercialName": acct.get("name") or ""}
    if acct.get("fein"):
        stub["FEIN"] = acct["fein"]
    if acct.get("emailAddress"):
        stub["EMail"] = acct["emailAddress"]
    if acct.get("phoneNumber"):
        stub["Phone"] = acct["phoneNumber"]
    return stub


def _read_insured_by_guid(nowcerts: NowCertsClient, name: str, guid: str) -> dict[str, Any] | None:
    """Read the full insured row for ``guid`` (the id GUID isn't directly
    filterable, so filter by commercial name and pick the row whose id matches).

    Duplicate insureds can share a commercial name, so selecting by id — rather
    than taking rows[0] — is what makes fill-blank deterministic and safe: it
    only ever acts on the exact insured this account is linked to.
    """
    if not name or not guid:
        return None
    try:
        body = nowcerts._get("/api/InsuredList", params={"$filter": f"commercialName eq '{name.replace(chr(39), chr(39)*2)}'"})
    except NowCertsClientError:
        return None
    rows = body if isinstance(body, list) else body.get("value", [])
    for row in rows:
        if isinstance(row, dict) and str(row.get("id")) == str(guid):
            return row
    return None


def _do_stub_channel(espo, nowcerts, cutoff, max_size, dry_run, result) -> None:
    body = espo.get("Opportunity", params={
        "maxSize": max_size,
        "select": _OPP_SELECT,
        "where": [
            {"type": "after", "attribute": "modifiedAt", "value": cutoff},
            {"type": "equals", "attribute": "stage", "value": WON_STAGE},
        ],
        "orderBy": "modifiedAt", "order": "desc",
    })
    opps = body.get("list", []) if isinstance(body, dict) else []
    log.info("Stub: %d Closed-Won opp(s) modified since %s", len(opps), cutoff)

    seen: set[str] = set()
    for opp in opps:
        account_id = opp.get("accountId")
        if not account_id or account_id in seen:
            continue
        seen.add(account_id)
        result.won_scanned += 1
        try:
            acct = espo.get(f"Account/{account_id}", params={"select": _ACCT_SELECT})
            if not isinstance(acct, dict):
                continue
            if acct.get("momentum_client_id"):
                result.already_linked += 1
                continue

            # Dedup against the AMS first.
            guid = nowcerts.find_insured_id(
                email=acct.get("emailAddress"),
                fein=acct.get("fein"),
                commercial_name=acct.get("name"),
            )
            matched = bool(guid)

            if dry_run:
                log.info("[DRY RUN] would %s insured for account %s (%s)",
                         "link" if matched else "stub", account_id, acct.get("name"))
                result.linked_existing += matched
                result.stubbed += not matched
                continue

            if not guid:
                resp = nowcerts.insert_insured_no_override(_account_stub(acct))
                guid = _extract_insured_id(resp)
            if not guid:
                result.failed += 1
                result.errors.append(f"Account {account_id}: no insured GUID returned")
                continue

            espo.patch(f"Account/{account_id}", json={"momentum_client_id": guid})
            if matched:
                result.linked_existing += 1
            else:
                result.stubbed += 1
        except (EspoClientError, NowCertsClientError) as exc:
            result.failed += 1
            result.errors.append(f"Account {account_id}: {exc}")
            log.warning("Stub for account %s failed: %s", account_id, exc)


def _do_fill_blank(espo, nowcerts, cutoff, max_size, dry_run, result) -> None:
    body = espo.get("Account", params={
        "maxSize": max_size,
        "select": _ACCT_SELECT,
        "where": [
            {"type": "after", "attribute": "modifiedAt", "value": cutoff},
            {"type": "isNotNull", "attribute": "momentum_client_id"},
        ],
        "orderBy": "modifiedAt", "order": "desc",
    })
    accts = body.get("list", []) if isinstance(body, dict) else []
    log.info("Fill-blank: %d linked account(s) modified since %s", len(accts), cutoff)

    for acct in accts:
        guid = acct.get("momentum_client_id")
        account_id = acct.get("id")
        try:
            # Resolves by name then selects the row whose id == the linked GUID,
            # so we only ever act on the exact insured this account links to
            # (returns None — safe skip — if that insured isn't among the matches).
            insured = _read_insured_by_guid(nowcerts, acct.get("name") or "", guid)
            if not insured:
                continue

            to_fill: dict[str, Any] = {}
            for espo_field, (nc_read, nc_write) in _FILL_MAP.items():
                espo_val = acct.get(espo_field)
                nc_val = insured.get(nc_read)
                if espo_val and not nc_val:
                    to_fill[nc_write] = espo_val
            if not to_fill:
                continue

            if dry_run:
                log.info("[DRY RUN] would fill %d blank field(s) on insured %s: %s",
                         len(to_fill), guid, ",".join(to_fill.keys()))
                result.filled += 1
                result.fields_filled += len(to_fill)
                continue

            # CommercialName is REQUIRED by InsertNoOverride (missing it -> HTTP 500).
            # We send the insured's CURRENT name (read above) so the required-field
            # check passes without changing it — the endpoint overrides sent fields,
            # and read-first guarantees to_fill holds only AMS-blank fields.
            nowcerts.insert_insured_no_override(
                {"DatabaseId": guid, "CommercialName": insured.get("commercialName") or "", **to_fill}
            )
            result.filled += 1
            result.fields_filled += len(to_fill)
        except (EspoClientError, NowCertsClientError) as exc:
            result.failed += 1
            result.errors.append(f"Account {account_id} fill-blank: {exc}")
            log.warning("Fill-blank for account %s failed: %s", account_id, exc)


def run_account_writeback(
    espo: EspoClient | None = None,
    nowcerts: NowCertsClient | None = None,
    *,
    dry_run: bool = False,
    since_hours: int = 24,
    max_size: int = 200,
    fill_blank: bool = True,
) -> AccountSyncResult:
    """New-client stub (on Opp Closed Won) + optional fill-blank corrections."""
    espo = espo or EspoClient()
    nowcerts = nowcerts or NowCertsClient()
    result = AccountSyncResult(dry_run=dry_run)
    cutoff = _cutoff(since_hours)

    _do_stub_channel(espo, nowcerts, cutoff, max_size, dry_run, result)
    if fill_blank:
        _do_fill_blank(espo, nowcerts, cutoff, max_size, dry_run, result)

    log.info(result.message)
    return result
