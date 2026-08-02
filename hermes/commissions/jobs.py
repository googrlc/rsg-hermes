"""The runner — what keeps the commission surface true when nobody logs in.

The nightly book sync and commission seed (both in rsg-hermes cron) already pull
new business and renewals from NowCerts into ``canonical_policies`` and write
the EXPECTED side of ``commission_ledger``. That is the top of the pipe and it
works. What was missing is everything after it:

  reconcile   ``run_rollup`` only ever ran when a human approved a statement, so
              a row's status was frozen at whatever it was the day money last
              landed. A term that ended last week still read ``missing_statement``
              — "more statements expected" — when it should read ``underpaid``
              and be chased. Status is derived, so it has to be re-derived on a
              clock, not on an upload.

  collect     Statements arrived in a Nextcloud folder and sat there until
              somebody uploaded them by hand. The poller stages them; it does
              **not** commit them. Nothing here approves money.

  watch       A cron job that stops running looks exactly like a quiet week.
              The watchdog reports the pipeline's own freshness, so a dead
              sync surfaces as an alert rather than as a ledger that stopped
              changing.

Each job returns a report and is safe to run twice: the reconcile recomputes
from transactions rather than accumulating, and the poller dedupes on the
statement's content hash — the same UNIQUE constraint that guards the upload
route. Nothing in this module writes to NowCerts.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from hermes_integrations.supabase_client import SupabaseClient

log = logging.getLogger(__name__)

# Where statements are dropped for the poller. A folder, not a mailbox: the
# mailbox rule that files them there is Nextcloud's job, and a folder is the
# thing a human can also drag a file into.
INBOX_DIR = os.environ.get("HERMES_COMMISSION_INBOX", "Commissions/Inbox")

# Extensions the poller will pick up. Anything else in the folder is left alone
# and reported — silently ignoring a file is how a statement goes missing.
INBOX_SUFFIXES = frozenset({"csv", "tsv", "txt", "xlsx", "xlsm", "pdf"})

# The identity a staged batch is attributed to. It is deliberately not a person:
# nobody approved anything by dropping a file in a folder.
POLLER_IDENTITY = os.environ.get("HERMES_COMMISSION_POLLER_ID", "commission-inbox-poller")

# How stale the ledger may get before the watchdog calls it a problem. The seed
# runs nightly, so a day and a half is one missed run plus room for a slow night.
#
# Only the LEDGER is alarmed on, and that is a deliberate choice about what the
# data can actually prove. ``canonical_policies`` has no updated_at — only
# created_at, which moves when a NEW policy appears and not when the sync runs.
# Several days this month added no policies at all, so a freshness rule on the
# book would fire on a quiet fortnight and teach everyone to ignore the channel.
#
# ``commission_ledger.updated_at`` is the honest signal: the 2:25 seed rewrites
# the expected side of every commissionable row every night, and it reads the
# book the 2:20 sync just refreshed. A fresh ledger therefore proves the whole
# nightly chain ran. A stale one means it did not.
LEDGER_FRESHNESS_HOURS = int(os.environ.get("HERMES_COMMISSION_STALE_HOURS", "36"))

SLACK_CHANNEL = os.environ.get("HERMES_COMMISSION_ALERT_CHANNEL", "#systems-check")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_stamp(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value).strip().replace("Z", "+00:00")
    # Postgres prints microseconds and a +00 offset; fromisoformat wants +00:00.
    if len(text) > 3 and text[-3] == "+" and text[-2:].isdigit():
        text += ":00"
    try:
        stamp = datetime.fromisoformat(text)
    except ValueError:
        return None
    return stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)


# --- nightly reconcile --------------------------------------------------------

@dataclass
class ReconcileReport:
    linked: int = 0
    ledger_rows_created: int = 0
    unmatched: int = 0
    examined: int = 0
    changed: int = 0
    by_status: dict[str, int] = field(default_factory=dict)
    transitions: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    dry_run: bool = False

    @property
    def message(self) -> str:
        counts = " ".join(f"{k}={v}" for k, v in sorted(self.by_status.items()))
        return (
            f"commission reconcile ({'dry-run' if self.dry_run else 'live'}): "
            f"linked={self.linked} (+{self.ledger_rows_created} ledger rows) "
            f"unmatched={self.unmatched} | examined={self.examined} "
            f"changed={self.changed} | {counts}"
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "linked": self.linked, "ledger_rows_created": self.ledger_rows_created,
            "unmatched": self.unmatched, "examined": self.examined,
            "changed": self.changed, "by_status": self.by_status,
            "transitions": self.transitions, "errors": self.errors,
            "dry_run": self.dry_run, "message": self.message,
        }


def nightly_reconcile(
    supa: "SupabaseClient", *, dry_run: bool = False, today: date | None = None,
) -> ReconcileReport:
    """Attach orphaned statement lines, then re-derive every affected row.

    Order matters: linking first means a line that arrived without a ledger row
    gets one before the rollup runs, so its money is counted on this pass rather
    than the next. Both steps are idempotent.

    A row whose term ended since the last run changes status here even though no
    new money arrived — that transition is the entire point of running this on a
    clock, and it is the reason the report lists transitions rather than only
    counts.
    """
    from hermes.commissions.matching import relink_unmatched
    from hermes.commissions.reconcile import run_rollup

    report = ReconcileReport(dry_run=dry_run)

    link = relink_unmatched(supa, dry_run=dry_run)
    report.linked = link.linked
    report.ledger_rows_created = link.ledger_rows_created
    report.unmatched = link.unmatched
    report.errors.extend(link.errors)

    roll = run_rollup(supa, dry_run=dry_run, today=today)
    report.examined = roll.examined
    report.changed = roll.changed
    report.by_status = dict(roll.by_status)
    report.transitions = list(roll.details)

    log.info("%s", report.message)
    return report


# --- the statement inbox ------------------------------------------------------

@dataclass
class InboxReport:
    folder: str = INBOX_DIR
    seen: int = 0
    staged: int = 0
    duplicates: int = 0
    failed: int = 0
    ignored: list[str] = field(default_factory=list)
    batches: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    dry_run: bool = False
    configured: bool = True
    # The drop folder does not exist. Distinct from "exists and is empty":
    # WebDAV answers 404 with an empty listing, so without this check a folder
    # nobody ever created reports "0 files" every night, forever, and looks
    # exactly like a quiet month. That is the silent-exclusion failure this
    # codebase has already paid for twice.
    folder_missing: bool = False

    @property
    def message(self) -> str:
        if not self.configured:
            return "commission inbox: Nextcloud is not configured — nothing polled"
        if self.folder_missing:
            return (
                f"commission inbox: the drop folder {self.folder!r} does not exist — "
                "no statement can ever arrive until it is created"
            )
        return (
            f"commission inbox ({'dry-run' if self.dry_run else 'live'}) {self.folder}: "
            f"seen={self.seen} staged={self.staged} duplicate={self.duplicates} "
            f"failed={self.failed} ignored={len(self.ignored)}"
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "folder": self.folder, "seen": self.seen, "staged": self.staged,
            "duplicates": self.duplicates, "failed": self.failed,
            "ignored": self.ignored, "batches": self.batches,
            "errors": self.errors, "dry_run": self.dry_run,
            "configured": self.configured,
            "folder_missing": self.folder_missing, "message": self.message,
        }


def _suffix(name: str) -> str:
    return (name.rsplit(".", 1)[-1] if "." in name else "").lower()


def poll_inbox(
    supa: "SupabaseClient",
    *,
    folder: str | None = None,
    dry_run: bool = False,
    nextcloud: Any = None,
    limit: int = 25,
) -> InboxReport:
    """Stage every new statement sitting in the drop folder.

    STAGES ONLY. Each file lands as a ``pending_review`` batch with its parse,
    its crosscheck and its match preview — exactly what an upload through the UI
    produces. Approval stays a human act with a name attached, which is the one
    property of this system worth never automating away.

    Re-polling is free: a file already staged has the same content hash and is
    rejected by the database before a line is parsed, so the folder does not
    need to be cleared for correctness. Files are left in place — moving them
    would make the folder the state, and the state is the batch table.
    """
    from hermes.commissions.statements import stage_statement

    report = InboxReport(folder=folder or INBOX_DIR, dry_run=dry_run)

    if nextcloud is None:
        from hermes_integrations.nextcloud_client import NextcloudClient

        nextcloud = NextcloudClient()
    if not nextcloud.is_configured():
        report.configured = False
        log.info("%s", report.message)
        return report

    # Ask whether the folder is there before asking what is in it: WebDAV answers
    # a missing folder with the same empty listing as an empty one.
    try:
        if not nextcloud.path_exists(report.folder):
            report.folder_missing = True
            log.warning("%s", report.message)
            return report
    except Exception as exc:  # noqa: BLE001 — an unreachable share is not a crash
        log.exception("commission inbox: could not reach %s", report.folder)
        report.errors.append(f"reach {report.folder}: {exc}")
        return report

    try:
        entries = nextcloud.list_dir(report.folder)
    except Exception as exc:  # noqa: BLE001
        log.exception("commission inbox: could not list %s", report.folder)
        report.errors.append(f"list {report.folder}: {exc}")
        return report

    for entry in entries:
        if entry.get("is_dir"):
            continue
        name = str(entry.get("name") or "")
        if _suffix(name) not in INBOX_SUFFIXES:
            report.ignored.append(name)
            continue

        report.seen += 1
        if report.staged + report.duplicates + report.failed >= limit:
            report.errors.append(
                f"stopped at {limit} files this run — the rest will be picked up "
                "on the next poll"
            )
            break
        if dry_run:
            report.batches.append({"file": name, "staged": False, "dry_run": True})
            continue

        try:
            content = nextcloud.read_file(str(entry.get("path") or name))
        except Exception as exc:  # noqa: BLE001
            report.failed += 1
            report.errors.append(f"read {name}: {exc}")
            continue

        try:
            staged = stage_statement(
                supa, content=content, filename=name, uploaded_by=POLLER_IDENTITY,
            )
        except Exception as exc:  # noqa: BLE001 — one bad file must not stop the folder
            report.failed += 1
            report.errors.append(f"stage {name}: {exc}")
            log.exception("commission inbox: staging failed for %s", name)
            continue

        if staged.duplicate_of:
            report.duplicates += 1
            continue

        report.staged += 1
        report.batches.append({
            "file": name,
            "batch_id": staged.batch_id,
            "carrier": staged.carrier,
            "lines": staged.line_count,
            "status": staged.status,
            "approvable": staged.approvable,
            "requires_confirmation": staged.requires_confirmation,
            "crosscheck_ok": staged.crosscheck.ok,
            "warnings": staged.warnings,
        })

    log.info("%s", report.message)
    return report


# --- the watchdog -------------------------------------------------------------

@dataclass
class Problem:
    kind: str
    detail: str

    def as_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "detail": self.detail}


@dataclass
class HealthReport:
    checked_at: str = ""
    book_policies: int = 0
    # The newest policy in the book — context, not a freshness signal. See the
    # comment on LEDGER_FRESHNESS_HOURS for why it is never alarmed on.
    book_newest_policy: str | None = None
    ledger_rows: int = 0
    ledger_last_updated: str | None = None
    pending_batches: int = 0
    unmatched_transactions: int = 0
    coverage: dict[str, Any] = field(default_factory=dict)
    rows_without_expected: int = 0
    problems: list[Problem] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems

    @property
    def message(self) -> str:
        headline = "commission pipeline OK" if self.ok else (
            f"commission pipeline: {len(self.problems)} problem(s)"
        )
        return (
            f"{headline} — book={self.book_policies} policies "
            f"ledger={self.ledger_rows} (updated {self.ledger_last_updated}) "
            f"pending_batches={self.pending_batches} "
            f"unmatched_lines={self.unmatched_transactions}"
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok, "checked_at": self.checked_at,
            "book_policies": self.book_policies,
            "book_newest_policy": self.book_newest_policy,
            "ledger_rows": self.ledger_rows,
            "ledger_last_updated": self.ledger_last_updated,
            "pending_batches": self.pending_batches,
            "unmatched_transactions": self.unmatched_transactions,
            "rows_without_expected": self.rows_without_expected,
            "coverage": self.coverage,
            "problems": [p.as_dict() for p in self.problems],
            "message": self.message,
        }


def _latest(rows: list[dict[str, Any]], *columns: str) -> datetime | None:
    stamps = [
        stamp
        for row in rows
        for column in columns
        if (stamp := _parse_stamp(row.get(column))) is not None
    ]
    return max(stamps) if stamps else None


def watchdog(supa: "SupabaseClient", *, now: datetime | None = None) -> HealthReport:
    """Is the pipeline actually running, and does its arithmetic still balance?

    Three questions, each mapping to a way this has failed or could:

      1. Did the nightly chain run? A stale ``commission_ledger.updated_at``
                                means the 2:25 seed did not, which means the
                                2:20 book sync it depends on probably did not
                                either. A dead cron otherwise looks exactly like
                                a quiet week.
      2. Does coverage balance? Every active policy must land in exactly one
                                bucket. An unbalanced count means a policy fell
                                off the map unexplained.
      3. Is work piling up?     Batches stuck in review and statement lines that
                                never matched are money nobody is looking at.
    """
    from hermes_core import book as ams_book
    from hermes.commissions.surface import coverage

    now = now or _now()
    report = HealthReport(checked_at=now.isoformat())

    try:
        policies = ams_book.select_policies(
            supa,
            columns="policy_number,status,active,effective_date,created_at,"
                    "annualized_premium,current_term_amount,premium_amount",
            limit=20000,
        )
    except Exception as exc:  # noqa: BLE001
        log.exception("watchdog: book read failed")
        report.problems.append(Problem("book_unreadable", f"could not read the book: {exc}"))
        policies = []

    report.book_policies = len(policies)
    newest = _latest(policies, "created_at")
    report.book_newest_policy = newest.isoformat() if newest else None

    try:
        ledger = supa.select(
            "commission_ledger",
            columns="id,policy_number,expected_commission,updated_at,created_at",
            limit=50000,
        )
    except Exception as exc:  # noqa: BLE001
        log.exception("watchdog: ledger read failed")
        report.problems.append(Problem("ledger_unreadable", f"could not read the ledger: {exc}"))
        ledger = []

    report.ledger_rows = len(ledger)
    ledger_updated = _latest(ledger, "updated_at", "created_at")
    report.ledger_last_updated = ledger_updated.isoformat() if ledger_updated else None
    report.rows_without_expected = sum(
        1 for row in ledger if row.get("expected_commission") in (None, "")
    )

    if ledger:
        if ledger_updated is None:
            report.problems.append(Problem(
                "no_timestamp",
                "commission_ledger carries no usable timestamp — freshness unknown",
            ))
        else:
            age = now - ledger_updated
            if age > timedelta(hours=LEDGER_FRESHNESS_HOURS):
                report.problems.append(Problem(
                    "stale",
                    f"commission_ledger last changed "
                    f"{int(age.total_seconds() // 3600)}h ago (limit "
                    f"{LEDGER_FRESHNESS_HOURS}h) — the nightly book sync and "
                    "commission seed may not be running",
                ))

    if policies:
        ledger_numbers = {
            str(row.get("policy_number") or "").strip()
            for row in ledger if row.get("policy_number")
        }
        cover = coverage(policies, ledger_numbers)
        report.coverage = cover.as_dict()
        if not cover.balanced:
            report.problems.append(Problem(
                "coverage_unbalanced",
                f"{cover.active_policies} active policies but {cover.accounted} "
                "accounted for — a policy is unexplained",
            ))
        if cover.missing_in_window:
            report.problems.append(Problem(
                "missing_from_ledger",
                f"{cover.missing_in_window} commissionable policies "
                f"(${cover.missing_in_window_premium:,.0f} premium) qualify but "
                "have no ledger row",
            ))

    try:
        batches = supa.select(
            "commission_ingest_batches", columns="id,ingest_status,created_at",
            params={"ingest_status": "eq.pending_review"}, limit=500,
        )
        report.pending_batches = len(batches)
    except Exception:  # noqa: BLE001 — a count is context, not the point of the check
        log.exception("watchdog: batch read failed")

    try:
        txns = supa.select("commission_transactions", columns="id,ledger_id", limit=50000)
        report.unmatched_transactions = sum(1 for row in txns if not row.get("ledger_id"))
    except Exception:  # noqa: BLE001
        log.exception("watchdog: transaction read failed")

    log.info("%s", report.message)
    return report


# --- alerting -----------------------------------------------------------------

def alert(report: HealthReport, *, notifier: Any = None) -> bool:
    """Post a watchdog failure to Slack. Returns whether anything was sent.

    A healthy run says nothing. A channel that only ever speaks when something
    is wrong is a channel people still read.
    """
    if report.ok:
        return False

    lines = [f":rotating_light: {report.message}", ""]
    lines.extend(f"• *{problem.kind}* — {problem.detail}" for problem in report.problems)

    if notifier is None:
        try:
            from hermes_integrations.slack_notifier import SlackNotifier

            notifier = SlackNotifier(channel=SLACK_CHANNEL)
        except Exception:  # noqa: BLE001
            log.exception("watchdog: no Slack notifier available")
            return False
    try:
        notifier.post_message(text="\n".join(lines))
    except Exception:  # noqa: BLE001 — a failed alert must not fail the job
        log.exception("watchdog: alert post failed")
        return False
    return True


# --- CLI ----------------------------------------------------------------------

def main() -> int:
    """``rsg-finance-jobs --reconcile | --poll-inbox | --watchdog``.

    Cron's entry point. Every job takes ``--dry-run``, and the watchdog exits
    non-zero when it finds a problem so a supervisor can notice too.
    """
    import argparse
    import json

    from dotenv import load_dotenv

    load_dotenv()
    logging.basicConfig(level=os.environ.get("HERMES_API_LOG_LEVEL", "INFO"))

    parser = argparse.ArgumentParser(description="RSG commission runner")
    parser.add_argument("--reconcile", action="store_true",
                        help="link orphaned statement lines and re-derive ledger status")
    parser.add_argument("--poll-inbox", action="store_true",
                        help="stage new statements from the Nextcloud drop folder")
    parser.add_argument("--watchdog", action="store_true",
                        help="report pipeline freshness and coverage; alert on problems")
    parser.add_argument("--dry-run", action="store_true", help="report without writing")
    parser.add_argument("--no-alert", action="store_true",
                        help="watchdog only: skip the Slack post")
    parser.add_argument("--json", action="store_true", help="print the full report as JSON")
    args = parser.parse_args()

    if not (args.reconcile or args.poll_inbox or args.watchdog):
        parser.error("choose at least one of --reconcile, --poll-inbox, --watchdog")

    from hermes_app import deps

    supa = deps.get_supa()
    reports: dict[str, Any] = {}
    exit_code = 0

    if args.poll_inbox:
        inbox = poll_inbox(supa, dry_run=args.dry_run)
        reports["inbox"] = inbox.as_dict()
        print(inbox.message)

    if args.reconcile:
        recon = nightly_reconcile(supa, dry_run=args.dry_run)
        reports["reconcile"] = recon.as_dict()
        print(recon.message)

    if args.watchdog:
        health = watchdog(supa)
        reports["health"] = health.as_dict()
        print(health.message)
        for problem in health.problems:
            print(f"  - {problem.kind}: {problem.detail}")
        if not args.no_alert and not args.dry_run:
            alert(health)
        if not health.ok:
            exit_code = 1

    if args.json:
        print(json.dumps(reports, indent=2, default=str))
    return exit_code
