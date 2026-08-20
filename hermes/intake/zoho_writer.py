"""Write an approved Hermes intake payload into Zoho CRM.

Called from ``commit_intake()`` *after* Supabase writes. Creates/updates the
Account, Contacts, and Deals (one per LOB), attaches notes/PDFs, and stamps
the Nextcloud folder URL on the Account.

Hard rules (agency):
  - Do NOT create AMS_Write_Queue / outbound_sync_queue entries.
  - Do NOT push to Momentum / NowCerts.
  - Skip restricted/sensitive facts (EIN, DOB, DL, SSN, …).
  - One record failing must not block the rest.
"""

from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

# Fact labels that must never land in a Zoho note (crm-intake-writer contract).
_RESTRICTED_FACT_LABELS = {
    "ein",
    "fein",
    "ssn",
    "dob",
    "date of birth",
    "dl",
    "driver's license",
    "driver's license number",
    "driver's license state",
    "drivers license",
    "drivers license number",
    "banking",
    "bank account",
    "health",
    "beneficiary",
}


def _is_restricted_fact(fact: dict[str, Any]) -> bool:
    sensitivity = str(fact.get("sensitivity") or "").strip().lower()
    if sensitivity == "restricted":
        return True
    label = str(fact.get("fact_label") or fact.get("label") or "").strip().lower()
    return label in _RESTRICTED_FACT_LABELS


def _account_block(payload: dict[str, Any]) -> dict[str, Any]:
    from hermes_integrations.zoho_document_fields import is_http_url

    account = dict(payload.get("account") or {})
    # Nextcloud URL may sit on the account or the commit result. A folder path
    # is not a Zoho website field — only stamp http(s) Files-app links.
    url = (
        account.get("nextcloud_folder_link")
        or account.get("nextcloud_folder_url")
        or payload.get("nextcloud_folder_url")
        or payload.get("nextcloud_folder_link")
    )
    if url and is_http_url(url):
        account["nextcloud_folder_url"] = url
        account["nextcloud_folder_link"] = url
    else:
        account.pop("nextcloud_folder_url", None)
        account.pop("nextcloud_folder_link", None)
    fid = account.get("nextcloud_file_id") or payload.get("nextcloud_file_id")
    if fid and str(fid).strip().isdigit():
        account["nextcloud_file_id"] = str(fid).strip()
    else:
        account.pop("nextcloud_file_id", None)
    # AMS GUID may already be stamped by the gateway AMS-first path.
    guid = (
        account.get("nowcerts_insured_guid")
        or payload.get("nowcerts_insured_guid")
        or payload.get("insured_database_id")
    )
    if guid and not account.get("nowcerts_insured_guid"):
        account["nowcerts_insured_guid"] = guid
    return account


def _note_body(payload: dict[str, Any], *, approved_by: str | None) -> tuple[str, str] | None:
    """Build a Zoho note title/body from the intake note + non-restricted facts."""
    note = payload.get("note") or {}
    title = str(note.get("title") or "").strip() or "Intake Note"
    parts: list[str] = []
    body = str(note.get("body") or "").strip()
    if body:
        parts.append(body)

    facts = payload.get("facts") or []
    safe_facts = [
        f for f in facts
        if isinstance(f, dict) and not _is_restricted_fact(f) and f.get("fact_value")
    ]
    if safe_facts:
        lines = ["", "## Cited facts"]
        for fact in safe_facts:
            label = fact.get("fact_label") or fact.get("label") or "Fact"
            entity = fact.get("entity") or ""
            value = fact.get("fact_value")
            prefix = f"{entity} — " if entity else ""
            lines.append(f"- {prefix}{label}: {value}")
        parts.append("\n".join(lines))

    if approved_by:
        parts.append(f"\n_Approved by {approved_by}_")

    content = "\n".join(parts).strip()
    if not content and not note:
        return None
    return title, content or "(no note body)"


