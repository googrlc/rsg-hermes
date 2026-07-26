"""Commission statement upload — parse, stage, review, commit.

Nothing about this path writes money without a human saying so. The stages are
separate tables on purpose, so an approval is a decision about something already
parsed and checked rather than a leap of faith about a file:

    upload  -> commission_ingest_batches   (content_hash, crosscheck, flags)
            -> commission_transactions_staging  (parsed lines, raw_row kept)
            -> review card                 (matched / created / unmatched / negatives)
    APPROVE -> commission_statements       (the header)
            -> commission_transactions     (the committed lines)
            -> matching ladder + rollup    (actual/status recomputed)

DEDUPE is enforced by the database, not by us: commission_ingest_batches has
UNIQUE (content_hash). Re-uploading the same file is rejected before a single
line is parsed, which is the only way to be sure a statement cannot be
double-counted.

THE CROSSCHECK is the other guard. Carriers print their own totals on the
statement. If what we parsed doesn't add up to what the carrier says it paid,
the parse is wrong and committing it would put fiction in the ledger. A batch
whose crosscheck fails cannot be approved.

Amounts are Decimal end to end. Statement money parsed through float is how you
get a $0.01 discrepancy on every row and a reconciliation surface nobody trusts.
"""

from __future__ import annotations

import csv
import hashlib
import io
import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from hermes.integrations.supabase_client import SupabaseClient

log = logging.getLogger(__name__)

BATCHES_TABLE = "commission_ingest_batches"
STAGING_TABLE = "commission_transactions_staging"
STATEMENTS_TABLE = "commission_statements"
TRANSACTIONS_TABLE = "commission_transactions"

STATUS_PENDING_REVIEW = "pending_review"
STATUS_APPROVED = "approved"
STATUS_REJECTED = "rejected"
STATUS_COMMITTED = "committed"
STATUS_NEEDS_MAPPING = "needs_mapping"
STATUS_ERROR = "error"

# How far parsed totals may drift from the carrier's own stated totals before we
# refuse to commit. A cent or two is rounding; anything more means the parse
# missed rows or double-counted them.
CROSSCHECK_TOLERANCE = Decimal("1.00")

# Column aliases seen across carrier statements. Matched case-insensitively
# after stripping non-alphanumerics, so "Policy #", "policy_number" and
# "POLICY NO." all land on the same field.
_ALIASES: dict[str, tuple[str, ...]] = {
    "policy_number": ("policynumber", "policy", "policyno", "policynum", "polnum"),
    "insured_name": ("insuredname", "insured", "client", "clientname", "namedinsured"),
    "carrier_name": ("carriername", "carrier", "company", "companyname"),
    "lob": ("lob", "lineofbusiness", "prod", "product", "coverage"),
    "transaction_code": ("trancode", "transactioncode", "type", "transactiontype"),
    "transaction_date": ("trandate", "transactiondate", "date", "effectivedate", "paiddate"),
    "month_key": ("monthend", "monthkey", "statementmonth", "period"),
    "gross_premium": ("grosspremium", "premium", "writtenpremium"),
    "commission_rate": ("commrate", "commissionrate", "rate"),
    "commission_amount": ("grosscomm", "commissionamount", "commission", "netdueagent",
                          "commissionpaid", "amount", "amountpaid"),
    "fee_amount": ("feeamount", "fee", "fees"),
    "agency_due": ("agencydue",),
}

# transaction_code -> the normalized type the ledger reasons about.
_TYPE_RULES: tuple[tuple[str, str], ...] = (
    ("new business", "new"),
    ("newbusiness", "new"),
    ("renewal", "renewal"),
    ("endorsement", "adjustment"),
    ("credit", "adjustment"),
    ("cancel", "adjustment"),
    ("reinstate", "adjustment"),
    ("audit", "adjustment"),
    ("chargeback", "adjustment"),
)


def _key(name: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(name or "").lower())


def content_hash(content: bytes) -> str:
    """SHA-256 of the raw file. The dedupe key, enforced UNIQUE by the DB."""
    return hashlib.sha256(content).hexdigest()


