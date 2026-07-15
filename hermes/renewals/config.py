"""Configuration for the renewals module.

Renewal Loop v6 canonical spec: pipeline_stage separates *where in the pipeline*
from *how it ended* (disposition). Terminal outcomes collapse to
PIPELINE_STAGE_CLOSED; the six disposition values carry the outcome detail.
"""
import os

# --- Entities ---
RENEWAL_ENTITY = "Renewal"
RENEWAL_WORKSHEET_ENTITY = "RenewalWorksheet"
TASK_ENTITY = "Task"

# --- Renewal.pipeline_stage enum (v6 spec §1.2) ---
PIPELINE_STAGE_IDENTIFIED = "Identified"
PIPELINE_STAGE_OUTREACH_SENT = "Outreach Sent"
PIPELINE_STAGE_QUOTE_REQUESTED = "Quote Requested"
PIPELINE_STAGE_PROPOSAL_SENT = "Proposal Sent"
PIPELINE_STAGE_NEGOTIATING = "Negotiating"
PIPELINE_STAGE_CLOSED = "Closed"

# Legacy stage values seen on records back-filled from the pre-v6 schema.
# Readers should treat these as equivalent to PIPELINE_STAGE_CLOSED and rely
# on the `disposition` field for the actual outcome.
LEGACY_PIPELINE_STAGE_WON = "Renewed - Won"
LEGACY_PIPELINE_STAGE_LOST = "Lost"

IN_FLIGHT_STAGES = {
    PIPELINE_STAGE_OUTREACH_SENT,
    PIPELINE_STAGE_QUOTE_REQUESTED,
    PIPELINE_STAGE_PROPOSAL_SENT,
    PIPELINE_STAGE_NEGOTIATING,
}
TERMINAL_STAGES = {
    PIPELINE_STAGE_CLOSED,
    LEGACY_PIPELINE_STAGE_WON,
    LEGACY_PIPELINE_STAGE_LOST,
}

# --- Back-compat aliases (deprecated; readers should migrate to *_CLOSED + disposition) ---
STAGE_IDENTIFIED = PIPELINE_STAGE_IDENTIFIED
STAGE_OUTREACH_SENT = PIPELINE_STAGE_OUTREACH_SENT
STAGE_QUOTE_REQUESTED = PIPELINE_STAGE_QUOTE_REQUESTED
STAGE_PROPOSAL_SENT = PIPELINE_STAGE_PROPOSAL_SENT
STAGE_NEGOTIATING = PIPELINE_STAGE_NEGOTIATING
# STAGE_WON / STAGE_LOST point at the legacy string values so any lingering
# caller reading them still sees the same on-disk representation.
STAGE_WON = LEGACY_PIPELINE_STAGE_WON
STAGE_LOST = LEGACY_PIPELINE_STAGE_LOST

# --- Renewal.disposition enum (v6 spec §1.2, six values) ---
DISPOSITION_RENEWED = "renewed"
DISPOSITION_REWRITTEN = "rewritten"
DISPOSITION_LOST_PRICE = "lost_price"
DISPOSITION_LOST_COVERAGE = "lost_coverage"
DISPOSITION_LOST_NO_RESPONSE = "lost_no_response"
DISPOSITION_DO_NOT_RENEW = "do_not_renew"

WIN_DISPOSITIONS = {DISPOSITION_RENEWED, DISPOSITION_REWRITTEN}
LOSS_DISPOSITIONS = {
    DISPOSITION_LOST_PRICE,
    DISPOSITION_LOST_COVERAGE,
    DISPOSITION_LOST_NO_RESPONSE,
    DISPOSITION_DO_NOT_RENEW,
}
TERMINAL_DISPOSITIONS = WIN_DISPOSITIONS | LOSS_DISPOSITIONS

# Human-readable labels used in Notes, cards, and Slack posts.
DISPOSITION_LABELS = {
    DISPOSITION_RENEWED: "Renewed",
    DISPOSITION_REWRITTEN: "Rewritten",
    DISPOSITION_LOST_PRICE: "Lost — Price",
    DISPOSITION_LOST_COVERAGE: "Lost — Coverage",
    DISPOSITION_LOST_NO_RESPONSE: "Lost — No response",
    DISPOSITION_DO_NOT_RENEW: "Do not renew",
}

# Deprecated alias: DISPOSITION_WON is retained as an alias for RENEWED because
# some existing callers used "won" as a generic terminal-win sentinel. New code
# should use DISPOSITION_RENEWED or WIN_DISPOSITIONS.
DISPOSITION_WON = DISPOSITION_RENEWED

