"""Desk Blueprint sequences and transition requirements.

Workflows fire alerts, assignments, and field updates. Blueprints enforce the
sequence of states and the required actions through resolution.
"""

from __future__ import annotations

from dataclasses import dataclass

from hermes.desk.spec import AUTO_DRIVER_SUBTYPES


@dataclass(frozen=True)
class Transition:
    action: str
    from_status: str
    to_status: str
    required_fields: tuple[str, ...] = ()
    required_true: tuple[str, ...] = ()
    notes: str = ""


@dataclass(frozen=True)
class Blueprint:
    key: str
    name: str
    category: str
    stages: tuple[str, ...]
    transitions: tuple[Transition, ...]

    def transition(self, action: str) -> Transition | None:
        for item in self.transitions:
            if item.action == action:
                return item
        return None


CERTIFICATE = Blueprint(
    key="certificate",
    name="Certificate request",
    category="Certificate Request",
    stages=(
        "New",
        "Information Needed",
        "Ready for Processing",
        "Pending Internal Approval",
        "Ready for Delivery",
        "Delivered",
        "Closed",
    ),
    transitions=(
        Transition(
            "Validate Request",
            "New",
            "Ready for Processing",
            required_fields=(
                "cf_ams_client_id",
                "cf_policy_number",
                "cf_holder_name",
                "cf_holder_address",
                "cf_required_by_date",
                "cf_special_wording_requested",
            ),
            notes="Contract attached when special wording or additional interest applies.",
        ),
        Transition(
            "Receive Missing Information",
            "Information Needed",
            "Ready for Processing",
            required_fields=("cf_missing_information",),
        ),
        Transition(
            "Prepare Certificate",
            "Ready for Processing",
            "Pending Internal Approval",
            required_true=(
                "cf_coverage_verified",
                "cf_wording_reviewed",
                "cf_additional_interest_reviewed",
            ),
            notes="No unapproved coverage representation.",
        ),
        Transition(
            "Approve or Return",
            "Pending Internal Approval",
            "Ready for Delivery",
            required_fields=("cf_approval_status",),
            notes="Required when special wording, unusual holder requirements, or a coverage exception exists.",
        ),
        Transition(
            "Send Certificate",
            "Ready for Delivery",
            "Delivered",
            required_fields=("cf_holder_email", "cf_delivery_method"),
            notes="Final copy attached or linked.",
        ),
        Transition(
            "Confirm and Document",
            "Delivered",
            "Closed",
            required_true=(
                "cf_customer_confirmation_sent",
                "cf_ams_activity_posted",
                "cf_final_documents_stored",
            ),
        ),
    ),
)

POLICY_CHANGE = Blueprint(
    key="policy_change",
    name="Policy change",
    category="Policy Change",
    stages=(
        "New",
        "Triaged",
        "Information Needed",
        "Ready for Processing",
        "Submitted to Carrier",
        "Waiting on Carrier",
        "In Progress",
        "Ready for Delivery",
        "Monitoring",
        "Closed",
    ),
    transitions=(
        Transition("Triage", "New", "Triaged"),
        Transition(
            "Complete Intake",
            "Information Needed",
            "Ready for Processing",
            required_fields=("cf_missing_information", "cf_effective_date_requested"),
        ),
        Transition(
            "Submit to Carrier",
            "Ready for Processing",
            "Submitted to Carrier",
            required_fields=("cf_carrier", "cf_change_description"),
        ),
        Transition("Receive Carrier Response", "Waiting on Carrier", "In Progress"),
        Transition("Review Change", "In Progress", "Ready for Delivery"),
        Transition("Deliver Confirmation", "Ready for Delivery", "Monitoring"),
        Transition(
            "Verify Issued Documents",
            "Monitoring",
            "Closed",
            notes="A carrier acknowledgement is not the issued endorsement.",
        ),
    ),
)

BILLING_CANCELLATION = Blueprint(
    key="billing_cancellation",
    name="Billing and cancellation",
    category="Billing and Payments",
    stages=(
        "New",
        "In Progress",
        "Waiting on Client",
        "Waiting on Carrier",
        "Pending Internal Approval",
        "Monitoring",
        "Resolved",
        "Closed",
    ),
    transitions=(
        Transition("Assess Deadline", "New", "In Progress"),
        Transition("Contact Client or Carrier", "In Progress", "Waiting on Client"),
        Transition("Receive Response", "Waiting on Client", "In Progress"),
        Transition("Approve Retention Action", "Pending Internal Approval", "Monitoring"),
        Transition("Verify Payment or Reinstatement", "Monitoring", "Resolved"),
        Transition("Document Outcome", "Resolved", "Closed"),
    ),
)

CLAIMS = Blueprint(
    key="claims",
    name="Claims assistance",
    category="Claims Assistance",
    stages=(
        "New",
        "Information Needed",
        "In Progress",
        "Waiting on Carrier",
        "Waiting on Client",
        "Monitoring",
        "Resolved",
        "Closed",
    ),
    transitions=(
        Transition("Verify Claim Information", "New", "In Progress"),
        Transition(
            "Complete Claim Intake",
            "Information Needed",
            "In Progress",
            required_fields=("cf_missing_information",),
        ),
        Transition("Coordinate with Carrier", "In Progress", "Waiting on Carrier"),
        Transition("Follow Up", "Waiting on Carrier", "In Progress"),
        Transition("Confirm Service Outcome", "Monitoring", "Resolved"),
        Transition("Post Final Note", "Resolved", "Closed"),
    ),
)

BLUEPRINTS: tuple[Blueprint, ...] = (
    CERTIFICATE,
    POLICY_CHANGE,
    BILLING_CANCELLATION,
    CLAIMS,
)

BLUEPRINT_BY_KEY = {item.key: item for item in BLUEPRINTS}


def blueprint_for_category(category: str, *, subtype: str | None = None) -> Blueprint | None:
    if category == "Certificate Request":
        return CERTIFICATE
    if category == "Policy Change":
        return POLICY_CHANGE
    if category in {"Billing and Payments", "Cancellations and Reinstatements"}:
        return BILLING_CANCELLATION
    if category == "Claims Assistance":
        return CLAIMS
    if subtype in AUTO_DRIVER_SUBTYPES:
        return POLICY_CHANGE
    return None


def _blank(value: object) -> bool:
    return value is None or value == ""


def missing_transition_fields(required: tuple[str, ...], present: dict[str, object]) -> tuple[str, ...]:
    """Fields that must be answered. Boolean False counts as answered."""
    return tuple(name for name in required if _blank(present.get(name)))


def missing_required_true(required: tuple[str, ...], present: dict[str, object]) -> tuple[str, ...]:
    """Checkboxes / flags that must be True to proceed."""
    return tuple(name for name in required if not present.get(name))


def can_transition(blueprint: Blueprint, action: str, present: dict[str, object]) -> tuple[bool, tuple[str, ...]]:
    step = blueprint.transition(action)
    if step is None:
        return False, ("unknown_action",)
    missing = missing_transition_fields(step.required_fields, present) + missing_required_true(
        step.required_true, present
    )
    return (not missing, missing)