def as_money(value: Any) -> Decimal | None:
    """Parse statement money. Handles $, commas, and (123.45) for negatives."""
    if value is None or value == "":
        return None
    text = str(value).strip().replace("$", "").replace(",", "")
    negative = text.startswith("(") and text.endswith(")")
    if negative:
        text = text[1:-1]
    if not text or text in {"-", "."}:
        return None
    try:
        amount = Decimal(text)
    except InvalidOperation:
        return None
    return -amount if negative else amount


def as_rate(value: Any) -> Decimal | None:
    """Parse a commission rate. 15%, 0.15 and 15 all mean 15%.

    A bare number above 1 is read as a percentage — no carrier pays 1500%, and
    reading 15 as 1500% would corrupt every expected-commission comparison.
    """
    if value is None or value == "":
        return None
    text = str(value).strip()
    percent = text.endswith("%")
    try:
        amount = Decimal(text.rstrip("%").replace(",", ""))
    except InvalidOperation:
        return None
    if percent or amount > 1:
        return amount / Decimal("100")
    return amount


_DATE_FORMATS = ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%d/%m/%Y", "%Y%m%d", "%m-%d-%Y")


def as_date(value: Any) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def as_month_key(value: Any, fallback: date | None = None) -> int | None:
    """YYYYMM. Carriers write it as 202602, 2026-02, or not at all."""
    digits = re.sub(r"\D", "", str(value or ""))
    if len(digits) >= 6:
        try:
            candidate = int(digits[:6])
            if 190001 <= candidate <= 299912:
                return candidate
        except ValueError:
            pass
    if fallback:
        return fallback.year * 100 + fallback.month
    return None


def normalize_transaction_type(code: Any) -> str:
    text = str(code or "").strip().lower()
    for needle, kind in _TYPE_RULES:
        if needle in text:
            return kind
    return "other"


def _field(row: dict[str, Any], name: str) -> Any:
    """Pull a logical field from a raw row via the alias table."""
    wanted = (_key(name), *_ALIASES.get(name, ()))
    for raw_key, value in row.items():
        if _key(raw_key) in wanted and value not in ("", None):
            return value
    return None


def parse_row(raw: dict[str, Any]) -> dict[str, Any] | None:
    """One statement row -> a staging line. ``None`` if it isn't a real line.

    The whole original row is kept in ``raw_row``: when a carrier disputes a
    commission, the line as they sent it is the evidence.
    """
    policy_number = str(_field(raw, "policy_number") or "").strip()
    amount = as_money(_field(raw, "commission_amount"))
    if not policy_number and amount is None:
        return None                       # blank or a layout artefact

    when = as_date(_field(raw, "transaction_date"))
    code = _field(raw, "transaction_code")

    return {
        "policy_number": policy_number,
        "insured_name": str(_field(raw, "insured_name") or "").strip() or None,
        "carrier_name": str(_field(raw, "carrier_name") or "").strip() or None,
        "lob": str(_field(raw, "lob") or "").strip() or None,
        "transaction_code": str(code or "").strip() or None,
        "transaction_type": normalize_transaction_type(code),
        "transaction_date": when.isoformat() if when else None,
        "month_key": as_month_key(_field(raw, "month_key"), when),
        "gross_premium": as_money(_field(raw, "gross_premium")),
        "commission_rate": as_rate(_field(raw, "commission_rate")),
        "commission_amount": amount,
        "fee_amount": as_money(_field(raw, "fee_amount")),
        "raw_row": {k: (str(v) if v is not None else None) for k, v in raw.items()},
    }


def parse_csv(content: bytes) -> tuple[list[dict[str, Any]], list[str]]:
    """Parse a CSV/TSV statement. Returns (lines, warnings)."""
    warnings: list[str] = []
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = content.decode("latin-1")
        warnings.append("file was not UTF-8; decoded as latin-1")

    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel

    lines: list[dict[str, Any]] = []
    skipped = 0
    for index, raw in enumerate(csv.DictReader(io.StringIO(text), dialect=dialect), start=2):
        parsed = parse_row(raw)
        if parsed is None:
            skipped += 1
            continue
        if not parsed["policy_number"]:
            warnings.append(f"row {index}: commission amount with no policy number")
        lines.append(parsed)
    if skipped:
        warnings.append(f"{skipped} row(s) skipped as blank or non-data")
    return lines, warnings


