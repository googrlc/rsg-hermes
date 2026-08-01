"""Commission reconciliation (Carrier Hunter)."""

from __future__ import annotations

import csv
import json
import os
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from hermes_integrations.supabase_client import SupabaseClient, SupabaseClientError
from hermes_integrations.slack_notifier import SlackNotifier, SlackNotifierError


@dataclass
class ReconciliationResult:
    ok: bool
    posted: bool
    message: str
    discrepancies: list[dict[str, Any]]
    matched_count: int
    unmatched_count: int
    unmatched_policy_numbers: list[str]
    warnings: list[str]


def run_reconciliation(
    supa: SupabaseClient,
    *,
    statement_path: str,
    notifier: SlackNotifier | None = None,
    dry_run: bool = False,
) -> ReconciliationResult:
    path = Path(statement_path).expanduser()
    if not path.exists():
        return ReconciliationResult(False, False, f"Statement file not found: {path}", [], 0, 0, [], [])
    parsed_rows, parse_warnings = _parse_statement(path)
    policy_index, fetch_warnings = _policy_index(supa)
    discrepancies, matched_count, unmatched_policy_numbers = _analyze_statement(parsed_rows, policy_index)
    warnings = [*parse_warnings, *fetch_warnings]
    text, blocks = _build_slack_payload(
        discrepancies,
        statement_name=path.name,
        matched_count=matched_count,
        unmatched_policy_numbers=unmatched_policy_numbers,
    )
    if dry_run:
        return ReconciliationResult(
            True,
            False,
            text,
            discrepancies,
            matched_count,
            len(unmatched_policy_numbers),
            unmatched_policy_numbers,
            warnings,
        )
    active_notifier = notifier or SlackNotifier(channel=os.environ.get("HERMES_COMMISSION_RECON_CHANNEL", "").strip() or None)
    try:
        active_notifier.post_message(text=text, blocks=blocks)
    except SlackNotifierError as e:
        return ReconciliationResult(
            False,
            False,
            f"Commission reconciliation Slack post failed: {e}",
            discrepancies,
            matched_count,
            len(unmatched_policy_numbers),
            unmatched_policy_numbers,
            warnings,
        )
    return ReconciliationResult(
        True,
        True,
        (
            f"Commission reconciliation posted ({len(discrepancies)} discrepancies; "
            f"matched {matched_count}, unmatched {len(unmatched_policy_numbers)})."
        ),
        discrepancies,
        matched_count,
        len(unmatched_policy_numbers),
        unmatched_policy_numbers,
        warnings,
    )


def _parse_statement(path: Path) -> tuple[list[dict[str, Any]], list[str]]:
    suffix = path.suffix.lower()
    if suffix in {".csv", ".txt"}:
        return _parse_csv(path)
    if suffix in {".xlsx", ".xlsm"}:
        return _parse_xlsx(path)
    if suffix == ".pdf":
        return _parse_pdf(path)
    return [], [f"Unsupported statement format: {suffix}"]