def _pdf_paths(payload: dict[str, Any]) -> list[str]:
    """Collect local PDF paths available on the intake payload (if any)."""
    paths: list[str] = []

    for key in ("source_pdf_path", "intake_pdf_path", "pdf_path"):
        value = payload.get(key)
        if value and Path(str(value)).is_file():
            paths.append(str(value))

    documents = payload.get("documents") or []
    if isinstance(documents, list):
        for doc in documents:
            if not isinstance(doc, dict):
                continue
            for key in ("path", "file_path", "local_path", "stored_path"):
                value = doc.get(key)
                if value and Path(str(value)).is_file():
                    paths.append(str(value))
                    break

    # Bytes-only source PDF: write a temp file so upload_attachment can send it.
    source_pdf = payload.get("source_pdf")
    if isinstance(source_pdf, (bytes, bytearray)) and source_pdf:
        name = str(payload.get("source_pdf_name") or "intake.pdf")
        if not name.lower().endswith(".pdf"):
            name = f"{name}.pdf"
        tmp = tempfile.NamedTemporaryFile(prefix="hermes-intake-", suffix=".pdf", delete=False)
        try:
            tmp.write(source_pdf)
            tmp.flush()
            paths.append(tmp.name)
            # Stash so the caller/tests can clean up if they want; we unlink after upload.
            payload.setdefault("_zoho_temp_pdfs", []).append(tmp.name)
        finally:
            tmp.close()

    # De-dupe while preserving order.
    seen: set[str] = set()
    out: list[str] = []
    for path in paths:
        if path not in seen:
            seen.add(path)
            out.append(path)
    return out