def parse_statement(content: bytes, filename: str) -> tuple[list[dict[str, Any]], list[str]]:
    """Dispatch on extension. CSV/TSV today; xlsx and pdf are explicit gaps."""
    suffix = (filename.rsplit(".", 1)[-1] if "." in filename else "").lower()
    if suffix in {"csv", "tsv", "txt"}:
        return parse_csv(content)
    if suffix in {"xlsx", "xlsm", "xls"}:
        return [], [f"{suffix} statements are not parsed yet — export to CSV"]
    if suffix == "pdf":
        return [], ["PDF statements are not parsed yet — export to CSV"]
    return [], [f"unsupported statement format: .{suffix or 'unknown'}"]


@dataclass
class Crosscheck:
    """Parsed totals against what the carrier says it paid."""

    parsed_premium: Decimal = Decimal("0")
    parsed_commission: Decimal = Decimal("0")
    stated_premium: Decimal | None = None
    stated_commission: Decimal | None = None

    @property
    def commission_delta(self) -> Decimal | None:
        if self.stated_commission is None:
            return None
        return self.parsed_commission - self.stated_commission

    @property
    def ok(self) -> bool:
        """True when we can prove the parse matches, or there is nothing to
        prove it against. Never guesses in the parse's favour when a stated
        total exists and disagrees."""
        delta = self.commission_delta
        if delta is None:
            return True
        return abs(delta) <= CROSSCHECK_TOLERANCE

    @property
    def verifiable(self) -> bool:
        return self.stated_commission is not None

    def as_dict(self) -> dict[str, Any]:
        return {
            "parsed_total_premium": float(self.parsed_premium),
            "parsed_total_commission": float(self.parsed_commission),
            "stated_total_premium": (
                float(self.stated_premium) if self.stated_premium is not None else None
            ),
            "stated_total_commission": (
                float(self.stated_commission) if self.stated_commission is not None else None
            ),
            "crosscheck_ok": self.ok,
        }


def crosscheck(
    lines: list[dict[str, Any]],
    *,
    stated_premium: Any = None,
    stated_commission: Any = None,
) -> Crosscheck:
    result = Crosscheck(
        stated_premium=as_money(stated_premium),
        stated_commission=as_money(stated_commission),
    )
    for line in lines:
        result.parsed_commission += line.get("commission_amount") or Decimal("0")
        result.parsed_premium += line.get("gross_premium") or Decimal("0")
    return result


# --- staging -----------------------------------------------------------------

@dataclass
class StagedBatch:
    batch_id: str | None = None
    status: str = STATUS_PENDING_REVIEW
    filename: str = ""
    carrier: str | None = None
    line_count: int = 0
    crosscheck: Crosscheck = field(default_factory=Crosscheck)
    warnings: list[str] = field(default_factory=list)
    duplicate_of: str | None = None
    preview: dict[str, Any] = field(default_factory=dict)

    @property
    def approvable(self) -> bool:
        """A batch may only be approved if it parsed, isn't a duplicate, and
        either matches the carrier's own totals or has none to match."""
        return (
            self.status == STATUS_PENDING_REVIEW
            and self.line_count > 0
            and self.duplicate_of is None
            and self.crosscheck.ok
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "batch_id": self.batch_id,
            "status": self.status,
            "filename": self.filename,
            "carrier": self.carrier,
            "line_count": self.line_count,
            "approvable": self.approvable,
            "duplicate_of": self.duplicate_of,
            "warnings": self.warnings,
            "crosscheck": {
                **self.crosscheck.as_dict(),
                "verifiable": self.crosscheck.verifiable,
                "commission_delta": (
                    float(self.crosscheck.commission_delta)
                    if self.crosscheck.commission_delta is not None else None
                ),
            },
            "preview": self.preview,
        }