def _parse_csv(path: Path) -> tuple[list[dict[str, Any]], list[str]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        for raw in reader:
            policy_number = str(_pick(raw, "policy", "policy #", "policy_number", "policy number", "policy no") or "").strip()
            paid = _as_money(_pick(raw, "commission paid", "commission_paid", "paid", "amount paid", "commission"))
            carrier = str(_pick(raw, "carrier", "company") or "").strip()
            if not policy_number:
                continue
            rows.append({"policy_number": policy_number, "paid_commission": paid, "carrier": carrier})
    return rows, []


def _parse_xlsx(path: Path) -> tuple[list[dict[str, Any]], list[str]]:
    try:
        import openpyxl  # type: ignore
    except Exception:
        return [], ["XLSX parsing requires openpyxl in runtime image."]
    wb = openpyxl.load_workbook(path, data_only=True)
    ws = wb.active
    headers: list[str] = []
    rows: list[dict[str, Any]] = []
    for idx, row in enumerate(ws.iter_rows(values_only=True), start=1):
        values = ["" if v is None else str(v).strip() for v in row]
        if idx == 1:
            headers = values
            continue
        raw = {headers[i].lower(): values[i] for i in range(min(len(headers), len(values)))}
        policy_number = str(_pick(raw, "policy", "policy #", "policy_number", "policy number", "policy no") or "").strip()
        paid = _as_money(_pick(raw, "commission paid", "commission_paid", "paid", "amount paid", "commission"))
        carrier = str(_pick(raw, "carrier", "company") or "").strip()
        if not policy_number:
            continue
        rows.append({"policy_number": policy_number, "paid_commission": paid, "carrier": carrier})
    return rows, []


def _parse_pdf(path: Path) -> tuple[list[dict[str, Any]], list[str]]:
    try:
        from pypdf import PdfReader  # type: ignore
    except Exception:
        return [], ["PDF parsing requires pypdf in runtime image."]
    reader = PdfReader(str(path))
    text = "\n".join((page.extract_text() or "") for page in reader.pages)
    rows: list[dict[str, Any]] = []
    for line in text.splitlines():
        normalized = line.strip()
        if not normalized:
            continue
        # Heuristic format: POLICY_NUMBER ... $123.45
        match = re.search(r"([A-Za-z0-9\-]{4,})\b.*?\$?\s*([0-9][0-9,]*\.?[0-9]{0,2})", normalized)
        if not match:
            continue
        policy_number = match.group(1)
        paid = _as_money(match.group(2))
        rows.append({"policy_number": policy_number, "paid_commission": paid, "carrier": ""})
    if not rows:
        return [], ["No policy/commission rows detected in PDF."]
    return rows, []


def _policy_index(supa: SupabaseClient) -> tuple[dict[str, dict[str, Any]], list[str]]:
    """Expected commission per policy, from the Supabase commission_ledger.

    The ledger is the reconciliation workspace: `expected_commission` is seeded
    from the canonical NowCerts book by `hermes/sync/commission_sync.py`, and a
    carrier statement is matched against it here.
    """
    warnings: list[str] = []
    try:
        rows = supa.select(
            "commission_ledger",
            columns="id,policy_number,carrier_name,expected_commission,gross_premium",
            limit=5000,
        )
    except SupabaseClientError as e:
        return {}, [str(e)]

    index: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        policy_number = str(row.get("policy_number") or "").strip()
        if not policy_number:
            continue
        payload = {
            "policy_id": str(row.get("id") or ""),
            "policy_number": policy_number,
            "carrier": str(row.get("carrier_name") or ""),
            "expected_commission": _as_money(row.get("expected_commission")),
        }
        for key in _matching_keys(policy_number):
            index[key] = payload
    return index, warnings


def _analyze_statement(
    statement_rows: list[dict[str, Any]],
    policy_index: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], int, list[str]]:
    discrepancies: list[dict[str, Any]] = []
    matched_count = 0
    unmatched: list[str] = []
    mode = os.environ.get("HERMES_COMMISSION_RECON_RULE", "any_difference").strip().lower()
    percent_threshold = _as_percent(os.environ.get("HERMES_COMMISSION_RECON_PERCENT_THRESHOLD", "1"))
    amount_threshold = _as_money(os.environ.get("HERMES_COMMISSION_RECON_AMOUNT_THRESHOLD", "25"))
    for row in statement_rows:
        policy_number = str(row.get("policy_number") or "").strip()
        if not policy_number:
            continue
        paid = _as_money(row.get("paid_commission"))
        policy = _lookup_policy(policy_index, policy_number)
        if not policy:
            unmatched.append(policy_number)
            continue
        matched_count += 1
        expected = _as_money(policy.get("expected_commission"))
        delta = expected - paid
        abs_delta = abs(delta)
        percent_delta = Decimal("0")
        if expected > 0:
            percent_delta = (abs_delta / expected) * Decimal("100")
        if not _is_flagged(mode, abs_delta, percent_delta, amount_threshold, percent_threshold):
            continue
        discrepancies.append(
            {
                "policy_id": policy.get("policy_id") or "",
                "policy_number": policy.get("policy_number") or policy_number,
                "carrier": row.get("carrier") or policy.get("carrier") or "",
                "expected_commission": expected,
                "paid_commission": paid,
                "delta": delta,
                "percent_delta": percent_delta,
            }
        )
    discrepancies.sort(key=lambda r: abs(_as_money(r.get("delta"))), reverse=True)
    return discrepancies, matched_count, sorted(set(unmatched))


