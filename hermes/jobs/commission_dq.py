"""Commission DQ / AMS anomaly scan (report-only).

Compares the AMS book mirror (``canonical_policies``), the rulebook
(``commission_rules`` + ``carrier_commission_profile``), and
``commission_ledger`` for row-level anomalies:

  * NB vs renewal misclassification
  * Missing expected ledger rows (seed-window aware)
  * Rate drift (AMS commission vs ledger expected)
  * Wrong NB/renewal rate when detectable from the rule
  * As-earned vs advance payment-model mismatches
  * Agency Bill / billing_type gaps
  * Blind spots (expected_commission null/≤0)

v1 is report-only: no NowCerts writes, no ledger auto-fixes. Posts at most once
per calendar day unless ``force=True`` (same idempotency shape as
``run_commission_audit``).
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from hermes.jobs.commission_ingest import (
    RENEWAL_STATUSES,
    _build_rule_index,
    _match_rule,
)
from hermes.sync.commission_sync import LEDGER_STATUSES, _has_commissionable_value
from hermes_core.canonical import normalize_billing_type, normalize_status
from hermes_integrations.slack_notifier import SlackNotifier, SlackNotifierError
from hermes_integrations.supabase_client import SupabaseClient, SupabaseClientError

log = logging.getLogger(__name__)

_CHARGEBACK_STATUSES = {"chargeback"}

# Rate drift: absolute dollars OR relative percent (either fires).
_RATE_ABS_THRESHOLD = Decimal("5")
_RATE_PCT_THRESHOLD = Decimal("5")

# Severity order for sorting / truncating report output.
_SEVERITY_RANK = {"High": 0, "Med": 1, "Info": 2}


@dataclass
class CommissionDqResult:
    ok: bool
    posted: bool
    skipped: bool
    message: str
    findings: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def run_commission_dq(
    *,
    supa: SupabaseClient | None = None,
    notifier: SlackNotifier | None = None,
    dry_run: bool = False,
    force: bool = False,
    limit: int | None = None,
    now: datetime | None = None,
) -> CommissionDqResult:
    """Scan book + ledger + rules and optionally post a daily DQ briefing."""
    if now is None:
        today = date.today()
    elif isinstance(now, datetime):
        today = now.date()
    else:
        today = now
    supa = supa or SupabaseClient()

    findings, warnings = scan_commission_dq(supa, limit=limit, today=today)
    text, blocks = _build_slack_payload(findings, today)

    if dry_run:
        return CommissionDqResult(
            ok=True,
            posted=False,
            skipped=False,
            message=text,
            findings=findings,
            warnings=warnings,
        )

    if not force and _already_sent_today(today):
        return CommissionDqResult(
            ok=True,
            posted=False,
            skipped=True,
            message=f"Commission DQ already posted for {today.isoformat()}; skipping duplicate post.",
            findings=findings,
            warnings=warnings,
        )

    channel = (
        os.environ.get("HERMES_COMMISSION_DQ_CHANNEL", "").strip()
        or os.environ.get("HERMES_COMMISSION_AUDIT_CHANNEL", "").strip()
        or os.environ.get("HERMES_COMMISSION_ALERT_CHANNEL", "").strip()
        or os.environ.get("HERMES_SYSTEMS_CHECK_CHANNEL", "").strip()
        or None
    )
    active_notifier = notifier or SlackNotifier(channel=channel)
    try:
        active_notifier.post_message(text=text, blocks=blocks)
    except SlackNotifierError as e:
        return CommissionDqResult(
            ok=False,
            posted=False,
            skipped=False,
            message=f"Commission DQ Slack post failed: {e}",
            findings=findings,
            warnings=warnings,
        )

    _write_state(today)
    return CommissionDqResult(
        ok=True,
        posted=True,
        skipped=False,
        message=f"Commission DQ posted for {today.isoformat()} ({_summarize_counts(findings)})",
        findings=findings,
        warnings=warnings,
    )


def scan_commission_dq(
    supa: Any,
    *,
    limit: int | None = None,
    today: date | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Pure scan — used by the job and by unit tests with a fake Supabase."""
    warnings: list[str] = []
    today = today or date.today()
    since = _seed_since()

    policies, rules, profiles, ledger, load_warnings = _load_tables(supa)
    warnings.extend(load_warnings)

    rule_by_id = {str(r.get("id")): r for r in rules if r.get("id") is not None}
    rule_index = _build_rule_index(rules)
    profile_by_carrier = _index_profiles(profiles)
    ledger_by_pn = {
        str(r.get("policy_number") or "").strip(): r
        for r in ledger
        if str(r.get("policy_number") or "").strip()
    }
    book_by_pn = {
        str(p.get("policy_number") or "").strip(): p
        for p in policies
        if str(p.get("policy_number") or "").strip()
    }

    findings: list[dict[str, Any]] = []

    # --- per-ledger checks ----------------------------------------------------
    for row in ledger:
        try:
            findings.extend(
                _check_ledger_row(
                    row,
                    book=book_by_pn.get(str(row.get("policy_number") or "").strip()),
                    rule_by_id=rule_by_id,
                    rule_index=rule_index,
                    rules=rules,
                    profile_by_carrier=profile_by_carrier,
                )
            )
        except Exception as exc:  # noqa: BLE001 — one bad row must not abort
            pn = str(row.get("policy_number") or "?")
            warnings.append(f"ledger {pn}: {exc}")
            log.warning("commission dq: ledger check failed for %s: %s", pn, exc)

    # --- book-only: missing ledger rows (DQ-NB2) ------------------------------
    for policy in policies:
        try:
            finding = _check_missing_ledger(
                policy,
                ledger_by_pn=ledger_by_pn,
                since=since,
                today=today,
            )
            if finding:
                findings.append(finding)
        except Exception as exc:  # noqa: BLE001
            pn = str(policy.get("policy_number") or "?")
            warnings.append(f"book {pn}: {exc}")
            log.warning("commission dq: book check failed for %s: %s", pn, exc)

    findings = _dedupe_findings(findings)
    findings.sort(
        key=lambda f: (
            _SEVERITY_RANK.get(str(f.get("severity")), 9),
            str(f.get("id") or ""),
            str(f.get("policy_number") or ""),
        )
    )
    if limit is not None and limit >= 0:
        findings = findings[:limit]
    return findings, warnings