def _deal_from_opportunity(
    opp: dict[str, Any],
    *,
    account: dict[str, Any],
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Flatten an intake opportunity into create_deal()'s expected keys."""
    deal = dict(opp)
    if not deal.get("client_identifier"):
        deal["client_identifier"] = payload.get("client_identifier")
    if not deal.get("insured_name"):
        deal["insured_name"] = (
            account.get("account_name")
            or account.get("legal_name")
            or account.get("name")
        )
    if not deal.get("insured_type"):
        deal["insured_type"] = account.get("insured_type") or account.get("account_type")
    if not deal.get("source"):
        source = payload.get("source")
        if isinstance(source, dict):
            deal["source"] = source.get("type") or source.get("source_ref")
        elif source:
            deal["source"] = source
    if not deal.get("producer_email"):
        # Prefer an explicit email; fall back to nothing (Owner stays unset).
        deal["producer_email"] = opp.get("producer_email") or opp.get("owner_email")
    if not deal.get("primary_folder_url"):
        deal["primary_folder_url"] = payload.get("deal_primary_folder_url")
    if not deal.get("document_url"):
        deal["document_url"] = payload.get("document_url")
    return deal


def write_intake_to_zoho(
    intake_payload: dict[str, Any],
    approved_by: str | None = None,
    *,
    client: Any = None,
) -> dict[str, Any]:
    """Create/update Account + Contacts + Deals in Zoho from an intake payload.

    Returns::

        {
          "zoho_account_id": str | None,
          "zoho_deal_ids": [str],
          "zoho_contact_ids": [str],
          "errors": [str],
        }

    Never stages AMS / NowCerts work. Failures on individual records are
    collected in ``errors`` so a partial write still returns what landed.
    """
    from hermes_integrations.zoho_client import ZohoClientError, get_client

    result: dict[str, Any] = {
        "zoho_account_id": None,
        "zoho_deal_ids": [],
        "zoho_contact_ids": [],
        "errors": [],
    }

    try:
        zoho = client or get_client()
    except Exception as exc:  # noqa: BLE001 — missing creds must not fail the commit
        msg = f"zoho client unavailable: {exc}"
        log.warning("write_intake_to_zoho: %s", msg)
        result["errors"].append(msg)
        return result

    account = _account_block(intake_payload)
    if not account.get("account_name") and not account.get("nowcerts_insured_guid"):
        msg = "intake payload has no account_name / nowcerts_insured_guid — nothing to write"
        log.warning("write_intake_to_zoho: %s", msg)
        result["errors"].append(msg)
        return result

    # 1. Account
    try:
        account_result = zoho.create_or_update_account(account)
        account_id = str(account_result["id"])
        result["zoho_account_id"] = account_id
        log.info(
            "write_intake_to_zoho: account %s (%s) approved_by=%s",
            account_id,
            account_result.get("action"),
            approved_by,
        )
    except ZohoClientError as exc:
        msg = f"account write failed: {exc}"
        log.exception("write_intake_to_zoho: %s", msg)
        result["errors"].append(msg)
        return result
    except Exception as exc:  # noqa: BLE001
        msg = f"account write failed: {exc}"
        log.exception("write_intake_to_zoho: %s", msg)
        result["errors"].append(msg)
        return result

    # 2. Contacts
    contacts = intake_payload.get("contacts") or []
    if isinstance(contacts, list):
        for idx, contact in enumerate(contacts):
            if not isinstance(contact, dict):
                continue
            try:
                contact_result = zoho.create_or_update_contact(contact, account_id)
                cid = str(contact_result["id"])
                result["zoho_contact_ids"].append(cid)
                log.info(
                    "write_intake_to_zoho: contact[%d] %s (%s)",
                    idx,
                    cid,
                    contact_result.get("action"),
                )
            except Exception as exc:  # noqa: BLE001 — continue on per-record failure
                msg = f"contact[{idx}] write failed: {exc}"
                log.exception("write_intake_to_zoho: %s", msg)
                result["errors"].append(msg)

    # 3. Deals (one per LOB — always create)
    opportunities = intake_payload.get("opportunities") or []
    if isinstance(opportunities, list):
        for idx, opp in enumerate(opportunities):
            if not isinstance(opp, dict):
                continue
            if not (opp.get("line_of_business") or opp.get("opportunity_name") or opp.get("Deal_Name")):
                result["errors"].append(f"opportunity[{idx}] skipped: no LOB / name")
                continue
            try:
                deal = _deal_from_opportunity(opp, account=account, payload=intake_payload)
                deal_result = zoho.create_deal(deal, account_id)
                did = str(deal_result["id"])
                result["zoho_deal_ids"].append(did)
                log.info(
                    "write_intake_to_zoho: deal[%d] %s lob=%r",
                    idx,
                    did,
                    opp.get("line_of_business"),
                )
            except Exception as exc:  # noqa: BLE001
                msg = f"deal[{idx}] write failed: {exc}"
                log.exception("write_intake_to_zoho: %s", msg)
                result["errors"].append(msg)

    # 4. Note (skip restricted facts)
    note = _note_body(intake_payload, approved_by=approved_by)
    if note:
        title, content = note
        try:
            note_result = zoho.create_note("Accounts", account_id, title, content)
            log.info("write_intake_to_zoho: note %s on account %s", note_result.get("id"), account_id)
        except Exception as exc:  # noqa: BLE001
            msg = f"note write failed: {exc}"
            log.exception("write_intake_to_zoho: %s", msg)
            result["errors"].append(msg)

    # 5. PDF attachments — only if Nextcloud did not produce a clickable URL.
    # Zoho is not a second document library.
    from hermes_integrations.zoho_document_fields import is_http_url

    folder_url = account.get("nextcloud_folder_url") or intake_payload.get("nextcloud_folder_url")
    temp_pdfs = list(intake_payload.get("_zoho_temp_pdfs") or [])
    try:
        if is_http_url(folder_url):
            log.info(
                "write_intake_to_zoho: skipping Zoho attachments; files live at %s",
                folder_url,
            )
        else:
            for pdf_path in _pdf_paths(intake_payload):
                try:
                    att = zoho.upload_attachment("Accounts", account_id, pdf_path)
                    log.info(
                        "write_intake_to_zoho: attachment %s file=%s",
                        att.get("id"),
                        os.path.basename(pdf_path),
                    )
                except Exception as exc:  # noqa: BLE001
                    msg = f"attachment {pdf_path!r} failed: {exc}"
                    log.exception("write_intake_to_zoho: %s", msg)
                    result["errors"].append(msg)
    finally:
        # Clean any temp files we created from source_pdf bytes.
        for path in list(intake_payload.get("_zoho_temp_pdfs") or []) + temp_pdfs:
            try:
                Path(path).unlink(missing_ok=True)
            except OSError:
                pass
        intake_payload.pop("_zoho_temp_pdfs", None)

    # 6. Nextcloud folder URL — already mapped in step 1 when present on the
    # account block. If the commit only produced the URL after the first write
    # (stashed on the payload top-level), stamp it now.
    folder_url = intake_payload.get("nextcloud_folder_url")
    if folder_url and result["zoho_account_id"] and not account.get("nextcloud_folder_url"):
        from hermes_integrations.zoho_document_fields import is_http_url

        if is_http_url(folder_url):
            try:
                zoho.create_or_update_account(
                    {
                        "account_name": account.get("account_name"),
                        "nowcerts_insured_guid": account.get("nowcerts_insured_guid"),
                        "nextcloud_folder_url": folder_url,
                        "nextcloud_file_id": intake_payload.get("nextcloud_file_id")
                        or account.get("nextcloud_file_id"),
                    }
                )
                log.info(
                    "write_intake_to_zoho: stamped Nextcloud_Folder_Link on %s",
                    result["zoho_account_id"],
                )
            except Exception as exc:  # noqa: BLE001
                msg = f"nextcloud url stamp failed: {exc}"
                log.exception("write_intake_to_zoho: %s", msg)
                result["errors"].append(msg)

    log.info(
        "write_intake_to_zoho: done account=%s contacts=%d deals=%d errors=%d",
        result["zoho_account_id"],
        len(result["zoho_contact_ids"]),
        len(result["zoho_deal_ids"]),
        len(result["errors"]),
    )
    return result