def _build_slack_payload(
    discrepancies: list[dict[str, Any]],
    *,
    statement_name: str,
    matched_count: int,
    unmatched_policy_numbers: list[str],
) -> tuple[str, list[dict[str, Any]]]:
    summary_line = (
        f"Matched: {matched_count} | Unmatched: {len(unmatched_policy_numbers)} | "
        f"Discrepancies: {len(discrepancies)}"
    )
    if not discrepancies:
        lines = [
            f"Commission reconciliation complete for `{statement_name}`: no discrepancies found.",
            summary_line,
        ]
        if unmatched_policy_numbers:
            preview = ", ".join(unmatched_policy_numbers[:10])
            lines.append(f"Unmatched policy numbers: {preview}")
        text = "\n".join(lines)
        blocks = [
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"{lines[0]}\n{summary_line}"},
            }
        ]
        if unmatched_policy_numbers:
            blocks.append(
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f":warning: *Unmatched policy numbers:* {', '.join(unmatched_policy_numbers[:20])}",
                    },
                }
            )
        return text, blocks
    lines = ["🚨 COMMISSION DISCREPANCY", "", f"Statement: {statement_name}", summary_line, ""]
    for row in discrepancies[:12]:
        lines.append(
            (
                f"• Policy #{row['policy_number']} ({row['carrier'] or 'Unknown Carrier'}) "
                f"paid ${row['paid_commission']:,.0f}, expected ${row['expected_commission']:,.0f}."
            )
        )
    text = "\n".join(lines)
    blocks: list[dict[str, Any]] = [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*🚨 COMMISSION DISCREPANCY*\nStatement: `{statement_name}`\n{summary_line}",
            },
        }
    ]
    if unmatched_policy_numbers:
        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f":warning: *Unmatched policy numbers:* {', '.join(unmatched_policy_numbers[:20])}",
                },
            }
        )
    for row in discrepancies[:12]:
        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"• Policy *#{row['policy_number']}* ({row['carrier'] or 'Unknown Carrier'}) "
                        f"paid `${row['paid_commission']:,.0f}`, expected `${row['expected_commission']:,.0f}`."
                    ),
                },
            }
        )
    return text, blocks


def _is_flagged(
    mode: str,
    abs_delta: Decimal,
    percent_delta: Decimal,
    amount_threshold: Decimal,
    percent_threshold: Decimal,
) -> bool:
    if mode == "any_difference":
        return abs_delta > Decimal("0")
    if mode == "percent_over_1":
        return percent_delta > percent_threshold
    if mode == "dollar_over_25":
        return abs_delta > amount_threshold
    if mode == "hybrid_1pct_or_25":
        return percent_delta > percent_threshold or abs_delta > amount_threshold
    return abs_delta > Decimal("0")


def _pick(row: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        for actual_key in row.keys():
            if actual_key.lower() == key.lower() and row[actual_key] not in ("", None):
                return row[actual_key]
    return None


def _lookup_policy(policy_index: dict[str, dict[str, Any]], policy_number: str) -> dict[str, Any] | None:
    for key in _matching_keys(policy_number):
        hit = policy_index.get(key)
        if hit:
            return hit
    return None


def _matching_keys(value: str) -> list[str]:
    raw = str(value or "").strip()
    if not raw:
        return []
    upper = raw.upper()
    compact = re.sub(r"[^A-Z0-9]", "", upper)
    keys = [upper]
    if compact and compact != upper:
        keys.append(compact)
    return keys


def _as_percent(value: Any) -> Decimal:
    if value in ("", None):
        return Decimal("0")
    try:
        return Decimal(str(value).replace("%", "").replace(",", ""))
    except (InvalidOperation, ValueError):
        return Decimal("0")


def _as_money(value: Any) -> Decimal:
    if value in ("", None):
        return Decimal("0")
    try:
        return Decimal(str(value).replace("$", "").replace(",", ""))
    except (InvalidOperation, ValueError):
        return Decimal("0")

