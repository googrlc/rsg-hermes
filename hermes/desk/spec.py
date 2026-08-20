"""Zoho Desk case-management catalog for Risk Solutions Group.

Desk owns the work. Momentum/NowCerts owns the policy record. Zoho CRM owns
the sales opportunity. Documents stay in Nextcloud (or SharePoint). Supabase
holds mapping tables, event history, and AI-ready structured data.

This module is the executable source of truth for departments, teams, statuses,
categories, priorities, views, and launch sequencing. Layout fields live in
``hermes.desk.fields``. Routing rules live in ``hermes.desk.routing``.
"""

from __future__ import annotations

from dataclasses import dataclass

# ── Operating model ──────────────────────────────────────────────────────────

DEPARTMENT = "Agency Service"

# The live CRM Plus portal already has one department named RSG. Treat it as
# Agency Service — never create a second department for queues.
DEPARTMENT_ALIASES = (DEPARTMENT, "RSG")

SYSTEMS_OF_RECORD = {
    "ams": "Momentum / NowCerts — clients, policies, coverages, transactions, carriers, documents of record",
    "crm": "Zoho CRM — leads, prospects, opportunities, referral partners, new-business pipeline",
    "desk": "Zoho Desk — service cases, ownership, status, communication, approvals, follow-up, SLA",
    "documents": "Nextcloud or SharePoint — controlled document storage and agency knowledge",
    "supabase": "Supabase — integration mapping, event history, queues, AI-ready structured data",
    "assistant": "Amy / Copilot — search, summarize, draft, classify, help staff complete cases",
}

# Queues live as ticket fields + teams inside ONE department. A second
# department is warranted only for a separate email address, security model,
# customer portal, or SLA policy.
QUEUES = (
    "Certificate Requests",
    "Policy Changes",
    "Billing and Payments",
    "Claims Assistance",
    "Renewals",
    "Policy Documents",
    "Cancellations and Reinstatements",
    "New Business Support",
    "Carrier and Underwriting",
    "Licensing and Compliance",
    "General Service",
    "Internal Operations",
)

TEAMS = (
    "Service Intake",
    "Certificates",
    "Commercial Auto Service",
    "Commercial Lines Service",
    "Personal Lines Service",
    "Claims Support",
    "Billing and Retention",
    "Renewals",
    "New Business Support",
    "Compliance",
    "Management Escalations",
)

STATUSES = (
    "New",
    "Triaged",
    "Information Needed",
    "Ready for Processing",
    "In Progress",
    "Submitted to Carrier",
    "Waiting on Carrier",
    "Waiting on Client",
    "Pending Internal Approval",
    "Ready for Delivery",
    "Delivered",
    "Monitoring",
    "Resolved",
    "Closed",
    "Cancelled",
    "Duplicate",
)

TERMINAL_STATUSES = frozenset({"Resolved", "Closed", "Cancelled", "Duplicate"})
WAITING_STATUSES = frozenset({"Waiting on Carrier", "Waiting on Client"})
OPEN_STATUSES = tuple(s for s in STATUSES if s not in TERMINAL_STATUSES)

PRIORITIES = ("Urgent", "High", "Normal", "Low")

CATEGORIES = (
    "Certificate Request",
    "Policy Change",
    "Billing and Payments",
    "Claims Assistance",
    "Renewals",
    "Policy Documents",
    "Cancellations and Reinstatements",
    "New Business Support",
    "Carrier and Underwriting",
    "Licensing and Compliance",
    "General Service",
    "Internal Operations",
)

# Short labels used in standardized case titles (CF-02).
CATEGORY_SHORT = {
    "Certificate Request": "Certificate",
    "Policy Change": "Policy Change",
    "Billing and Payments": "Billing",
    "Claims Assistance": "Claim",
    "Renewals": "Renewal",
    "Policy Documents": "Documents",
    "Cancellations and Reinstatements": "Cancellation",
    "New Business Support": "New Business",
    "Carrier and Underwriting": "Carrier",
    "Licensing and Compliance": "Compliance",
    "General Service": "Service",
    "Internal Operations": "Internal",
}

AUTO_DRIVER_SUBTYPES = (
    "Add vehicle",
    "Remove vehicle",
    "Replace vehicle",
    "Add driver",
    "Remove driver",
    "Change garaging",
    "Change use",
)

POLICY_CHANGE_SUBTYPES = AUTO_DRIVER_SUBTYPES + (
    "General endorsement",
    "Location change",
    "Exposure change",
    "Payroll or sales change",
    "Mortgagee or loss payee",
    "Other",
)