# Legacy Renewal.lost_reason values → v6 disposition mapping. Used during
# back-fill and by _disposition() to synthesize a v6 value from a legacy record.
LEGACY_LOST_REASON_TO_DISPOSITION = {
    "Price": DISPOSITION_LOST_PRICE,
    "Coverage": DISPOSITION_LOST_COVERAGE,
    "Unresponsive": DISPOSITION_LOST_NO_RESPONSE,
    "Moved carrier": DISPOSITION_REWRITTEN,
    "Other": DISPOSITION_DO_NOT_RENEW,
}

# --- Task field values (confirmed Task.json) ---
TASK_STATUS_INBOX = "Inbox"
TASK_STATUS_COMPLETED = "Completed"
TASK_TYPE_RENEWAL = "Renewal"
TASK_SOURCE_ACCOUNT = "Account"
TASK_SYNC_SOURCE = "Hermes"  # NOTE: must be added to Task.syncSource enum first (see README)

# --- Document store (confirmed hermes/documents/store.py VALID_DOC_TYPES) ---
DOC_TYPE_RENEWAL = "renewal"

# --- Slack channels (RSG) — override via env if they change ---
SLACK_GRETCHEN_TASKS = os.environ.get("SLACK_GRETCHEN_TASKS", "C0AUP125PRU")  # #gretchen-tasks
SLACK_THE_BOSS = os.environ.get("SLACK_THE_BOSS", "C0ANQUENX4P")              # #the-boss
SLACK_RSG_WINS = os.environ.get("SLACK_RSG_WINS", "C0ANFKMDRUH")              # #rsg-wins
SLACK_SYSTEMS_CHECK = os.environ.get("HERMES_SYSTEMS_CHECK_CHANNEL", "C0ANSEP6SSD")

# --- Task assignee (Gretchen) ---
GRETCHEN_USERNAME = os.environ.get("HERMES_RENEWALS_GRETCHEN_USERNAME", "gretchcoates")
GRETCHEN_USER_ID = os.environ.get("HERMES_RENEWALS_GRETCHEN_USER_ID")

# --- Premium-change decision bands (percent) — v6 §1.3, sacred retention logic ---
# premium_change on Renewal is the AGENT delta:
#   ((renewal_premium - current_premium) / current_premium) * 100
# carrier_premium_change is info-only and does not drive banding.
BAND_STANDARD_MAX = 10.0
BAND_REVIEW_MAX = 25.0

# --- Webhook auth ---
SERVICE_WEBHOOK_SECRET = os.environ.get("SERVICE_WEBHOOK_SECRET", "")

# --- EspoCRM base URL ---
ESPO_BASE_URL = os.environ.get("ESPO_URL", "").rstrip("/")

# --- Worksheet checkbox fields (legacy Renewal-side booleans; deprecated in v6) ---
CHECKBOX_FIELDS = [
    "renewal_reviewed",
    "account_confirmed",
    "renewal_email_sent",
    "ams_updated",
]

WORKSHEET_LOOKUP_KEYS = (
    "renewalWorksheet",
    "renewal_worksheet",
    "worksheet",
    "renewalWorksheetData",
)
WORKSHEET_ID_KEYS = ("renewalWorksheetId", "renewal_worksheet_id", "worksheetId")
WORKSHEET_HIDDEN_FIELDS = {
    "id",
    "name",
    "createdAt",
    "modifiedAt",
    "assignedUserId",
    "assignedUserName",
    "createdById",
    "createdByName",
    "modifiedById",
    "modifiedByName",
    "renewalId",
    "renewalName",
    "accountId",
    "accountName",
    "completion_type",
}

RENEWAL_TEMPLATE_DOC_ID = os.environ.get("RENEWAL_TEMPLATE_DOC_ID")

# --- Momentum (NowCerts) MCP notes writeback ---
# Consumed by the renewal executor's `note` channel (request_terms /
# client_follow_up). Loop v6's disposition writeback that once used this is retired.
MOMENTUM_MCP_URL = os.environ.get("MOMENTUM_MCP_URL", "https://mcp.momentumamp.com/mcp").rstrip("/")
MOMENTUM_MCP_API_KEY = os.environ.get("MOMENTUM_MCP_API_KEY", "")
MOMENTUM_MCP_TOOL_NOTES = "manage_notes"