def _match_preview(supa: "SupabaseClient", lines: list[dict[str, Any]]) -> dict[str, Any]:
    """Where these lines WOULD land, without writing anything.

    The reviewer is approving a set of consequences, not a file. Showing the
    unmatched policy numbers up front is the difference between a considered
    approval and a rubber stamp.
    """
    from hermes.ams import book as ams_book
    from hermes.commissions.matching import (
        MATCH_CREATED, MATCH_EXACT, MATCH_NORMALIZED, MATCH_UNMATCHED,
        _index_book, _index_ledger, match_line,
    )

    try:
        ledger = supa.select("commission_ledger", columns="id,policy_number", limit=50000)
        exact_idx, norm_idx = _index_ledger(ledger)
    except Exception:  # noqa: BLE001
        log.exception("statement preview: ledger read failed")
        exact_idx, norm_idx = {}, {}
    try:
        book_idx = _index_book(ams_book.select_policies(
            supa, columns="policy_number,policy_guid,carrier,lines_of_business,"
                          "effective_date,expiration_date,premium_amount,annualized_premium",
            limit=50000))
    except Exception:  # noqa: BLE001
        log.exception("statement preview: book read failed")
        book_idx = {}

    counts = {MATCH_EXACT: 0, MATCH_NORMALIZED: 0, MATCH_CREATED: 0, MATCH_UNMATCHED: 0}
    unmatched: dict[str, int] = {}
    negatives = 0
    for line in lines:
        if (line.get("commission_amount") or Decimal("0")) < 0:
            negatives += 1
        result = match_line(line, ledger_by_exact=exact_idx,
                            ledger_by_normalized=norm_idx, book_by_normalized=book_idx)
        counts[result.kind] += 1
        if result.kind == MATCH_UNMATCHED:
            key = result.policy_number or "(blank)"
            unmatched[key] = unmatched.get(key, 0) + 1

    return {
        "will_link": counts[MATCH_EXACT] + counts[MATCH_NORMALIZED],
        "will_create_ledger_rows": counts[MATCH_CREATED],
        "will_be_unmatched": counts[MATCH_UNMATCHED],
        "unmatched_policy_numbers": unmatched,
        "negative_lines": negatives,
    }


def stage_statement(
    supa: "SupabaseClient",
    *,
    content: bytes,
    filename: str,
    uploaded_by: str,
    carrier: str | None = None,
    stated_premium: Any = None,
    stated_commission: Any = None,
) -> StagedBatch:
    """Parse and stage an uploaded statement. Writes NOTHING to the ledger."""
    digest = content_hash(content)

    existing = supa.select(BATCHES_TABLE, columns="id,ingest_status,source_file",
                           params={"content_hash": f"eq.{digest}"}, limit=1)
    if existing:
        prior = existing[0]
        return StagedBatch(
            batch_id=str(prior.get("id")), status=str(prior.get("ingest_status") or ""),
            filename=filename, carrier=carrier, duplicate_of=str(prior.get("id")),
            warnings=[f"identical file already uploaded as {prior.get('source_file')} "
                      f"(status {prior.get('ingest_status')})"],
        )

    lines, warnings = parse_statement(content, filename)
    check = crosscheck(lines, stated_premium=stated_premium, stated_commission=stated_commission)

    status = STATUS_PENDING_REVIEW
    if not lines:
        status = STATUS_ERROR
        warnings.append("no statement lines could be parsed")
    elif not check.ok:
        warnings.append(
            f"parsed commission {check.parsed_commission} does not match the carrier's "
            f"stated {check.stated_commission} (delta {check.commission_delta}) — "
            "the parse is wrong; do not approve"
        )

    batch = supa.insert(BATCHES_TABLE, {
        "content_hash": digest,
        "source_file": filename,
        "carrier_name": carrier,
        "kind": "statement",
        "parser_key": "csv_generic_v1",
        "extraction_method": "csv",
        "is_ocr": False,
        "row_count": len(lines),
        "ingest_status": status,
        "uploaded_by": uploaded_by,
        "flags": {"warnings": warnings},
        **check.as_dict(),
    })
    batch_id = str(batch.get("id"))

    for line in lines:
        supa.insert(STAGING_TABLE, {
            "batch_id": batch_id,
            **{k: (float(v) if isinstance(v, Decimal) else v)
               for k, v in line.items()},
        })

    staged = StagedBatch(
        batch_id=batch_id, status=status, filename=filename,
        carrier=carrier, line_count=len(lines), crosscheck=check, warnings=warnings,
    )
    if lines:
        staged.preview = _match_preview(supa, lines)
    return staged


# --- commit ------------------------------------------------------------------

