"""On-demand ACORD pack generator (P2).

The "generate ACORD copies for the underwriter" action. Given the forms an agent
selected for a commercial submission, it fills the real PDFs and returns the
opportunity drafts to stage — behind an explicit choice, never auto-run, never
auto-sent.

  plan_selection(sub, form_ids)
    → fill the combined 125/126 once (125 hub + 126 GL section if GL selected)
    → fill one ACORD 140 per building (if property selected)
    → return {artifacts, opportunities, missing_templates, needs_filler, unknown}

Templates are resolved from each form's ``template_env`` (the licensed PDFs live
on the box, never in the repo). A missing template is reported, not fatal —
the rest of the pack still generates. PDF I/O is injected (``fill_fn``) so the
orchestration is testable without the licensed templates.
"""

from __future__ import annotations

import os
import re
from typing import Any, Callable, Optional

from hermes.deliverables import (
    acord130,
    acord131,
    acord140,
    acord_commercial_pack,
    acord_pdf,
    acord_registry,
    acord_selection,
)

TEMPLATE_125_126_ENV = "HERMES_ACORD_125_126_TEMPLATE"
TEMPLATE_140_ENV = "HERMES_ACORD_140_TEMPLATE"

# Single-template, single-fill forms (their own PDF, not the combined 125/126 and
# not per-building). Dispatched generically: add a form here + its filler module
# and it generates from selection with no other change.
_SIMPLE_FILLERS: dict[str, tuple[Any, str, str]] = {
    "acord_130": (acord130, "HERMES_ACORD_130_TEMPLATE", "ACORD 130"),
    "acord_131": (acord131, "HERMES_ACORD_131_TEMPLATE", "ACORD 131"),
}


def _safe_name(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", name or "client").strip("_") or "client"


def _resolve_template(env_var: Optional[str], templates: dict[str, str]) -> Optional[str]:
    """Template path from the passed override dict, else the environment."""
    if not env_var:
        return None
    return templates.get(env_var) or (os.environ.get(env_var) or "").strip() or None


def generate_pack(
    sub: Any,
    form_ids: list[str],
    *,
    output_dir: str,
    templates: Optional[dict[str, str]] = None,
    fill_fn: Callable[..., dict[str, Any]] = acord_pdf.fill_pdf,
) -> dict[str, Any]:
    """Fill the selected ACORDs for a submission. Returns a manifest.

    ``templates`` overrides the ``*_TEMPLATE`` env lookups (tests pass fakes).
    ``fill_fn`` defaults to the real filler but is injectable. Nothing is sent.
    """
    templates = templates or {}
    plan = acord_selection.plan_selection(sub, form_ids)
    account = _safe_name(sub.client_name or getattr(sub.applicant, "legal_name", "") or "client")

    artifacts: list[dict[str, Any]] = []
    missing_templates: list[str] = []

    # ── 1) combined 125/126 (base 125 always; 126 GL section only if GL selected)
    tpl = _resolve_template(TEMPLATE_125_126_ENV, templates)
    if tpl:
        text, checks = acord_commercial_pack.combined_field_map(sub, selected_lobs=plan.lines)
        out_path = f"{output_dir}/{account}_ACORD_125_126.pdf"
        result = fill_fn(tpl, text, out_path, checkboxes=checks, form_label="ACORD 125/126")
        artifacts.append({
            "form": "ACORD 125/126", "output_path": out_path,
            "placed": result.get("placed", []), "skipped": result.get("skipped", []),
            "auto_sent": False,
        })
    else:
        missing_templates.append(TEMPLATE_125_126_ENV)

    # ── 2) one ACORD 140 per building (only if property is a selected line) ──────
    if "commercial_property" in plan.lines:
        tpl140 = _resolve_template(TEMPLATE_140_ENV, templates)
        if tpl140:
            n = max(1, len(sub.property_locations or []))
            for i in range(n):
                a140 = acord140.from_submission(sub, location_index=i)
                out_path = f"{output_dir}/{account}_ACORD_140_Location_{i + 1}.pdf"
                result = fill_fn(tpl140, acord140.build_field_map(a140), out_path,
                                 form_label="ACORD 140")
                artifacts.append({
                    "form": "ACORD 140", "location": i + 1, "output_path": out_path,
                    "placed": result.get("placed", []), "skipped": result.get("skipped", []),
                    "auto_sent": False,
                })
        else:
            missing_templates.append(TEMPLATE_140_ENV)

    # ── 3) single-template supplemental forms (130 WC, 131 Umbrella, …) ─────────
    for form in plan.forms_to_fill:
        simple = _SIMPLE_FILLERS.get(form.form_id)
        if not simple:
            continue
        module, env_var, label = simple
        tpl_s = _resolve_template(env_var, templates)
        if not tpl_s:
            missing_templates.append(env_var)
            continue
        obj = module.from_submission(sub)
        checks = module.build_checkbox_map(obj) if hasattr(module, "build_checkbox_map") else None
        out_path = f"{output_dir}/{account}_{label.replace(' ', '_')}.pdf"
        result = fill_fn(tpl_s, module.build_field_map(obj), out_path,
                         checkboxes=checks, form_label=label)
        artifacts.append({
            "form": label, "output_path": out_path,
            "placed": result.get("placed", []), "skipped": result.get("skipped", []),
            "auto_sent": False,
        })

    return {
        "artifacts": artifacts,
        "opportunities": plan.opportunities,          # one per checked line — caller stages
        "lines": plan.lines,
        "missing_templates": missing_templates,
        "needs_filler": plan.selectable_without_filler,   # checked lines with no PDF yet
        "unknown_form_ids": plan.unknown_form_ids,
        "auto_sent": False,
    }