LAYOUTS = (
    "shared",
    "certificate",
    "auto_driver",
    "policy_change",
    "claims",
    "billing",
    "cancellation",
    "renewal",
)

LAYOUT_LABELS = {
    "shared": "General Service (primary)",
    "certificate": "Certificate Request",
    "auto_driver": "Auto or Driver Change",
    "policy_change": "General Policy Change",
    "claims": "Claims Assistance",
    "billing": "Billing and Payment",
    "cancellation": "Cancellation or Nonrenewal",
    "renewal": "Renewal Service",
}

CATEGORY_TO_LAYOUT = {
    "Certificate Request": "certificate",
    "Policy Change": "policy_change",
    "Billing and Payments": "billing",
    "Claims Assistance": "claims",
    "Renewals": "renewal",
    "Cancellations and Reinstatements": "cancellation",
    "Policy Documents": "shared",
    "New Business Support": "shared",
    "Carrier and Underwriting": "shared",
    "Licensing and Compliance": "shared",
    "General Service": "shared",
    "Internal Operations": "shared",
}

CATEGORY_TO_TEAM = {
    "Certificate Request": "Certificates",
    "Policy Change": "Commercial Lines Service",
    "Billing and Payments": "Billing and Retention",
    "Claims Assistance": "Claims Support",
    "Renewals": "Renewals",
    "Cancellations and Reinstatements": "Billing and Retention",
    "Policy Documents": "Service Intake",
    "New Business Support": "New Business Support",
    "Carrier and Underwriting": "Commercial Lines Service",
    "Licensing and Compliance": "Compliance",
    "General Service": "Service Intake",
    "Internal Operations": "Service Intake",
}

PERSONAL_LINES = frozenset({"Personal Auto", "Home", "Renters", "Dwelling Fire", "Boat", "Umbrella Personal"})
COMMERCIAL_AUTO_LINES = frozenset({"Commercial Auto"})

LINES_OF_BUSINESS = (
    "Commercial Auto",
    "Personal Auto",
    "General Liability",
    "BOP",
    "Workers Compensation",
    "Property",
    "Inland Marine",
    "Umbrella",
    "Umbrella Personal",
    "Home",
    "Renters",
    "Dwelling Fire",
    "Boat",
    "Other",
)

PICKLISTS = {
    "request_category": CATEGORIES,
    "request_subtype": POLICY_CHANGE_SUBTYPES,
    "service_tier": ("Standard", "Priority", "VIP"),
    "source": ("Email", "Phone", "Portal", "Internal", "Slack", "AMS", "CRM"),
    "priority": PRIORITIES,
    "urgency_reason": (
        "Cancellation or lapse imminent",
        "Proof of insurance blocking active work",
        "Vehicle or driver needs coverage before operation",
        "Active claim has an immediate service problem",
        "Binding or effective-date issue",
        "Regulatory or contractual deadline imminent",
        "Time-sensitive certificate",
        "Reinstatement request",
        "Material policy correction",
        "Carrier follow-up affecting coverage",
        "Client escalation",
        "Not urgent",
    ),
    "business_impact": (
        "Coverage at risk",
        "Work blocked",
        "Billing",
        "Informational",
    ),
    "waiting_on": (
        "None",
        "Client",
        "Carrier",
        "Internal",
        "Finance company",
        "Producer",
    ),
    "approval_status": ("Not required", "Pending", "Approved", "Returned"),
    "integration_status": ("Not synced", "Pending", "Synced", "Failed"),
    "resolution_type": (
        "Completed",
        "Unable to complete",
        "Duplicate",
        "Cancelled",
        "Referred to sales",
        "Referred to carrier",
    ),
    "delivery_method": ("Email", "Portal", "Fax", "Mail", "In person"),
    "billing_type": ("Direct bill", "Agency bill", "Premium finance"),
    "line_of_business": LINES_OF_BUSINESS,
    "client_decision": ("Pending", "Renew as is", "Remarket", "Nonrenew", "Rewrite"),
    "request_initiated_by": ("Client", "Carrier", "Finance company", "Agency", "Producer"),
}

# Status rules from the operating model (enforced in routing / closure).
STATUS_RULES = {
    "New": "Must not remain assigned to an unmonitored general queue.",
    "Information Needed": "Must identify the missing item.",
    "Waiting on Carrier": "Must have a next follow-up date.",
    "Waiting on Client": "Must have a next follow-up date.",
    "Resolved": "Operational work is complete.",
    "Closed": "Confirmation and recordkeeping are complete.",
    "Cancelled": "Requires a cancellation reason.",
    "Duplicate": "Requires the related ticket number.",
}

