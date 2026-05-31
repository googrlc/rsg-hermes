"""Email triage: poll a mailbox, route each message.

For each inbound message:
  • actionable insurance matter → insert an ``intake_submissions`` row
    (status=received). The existing intake worker then synthesizes → drafts →
    awaits approval → writes Contact / Opportunity / Task into EspoCRM. Triage
    deliberately performs **no** CRM writes itself.
  • noise (newsletters / bulk / automated) → moved to a quarantine folder
    (default "Hermes Triage") for the operator to review and delete. Never
    auto-deleted.

Idempotency: every processed message is tagged with the ``Hermes Triaged``
category; already-tagged messages are skipped on the next poll. Actionable
messages additionally dedupe downstream via ``internetMessageId`` as the
intake idempotency key.

``dry_run=True`` (the default for the CLI) classifies and logs the intended
action for every message without inserting intake rows or moving any mail —
use it to validate classifier accuracy before going live.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from hermes.integrations.intake_submissions import insert_submission
from hermes.integrations.ms365_client import MS365Client, MS365ClientError
from hermes.integrations.supabase_client import SupabaseClient
from hermes.sync.email_classifier import classify

log = logging.getLogger(__name__)

# Microsoft 365 (Outlook folders + categories).
TRIAGE_CATEGORY = "Hermes Triaged"
DEFAULT_QUARANTINE_FOLDER = "Hermes Triage"

# Gmail (labels — there are no folders). The triaged marker keeps re-polls
# idempotent; the quarantine label + INBOX removal is the "move".
GMAIL_TRIAGED_LABEL = "Hermes/Triaged"
GMAIL_QUARANTINE_LABEL = "Hermes/Triage"

# intake_submissions vocabulary (see its CHECK constraints). intake_kind is the
# TYPE of intake (full_intake|task|note|update|other) — NOT the insurance line
# of business; the classifier's LOB guess rides along in the payload instead.
# agent is the human the intake belongs to (lamar|gretchen) — the mailbox owner.
INTAKE_KIND = "full_intake"
INTAKE_AGENT = "lamar"


@dataclass
class EmailTriageResult:
    """Summary of one triage run."""

    provider: str = "ms365"
    mailboxes: list[str] = field(default_factory=list)
    scanned: int = 0
    actionable: int = 0
    quarantined: int = 0
    skipped: int = 0
    records_failed: int = 0
    errors: list[str] = field(default_factory=list)
    dry_run: bool = False

    @property
    def ok(self) -> bool:
        return self.records_failed == 0

    @property
    def message(self) -> str:
        prefix = "DRY RUN: " if self.dry_run else ""
        return (
            f"{prefix}{self.provider} triage complete across {len(self.mailboxes)} "
            f"mailbox(es): scanned={self.scanned} actionable={self.actionable} "
            f"quarantined={self.quarantined} skipped={self.skipped} "
            f"failed={self.records_failed}"
        )


def _sender_address(msg: dict[str, Any]) -> str:
    addr = (msg.get("from") or msg.get("sender") or {}).get("emailAddress") or {}
    return addr.get("address", "") or ""


def _sender_label(msg: dict[str, Any]) -> str:
    addr = (msg.get("from") or msg.get("sender") or {}).get("emailAddress") or {}
    name, email = addr.get("name", ""), addr.get("address", "")
    return f"{name} <{email}>".strip() if name else email


def run_ms365_triage(
    client: MS365Client,
    supa: SupabaseClient,
    *,
    mailboxes: list[str],
    since_hours: int = 24,
    dry_run: bool = True,
    quarantine_folder: str = DEFAULT_QUARANTINE_FOLDER,
) -> EmailTriageResult:
    """Triage the Inbox of each mailbox via Microsoft Graph."""
    result = EmailTriageResult(
        provider="ms365", mailboxes=list(mailboxes), dry_run=dry_run
    )
    since_iso = (
        datetime.now(timezone.utc) - timedelta(hours=since_hours)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")

    for mailbox in mailboxes:
        try:
            messages = client.list_inbox_messages(mailbox, since_iso=since_iso)
        except MS365ClientError as exc:
            result.records_failed += 1
            result.errors.append(f"{mailbox}: list failed: {exc}")
            continue

        # Resolve the quarantine folder once per mailbox (live runs only).
        quarantine_id: str | None = None

        for msg in messages:
            result.scanned += 1
            msg_id = msg.get("id", "")
            if TRIAGE_CATEGORY in (msg.get("categories") or []):
                result.skipped += 1
                continue

            sender = _sender_address(msg)
            subject = msg.get("subject", "") or ""
            preview = msg.get("bodyPreview", "") or ""

            verdict = classify(sender=sender, subject=subject, preview=preview)
            tag = f"[{mailbox}] {subject[:60]!r} <- {_sender_label(msg)}"

            try:
                if verdict.is_actionable:
                    _handle_actionable(
                        client, supa, mailbox, msg, verdict, dry_run=dry_run
                    )
                    result.actionable += 1
                    log.info(
                        "%sACTIONABLE %s (kind=%s, %.2f): %s",
                        "DRY " if dry_run else "", tag, verdict.intake_kind,
                        verdict.confidence, verdict.reason,
                    )
                else:
                    if not dry_run:
                        if quarantine_id is None:
                            quarantine_id = client.ensure_folder(mailbox, quarantine_folder)
                        client.add_category(mailbox, msg_id, TRIAGE_CATEGORY)
                        client.move_message(mailbox, msg_id, quarantine_id)
                    result.quarantined += 1
                    log.info(
                        "%sQUARANTINE %s: %s",
                        "DRY " if dry_run else "", tag, verdict.reason,
                    )
            except Exception as exc:  # noqa: BLE001 — isolate per-message failures
                result.records_failed += 1
                result.errors.append(f"{tag}: {exc}")

    return result


def _handle_actionable(
    client: MS365Client,
    supa: SupabaseClient,
    mailbox: str,
    msg: dict[str, Any],
    verdict: Any,
    *,
    dry_run: bool,
) -> None:
    """Drop an actionable email into the intake pipeline (no CRM write here)."""
    if dry_run:
        return

    msg_id = msg["id"]
    full = client.get_message_body(mailbox, msg_id)
    body = full.get("body") or {}
    internet_id = msg.get("internetMessageId") or msg_id

    payload = {
        "channel": "email",
        "provider": "ms365",
        "mailbox": mailbox,
        "message_id": msg_id,
        "internet_message_id": internet_id,
        "from": _sender_label(msg),
        "from_address": _sender_address(msg),
        "subject": msg.get("subject", ""),
        "received_at": msg.get("receivedDateTime", ""),
        "body_content_type": body.get("contentType", "text"),
        "body": body.get("content", "") or msg.get("bodyPreview", ""),
        "classifier_reason": verdict.reason,
        "lob_guess": verdict.intake_kind,
    }

    insert_submission(
        supa,
        idempotency_key=internet_id,
        source="email-ms365",
        agent=INTAKE_AGENT,
        intake_kind=INTAKE_KIND,
        client_identifier=_sender_address(msg) or None,
        lob_code=None,
        captured_at=datetime.now(timezone.utc),
        payload=payload,
    )
    # Tag so the next poll skips it even though it stays in the Inbox.
    client.add_category(mailbox, msg_id, TRIAGE_CATEGORY)


# ── Gmail ─────────────────────────────────────────────────────────────────


def run_gmail_triage(
    client: "GmailClient",  # noqa: F821 — imported lazily by the caller
    supa: SupabaseClient,
    *,
    mailboxes: list[str],
    since_hours: int = 24,
    dry_run: bool = True,
) -> EmailTriageResult:
    """Triage the Inbox of each Gmail mailbox via the Gmail API.

    Idempotency uses the ``Hermes/Triaged`` label (excluded server-side on each
    poll). Noise is "moved" by adding ``Hermes/Triage`` and removing ``INBOX``.
    """
    result = EmailTriageResult(
        provider="gmail", mailboxes=list(mailboxes), dry_run=dry_run
    )
    after_epoch = int(
        (datetime.now(timezone.utc) - timedelta(hours=since_hours)).timestamp()
    )

    for mailbox in mailboxes:
        try:
            messages = client.list_inbox_messages(
                mailbox,
                after_epoch=after_epoch,
                exclude_label=GMAIL_TRIAGED_LABEL,
            )
        except Exception as exc:  # noqa: BLE001
            result.records_failed += 1
            result.errors.append(f"{mailbox}: list failed: {exc}")
            continue

        triaged_id: str | None = None
        quarantine_id: str | None = None

        for msg in messages:
            result.scanned += 1
            msg_id = msg.get("id", "")
            sender = client.header(msg, "From")
            subject = client.header(msg, "Subject")
            preview = msg.get("snippet", "") or ""
            has_unsub = bool(client.header(msg, "List-Unsubscribe"))

            verdict = classify(
                sender=sender, subject=subject, preview=preview, has_unsubscribe=has_unsub
            )
            tag = f"[{mailbox}] {subject[:60]!r} <- {sender}"

            try:
                if not dry_run and triaged_id is None:
                    triaged_id = client.ensure_label(mailbox, GMAIL_TRIAGED_LABEL)

                if verdict.is_actionable:
                    if not dry_run:
                        _handle_actionable_gmail(client, supa, mailbox, msg, verdict)
                        client.modify_message(mailbox, msg_id, add_label_ids=[triaged_id])
                    result.actionable += 1
                    log.info(
                        "%sACTIONABLE %s (kind=%s, %.2f): %s",
                        "DRY " if dry_run else "", tag, verdict.intake_kind,
                        verdict.confidence, verdict.reason,
                    )
                else:
                    if not dry_run:
                        if quarantine_id is None:
                            quarantine_id = client.ensure_label(mailbox, GMAIL_QUARANTINE_LABEL)
                        client.modify_message(
                            mailbox, msg_id,
                            add_label_ids=[quarantine_id, triaged_id],
                            remove_label_ids=["INBOX"],
                        )
                    result.quarantined += 1
                    log.info(
                        "%sQUARANTINE %s: %s",
                        "DRY " if dry_run else "", tag, verdict.reason,
                    )
            except Exception as exc:  # noqa: BLE001
                result.records_failed += 1
                result.errors.append(f"{tag}: {exc}")

    return result


def _handle_actionable_gmail(
    client: "GmailClient",  # noqa: F821
    supa: SupabaseClient,
    mailbox: str,
    msg: dict[str, Any],
    verdict: Any,
) -> None:
    """Drop an actionable Gmail message into the intake pipeline."""
    msg_id = msg["id"]
    full = client.get_message_full(mailbox, msg_id)
    # RFC822 Message-Id is the stable cross-system idempotency key.
    internet_id = client.header(full, "Message-Id") or f"gmail:{msg_id}"
    sender = client.header(full, "From")

    payload = {
        "channel": "email",
        "provider": "gmail",
        "mailbox": mailbox,
        "message_id": msg_id,
        "internet_message_id": internet_id,
        "from": sender,
        "subject": client.header(full, "Subject"),
        "received_at": client.header(full, "Date"),
        "body_content_type": "text",
        "body": client.extract_text(full),
        "classifier_reason": verdict.reason,
        "lob_guess": verdict.intake_kind,
    }

    insert_submission(
        supa,
        idempotency_key=internet_id,
        source="email-gmail",
        agent=INTAKE_AGENT,
        intake_kind=INTAKE_KIND,
        client_identifier=sender or None,
        lob_code=None,
        captured_at=datetime.now(timezone.utc),
        payload=payload,
    )
