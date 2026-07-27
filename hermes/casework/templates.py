"""Case templates — a case type plus the checklist that has to happen for it.

Why these exist: the agency already had the playbooks written down (the skills
under .agents/skills/), but nothing connected them to the CRM. Somebody had to
remember the eight onboarding steps and type each one in, which is exactly how a
half-onboarded client happens. A template turns "open an onboarding case" into
one action that lands the whole checklist.

The checklists here are deliberately derived from the existing skills rather than
invented, so the CRM enforces the SOP the agency already agreed to:

    new_business_onboarding  <- client-onboarding/SKILL.md (Step 7 follow-ups)
    coi_request              <- coi-processing/SKILL.md
    endorsement              <- service-sops/SKILL.md
    offboarding              <- service-sops/SKILL.md (cancellation)
    claim                    <- service-sops/SKILL.md
    premium_audit            <- commercial-risk practice
    billing_collections      <- service-sops/SKILL.md (billing)

Two rules encoded in the data:

``required``
    The task blocks case closure. Reserve it for steps where skipping means the
    work is genuinely not done — money moved, a filing made, the client told.
    Everything else is guidance, and guidance should not stop somebody closing a
    case they have actually finished.

``due_days``
    Business-ish offset from case open, used to stagger the checklist so the
    whole list doesn't land due today and train everyone to ignore due dates.
"""

from __future__ import annotations

from typing import Any