# ── Views / dashboards ───────────────────────────────────────────────────────

OPERATIONAL_VIEWS = (
    "Unassigned New Tickets",
    "My Open Cases",
    "My Follow-Ups Due Today",
    "Urgent and High Priority",
    "Waiting on Client",
    "Waiting on Carrier",
    "Certificates Due",
    "Cancellation Risk",
    "Reinstatements Pending",
    "Changes Submitted, Not Issued",
    "Renewal Intake Incomplete",
    "Claims Needing Follow-Up",
    "Cases Missing Policy Number",
    "Cases Missing AMS Client ID",
    "Resolved, Not Posted to AMS",
    "Integration Failures",
    "Reopened Cases",
    "Cases Over Internal Target",
    "Management Escalations",
)

DASHBOARD_WIDGETS = (
    "New unassigned tickets",
    "Open tickets by category",
    "Tickets by status",
    "Follow-ups due",
    "Overdue required-by dates",
    "Waiting on carrier",
    "Waiting on client",
    "Reopened tickets",
    "Cancellation-risk cases",
    "Integration failures",
    "Cases resolved without AMS posting",
    "Certificate request volume",
    "Policy-change volume",
    "Renewal workflow stage",
)

EMAIL_TEMPLATES = (
    "Case received",
    "Missing information",
    "Certificate request received",
    "Certificate completed",
    "Policy change submitted",
    "Carrier follow-up",
    "Change completed",
    "Billing issue acknowledged",
    "Cancellation warning outreach",
    "Reinstatement pending",
    "Claim information received",
    "Adjuster details provided",
    "Renewal information request",
    "Renewal reminder",
    "Case resolved",
    "Unable to complete request",
    "Client approval needed",
    "Secure-document upload instructions",
)

EMAIL_TEMPLATE_REQUIRED_TOKENS = (
    "ticket_number",
    "client_name",
    "policy_number",
    "summary",
    "action_needed",
    "deadline",
    "case_owner",
    "reply_instructions",
)

KNOWLEDGE_BASE_INTERNAL = (
    "Certificate issuance standards",
    "Additional insured requests",
    "Waiver of subrogation handling",
    "Vehicle change procedures",
    "Driver change procedures",
    "Cancellation and reinstatement",
    "Claim reporting by carrier",
    "Billing contacts by carrier",
    "Endorsement follow-up",
    "Renewal checklist",
    "Carrier portal directory",
    "Agency codes",
    "Escalation contacts",
    "Policy-document naming",
    "AMS activity standards",
    "Secure information handling",
    "Approved client email language",
)

# Public KB is client-safe instructions only. Credentials, agency codes,
# internal escalation paths, and procedural notes stay internal.
KNOWLEDGE_BASE_PUBLIC_SAFE = (
    "How to request a certificate",
    "How to report a vehicle or driver change",
    "How to send documents securely",
    "What to do if you receive a cancellation notice",
    "How to report a claim",
)

# ── Build sequence ───────────────────────────────────────────────────────────

PHASES = (
    (
        1,
        "Foundation",
        (
            "Create Agency Service department",
            "Create teams",
            "Create shared ticket fields",
            "Create core statuses",
            "Build the General Service layout",
            "Connect one service email channel",
            "Create essential views",
        ),
    ),
    (
        2,
        "High-volume workflows",
        (
            "Certificate layout and Blueprint",
            "Policy Change layout and Blueprint",
            "Billing/Cancellation layout and Blueprint",
            "Claims layout and Blueprint",
            "Email templates",
            "Required-date reminders",
            "Waiting-state follow-up rules",
        ),
    ),
    (
        3,
        "Integration",
        (
            "Contact and account synchronization",
            "AMS client and policy lookup",
            "CRM opportunity handoff",
            "Document-folder linking",
            "AMS activity posting",
            "Integration exception handling",
        ),
    ),
    (
        4,
        "Intelligence",
        (
            "Ticket classification",
            "Suggested replies",
            "Request summarization",
            "Missing-information detection",
            "Knowledge article recommendations",
            "Amy/Copilot case search",
            "Management case briefings",
        ),
    ),
)

# First four workflows to launch. Claims and renewals wait until these run cleanly.
LAUNCH_WORKFLOWS = (
    "Certificate Requests",
    "Vehicle and Driver Changes",
    "Billing, Cancellation, and Reinstatement",
    "General Policy Changes",
)

CUSTOM_FUNCTIONS = (
    "CF-01 Find the account and policy",
    "CF-02 Generate standardized case title",
    "CF-03 Create secure document folder",
    "CF-04 Post AMS activity",
    "CF-05 Integration error handler",
    "CF-06 Renewal case generation",
)

