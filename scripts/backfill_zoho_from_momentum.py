#!/usr/bin/env python3
"""Backfill Zoho CRM from Momentum AMS (NowCerts).

Pulls insureds + policies from NowCerts, upserts Zoho Accounts / Policies /
Renewals, and stamps each Account with a Nextcloud folder URL.

Designed to run on Elestio where Hermes env credentials are present:

    cd /opt/rsg-hermes
    PYTHONPATH=packages/rsg-hermes-core:. \\
      python scripts/backfill_zoho_from_momentum.py --dry-run --limit 5

    PYTHONPATH=packages/rsg-hermes-core:. \\
      python scripts/backfill_zoho_from_momentum.py

Flags:
  --dry-run          Preview mappings; do not write to Zoho / Nextcloud
  --limit N          Process only the first N insureds
  --batch-size N     Insureds per batch (default 50)
  --batch-delay SEC  Sleep between batches (default 1.0)
  --renewal-days N   Create Renewals for policies expiring within N days (default 120)
  --report PATH      CSV report path (default ./backfill_zoho_report.csv)
  --skip-policies    Only upsert Accounts
  --skip-renewals    Skip the Renewals module
  --skip-nextcloud   Do not create / stamp Nextcloud folders

Env (optional overrides):
  ZOHO_POLICIES_MODULE   default "Policies"
  ZOHO_RENEWALS_MODULE   default "Renewals"
"""

from __future__ import annotations

import argparse
import csv
import logging
import os
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger("backfill_zoho")

BATCH_SIZE_DEFAULT = 50
BATCH_DELAY_DEFAULT = 1.0
RENEWAL_DAYS_DEFAULT = 120

# Zoho custom-module API names (override if the org renamed them).
POLICIES_MODULE = os.environ.get("ZOHO_POLICIES_MODULE", "Policies")
RENEWALS_MODULE = os.environ.get("ZOHO_RENEWALS_MODULE", "Renewals")


# ── Momentum field helpers ────────────────────────────────────────────────