# --- individual checks --------------------------------------------------------

def _check_ledger_row(
    row: dict[str, Any],
    *,
    book: dict[str, Any] | None,
    rule_by_id: dict[str, dict[str, Any]],
    rule_index: dict[tuple[str, str], dict[str, Any]],
    rules: list[dict[str, Any]],
    profile_by_carrier: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    recon = str(_pick(row, "reconciliation_status") or "").strip().lower()
    is_chargeback = recon in _CHARGEBACK_STATUSES

    # DQ-BLIND — same idea as commission audit
    if not is_chargeback:
        expected = _as_decimal(_pick(row, "expected_commission"))
        if expected is None or expected <= Decimal("0"):
            out.append(
                _finding(
                    "DQ-BLIND",
                    "High",
                    row,
                    book,
                    detail="expected_commission is null or ≤ 0 on a non-chargeback ledger row",
                    sources=["commission_ledger"],
                )
            )

    if book is not None:
        # DQ-NB1 — ledger is_renewal vs AMS renewal signal
        ams_renewal = _ams_is_renewal(book)
        ledger_renewal = _as_bool(_pick(row, "is_renewal"))
        if ledger_renewal is not None and ledger_renewal != ams_renewal:
            out.append(
                _finding(
                    "DQ-NB1",
                    "High",
                    row,
                    book,
                    detail=(
                        f"ledger is_renewal={ledger_renewal} but AMS renewal signal "
                        f"is {ams_renewal} (renewed_policy/business_type/status)"
                    ),
                    sources=["commission_ledger", "canonical_policies"],
                    is_renewal=ledger_renewal,
                )
            )

        # DQ-RATE1 — AMS agency commission vs ledger expected
        ams_amt = _as_decimal(_pick(book, "agency_commission_amount"))
        ledger_exp = _as_decimal(_pick(row, "expected_commission"))
        if (
            ams_amt is not None
            and ams_amt > Decimal("0")
            and ledger_exp is not None
            and _rate_drift(ams_amt, ledger_exp)
        ):
            pct = _pct_diff(ams_amt, ledger_exp)
            out.append(
                _finding(
                    "DQ-RATE1",
                    "High",
                    row,
                    book,
                    detail=(
                        f"AMS agency_commission_amount={ams_amt} vs ledger "
                        f"expected_commission={ledger_exp} "
                        f"(Δ ${_money(abs(ams_amt - ledger_exp))}, {pct}%)"
                    ),
                    sources=["canonical_policies", "commission_ledger"],
                )
            )

        # DQ-BILL1 — ledger missing billing_type while canonical has one
        book_billing = normalize_billing_type(_pick(book, "billing_type"))
        led_billing = normalize_billing_type(_pick(row, "billing_type"))
        if book_billing and not led_billing:
            out.append(
                _finding(
                    "DQ-BILL1",
                    "Med",
                    row,
                    book,
                    detail=f"ledger billing_type missing; canonical has {book_billing!r}",
                    sources=["commission_ledger", "canonical_policies"],
                    billing_type=book_billing,
                )
            )

    # DQ-RATE2 — NB/renewal rate mismatch when detectable from the rule
    rate2 = _check_rate2(row, book, rule_by_id, rule_index, rules)
    if rate2:
        out.append(rate2)

    # DQ-TIME1 — carrier payment_model vs rule/ledger timing
    time1 = _check_time1(row, book, rule_by_id, rule_index, rules, profile_by_carrier)
    if time1:
        out.append(time1)

    # DQ-BILL2 — Agency Bill with null/0 agency fee (Info; AMS often omits fee)
    billing = normalize_billing_type(
        _pick(row, "billing_type")
        or (book and _pick(book, "billing_type"))
    )
    if billing and billing.lower().startswith("agency bill"):
        fee = _as_decimal(_pick(row, "agency_fee_amount"))
        if fee is None or fee == Decimal("0"):
            # Prefer book fee if ledger is empty but book has one — still Info gap on ledger
            book_fee = _as_decimal(book and _pick(book, "agency_fee_amount")) if book else None
            detail = "Agency Bill ledger row has null/0 agency_fee_amount"
            if book_fee and book_fee > 0:
                detail += f" (canonical has {book_fee})"
            else:
                detail += " (AMS list often omits fee — verify manually)"
            out.append(
                _finding(
                    "DQ-BILL2",
                    "Info",
                    row,
                    book,
                    detail=detail,
                    sources=["commission_ledger", "canonical_policies"],
                    billing_type=billing,
                )
            )

    return out


def _check_missing_ledger(
    policy: dict[str, Any],
    *,
    ledger_by_pn: dict[str, dict[str, Any]],
    since: str,
    today: date,
) -> dict[str, Any] | None:
    """DQ-NB2: Active/Renewed commissionable book row with no ledger match."""
    pn = str(policy.get("policy_number") or "").strip()
    if not pn or pn in ledger_by_pn:
        return None
    status = normalize_status(policy.get("status"))
    if status not in LEDGER_STATUSES:
        return None
    if not _has_commissionable_value(policy):
        return None
    eff_s = str(policy.get("effective_date") or "")[:10]
    today_s = today.isoformat()
    if not eff_s or eff_s < since or eff_s > today_s:
        return None
    return _finding(
        "DQ-NB2",
        "High",
        {"policy_number": pn, "carrier_name": policy.get("carrier"),
         "client_name": None, "is_renewal": _ams_is_renewal(policy),
         "billing_type": policy.get("billing_type")},
        policy,
        detail=(
            f"Active/Renewed commissionable policy effective {eff_s} "
            f"(seed window since {since}) has no commission_ledger row"
        ),
        sources=["canonical_policies"],
        is_renewal=_ams_is_renewal(policy),
    )


def _check_rate2(
    row: dict[str, Any],
    book: dict[str, Any] | None,
    rule_by_id: dict[str, dict[str, Any]],
    rule_index: dict[tuple[str, str], dict[str, Any]],
    rules: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """DQ-RATE2: renewal matched NB-only rate (or NB matched renewal-only)."""
    rule = _resolve_rule(row, book, rule_by_id, rule_index, rules)
    if not rule:
        return None

    is_renewal = _as_bool(_pick(row, "is_renewal"))
    if is_renewal is None and book is not None:
        is_renewal = _ams_is_renewal(book)
    if is_renewal is None:
        return None

    nb = _as_decimal(rule.get("nb_percent"))
    ren = _as_decimal(rule.get("renewal_percent"))

    # Explicit: renewal row but rule only has NB percent (renewal_percent null)
    if is_renewal and nb is not None and ren is None:
        return _finding(
            "DQ-RATE2",
            "High",
            row,
            book,
            detail=(
                f"renewal row matched NB-only rule rate "
                f"(nb_percent={nb}, renewal_percent=null, rule={rule.get('id')})"
            ),
            sources=["commission_ledger", "commission_rules"],
            is_renewal=True,
        )
    # Explicit: NB row but rule only has renewal percent
    if (not is_renewal) and ren is not None and nb is None:
        return _finding(
            "DQ-RATE2",
            "High",
            row,
            book,
            detail=(
                f"new-business row matched renewal-only rule rate "
                f"(renewal_percent={ren}, nb_percent=null, rule={rule.get('id')})"
            ),
            sources=["commission_ledger", "commission_rules"],
            is_renewal=False,
        )

    # Arithmetic: both rates present and differ; expected ≈ wrong rate × premium
    if nb is None or ren is None or nb == ren:
        return None
    premium = _as_decimal(_pick(row, "gross_premium"))
    expected = _as_decimal(_pick(row, "expected_commission"))
    if premium is None or premium <= 0 or expected is None:
        return None
    nb_amt = (premium * nb / Decimal("100")).quantize(Decimal("0.01"))
    ren_amt = (premium * ren / Decimal("100")).quantize(Decimal("0.01"))
    # Skip if AMS direct commission was preferred (matches neither cleanly) —
    # only fire when expected clearly tracks the wrong side.
    tol = Decimal("0.02")
    if is_renewal and abs(expected - nb_amt) <= tol and abs(expected - ren_amt) > tol:
        return _finding(
            "DQ-RATE2",
            "High",
            row,
            book,
            detail=(
                f"renewal expected_commission={expected} matches NB rate "
                f"{nb}% (={nb_amt}) not renewal {ren}% (={ren_amt})"
            ),
            sources=["commission_ledger", "commission_rules"],
            is_renewal=True,
        )
    if (not is_renewal) and abs(expected - ren_amt) <= tol and abs(expected - nb_amt) > tol:
        return _finding(
            "DQ-RATE2",
            "High",
            row,
            book,
            detail=(
                f"new-business expected_commission={expected} matches renewal rate "
                f"{ren}% (={ren_amt}) not NB {nb}% (={nb_amt})"
            ),
            sources=["commission_ledger", "commission_rules"],
            is_renewal=False,
        )
    return None


def _check_time1(
    row: dict[str, Any],
    book: dict[str, Any] | None,
    rule_by_id: dict[str, dict[str, Any]],
    rule_index: dict[tuple[str, str], dict[str, Any]],
    rules: list[dict[str, Any]],
    profile_by_carrier: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    """DQ-TIME1: carrier payment_model vs rule/ledger timing (as_earned ↔ advance)."""
    carrier = str(
        _pick(row, "carrier_name")
        or (book and _pick(book, "carrier"))
        or ""
    ).strip()
    profile = _lookup_profile(carrier, profile_by_carrier)
    if not profile:
        return None
    profile_model = _normalize_timing(profile.get("payment_model"))
    if profile_model not in {"as_earned", "advance"}:
        return None

    rule = _resolve_rule(row, book, rule_by_id, rule_index, rules)
    candidates = [
        _pick(row, "payment_timing"),
        _pick(row, "commission_basis"),
        (rule or {}).get("commission_basis") if rule else None,
        (rule or {}).get("payment_timing") if rule else None,
    ]
    row_model = None
    for raw in candidates:
        normalized = _normalize_timing(raw)
        if normalized in {"as_earned", "advance"}:
            row_model = normalized
            break
    if row_model is None or row_model == profile_model:
        return None
    return _finding(
        "DQ-TIME1",
        "Med",
        row,
        book,
        detail=(
            f"carrier payment_model={profile_model} but rule/ledger timing "
            f"is {row_model}"
        ),
        sources=["carrier_commission_profile", "commission_ledger", "commission_rules"],
    )


# --- data load / helpers ------------------------------------------------------

def _load_tables(
    supa: Any,
) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[str],
]:
    warnings: list[str] = []
    policies: list[dict[str, Any]] = []
    rules: list[dict[str, Any]] = []
    profiles: list[dict[str, Any]] = []
    ledger: list[dict[str, Any]] = []

    try:
        policies = list(
            supa.select(
                "canonical_policies",
                columns=(
                    "policy_number,policy_guid,status,carrier,lines_of_business,"
                    "business_type,business_sub_type,renewed_policy,effective_date,"
                    "expiration_date,premium_amount,annualized_premium,"
                    "agency_commission_amount,billing_type,agency_fee_amount,active"
                ),
                params={"order": "effective_date.desc"},
                limit=50000,
            )
            or []
        )
    except (SupabaseClientError, Exception) as e:  # noqa: BLE001
        warnings.append(f"canonical_policies: {e}")

    try:
        rules = list(supa.select("commission_rules", columns="*", limit=5000) or [])
    except (SupabaseClientError, Exception) as e:  # noqa: BLE001
        warnings.append(f"commission_rules: {e}")

    try:
        profiles = list(
            supa.select(
                "carrier_commission_profile",
                columns="carrier_name,payment_model,default_nb_percent,default_renewal_percent",
                limit=5000,
            )
            or []
        )
    except (SupabaseClientError, Exception) as e:  # noqa: BLE001
        warnings.append(f"carrier_commission_profile: {e}")

    try:
        ledger = list(
            supa.select(
                "commission_ledger",
                columns=(
                    "id,policy_number,carrier_name,lob,client_name,is_renewal,"
                    "gross_premium,expected_commission,reconciliation_status,"
                    "commission_rule_id,commission_basis,payment_timing,"
                    "billing_type,agency_fee_amount,policy_effective_date"
                ),
                params={"order": "policy_number.asc"},
                limit=50000,
            )
            or []
        )
    except (SupabaseClientError, Exception) as e:  # noqa: BLE001
        warnings.append(f"commission_ledger: {e}")

    return policies, rules, profiles, ledger, warnings


def _seed_since() -> str:
    return (os.environ.get("HERMES_COMMISSION_SINCE") or "2026-01-01").strip()[:10]


def _resolve_rule(
    row: dict[str, Any],
    book: dict[str, Any] | None,
    rule_by_id: dict[str, dict[str, Any]],
    rule_index: dict[tuple[str, str], dict[str, Any]],
    rules: list[dict[str, Any]],
) -> dict[str, Any] | None:
    rid = _pick(row, "commission_rule_id")
    if rid is not None and str(rid) in rule_by_id:
        return rule_by_id[str(rid)]
    carrier = str(
        _pick(row, "carrier_name")
        or (book and _pick(book, "carrier"))
        or ""
    )
    lob = str(
        _pick(row, "lob")
        or (book and _pick(book, "lines_of_business"))
        or ""
    )
    if carrier and lob:
        return _match_rule(carrier, lob, rule_index, rules)
    return None


def _index_profiles(profiles: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for p in profiles:
        key = _normalize_carrier_key(p.get("carrier_name"))
        if key:
            out[key] = p
    return out


def _lookup_profile(
    carrier: str,
    profile_by_carrier: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    key = _normalize_carrier_key(carrier)
    if not key:
        return None
    if key in profile_by_carrier:
        return profile_by_carrier[key]
    # Prefix soft-match (Progressive Mountain → Progressive)
    for pk, profile in profile_by_carrier.items():
        if key.startswith(pk) or pk.startswith(key):
            return profile
    return None


def _normalize_carrier_key(name: Any) -> str:
    return " ".join(str(name or "").upper().split())


def _normalize_timing(raw: Any) -> str | None:
    if raw in ("", None):
        return None
    text = " ".join(str(raw).strip().lower().replace("-", " ").replace("_", " ").split())
    if text in {"as earned", "asearned", "as earned monthly"}:
        return "as_earned"
    if text in {"advance", "in advance", "upfront", "up front"}:
        return "advance"
    # Human labels from the tracker UI / mixed spellings
    if "advance" in text:
        return "advance"
    if "earned" in text:
        return "as_earned"
    return None


def _ams_is_renewal(policy: dict[str, Any]) -> bool:
    """AMS renewal signal: renewed_policy, business_type, or renewal status."""
    if _pick(policy, "renewed_policy", "renewedFrom"):
        return True
    status = normalize_status(policy.get("status"))
    if status in RENEWAL_STATUSES:
        return True
    marker = str(
        _pick(
            policy,
            "business_type",
            "business_sub_type",
            "businessType",
            "policyType",
            "type",
        )
        or ""
    ).lower()
    return "renew" in marker


def _rate_drift(ams: Decimal, ledger: Decimal) -> bool:
    if ams == ledger:
        return False
    abs_diff = abs(ams - ledger)
    if abs_diff > _RATE_ABS_THRESHOLD:
        return True
    base = abs(ams) if ams != 0 else abs(ledger)
    if base <= 0:
        return abs_diff > 0
    pct = (abs_diff / base) * Decimal("100")
    return pct > _RATE_PCT_THRESHOLD


def _pct_diff(a: Decimal, b: Decimal) -> str:
    base = abs(a) if a != 0 else abs(b)
    if base <= 0:
        return "n/a"
    pct = (abs(a - b) / base) * Decimal("100")
    return f"{pct.quantize(Decimal('0.1'))}"


def _finding(
    check_id: str,
    severity: str,
    row: dict[str, Any],
    book: dict[str, Any] | None,
    *,
    detail: str,
    sources: list[str],
    is_renewal: bool | None = None,
    billing_type: str | None = None,
) -> dict[str, Any]:
    if is_renewal is None:
        is_renewal = _as_bool(_pick(row, "is_renewal"))
        if is_renewal is None and book is not None:
            is_renewal = _ams_is_renewal(book)
    if billing_type is None:
        billing_type = normalize_billing_type(
            _pick(row, "billing_type")
            or (book and _pick(book, "billing_type"))
        )
    return {
        "id": check_id,
        "severity": severity,
        "policy_number": str(
            _pick(row, "policy_number")
            or (book and _pick(book, "policy_number"))
            or ""
        ),
        "client_name": str(
            _pick(row, "client_name")
            or (book and _pick(book, "client_name", "insured_name"))
            or ""
        )
        or None,
        "carrier_name": str(
            _pick(row, "carrier_name")
            or (book and _pick(book, "carrier"))
            or ""
        )
        or None,
        "is_renewal": is_renewal,
        "billing_type": billing_type,
        "detail": detail,
        "sources": list(sources),
    }


def _dedupe_findings(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, str]] = set()
    out: list[dict[str, Any]] = []
    for f in findings:
        key = (str(f.get("id") or ""), str(f.get("policy_number") or ""))
        if key in seen:
            continue
        seen.add(key)
        out.append(f)
    return out


def _build_slack_payload(
    findings: list[dict[str, Any]],
    day: date,
) -> tuple[str, list[dict[str, Any]]]:
    counts = _summarize_counts(findings)
    if not findings:
        text = f"Commission DQ ({day.isoformat()}): 0 anomalies — book/ledger/rules look clean."
        blocks = [{"type": "section", "text": {"type": "mrkdwn", "text": text}}]
        return text, blocks

    high = [f for f in findings if f.get("severity") == "High"]
    med = [f for f in findings if f.get("severity") == "Med"]
    info = [f for f in findings if f.get("severity") == "Info"]

    lines = [
        f"🔎 COMMISSION DQ — {day.isoformat()}",
        "",
        counts,
        "",
    ]
    for bucket, label in ((high, "High"), (med, "Med"), (info, "Info")):
        if not bucket:
            continue
        lines.append(f"*{label}* ({len(bucket)})")
        for f in bucket[:15]:
            client = f.get("client_name") or "—"
            pn = f.get("policy_number") or "?"
            lines.append(f"• `{f['id']}` {client} `{pn}` — {f.get('detail')}")
        if len(bucket) > 15:
            lines.append(f"  … +{len(bucket) - 15} more")
        lines.append("")

    text = "\n".join(lines).rstrip()
    blocks: list[dict[str, Any]] = [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*🔎 COMMISSION DQ — {day.isoformat()}*\n{counts}",
            },
        }
    ]
    # Keep Slack payload bounded — first 20 findings as sections.
    for f in findings[:20]:
        client = f.get("client_name") or "—"
        pn = f.get("policy_number") or "?"
        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"• *{f.get('severity')}* `{f['id']}` {client} `{pn}`\n"
                        f"  {f.get('detail')}"
                    ),
                },
            }
        )
    if len(findings) > 20:
        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"_… +{len(findings) - 20} more findings (see dry-run for full report)_",
                },
            }
        )
    return text, blocks