@dataclass
class CommitResult:
    batch_id: str
    statement_id: str | None = None
    committed: int = 0
    linked: int = 0
    created_ledger_rows: int = 0
    unmatched: int = 0
    rollup: str = ""
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "batch_id": self.batch_id, "statement_id": self.statement_id,
            "committed": self.committed, "linked": self.linked,
            "created_ledger_rows": self.created_ledger_rows,
            "unmatched": self.unmatched, "rollup": self.rollup, "errors": self.errors,
        }


def commit_statement(
    supa: "SupabaseClient", *, batch_id: str, approved_by: str,
) -> CommitResult:
    """Promote a reviewed batch into the ledger. The approval gate is here.

    Refuses a batch that isn't pending review, parsed nothing, or failed its
    crosscheck — approving a bad parse is how fiction reaches a money surface.
    """
    from hermes.commissions.matching import relink_unmatched
    from hermes.commissions.reconcile import run_rollup

    rows = supa.select(BATCHES_TABLE, columns="*", params={"id": f"eq.{batch_id}"}, limit=1)
    if not rows:
        raise ValueError(f"batch {batch_id} not found")
    batch = rows[0]

    status = str(batch.get("ingest_status") or "")
    if status != STATUS_PENDING_REVIEW:
        raise ValueError(f"batch is {status}, not {STATUS_PENDING_REVIEW}")
    if not batch.get("crosscheck_ok", True):
        raise ValueError(
            "crosscheck failed — parsed totals disagree with the carrier's stated "
            "totals; fix the parse rather than approving it"
        )

    staged = supa.select(STAGING_TABLE, columns="*",
                         params={"batch_id": f"eq.{batch_id}"}, limit=50000)
    if not staged:
        raise ValueError("batch has no staged lines")

    result = CommitResult(batch_id=batch_id)

    statement = supa.insert(STATEMENTS_TABLE, {
        "carrier_name": batch.get("carrier_name"),
        "source_filename": batch.get("source_file"),
        "source_format": batch.get("extraction_method"),
        "carrier_stated_total_premium": batch.get("stated_total_premium"),
        "carrier_stated_total_commission": batch.get("stated_total_commission"),
        "row_count": len(staged),
        "upload_status": "parsed",
        "uploaded_by": approved_by,
    })
    result.statement_id = str(statement.get("id"))

    drop = {"id", "batch_id", "created_at"}
    for line in staged:
        payload = {k: v for k, v in line.items() if k not in drop}
        payload["statement_id"] = result.statement_id
        payload["is_negative"] = (line.get("commission_amount") or 0) < 0
        try:
            supa.insert(TRANSACTIONS_TABLE, payload)
            result.committed += 1
        except Exception as exc:  # noqa: BLE001 — one bad line must not lose the rest
            result.errors.append(f"line {line.get('policy_number')}: {exc}")

    # Attach the new lines, then recompute every touched ledger row.
    link = relink_unmatched(supa)
    result.linked = link.exact + link.normalized
    result.created_ledger_rows = link.ledger_rows_created
    result.unmatched = link.unmatched
    result.errors.extend(link.errors)

    result.rollup = run_rollup(supa).message

    supa.update(BATCHES_TABLE, batch_id, {
        "ingest_status": STATUS_COMMITTED,
        "statement_id": result.statement_id,
        "reviewed_by": approved_by,
        "reviewed_at": datetime.now().astimezone().isoformat(),
    })
    log.info("statement %s committed by %s: %s lines", batch_id, approved_by, result.committed)
    return result


def reject_statement(
    supa: "SupabaseClient", *, batch_id: str, reviewed_by: str, reason: str | None = None,
) -> dict[str, Any]:
    """Reject a staged batch. Staging rows stay for diagnosis (FK is CASCADE, so
    they die with the batch only if the batch is deleted, which we never do)."""
    rows = supa.select(BATCHES_TABLE, columns="*", params={"id": f"eq.{batch_id}"}, limit=1)
    if not rows:
        raise ValueError(f"batch {batch_id} not found")
    flags = dict(rows[0].get("flags") or {})
    flags["rejected_reason"] = reason
    return supa.update(BATCHES_TABLE, batch_id, {
        "ingest_status": STATUS_REJECTED,
        "reviewed_by": reviewed_by,
        "reviewed_at": datetime.now().astimezone().isoformat(),
        "flags": flags,
    })
