"""Zoho Desk ticket field catalog.

Custom field types cannot be changed after create in Desk, so types here are
the rollout contract. API names use the ``cf_`` prefix Desk applies to custom
fields; native Desk fields (subject, status, priority, contact, account) are
not listed.

Shared fields belong on the primary layout and are reused on every other
layout. Layout-specific fields are additive.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from hermes.desk.spec import LAYOUT_LABELS, OPERATIONAL_VIEWS, PICKLISTS

CSV_COLUMNS = (
    "Layout",
    "Display_Label",
    "API_Name",
    "Data_Type",
    "Length",
    "Mandatory",
    "Picklist_Values",
    "Section",
    "Sensitive",
    "Notes",
)

# Desk custom-field types. Choose carefully — type cannot be changed later.
TEXT = "Text"
EMAIL = "Email"
PHONE = "Phone"
PICKLIST = "Picklist"
DATE = "Date"
DATETIME = "DateTime"
BOOLEAN = "Boolean"
NUMBER = "Number"
DECIMAL = "Decimal"
URL = "URL"
TEXTAREA = "Textarea"


@dataclass(frozen=True)
class Field:
    layout: str
    label: str
    api_name: str
    data_type: str
    section: str
    mandatory: bool = False
    length: int = 255
    picklist_key: str | None = None
    sensitive: bool = False
    notes: str = ""

    @property
    def picklist_values(self) -> tuple[str, ...]:
        if not self.picklist_key:
            return ()
        return PICKLISTS[self.picklist_key]


def _f(
    layout: str,
    label: str,
    api_name: str,
    data_type: str,
    section: str,
    *,
    mandatory: bool = False,
    length: int | None = None,
    picklist_key: str | None = None,
    sensitive: bool = False,
    notes: str = "",
) -> Field:
    if length is None:
        length = {
            TEXT: 255,
            EMAIL: 100,
            PHONE: 30,
            PICKLIST: 100,
            URL: 255,
            TEXTAREA: 4000,
            NUMBER: 9,
            DECIMAL: 16,
            DATE: 0,
            DATETIME: 0,
            BOOLEAN: 0,
        }.get(data_type, 255)
    return Field(
        layout=layout,
        label=label,
        api_name=api_name,
        data_type=data_type,
        section=section,
        mandatory=mandatory,
        length=length,
        picklist_key=picklist_key,
        sensitive=sensitive,
        notes=notes,
    )


SHARED_FIELDS: tuple[Field, ...] = (
    # Identification
    _f("shared", "AMS Client ID", "cf_ams_client_id", TEXT, "Identification", notes="NowCerts insured GUID"),
    _f("shared", "CRM Account ID", "cf_crm_account_id", TEXT, "Identification", notes="Zoho CRM Account id"),
    _f("shared", "Policy Number", "cf_policy_number", TEXT, "Identification", length=64),
    _f("shared", "Carrier", "cf_carrier", TEXT, "Identification"),
    _f("shared", "Line of Business", "cf_line_of_business", PICKLIST, "Identification", picklist_key="line_of_business"),
    _f("shared", "Producer", "cf_producer", TEXT, "Identification"),
    _f("shared", "Service Owner", "cf_service_owner", TEXT, "Identification"),
    _f("shared", "Agency Location", "cf_agency_location", TEXT, "Identification"),
    # Classification
    _f("shared", "Request Category", "cf_request_category", PICKLIST, "Classification", mandatory=True, picklist_key="request_category"),
    _f("shared", "Request Subtype", "cf_request_subtype", PICKLIST, "Classification", picklist_key="request_subtype"),
    _f("shared", "Service Tier", "cf_service_tier", PICKLIST, "Classification", picklist_key="service_tier"),
    _f("shared", "Source", "cf_source", PICKLIST, "Classification", picklist_key="source"),
    _f("shared", "Urgency Reason", "cf_urgency_reason", PICKLIST, "Classification", picklist_key="urgency_reason", notes="Required when Priority is Urgent. Keywords such as ASAP do not auto-escalate."),
    _f("shared", "Business Impact", "cf_business_impact", PICKLIST, "Classification", picklist_key="business_impact"),
    _f("shared", "Regulatory or Cancellation Deadline", "cf_regulatory_deadline", DATE, "Classification"),
    _f("shared", "Required-By Date", "cf_required_by_date", DATE, "Classification"),
    _f("shared", "External Party Involved", "cf_external_party_involved", BOOLEAN, "Classification"),
    _f("shared", "Carrier Action Required", "cf_carrier_action_required", BOOLEAN, "Classification"),
    _f("shared", "Client Action Required", "cf_client_action_required", BOOLEAN, "Classification"),
    # Workflow
    _f("shared", "Current Stage", "cf_current_stage", TEXT, "Workflow"),
    _f("shared", "Waiting On", "cf_waiting_on", PICKLIST, "Workflow", picklist_key="waiting_on"),
    _f("shared", "Missing Information", "cf_missing_information", TEXTAREA, "Workflow", notes="Required when status is Information Needed or Waiting on Client."),
    _f("shared", "Next Action", "cf_next_action", TEXT, "Workflow"),
    _f("shared", "Next Follow-Up Date", "cf_next_follow_up_date", DATE, "Workflow", notes="Required for Waiting on Client / Waiting on Carrier."),
    _f("shared", "Last Client Contact Date", "cf_last_client_contact_date", DATETIME, "Workflow"),
    _f("shared", "Last Carrier Contact Date", "cf_last_carrier_contact_date", DATETIME, "Workflow"),
    _f("shared", "Approval Required", "cf_approval_required", BOOLEAN, "Workflow"),
    _f("shared", "Approval Status", "cf_approval_status", PICKLIST, "Workflow", picklist_key="approval_status"),
    _f("shared", "Escalation Reason", "cf_escalation_reason", TEXT, "Workflow"),
    _f("shared", "Reopened Reason", "cf_reopened_reason", TEXT, "Workflow", notes="Required when a resolved ticket is reopened."),
    _f("shared", "Reopen Count", "cf_reopen_count", NUMBER, "Workflow"),
    _f("shared", "Resolution Type", "cf_resolution_type", PICKLIST, "Workflow", picklist_key="resolution_type"),
    _f("shared", "Root Cause", "cf_root_cause", TEXT, "Workflow"),
    _f("shared", "Cancellation Reason", "cf_cancellation_reason", TEXT, "Workflow", notes="Required when status is Cancelled."),
    _f("shared", "Related Ticket Number", "cf_related_ticket_number", TEXT, "Workflow", notes="Required when status is Duplicate."),
    _f("shared", "Sales Opportunity", "cf_sales_opportunity", BOOLEAN, "Workflow", notes="AUT-13 CRM handoff flag."),
    _f("shared", "AMS Activity Posted", "cf_ams_activity_posted", BOOLEAN, "Workflow"),
    _f("shared", "Final Documents Stored", "cf_final_documents_stored", BOOLEAN, "Workflow"),
    _f("shared", "Customer Confirmation Sent", "cf_customer_confirmation_sent", BOOLEAN, "Workflow"),
    # Integration
    _f("shared", "AMS Record ID", "cf_ams_record_id", TEXT, "Integration"),
    _f("shared", "CRM Record ID", "cf_crm_record_id", TEXT, "Integration"),
    _f("shared", "Supabase Record ID", "cf_supabase_record_id", TEXT, "Integration"),
    _f("shared", "Document Folder Link", "cf_document_folder_link", URL, "Integration"),
    _f("shared", "Integration Status", "cf_integration_status", PICKLIST, "Integration", picklist_key="integration_status"),
    _f("shared", "Last Sync Date", "cf_last_sync_date", DATETIME, "Integration"),
    _f("shared", "Integration Error", "cf_integration_error", TEXTAREA, "Integration"),
    _f("shared", "Original Source ID", "cf_original_source_id", TEXT, "Integration"),
    _f("shared", "Parent Ticket ID", "cf_parent_ticket_id", TEXT, "Integration"),
)

CERTIFICATE_FIELDS: tuple[Field, ...] = (
    _f("certificate", "Certificate Holder Name", "cf_holder_name", TEXT, "Certificate", mandatory=True),
    _f("certificate", "Certificate Holder Address", "cf_holder_address", TEXTAREA, "Certificate", mandatory=True),
    _f("certificate", "Holder Email", "cf_holder_email", EMAIL, "Certificate"),
    _f("certificate", "Description of Operations", "cf_description_of_operations", TEXTAREA, "Certificate"),
    _f("certificate", "Additional Insured Requested", "cf_additional_insured_requested", BOOLEAN, "Certificate"),
    _f("certificate", "Waiver of Subrogation Requested", "cf_waiver_of_subrogation_requested", BOOLEAN, "Certificate"),
    _f("certificate", "Primary and Noncontributory Requested", "cf_pnc_requested", BOOLEAN, "Certificate"),
    _f("certificate", "Special Wording Requested", "cf_special_wording_requested", BOOLEAN, "Certificate"),
    _f("certificate", "Special Wording Text", "cf_special_wording_text", TEXTAREA, "Certificate", notes="Quote requested wording exactly."),
    _f("certificate", "Contract Attached", "cf_contract_attached", BOOLEAN, "Certificate"),
    _f("certificate", "Delivery Method", "cf_delivery_method", PICKLIST, "Certificate", picklist_key="delivery_method"),
    _f("certificate", "Coverage Verified", "cf_coverage_verified", BOOLEAN, "Certificate"),
    _f("certificate", "Wording Reviewed", "cf_wording_reviewed", BOOLEAN, "Certificate"),
    _f("certificate", "Additional Interest Reviewed", "cf_additional_interest_reviewed", BOOLEAN, "Certificate"),
)

AUTO_DRIVER_FIELDS: tuple[Field, ...] = (
    _f("auto_driver", "Effective Date Requested", "cf_effective_date_requested", DATE, "Transaction", mandatory=True),
    _f("auto_driver", "Transaction Type", "cf_transaction_type", PICKLIST, "Transaction", mandatory=True, picklist_key="request_subtype"),
    _f("auto_driver", "VIN", "cf_vin", TEXT, "Vehicle", length=17, notes="Required for vehicle add/remove/replace."),
    _f("auto_driver", "Year", "cf_vehicle_year", NUMBER, "Vehicle"),
    _f("auto_driver", "Make", "cf_vehicle_make", TEXT, "Vehicle", length=64),
    _f("auto_driver", "Model", "cf_vehicle_model", TEXT, "Vehicle", length=64),
    _f("auto_driver", "Stated Value or Cost New", "cf_stated_value", DECIMAL, "Vehicle"),
    _f("auto_driver", "Garaging Address", "cf_garaging_address", TEXTAREA, "Vehicle"),
    _f("auto_driver", "Vehicle Use", "cf_vehicle_use", TEXT, "Vehicle"),
    _f("auto_driver", "Radius", "cf_radius", TEXT, "Vehicle", length=32),
    _f("auto_driver", "Driver Name", "cf_driver_name", TEXT, "Driver", notes="Required for driver add/remove."),
    _f("auto_driver", "Date of Birth", "cf_driver_dob", DATE, "Driver", sensitive=True, notes="Store only under approved access and retention controls."),
    _f("auto_driver", "License Number", "cf_license_number", TEXT, "Driver", sensitive=True, length=32, notes="Store only under approved access and retention controls."),
    _f("auto_driver", "License State", "cf_license_state", TEXT, "Driver", length=2),
    _f("auto_driver", "Supporting Documents Received", "cf_supporting_documents_received", BOOLEAN, "Transaction"),
)

POLICY_CHANGE_FIELDS: tuple[Field, ...] = (
    _f("policy_change", "Effective Date Requested", "cf_effective_date_requested", DATE, "Change", mandatory=True),
    _f("policy_change", "Change Type", "cf_change_type", PICKLIST, "Change", mandatory=True, picklist_key="request_subtype"),
    _f("policy_change", "Change Description", "cf_change_description", TEXTAREA, "Change", mandatory=True),
    _f("policy_change", "Location Involved", "cf_location_involved", TEXT, "Change"),
    _f("policy_change", "Exposure Change", "cf_exposure_change", TEXTAREA, "Change"),
    _f("policy_change", "Payroll or Sales Change", "cf_payroll_or_sales_change", TEXTAREA, "Change"),
    _f("policy_change", "Mortgagee or Loss Payee", "cf_mortgagee_or_loss_payee", TEXT, "Change"),
    _f("policy_change", "Supporting Documents", "cf_supporting_documents", BOOLEAN, "Change"),
    _f("policy_change", "Estimated Premium Impact", "cf_estimated_premium_impact", DECIMAL, "Change"),
    _f("policy_change", "Insured Approval Received", "cf_insured_approval_received", BOOLEAN, "Change"),
)

CLAIMS_FIELDS: tuple[Field, ...] = (
    _f("claims", "Claim Number", "cf_claim_number", TEXT, "Claim", length=64, notes="Carrier/AMS claim record remains authoritative."),
    _f("claims", "Date of Loss", "cf_date_of_loss", DATE, "Claim"),
    _f("claims", "Type of Loss", "cf_type_of_loss", TEXT, "Claim"),
    _f("claims", "Claim Status", "cf_claim_status", TEXT, "Claim"),
    _f("claims", "Adjuster Name", "cf_adjuster_name", TEXT, "Claim"),
    _f("claims", "Adjuster Email", "cf_adjuster_email", EMAIL, "Claim"),
    _f("claims", "Adjuster Phone", "cf_adjuster_phone", PHONE, "Claim"),
    _f("claims", "Urgent Safety Issue", "cf_urgent_safety_issue", BOOLEAN, "Claim"),
    _f("claims", "Litigation Involved", "cf_litigation_involved", BOOLEAN, "Claim"),
    _f("claims", "Client Requested Assistance", "cf_client_requested_assistance", TEXTAREA, "Claim"),
)

BILLING_FIELDS: tuple[Field, ...] = (
    _f("billing", "Billing Type", "cf_billing_type", PICKLIST, "Billing", picklist_key="billing_type"),
    _f("billing", "Due Date", "cf_due_date", DATE, "Billing"),
    _f("billing", "Amount Due", "cf_amount_due", DECIMAL, "Billing"),
    _f("billing", "Cancellation Warning Received", "cf_cancellation_warning_received", BOOLEAN, "Billing"),
    _f("billing", "Cancellation Date", "cf_cancellation_date", DATE, "Billing"),
    _f("billing", "Reinstatement Requested", "cf_reinstatement_requested", BOOLEAN, "Billing"),
    _f("billing", "Payment Submitted", "cf_payment_submitted", BOOLEAN, "Billing"),
    _f("billing", "Confirmation Number", "cf_confirmation_number", TEXT, "Billing", length=64),
    _f("billing", "Escalation Required", "cf_escalation_required", BOOLEAN, "Billing"),
    _f("billing", "Documented Disposition", "cf_documented_disposition", TEXTAREA, "Billing", notes="Required before resolving a cancellation-warning case."),
)

CANCELLATION_FIELDS: tuple[Field, ...] = (
    _f("cancellation", "Reason", "cf_cancel_reason", TEXTAREA, "Cancellation", mandatory=True),
    _f("cancellation", "Request Initiated By", "cf_request_initiated_by", PICKLIST, "Cancellation", picklist_key="request_initiated_by"),
    _f("cancellation", "Requested Effective Date", "cf_requested_effective_date", DATE, "Cancellation"),
    _f("cancellation", "Signed Request Received", "cf_signed_request_received", BOOLEAN, "Cancellation"),
    _f("cancellation", "Finance Company Involved", "cf_finance_company_involved", BOOLEAN, "Cancellation"),
    _f("cancellation", "Return Premium Expected", "cf_return_premium_expected", BOOLEAN, "Cancellation"),
    _f("cancellation", "Replacement Coverage Required", "cf_replacement_coverage_required", BOOLEAN, "Cancellation"),
    _f("cancellation", "Management Review Required", "cf_management_review_required", BOOLEAN, "Cancellation"),
    _f("cancellation", "Proof Sent to Client", "cf_proof_sent_to_client", BOOLEAN, "Cancellation"),
    _f("cancellation", "Cancellation Warning Received", "cf_cancellation_warning_received", BOOLEAN, "Cancellation"),
    _f("cancellation", "Cancellation Date", "cf_cancellation_date", DATE, "Cancellation"),
    _f("cancellation", "Documented Disposition", "cf_documented_disposition", TEXTAREA, "Cancellation"),
)

RENEWAL_FIELDS: tuple[Field, ...] = (
    _f("renewal", "Expiration Date", "cf_expiration_date", DATE, "Renewal", mandatory=True, notes="One renewal case per policy expiration — not three tickets for 90/60/30."),
    _f("renewal", "Assigned Producer", "cf_assigned_producer", TEXT, "Renewal"),
    _f("renewal", "Assigned Service Owner", "cf_assigned_service_owner", TEXT, "Renewal"),
    _f("renewal", "Renewal Stage", "cf_renewal_stage", TEXT, "Renewal", notes="90-day, 60-day, 30-day, or Completion."),
    _f("renewal", "Renewal Questionnaire Sent", "cf_renewal_questionnaire_sent", BOOLEAN, "Renewal"),
    _f("renewal", "Exposure Updates Received", "cf_exposure_updates_received", BOOLEAN, "Renewal"),
    _f("renewal", "Loss Runs Ordered", "cf_loss_runs_ordered", BOOLEAN, "Renewal"),
    _f("renewal", "Current Loss Runs Received", "cf_current_loss_runs_received", BOOLEAN, "Renewal"),
    _f("renewal", "Marketing Required", "cf_marketing_required", BOOLEAN, "Renewal"),
    _f("renewal", "Renewal Proposal Received", "cf_renewal_proposal_received", BOOLEAN, "Renewal"),
    _f("renewal", "Client Decision", "cf_client_decision", PICKLIST, "Renewal", picklist_key="client_decision"),
    _f("renewal", "Renewal Completed", "cf_renewal_completed", BOOLEAN, "Renewal"),
)

LAYOUT_FIELDS: dict[str, tuple[Field, ...]] = {
    "shared": SHARED_FIELDS,
    "certificate": CERTIFICATE_FIELDS,
    "auto_driver": AUTO_DRIVER_FIELDS,
    "policy_change": POLICY_CHANGE_FIELDS,
    "claims": CLAIMS_FIELDS,
    "billing": BILLING_FIELDS,
    "cancellation": CANCELLATION_FIELDS,
    "renewal": RENEWAL_FIELDS,
}

ALL_FIELDS: tuple[Field, ...] = tuple(
    field for layout in LAYOUT_FIELDS.values() for field in layout
)


def fields_for(layout: str) -> tuple[Field, ...]:
    if layout == "shared":
        return SHARED_FIELDS
    return SHARED_FIELDS + LAYOUT_FIELDS[layout]


def required_api_names(layout: str) -> tuple[str, ...]:
    return tuple(f.api_name for f in fields_for(layout) if f.mandatory)


def field_by_api_name(api_name: str, *, layout: str | None = None) -> Field | None:
    pool: Iterable[Field]
    if layout:
        pool = fields_for(layout)
    else:
        pool = ALL_FIELDS
    for field in pool:
        if field.api_name == api_name:
            return field
    return None


def _row(field: Field) -> dict[str, str]:
    return {
        "Layout": LAYOUT_LABELS[field.layout],
        "Display_Label": field.label,
        "API_Name": field.api_name,
        "Data_Type": field.data_type,
        "Length": "" if field.length == 0 else str(field.length),
        "Mandatory": "Y" if field.mandatory else "N",
        "Picklist_Values": " | ".join(field.picklist_values),
        "Section": field.section,
        "Sensitive": "Y" if field.sensitive else "N",
        "Notes": field.notes,
    }


def render_fields_csv(layout: str) -> str:
    """Render the operator field-create CSV for one layout (layout-owned rows only)."""
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=CSV_COLUMNS, lineterminator="\n")
    writer.writeheader()
    for field in LAYOUT_FIELDS[layout]:
        writer.writerow(_row(field))
    return buf.getvalue()


def render_picklists_csv() -> str:
    buf = io.StringIO()
    writer = csv.DictWriter(
        buf, fieldnames=("List_Key", "Order", "Value"), lineterminator="\n"
    )
    writer.writeheader()
    for key, values in PICKLISTS.items():
        for order, value in enumerate(values):
            writer.writerow({"List_Key": key, "Order": str(order), "Value": value})
    return buf.getvalue()


def render_views_csv() -> str:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=("View", "Purpose"), lineterminator="\n")
    writer.writeheader()
    purposes = {
        "Unassigned New Tickets": "AUT-01 — New must not sit in an unmonitored queue",
        "My Open Cases": "Owner worklist",
        "My Follow-Ups Due Today": "Waiting-state follow-up (AUT-05 / AUT-06)",
        "Urgent and High Priority": "Priority model — recorded urgency reason required",
        "Waiting on Client": "AUT-05",
        "Waiting on Carrier": "AUT-06",
        "Certificates Due": "Certificate required-by date",
        "Cancellation Risk": "AUT-04 cancellation warning",
        "Reinstatements Pending": "Billing / cancellation blueprint monitoring",
        "Changes Submitted, Not Issued": "Policy-change Monitoring stage",
        "Renewal Intake Incomplete": "Single renewal case, stage incomplete",
        "Claims Needing Follow-Up": "Claims blueprint follow-up",
        "Cases Missing Policy Number": "Identification quality",
        "Cases Missing AMS Client ID": "Identification quality",
        "Resolved, Not Posted to AMS": "AUT-14 / AUT-10",
        "Integration Failures": "CF-05",
        "Reopened Cases": "AUT-11",
        "Cases Over Internal Target": "Required-by / SLA aging",
        "Management Escalations": "Escalation team queue",
    }
    for name in OPERATIONAL_VIEWS:
        writer.writerow({"View": name, "Purpose": purposes.get(name, "")})
    return buf.getvalue()


def write_field_csvs(docs_dir: Path) -> list[Path]:
    docs_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for layout in LAYOUT_FIELDS:
        path = docs_dir / f"fields_{layout}.csv"
        path.write_text(render_fields_csv(layout), encoding="utf-8")
        written.append(path)
    picklists = docs_dir / "picklists.csv"
    picklists.write_text(render_picklists_csv(), encoding="utf-8")
    written.append(picklists)
    views = docs_dir / "views.csv"
    views.write_text(render_views_csv(), encoding="utf-8")
    written.append(views)
    return written