# Each template: case_type is what the AMS and reporting bucket on; label is what
# a human picks from a menu; title is formatted with the insured name.
CASE_TEMPLATES: dict[str, dict[str, Any]] = {
    # ── New business ────────────────────────────────────────────────────────
    "new_business_onboarding": {
        "label": "New Business Onboarding",
        "case_type": "onboarding",
        "title": "Onboarding — {insured_name}",
        "priority": "high",
        "due_days": 45,
        "description": (
            "New client setup. Not done until the client has heard from a human, "
            "every line has its own opportunity, and the renewal is registered."
        ),
        "tasks": [
            {"title": "Create account and contacts (check for duplicates first)",
             "due_days": 1, "priority": "high", "required": True},
            {"title": "Open one opportunity per line of business with correct premium",
             "due_days": 1, "priority": "high", "required": True},
            {"title": "Register renewal date and assign a renewal owner",
             "due_days": 2, "priority": "high", "required": True,
             "description": "No renewal date means no renewal cadence, which is how retention leaks."},
            {"title": "Build the NextCloud folder structure",
             "due_days": 2, "priority": "medium", "required": False},
            {"title": "Send the welcome email (sent, not drafted)",
             "due_days": 2, "priority": "high", "required": True,
             "description": (
                 "Template email lives in Codex: "
                 "https://chatgpt.com/plugins/Plugin_58e8181057248191b9c6e46ba7183bcf?open_in_codex"
             )},
            {"title": "Welcome call to the client",
             "due_days": 3, "priority": "high", "required": False},
            {"title": "Confirm policy bind and verify documents received",
             "due_days": 5, "priority": "high", "required": True},
            {"title": "Review cross-sell gaps and open opportunities",
             "due_days": 14, "priority": "medium", "required": False,
             "description": "Do this while the relationship is new — this is the money."},
            {"title": "30-day check-in",
             "due_days": 30, "priority": "medium", "required": False},
        ],
    },
    # ── Off-boarding ────────────────────────────────────────────────────────
    "offboarding": {
        "label": "Off-boarding / Cancellation",
        "case_type": "offboarding",
        "title": "Off-boarding — {insured_name}",
        "priority": "high",
        "due_days": 30,
        "description": (
            "Client is leaving or a policy is cancelling. Every step here exists "
            "because skipping it costs money or creates an E&O gap."
        ),
        "tasks": [
            {"title": "Capture the cancellation request in writing with effective date",
             "due_days": 0, "priority": "high", "required": True,
             "description": "Verbal cancellation is an E&O exposure. Get it in writing."},
            {"title": "Attempt the retention save before processing",
             "due_days": 1, "priority": "high", "required": False,
             "description": "Ask why. A remarket often saves the account."},
            {"title": "Submit cancellation to the carrier and confirm receipt",
             "due_days": 2, "priority": "high", "required": True},
            {"title": "Confirm return premium / pro-rata calculation",
             "due_days": 5, "priority": "high", "required": True},
            {"title": "Reconcile the commission chargeback",
             "due_days": 7, "priority": "medium", "required": True},
            {"title": "Update policy status in the AMS",
             "due_days": 3, "priority": "high", "required": True},
            {"title": "Archive the client folder and close open tasks",
             "due_days": 10, "priority": "low", "required": False},
            {"title": "Log the loss reason for retention reporting",
             "due_days": 10, "priority": "medium", "required": True,
             "description": "Without a reason the retention number teaches us nothing."},
        ],
    },
    # ── Endorsement ─────────────────────────────────────────────────────────
    "endorsement": {
        "label": "Endorsement / Policy Change",
        "case_type": "endorsement",
        "title": "Endorsement — {insured_name}",
        "priority": "high",
        "due_days": 14,
        "description": "A mid-term policy change, tracked until the carrier confirms it.",
        "tasks": [
            {"title": "Capture the requested change and effective date in writing",
             "due_days": 0, "priority": "high", "required": True},
            {"title": "Confirm the change is within carrier appetite / eligibility",
             "due_days": 1, "priority": "medium", "required": False},
            {"title": "Submit the endorsement request to the carrier",
             "due_days": 1, "priority": "high", "required": True},
            {"title": "Receive the endorsement and verify it matches the request",
             "due_days": 7, "priority": "high", "required": True,
             "description": "Read what came back. Carriers issue what they understood, not what you asked."},
            {"title": "Confirm the premium change and update billing expectations",
             "due_days": 8, "priority": "high", "required": True},
            {"title": "Update the AMS and file the endorsement document",
             "due_days": 8, "priority": "high", "required": True},
            {"title": "Notify the client the change is in force",
             "due_days": 9, "priority": "high", "required": True},
            {"title": "Reissue any certificates affected by the change",
             "due_days": 10, "priority": "medium", "required": False},
        ],
    },
    # ── COI ─────────────────────────────────────────────────────────────────
    "coi_request": {
        "label": "Certificate of Insurance",
        "case_type": "service",
        "title": "COI — {insured_name}",
        "priority": "high",
        "due_days": 2,
        "description": "Certificate request. Fast turnaround; the holder is usually waiting on it.",
        "tasks": [
            {"title": "Collect holder name, address and delivery instructions",
             "due_days": 0, "priority": "high", "required": True},
            {"title": "Confirm named insured matches the policy exactly",
             "due_days": 0, "priority": "high", "required": True},
            {"title": "Identify additional insured / waiver of subrogation / primary & noncontributory requirements",
             "due_days": 0, "priority": "high", "required": True,
             "description": "Quote requested special wording exactly. Do not paraphrase."},
            {"title": "Verify the policy actually supports the requested wording",
             "due_days": 1, "priority": "high", "required": True,
             "description": "If the endorsement isn't on the policy, the certificate cannot say it is."},
            {"title": "Get carrier approval for any special wording",
             "due_days": 1, "priority": "medium", "required": False},
            {"title": "Issue the certificate and deliver to the holder",
             "due_days": 1, "priority": "high", "required": True},
            {"title": "File the certificate and record the holder",
             "due_days": 2, "priority": "medium", "required": True},
        ],
    },
    # ── Claim ───────────────────────────────────────────────────────────────
    "claim": {
        "label": "Claim Reported",
        "case_type": "claim",
        "title": "Claim — {insured_name}",
        "priority": "urgent",
        "due_days": 30,
        "description": "First notice of loss through to resolution. Speed matters most on day one.",
        "tasks": [
            {"title": "Capture loss details, date of loss and contact information",
             "due_days": 0, "priority": "urgent", "required": True},
            {"title": "Report the claim to the carrier and record the claim number",
             "due_days": 0, "priority": "urgent", "required": True,
             "description": "Same day. Late notice is a coverage defence for the carrier."},
            {"title": "Send the client the claim number and adjuster contact",
             "due_days": 1, "priority": "high", "required": True},
            {"title": "Confirm the adjuster has made contact",
             "due_days": 3, "priority": "high", "required": True},
            {"title": "Follow up on claim status",
             "due_days": 14, "priority": "medium", "required": False},
            {"title": "Record the outcome and note any renewal/rating impact",
             "due_days": 30, "priority": "medium", "required": True},
        ],
    },
    # ── Premium audit ───────────────────────────────────────────────────────
    "premium_audit": {
        "label": "Premium Audit",
        "case_type": "service",
        "title": "Premium audit — {insured_name}",
        "priority": "medium",
        "due_days": 45,
        "description": "Carrier audit of actual exposures. Unmanaged audits become surprise bills.",
        "tasks": [
            {"title": "Notify the client the audit is due and what records are needed",
             "due_days": 0, "priority": "high", "required": True},
            {"title": "Collect payroll / sales / subcontractor records",
             "due_days": 14, "priority": "high", "required": True},
            {"title": "Review figures for obvious misclassification before submitting",
             "due_days": 16, "priority": "medium", "required": False,
             "description": "A wrong class code here bills the client for the next year too."},
            {"title": "Submit the audit to the carrier",
             "due_days": 18, "priority": "high", "required": True},
            {"title": "Review the audit result and dispute if incorrect",
             "due_days": 35, "priority": "high", "required": True},
            {"title": "Explain the additional or return premium to the client",
             "due_days": 40, "priority": "high", "required": True},
        ],
    },
    # ── Billing ─────────────────────────────────────────────────────────────
    "billing_collections": {
        "label": "Billing / Unpaid Invoice",
        "case_type": "service",
        "title": "Billing — {insured_name}",
        "priority": "high",
        "due_days": 21,
        "description": "Unpaid premium chased before it becomes a cancellation.",
        "tasks": [
            {"title": "Confirm the amount owed and which invoice / policy it belongs to",
             "due_days": 0, "priority": "high", "required": True},
            {"title": "Contact the client about the balance",
             "due_days": 1, "priority": "high", "required": True},
            {"title": "Check for a pending cancellation date on the policy",
             "due_days": 1, "priority": "urgent", "required": True,
             "description": "This is the one that turns a billing issue into a lapse."},
            {"title": "Confirm payment received and posted with the carrier",
             "due_days": 14, "priority": "high", "required": True},
            {"title": "If unpaid, process cancellation and record the reason",
             "due_days": 21, "priority": "high", "required": False},
        ],
    },
}


