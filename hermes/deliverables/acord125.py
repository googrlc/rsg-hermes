"""ACORD 125 — Commercial Insurance Application (Applicant Information).

The carrier-facing application section for a commercial submission. Filled from
the Command Center **SubmissionObject** (the intake candidate data), NOT from
bound-policy records — a 125 is new business going *to* the underwriter.

Design mirrors ``acord25.py`` (the testable core has no I/O):

  SubmissionObject  --from_submission-->  Acord125 (logical model)
  Acord125          --build_field_map-->  {pdf_field_name: value}
  Acord125          --render_preview--->  markdown preview (template-free)
  {field: value}    --fill_pdf--------->  filled PDF bytes (licensed template)
  filled PDF        --draft_acord125--->  Nextcloud + Slack + Supabase log

``from_submission``, ``build_field_map`` and ``render_preview`` are pure and
unit-tested. ``fill_pdf`` (shared via ``acord_pdf``) needs RSG's **licensed**
ACORD 125 template — the forms are copyrighted; pull ours from NowCerts / agency
files. ``draft_acord125`` wires the live integrations, injected so they can be
faked in tests. **Never auto-sent** — a human reviews before it leaves.

HARD RULE — never fabricate. A field the submission does not have stays blank;
it never gets an invented value. Missing-but-required fields surface in
``render_preview`` so a human knows what to collect before the 125 is complete.

IMPORTANT — template field names: the keys in ``FIELD_NAMES`` are readable
placeholders. Real ACORD 125 AcroForm names vary by template source; before first
use run ``acord_pdf.list_template_fields(<our template>)`` and reconcile via the
``field_names`` arg or the ``HERMES_ACORD125_FIELDMAP`` json env override.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from hermes.deliverables import acord_pdf

log = logging.getLogger(__name__)

FIELDMAP_ENV = "HERMES_ACORD125_FIELDMAP"
FORM_LABEL = "ACORD 125"


# ---------------------------------------------------------------------------
# Logical model — what an ACORD 125 applicant section needs, PDF-independent.
# ---------------------------------------------------------------------------
@dataclass
class Acord125:
    producer_name: str = "Risk Solutions Group"
    named_insured: str = ""
    dba: str = ""
    mailing_address: str = ""
    entity_type: str = ""
    fein: str = ""
    phone: str = ""
    email: str = ""
    website: str = ""
    naics: str = ""
    sic: str = ""
    proposed_eff_date: str = ""
    premises: list[str] = field(default_factory=list)
    prior_carrier: str = ""
    prior_policy_number: str = ""
    prior_premium: str = ""
    prior_expiration: str = ""


# ---------------------------------------------------------------------------
# Logical field -> PDF AcroForm field name. VERIFY against our template.
# ---------------------------------------------------------------------------
FIELD_NAMES: dict[str, str] = {
    "producer_name": "PRODUCER",
    "named_insured": "NAMED INSURED",
    "dba": "DBA",
    "mailing_address": "MAILING ADDRESS",
    "entity_type": "ENTITY TYPE",
    "fein": "FEIN",
    "phone": "PHONE",
    "email": "EMAIL",
    "website": "WEBSITE",
    "naics": "NAICS",
    "sic": "SIC",
    "proposed_eff_date": "PROPOSED EFF DATE",
    "prior_carrier": "PRIOR CARRIER",
    "prior_policy_number": "PRIOR POLICY NUMBER",
    "prior_premium": "PRIOR PREMIUM",
    "prior_expiration": "PRIOR EXP DATE",
}

# The 125 premises section holds several location rows; map the first few.
PREMISES_FIELDS = ["PREMISES 1", "PREMISES 2", "PREMISES 3"]


# ---------------------------------------------------------------------------
# SubmissionObject -> Acord125  (pure)
# ---------------------------------------------------------------------------
def _s(v: Any) -> str:
    """A submission value as a clean string; None/empty -> ''. Never invents."""
    if v in (None, ""):
        return ""
    return str(v).strip()


def _fmt_address(addr: Any) -> str:
    """An Address (or anything with street/city/state/zip) -> one line, or ''."""
    if addr is None:
        return ""
    parts = [
        _s(getattr(addr, "street", "")),
        _s(getattr(addr, "city", "")),
        _s(getattr(addr, "state", "")),
        _s(getattr(addr, "zip", "")),
    ]
    line = ", ".join(p for p in parts[:2] if p)
    tail = " ".join(p for p in parts[2:] if p)
    return " ".join(x for x in (line, tail) if x).strip(", ").strip()


def _entity_label(entity_type: Any) -> str:
    """EntityType enum (or str) -> a human ACORD label, or '' if unset."""
    if entity_type in (None, ""):
        return ""
    raw = getattr(entity_type, "value", entity_type)
    return str(raw).replace("_", " ").upper()


def from_submission(sub: Any) -> Acord125:
    """Build an Acord125 from a SubmissionObject. Missing data stays blank."""
    a = sub.applicant
    premises = [
        line for line in (_fmt_address(p.address) for p in (sub.property_locations or [])) if line
    ]
    prior = (sub.prior_carriers or [None])[0]
    return Acord125(
        named_insured=_s(a.legal_name) or _s(sub.client_name),
        dba=", ".join(_s(d) for d in (a.dbas or []) if _s(d)),
        mailing_address=_fmt_address(a.mailing_address),
        entity_type=_entity_label(a.entity_type),
        fein=_s(a.fein),
        phone=_s(a.phone),
        email=_s(a.email),
        website=_s(a.website),
        naics=_s(a.naics),
        sic=_s(a.sic),
        proposed_eff_date=_s(sub.target_effective_date),
        premises=premises,
        prior_carrier=_s(getattr(prior, "carrier", "")) if prior else "",
        prior_policy_number=_s(getattr(prior, "policy_no", "")) if prior else "",
        prior_premium=_s(getattr(prior, "premium", "")) if prior else "",
        prior_expiration=_s(getattr(prior, "expiration", "")) if prior else "",
    )


# ---------------------------------------------------------------------------
# Acord125 -> {pdf_field_name: value}  (pure)
# ---------------------------------------------------------------------------
def build_field_map(a125: Acord125, field_names: Optional[dict[str, str]] = None) -> dict[str, str]:
    """Flatten an Acord125 into PDF field values. Empty/missing are dropped."""
    names = {**FIELD_NAMES, **(field_names or {})}
    out: dict[str, str] = {}
    for logical, pdf_name in names.items():
        out[pdf_name] = _s(getattr(a125, logical, ""))
    for slot, premises_line in zip(PREMISES_FIELDS, a125.premises):
        out[slot] = premises_line
    return {k: v for k, v in out.items() if v}


# ---------------------------------------------------------------------------
# Acord125 -> markdown preview (template-free; the Command Center deliverable)
# ---------------------------------------------------------------------------
# Fields a 125 needs that the submission cannot currently supply — surfaced so a
# human collects them, never invented.
_COMPLETENESS_FIELDS = [
    ("named_insured", "Named insured"),
    ("mailing_address", "Mailing address"),
    ("fein", "FEIN"),
    ("entity_type", "Legal entity type"),
    ("naics", "NAICS"),
    ("proposed_eff_date", "Proposed effective date"),
]


def _dash(v: str) -> str:
    return v if v else "—"


def render_preview(a125: Acord125) -> str:
    """Human-readable preview of exactly what will be placed on the ACORD 125.

    Template-free, so it works in the download pack before the licensed template
    is installed. The same values feed ``fill_pdf`` once it is.
    """
    lines = [
        f"# ACORD 125 (Commercial Application) — {_dash(a125.named_insured)}",
        "",
        "## Applicant",
        f"- **Named insured:** {_dash(a125.named_insured)}",
        f"- **DBA:** {_dash(a125.dba)}",
        f"- **Mailing address:** {_dash(a125.mailing_address)}",
        f"- **Entity type:** {_dash(a125.entity_type)}",
        f"- **FEIN:** {_dash(a125.fein)}",
        f"- **NAICS / SIC:** {_dash(a125.naics)} / {_dash(a125.sic)}",
        f"- **Phone / email:** {_dash(a125.phone)} / {_dash(a125.email)}",
        f"- **Website:** {_dash(a125.website)}",
        f"- **Proposed effective date:** {_dash(a125.proposed_eff_date)}",
    ]
    lines += ["", "## Premises"]
    if a125.premises:
        lines += [f"- {p}" for p in a125.premises]
    else:
        lines += ["- —"]
    lines += [
        "",
        "## Prior carrier",
        f"- **Carrier:** {_dash(a125.prior_carrier)}   "
        f"**Policy #:** {_dash(a125.prior_policy_number)}",
        f"- **Premium:** {_dash(a125.prior_premium)}   "
        f"**Expiration:** {_dash(a125.prior_expiration)}",
    ]
    missing = [label for attr, label in _COMPLETENESS_FIELDS if not getattr(a125, attr)]
    if missing:
        lines += [
            "",
            "## Still needed for a complete ACORD 125",
            *[f"- {m}" for m in missing],
        ]
    lines += [
        "",
        "_Draft field data for the ACORD 125. Filled PDF is generated from the "
        "same values via `draft_acord125` once the licensed template is installed._",
    ]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Review note + orchestration (live integrations injected).
# ---------------------------------------------------------------------------
def pre_send_checklist(a125: Acord125) -> str:
    who = a125.named_insured or "this applicant"
    return (
        f"*Draft ACORD 125 for {who}* — please review before it goes to the underwriter:\n"
        f"1. *Named insured & entity type* exactly as legally registered?\n"
        f"2. *FEIN, NAICS, and mailing address* correct?\n"
        f"3. *Premises* list every location to be covered?\n"
        f"4. *Proposed effective date* and prior-carrier info right?\n"
        f"_Nothing is submitted automatically — you send it once it looks right._"
    )


def supabase_logger(supa) -> Callable[[dict[str, Any]], None]:
    """A ``supa_log`` callable recording a draft into acord_drafts, agent_id-stamped."""
    from hermes.core.identity import agent_id

    def _log(summary: dict[str, Any]) -> None:
        supa.insert("acord_drafts", {
            "agent_id": agent_id(),
            "form": FORM_LABEL,
            "account": summary.get("account", ""),
            "output_path": summary.get("output_path"),
            "file_url": summary.get("file_url"),
            "placed_fields": summary.get("placed_fields", []),
            "skipped_fields": summary.get("skipped_fields", []),
            "auto_sent": False,
        })

    return _log


def draft_acord125(
    a125: Acord125,
    *,
    template_path: str,
    output_path: str,
    account_name: str,
    file_upload: Optional[Callable[[str], str]] = None,
    slack_post: Optional[Callable[[str], None]] = None,
    supa_log: Optional[Callable[[dict[str, Any]], None]] = None,
    field_names: Optional[dict[str, str]] = None,
) -> dict[str, Any]:
    """Fill → (Nextcloud) → (Slack) → (Supabase log). Returns a summary.

    Each side effect is optional/injected: real callables in production, omitted
    or faked in tests. The PDF fill always runs. Never auto-sends.
    """
    overrides = {**acord_pdf.load_fieldmap_override(FIELDMAP_ENV), **(field_names or {})}
    values = build_field_map(a125, overrides)
    fill_result = acord_pdf.fill_pdf(template_path, values, output_path, form_label=FORM_LABEL)

    file_url = file_upload(output_path) if file_upload else None
    if slack_post:
        msg = pre_send_checklist(a125)
        if file_url:
            msg += f"\nDraft: {file_url}"
        slack_post(msg)

    summary = {
        "account": account_name,
        "output_path": output_path,
        "file_url": file_url,
        "placed_fields": fill_result["placed"],
        "skipped_fields": fill_result["skipped"],
        "auto_sent": False,
    }
    if supa_log:
        supa_log(summary)
    return summary