def _s(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _insured_guid(ins: dict[str, Any]) -> str | None:
    return _s(ins.get("databaseId") or ins.get("id") or ins.get("DatabaseId"))


def _insured_name(ins: dict[str, Any]) -> str | None:
    commercial = _s(ins.get("commercialName") or ins.get("insuredCommercialName"))
    if commercial:
        return commercial
    parts = [_s(ins.get("firstName")), _s(ins.get("lastName"))]
    joined = " ".join(p for p in parts if p)
    return joined or None


def _insured_type(ins: dict[str, Any]) -> str | None:
    """Map Momentum insuredType → Zoho Insured_Type (Commercial / Personal)."""
    raw = ins.get("insuredType")
    if raw is None:
        raw = ins.get("InsuredType")
    if raw in (0, "0"):
        return "Commercial"
    if raw in (1, "1"):
        return "Personal"
    text = _s(raw)
    if not text:
        # Heuristic: commercial name present → Commercial.
        if _s(ins.get("commercialName") or ins.get("insuredCommercialName")):
            return "Commercial"
        return "Personal"
    lower = text.lower()
    if lower in ("0", "commercial", "c"):
        return "Commercial"
    if lower in ("1", "personal", "p"):
        return "Personal"
    return text


def _policy_guid(pol: dict[str, Any]) -> str | None:
    return _s(pol.get("databaseId") or pol.get("DatabaseId") or pol.get("id"))


def _policy_number(pol: dict[str, Any]) -> str | None:
    return _s(pol.get("number") or pol.get("policyNumber") or pol.get("Number"))


def _policy_lob(pol: dict[str, Any]) -> str | None:
    lob_list = pol.get("lineOfBusinesses") or pol.get("linesOfBusiness")
    if isinstance(lob_list, list) and lob_list:
        first = lob_list[0]
        if isinstance(first, dict):
            return _s(first.get("lineOfBusinessName") or first.get("name"))
        return _s(first)
    for key in ("lineOfBusinessName", "lineOfBusiness", "LineOfBusinessName"):
        if pol.get(key):
            return _s(pol[key])
    return None


def _policy_premium(pol: dict[str, Any]) -> float | None:
    for key in ("totalPremium", "premium", "Premium", "annualizedPremium"):
        val = pol.get(key)
        if val is None or val == "":
            continue
        try:
            return float(val)
        except (TypeError, ValueError):
            continue
    return None


def _policy_date(pol: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        raw = pol.get(key)
        if not raw:
            continue
        text = str(raw).strip()
        if not text:
            continue
        # Zoho date fields want YYYY-MM-DD.
        if "T" in text:
            text = text.split("T", 1)[0]
        return text[:10]
    return None


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def map_insured_to_account(ins: dict[str, Any], *, nextcloud_url: str | None = None) -> dict[str, Any]:
    """Momentum insured → Zoho Account fields (snake_case keys for create_or_update_account)."""
    return {
        "account_name": _insured_name(ins),
        "fein": _s(ins.get("fein") or ins.get("FEIN") or ins.get("ein")),
        "insured_type": _insured_type(ins),
        "email": _s(ins.get("eMail") or ins.get("email") or ins.get("EMail")),
        "phone": _s(ins.get("phone") or ins.get("PhoneNumber") or ins.get("phoneNumber")),
        "cell_phone": _s(ins.get("cellPhone") or ins.get("CellPhone")),
        "address": _s(ins.get("addressLine1") or ins.get("AddressLine1")),
        "city": _s(ins.get("city") or ins.get("City")),
        "state": _s(ins.get("state") or ins.get("State")),
        "zip": _s(ins.get("zipCode") or ins.get("ZipCode") or ins.get("zip")),
        "nowcerts_insured_guid": _insured_guid(ins),
        "nextcloud_folder_url": nextcloud_url,
    }


def map_policy_to_zoho(
    pol: dict[str, Any],
    *,
    account_id: str,
    primary_folder_url: str | None = None,
) -> dict[str, Any]:
    """Momentum policy → Zoho Policies module fields."""
    number = _policy_number(pol)
    guid = _policy_guid(pol)
    lob = _policy_lob(pol)
    payload = {
        # Zoho custom modules usually require Name — use the policy number.
        "Name": number or guid or "Policy",
        "Policy_Number": number,
        "Carrier": _s(pol.get("carrierName") or pol.get("CarrierName") or pol.get("carrier")),
        "Line_of_Business": lob,
        "Premium": _policy_premium(pol),
        "Effective_Date": _policy_date(pol, "effectiveDate", "EffectiveDate"),
        "Expiration_Date": _policy_date(pol, "expirationDate", "ExpirationDate"),
        "Policy_Status": _s(pol.get("status") or pol.get("Status")),
        "Billing_Type": _s(pol.get("billingType") or pol.get("BillingType")),
        "NowCerts_Policy_GUID": guid,
        "Account_Name": {"id": account_id},
        "Primary_Folder_URL": primary_folder_url,
    }
    return {k: v for k, v in payload.items() if v is not None}


def map_renewal_to_zoho(
    pol: dict[str, Any],
    *,
    account_id: str,
    account_name: str | None,
    risk_status: str = "Upcoming",
    primary_folder_url: str | None = None,
) -> dict[str, Any]:
    """Momentum expiring policy → Zoho Renewals module fields."""
    number = _policy_number(pol)
    exp = _policy_date(pol, "expirationDate", "ExpirationDate")
    name = f"{account_name or 'Client'} — {number or 'policy'} renewal"
    payload = {
        "Name": name[:100],
        "Policy_Number": number,
        "Client_Name": account_name,
        "Expiration_Date": exp,
        "Premium_Current": _policy_premium(pol),
        "Risk_Status": risk_status,
        "Carrier": _s(pol.get("carrierName") or pol.get("CarrierName") or pol.get("carrier")),
        "Line_of_Business": _policy_lob(pol),
        "NowCerts_Policy_GUID": _policy_guid(pol),
        "Account_Name": {"id": account_id},
        "Primary_Folder_URL": primary_folder_url,
    }
    return {k: v for k, v in payload.items() if v is not None}


def nextcloud_folder_url(nc: Any, client_name: str) -> tuple[str | None, str | None]:
    """Ensure Clients/{name}/… and return (rel_path, browser_url)."""
    if nc is None or not getattr(nc, "is_configured", lambda: False)():
        return None, None
    folder = nc.ensure_client_folders(client_name)
    url = None
    browser = getattr(nc, "browser_dir_url", None)
    if callable(browser):
        try:
            maybe = browser(folder)
        except Exception:
            maybe = None
        if isinstance(maybe, str) and maybe.lower().startswith(("http://", "https://")):
            url = maybe
    return folder, url


# ── Report / counters ─────────────────────────────────────────────────────


class Counters:
    def __init__(self) -> None:
        self.accounts_created = 0
        self.accounts_updated = 0
        self.accounts_skipped = 0
        self.policies_created = 0
        self.policies_updated = 0
        self.policies_skipped = 0
        self.renewals_created = 0
        self.renewals_updated = 0
        self.renewals_skipped = 0
        self.errors = 0

    def bump(self, kind: str, action: str) -> None:
        key = f"{kind}_{action}"
        if hasattr(self, key):
            setattr(self, key, getattr(self, key) + 1)

    def summary(self) -> str:
        return (
            f"Created {self.accounts_created} accounts, "
            f"{self.policies_created} policies, "
            f"{self.renewals_created} renewals. "
            f"Updated {self.accounts_updated} accounts, "
            f"{self.policies_updated} policies, "
            f"{self.renewals_updated} renewals. "
            f"Skipped {self.accounts_skipped + self.policies_skipped + self.renewals_skipped}. "
            f"Errors: {self.errors}"
        )


def _open_report(path: Path) -> tuple[Any, csv.DictWriter]:
    path.parent.mkdir(parents=True, exist_ok=True)
    fh = path.open("w", newline="", encoding="utf-8")
    writer = csv.DictWriter(
        fh,
        fieldnames=[
            "timestamp",
            "entity",
            "action",
            "momentum_guid",
            "momentum_name",
            "zoho_id",
            "detail",
            "error",
        ],
    )
    writer.writeheader()
    return fh, writer


def _row(
    writer: csv.DictWriter,
    *,
    entity: str,
    action: str,
    momentum_guid: str | None = None,
    momentum_name: str | None = None,
    zoho_id: str | None = None,
    detail: str | None = None,
    error: str | None = None,
) -> None:
    writer.writerow(
        {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "entity": entity,
            "action": action,
            "momentum_guid": momentum_guid or "",
            "momentum_name": momentum_name or "",
            "zoho_id": zoho_id or "",
            "detail": detail or "",
            "error": error or "",
        }
    )


# ── Core backfill ─────────────────────────────────────────────────────────


def _policies_for_insured(all_policies: list[dict[str, Any]], insured_guid: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for pol in all_policies:
        ig = _s(pol.get("insuredDatabaseId") or pol.get("insuredId") or pol.get("InsuredDatabaseId"))
        if ig and ig == insured_guid:
            out.append(pol)
    return out


def run_backfill(args: argparse.Namespace) -> int:
    from hermes_integrations.nowcerts_client import NowCertsClient, get_client as get_nowcerts
    from hermes_integrations.zoho_client import ZohoClient, ZohoClientError, get_client as get_zoho
    from hermes_integrations.nextcloud_client import NextcloudClient

    dry_run: bool = bool(args.dry_run)
    limit: int | None = args.limit
    batch_size: int = max(1, int(args.batch_size))
    batch_delay: float = max(0.0, float(args.batch_delay))
    renewal_days: int = max(1, int(args.renewal_days))
    report_path = Path(args.report)

    log.info("Connecting to Momentum (NowCerts)…")
    try:
        nowcerts: NowCertsClient = get_nowcerts()
    except Exception as exc:
        log.error("NowCerts client unavailable: %s", exc)
        return 2

    zoho: ZohoClient | None = None
    if not dry_run:
        log.info("Connecting to Zoho CRM…")
        try:
            zoho = get_zoho()
        except Exception as exc:
            log.error("Zoho client unavailable: %s", exc)
            return 2
    else:
        log.info("DRY RUN — Zoho / Nextcloud writes disabled")

    nc: NextcloudClient | None = None
    if not args.skip_nextcloud:
        nc = NextcloudClient()
        if not nc.is_configured():
            log.warning("Nextcloud not configured — Accounts will not get folder URLs")
            nc = None

    # Step 1: pull all insureds
    log.info("Step 1: pulling insureds from Momentum…")
    insureds = nowcerts.fetch_insureds(page_size=100)
    log.info("Fetched %d insureds", len(insureds))
    if limit is not None:
        insureds = insureds[:limit]
        log.info("Limiting to first %d insureds (--limit)", limit)

    # Step 3 prelude: pull all policies once (cheaper than per-insured live filters
    # when the book is small-to-medium; PolicyDetailList is the same source).
    all_policies: list[dict[str, Any]] = []
    if not args.skip_policies or not args.skip_renewals:
        log.info("Step 3: pulling policies from Momentum…")
        all_policies = nowcerts.fetch_policies(page_size=100)
        log.info("Fetched %d policies", len(all_policies))

    today = date.today()
    renewal_cutoff = today + timedelta(days=renewal_days)

    counters = Counters()
    report_fh, writer = _open_report(report_path)
    guid_to_zoho_account: dict[str, str] = {}

    try:
        total = len(insureds)
        for batch_start in range(0, total, batch_size):
            batch = insureds[batch_start : batch_start + batch_size]
            for offset, ins in enumerate(batch):
                idx = batch_start + offset + 1
                name = _insured_name(ins) or "(unnamed)"
                guid = _insured_guid(ins)
                log.info("Processing insured %d/%d: %s…", idx, total, name)

                if not guid:
                    counters.accounts_skipped += 1
                    counters.errors += 1
                    _row(
                        writer,
                        entity="account",
                        action="skipped",
                        momentum_name=name,
                        error="missing Momentum databaseId/GUID",
                    )
                    continue

                # Nextcloud folder URL (best-effort)
                folder_url = None
                policies_folder_url = None
                renewal_folder_url = None
                if nc is not None and not dry_run:
                    try:
                        _folder, folder_url = nextcloud_folder_url(nc, name)
                        cat_url = getattr(nc, "client_category_url", None)
                        if callable(cat_url):
                            maybe_p = cat_url(name, "Policies")
                            maybe_r = cat_url(name, "Renewal Reviews")
                            if isinstance(maybe_p, str) and maybe_p.lower().startswith("http"):
                                policies_folder_url = maybe_p
                            if isinstance(maybe_r, str) and maybe_r.lower().startswith("http"):
                                renewal_folder_url = maybe_r
                    except Exception as exc:  # noqa: BLE001
                        log.warning("Nextcloud folder failed for %s: %s", name, exc)
                        _row(
                            writer,
                            entity="nextcloud",
                            action="failed",
                            momentum_guid=guid,
                            momentum_name=name,
                            error=str(exc),
                        )

                account_data = map_insured_to_account(ins, nextcloud_url=folder_url)

                # Step 2: Account upsert
                account_id: str | None = None
                try:
                    if dry_run:
                        log.info(
                            "  [dry-run] Account %s guid=%s type=%s email=%s",
                            account_data.get("account_name"),
                            guid,
                            account_data.get("insured_type"),
                            account_data.get("email"),
                        )
                        counters.bump("accounts", "skipped")
                        _row(
                            writer,
                            entity="account",
                            action="dry_run",
                            momentum_guid=guid,
                            momentum_name=name,
                            detail=str({k: v for k, v in account_data.items() if v is not None}),
                        )
                        # Synthetic id so policy dry-run still links conceptually.
                        account_id = f"dry-run-account:{guid}"
                    else:
                        assert zoho is not None
                        result = zoho.create_or_update_account(account_data)
                        account_id = str(result["id"])
                        action = str(result.get("action") or "updated")
                        counters.bump("accounts", action if action in ("created", "updated") else "updated")
                        _row(
                            writer,
                            entity="account",
                            action=action,
                            momentum_guid=guid,
                            momentum_name=name,
                            zoho_id=account_id,
                        )
                    guid_to_zoho_account[guid] = account_id
                except Exception as exc:  # noqa: BLE001 — continue per insured
                    counters.errors += 1
                    counters.accounts_skipped += 1
                    log.exception("  Account failed for %s (%s)", name, guid)
                    _row(
                        writer,
                        entity="account",
                        action="failed",
                        momentum_guid=guid,
                        momentum_name=name,
                        error=str(exc),
                    )
                    continue

                # Steps 4–5: Policies + Renewals for this insured
                if args.skip_policies and args.skip_renewals:
                    continue
                policies = _policies_for_insured(all_policies, guid)
                log.info("  %d policies", len(policies))
                for pol in policies:
                    pguid = _policy_guid(pol)
                    pnum = _policy_number(pol)

                    if not args.skip_policies:
                        try:
                            if not pguid and not pnum:
                                counters.policies_skipped += 1
                                _row(
                                    writer,
                                    entity="policy",
                                    action="skipped",
                                    momentum_guid=guid,
                                    momentum_name=name,
                                    error="policy missing number and GUID",
                                )
                            else:
                                payload = map_policy_to_zoho(
                                    pol,
                                    account_id=account_id,
                                    primary_folder_url=policies_folder_url,
                                )
                                match_value = pguid or pnum
                                match_field = "NowCerts_Policy_GUID" if pguid else "Policy_Number"
                                if dry_run:
                                    counters.policies_skipped += 1
                                    _row(
                                        writer,
                                        entity="policy",
                                        action="dry_run",
                                        momentum_guid=pguid,
                                        momentum_name=pnum or name,
                                        detail=str({k: v for k, v in payload.items() if k != "Account_Name"}),
                                    )
                                else:
                                    assert zoho is not None
                                    result = zoho.upsert_by_field(
                                        POLICIES_MODULE,
                                        payload,
                                        match_field=match_field,
                                        match_value=str(match_value),
                                    )
                                    action = str(result.get("action") or "updated")
                                    counters.bump(
                                        "policies",
                                        action if action in ("created", "updated") else "updated",
                                    )
                                    _row(
                                        writer,
                                        entity="policy",
                                        action=action,
                                        momentum_guid=pguid,
                                        momentum_name=pnum,
                                        zoho_id=str(result.get("id") or ""),
                                    )
                        except Exception as exc:  # noqa: BLE001
                            counters.errors += 1
                            counters.policies_skipped += 1
                            log.exception("  Policy failed %s / %s", name, pnum)
                            _row(
                                writer,
                                entity="policy",
                                action="failed",
                                momentum_guid=pguid,
                                momentum_name=pnum or name,
                                error=str(exc),
                            )

                    # Step 5: Renewal if expiring within window
                    if args.skip_renewals:
                        continue
                    exp = _parse_date(_policy_date(pol, "expirationDate", "ExpirationDate"))
                    if exp is None or exp < today or exp > renewal_cutoff:
                        continue
                    try:
                        days_left = (exp - today).days
                        risk = (
                            "Urgent" if days_left <= 30
                            else "At Risk" if days_left <= 60
                            else "Upcoming"
                        )
                        renewal = map_renewal_to_zoho(
                            pol,
                            account_id=account_id,
                            account_name=name,
                            risk_status=risk,
                            primary_folder_url=renewal_folder_url,
                        )
                        match_field = "NowCerts_Policy_GUID"
                        match_value = pguid or pnum
                        if not match_value:
                            counters.renewals_skipped += 1
                            continue
                        if dry_run:
                            counters.renewals_skipped += 1
                            _row(
                                writer,
                                entity="renewal",
                                action="dry_run",
                                momentum_guid=pguid,
                                momentum_name=pnum or name,
                                detail=f"expires={exp.isoformat()} risk={risk}",
                            )
                        else:
                            assert zoho is not None
                            if not pguid:
                                match_field = "Policy_Number"
                            result = zoho.upsert_by_field(
                                RENEWALS_MODULE,
                                renewal,
                                match_field=match_field,
                                match_value=str(match_value),
                            )
                            action = str(result.get("action") or "updated")
                            counters.bump(
                                "renewals",
                                action if action in ("created", "updated") else "updated",
                            )
                            _row(
                                writer,
                                entity="renewal",
                                action=action,
                                momentum_guid=pguid,
                                momentum_name=pnum or name,
                                zoho_id=str(result.get("id") or ""),
                                detail=f"expires={exp.isoformat()} risk={risk}",
                            )
                    except Exception as exc:  # noqa: BLE001
                        counters.errors += 1
                        counters.renewals_skipped += 1
                        log.exception("  Renewal failed %s / %s", name, pnum)
                        _row(
                            writer,
                            entity="renewal",
                            action="failed",
                            momentum_guid=pguid,
                            momentum_name=pnum or name,
                            error=str(exc),
                        )

            # Rate-limit pause between batches (skip after the last batch).
            if batch_start + batch_size < total and batch_delay > 0:
                log.info(
                    "Batch complete (%d–%d). Sleeping %.1fs…",
                    batch_start + 1,
                    min(batch_start + batch_size, total),
                    batch_delay,
                )
                time.sleep(batch_delay)
    finally:
        report_fh.close()

    print(counters.summary())
    print(f"CSV report: {report_path.resolve()}")
    log.info("%s", counters.summary())
    return 1 if counters.errors else 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Backfill Zoho CRM Accounts/Policies/Renewals from Momentum AMS (NowCerts).",
    )
    p.add_argument("--dry-run", action="store_true", help="Preview only; do not write to Zoho")
    p.add_argument("--limit", type=int, default=None, help="Process only the first N insureds")
    p.add_argument("--batch-size", type=int, default=BATCH_SIZE_DEFAULT, help="Insureds per batch (default 50)")
    p.add_argument("--batch-delay", type=float, default=BATCH_DELAY_DEFAULT, help="Seconds between batches (default 1)")
    p.add_argument("--renewal-days", type=int, default=RENEWAL_DAYS_DEFAULT, help="Renewal window in days (default 120)")
    p.add_argument("--report", default="backfill_zoho_report.csv", help="CSV report output path")
    p.add_argument("--skip-policies", action="store_true", help="Skip Policies module writes")
    p.add_argument("--skip-renewals", action="store_true", help="Skip Renewals module writes")
    p.add_argument("--skip-nextcloud", action="store_true", help="Do not create/stamp Nextcloud folders")
    p.add_argument("-v", "--verbose", action="store_true", help="DEBUG logging")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        return run_backfill(args)
    except KeyboardInterrupt:
        log.error("Interrupted")
        return 130
    except Exception as exc:  # noqa: BLE001
        log.exception("Backfill aborted: %s", exc)
        return 2


if __name__ == "__main__":
    sys.exit(main())