AUTOMATION_IDS = tuple(f"AUT-{n:02d}" for n in range(1, 15))


@dataclass(frozen=True)
class AutomationSpec:
    automation_id: str
    name: str
    trigger: str
    conditions: str
    summary: str


AUTOMATIONS: tuple[AutomationSpec, ...] = (
    AutomationSpec(
        "AUT-01",
        "New ticket classification",
        "Ticket created",
        "Any channel",
        "Set New, stamp received time, identify source, associate contact/account, "
        "assign Service Intake when uncertain, notify if no account match.",
    ),
    AutomationSpec(
        "AUT-02",
        "Certificate routing",
        "Ticket created or category changed",
        "Category = Certificate Request",
        "Apply Certificate layout, assign Certificates team, set priority from "
        "required-by date and business impact, send intake acknowledgement, "
        "create follow-up if required fields are missing.",
    ),
    AutomationSpec(
        "AUT-03",
        "Auto and driver routing",
        "Ticket created or category/subtype changed",
        "Category = Policy Change and subtype is vehicle or driver",
        "Apply Auto or Driver Change layout, route personal vs commercial, "
        "require effective date and VIN/driver fields, block Ready for Processing "
        "until required information is present.",
    ),
    AutomationSpec(
        "AUT-04",
        "Cancellation warning",
        "Ticket created or field changed",
        "Category = Billing or Cancellation and cancellation warning = Yes",
        "Set High or Urgent from the recorded deadline, assign Billing and Retention, "
        "notify owner and escalation recipient, create follow-up before cancellation "
        "date, require documented disposition before resolution.",
    ),
    AutomationSpec(
        "AUT-05",
        "Waiting on client",
        "Status changes to Waiting on Client",
        "Status = Waiting on Client",
        "Require Missing Information and Next Follow-Up Date, send missing-information "
        "template, create follow-up task, escalate if the deadline passes.",
    ),
    AutomationSpec(
        "AUT-06",
        "Waiting on carrier",
        "Status changes to Waiting on Carrier",
        "Status = Waiting on Carrier",
        "Require carrier and last-contact date plus next follow-up date, create "
        "follow-up task, notify owner when due, escalate aging items by priority.",
    ),
    AutomationSpec(
        "AUT-07",
        "Client reply received",
        "Customer replies",
        "Status = Waiting on Client",
        "Change status to Ready for Processing, reassign previous owner, clear "
        "waiting reason, notify owner.",
    ),
    AutomationSpec(
        "AUT-08",
        "Carrier response received",
        "Carrier reply or internal update",
        "Status = Waiting on Carrier",
        "Change status to In Progress, notify owner, cancel obsolete carrier follow-up.",
    ),
    AutomationSpec(
        "AUT-09",
        "Required-by reminder",
        "Scheduled rule",
        "Required-by date approaching and status not Resolved or Closed",
        "Notify owner, increase priority at agency thresholds, escalate overdue "
        "tickets to the service lead.",
    ),
    AutomationSpec(
        "AUT-10",
        "Closure control",
        "Status changes to Resolved or Closed",
        "Status in (Resolved, Closed)",
        "Require resolution type, final customer communication, AMS activity posted, "
        "final documents stored, no outstanding task, no pending carrier/client "
        "requirement. Failed checks roll the ticket back to In Progress.",
    ),
    AutomationSpec(
        "AUT-11",
        "Reopened ticket",
        "Client replies after resolution",
        "Prior status Resolved or Closed",
        "Reopen, restore prior owner, require reopened reason, increment reopen count, "
        "notify owner.",
    ),
    AutomationSpec(
        "AUT-12",
        "Duplicate detection",
        "Ticket created",
        "Open tickets with same contact, policy, category, similar subject, recent",
        "Flag a possible duplicate for review. Never auto-delete.",
    ),
    AutomationSpec(
        "AUT-13",
        "CRM handoff",
        "Ticket created or updated",
        "Interest in new coverage, cross-sell, additional location, or new entity",
        "Flag Sales Opportunity = Yes, create/update CRM opportunity through the "
        "approved integration, store CRM record ID, assign sales follow-up, keep "
        "the service ticket open until the service issue is addressed.",
    ),
    AutomationSpec(
        "AUT-14",
        "AMS posting",
        "Ticket reaches Ready for Delivery or Resolved",
        "Status in (Ready for Delivery, Resolved)",
        "Send structured activity data to Momentum. Mark AMS Activity Posted only "
        "after a successful response. Route failures to Integration Exceptions.",
    ),
)