def list_templates() -> list[dict[str, Any]]:
    """Template menu for the cockpit — enough to render a picker, no task bodies."""
    return [
        {
            "key": key,
            "label": tpl["label"],
            "case_type": tpl["case_type"],
            "priority": tpl.get("priority", "medium"),
            "description": tpl.get("description"),
            "task_count": len(tpl["tasks"]),
            "required_count": sum(1 for t in tpl["tasks"] if t.get("required")),
        }
        for key, tpl in sorted(CASE_TEMPLATES.items(), key=lambda kv: kv[1]["label"])
    ]


def get_template(key: str) -> dict[str, Any] | None:
    return CASE_TEMPLATES.get(key)


def render_title(key: str, insured_name: str | None) -> str:
    """Case title for a template. Falls back gracefully when no insured is known —
    an unnamed case is still better than refusing to open one."""
    tpl = CASE_TEMPLATES[key]
    return tpl["title"].format(insured_name=insured_name or "Unassigned")


def build_summary(case: dict[str, Any], tasks: list[dict[str, Any]]) -> str:
    """The text that goes to the AMS on close.

    Deliberately a summary, not a checklist dump: NowCerts wants to know what
    happened and that it is done. The per-task detail and timings stay in the CRM,
    which is where they are actually useful.
    """
    done = [t for t in tasks if t.get("status") == "completed"]
    skipped = [t for t in tasks if t.get("status") == "cancelled"]

    lines = [
        f"{case.get('title') or 'Case'} ({case.get('case_number')})",
        f"Resolution: {case.get('resolution') or 'Closed.'}",
        f"Completed {len(done)} of {len(tasks)} steps"
        + (f", {len(skipped)} not applicable" if skipped else "")
        + ".",
    ]
    if case.get("opened_at") and case.get("closed_at"):
        lines.append(f"Opened {case['opened_at']} · closed {case['closed_at']}.")
    return "\n".join(lines)
