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

from hermes.core.client import EspoClient, EspoClientError
from hermes.integrations.slack_notifier import SlackNotifier, SlackNotifierError


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
    client: EspoClient,
    *,
    statement_path: str,
    notifier: SlackNotifier | None = None,
    dry_run: bool = False,
) -> ReconciliationResult:
    path = Path(statement_path).expanduser()
    if not path.exists():
        return ReconciliationResult(False, False, f"Statement file not found: {path}", [], 0, 0, [], [])
    parsed_rows, parse_warnings = _parse_statement(path)
    policy_index, fetch_warnings = _policy_index(client)
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


def build_dispute_action_value(*, policy_id: str, policy_number: str, carrier: str) -> str:
    return json.dumps(
        {
            "policy_id": policy_id,
            "policy_number": policy_number,
            "carrier": carrier,
        },
        separators=(",", ":"),
    )


def handle_dispute_action(*, client: EspoClient, action: str, action_value: str) -> str:
    if action != "commission_create_dispute":
        return "Unknown commission reconciliation action."
    payload = _parse_dispute_action_value(action_value)
    assigned_user_id = (
        os.environ.get("HERMES_COMMISSION_TASK_ASSIGNEE_ID", "").strip()
        or os.environ.get("HERMES_SENTINEL_GRETCHEN_USER_ID", "").strip()
    )
    task: dict[str, Any] = {
        "name": f"Commission dispute: Policy {payload['policy_number']}",
        "status": "Not Started",
        "description": (
            f"Carrier reconciliation discrepancy for policy {payload['policy_number']} "
            f"(Carrier: {payload['carrier'] or 'Unknown'})."
        ),
    }
    if assigned_user_id:
        task["assignedUserId"] = assigned_user_id
    client.create("Task", task)
    return "Dispute task created."


def _parse_dispute_action_value(raw_value: str) -> dict[str, str]:
    try:
        data = json.loads(raw_value)
    except json.JSONDecodeError as e:
        raise ValueError("Invalid dispute action payload.") from e
    if not isinstance(data, dict):
        raise ValueError("Invalid dispute action payload shape.")
    policy_number = str(data.get("policy_number") or "").strip()
    policy_id = str(data.get("policy_id") or "").strip()
    carrier = str(data.get("carrier") or "").strip()
    if not policy_number:
        raise ValueError("Dispute action payload missing policy number.")
    return {"policy_number": policy_number, "policy_id": policy_id, "carrier": carrier}


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


def _policy_index(client: EspoClient) -> tuple[dict[str, dict[str, Any]], list[str]]:
    warnings: list[str] = []
    try:
        body = client.get("Policy", params={"maxSize": 500})
    except EspoClientError as e:
        return {}, [str(e)]
    rows = body.get("list", []) if isinstance(body, dict) else []
    index: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        raw_candidates = [
            _pick(row, "policyNumber", "policy_number"),
            _pick(row, "caCurrentPolicyNum", "currentPolicyNumber", "current_policy_number"),
            _pick(row, "name"),
            _pick(row, "id"),
        ]
        candidates = [str(x).strip() for x in raw_candidates if x not in ("", None)]
        if not candidates:
            continue
        expected = _as_money(_pick(row, "commissionAmount", "commission_amount"))
        if expected <= 0:
            premium = _as_money(_pick(row, "premiumAmount", "premium_amount", "amount"))
            rate = _as_percent(_pick(row, "commissionRate", "commission_rate"))
            expected = (premium * rate) / Decimal("100")
        canonical = candidates[0]
        payload = {
            "policy_id": str(row.get("id") or ""),
            "policy_number": canonical,
            "carrier": str(_pick(row, "carrier") or ""),
            "expected_commission": expected,
        }
        for candidate in candidates:
            for key in _matching_keys(candidate):
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
        blocks.append(
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Create dispute task"},
                        "action_id": "commission_create_dispute",
                        "value": build_dispute_action_value(
                            policy_id=row["policy_id"],
                            policy_number=row["policy_number"],
                            carrier=str(row.get("carrier") or ""),
                        ),
                    }
                ],
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