def _summarize_counts(findings: list[dict[str, Any]]) -> str:
    high = sum(1 for f in findings if f.get("severity") == "High")
    med = sum(1 for f in findings if f.get("severity") == "Med")
    info = sum(1 for f in findings if f.get("severity") == "Info")
    by_id: dict[str, int] = {}
    for f in findings:
        by_id[str(f.get("id") or "?")] = by_id.get(str(f.get("id") or "?"), 0) + 1
    id_bits = ", ".join(f"{k}={v}" for k, v in sorted(by_id.items()))
    base = f"{len(findings)} anomalies (High={high}, Med={med}, Info={info})"
    return f"{base}" + (f" — {id_bits}" if id_bits else "")


def _state_path() -> Path:
    raw = os.environ.get(
        "HERMES_COMMISSION_DQ_STATE_FILE",
        ".hermes/commission_dq_state.json",
    ).strip()
    return Path(raw).expanduser()


def _already_sent_today(day: date) -> bool:
    path = _state_path()
    if not path.exists():
        return False
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return False
    return isinstance(data, dict) and str(data.get("last_sent_date")) == day.isoformat()


def _write_state(day: date) -> None:
    path = _state_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"last_sent_date": day.isoformat()}))
    except OSError:
        return


def _pick(row: dict[str, Any] | None, *keys: str) -> Any:
    if not row:
        return None
    for key in keys:
        if key in row and row[key] not in ("", None):
            return row[key]
    return None


def _as_decimal(value: Any) -> Decimal | None:
    if value in ("", None):
        return None
    try:
        return Decimal(str(value).replace("%", "").replace(",", "").replace("$", ""))
    except (InvalidOperation, ValueError):
        return None


def _as_bool(value: Any) -> bool | None:
    if value in ("", None):
        return None
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "t", "yes", "y"}:
        return True
    if text in {"0", "false", "f", "no", "n"}:
        return False
    return None


def _money(value: Decimal) -> str:
    return f"{value.quantize(Decimal('0.01'))}"
