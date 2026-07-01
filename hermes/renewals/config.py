"""Configuration for the renewals module."""
import os

# --- Entities ---
RENEWAL_ENTITY = "Renewal"
RENEWAL_WORKSHEET_ENTITY = "RenewalWorksheet"
TASK_ENTITY = "Task"

# --- Renewal.pipeline_stage enum ---
PIPELINE_STAGE_IDENTIFIED = "Identified"
PIPELINE_STAGE_OUTREACH_SENT = "Outreach Sent"
PIPELINE_STAGE_QUOTE_REQUESTED = "Quote Requested"
PIPELINE_STAGE_PROPOSAL_SENT = "Proposal Sent"
PIPELINE_STAGE_NEGOTIATING = "Negotiating"
PIPELINE_STAGE_WON = "Renewed - Won"
PIPELINE_STAGE_LOST = "Lost"

# Back-compat aliases while the reshape rolls out.
STAGE_IDENTIFIED = PIPELINE_STAGE_IDENTIFIED
STAGE_OUTREACH_SENT = PIPELINE_STAGE_OUTREACH_SENT
STAGE_QUOTE_REQUESTED = PIPELINE_STAGE_QUOTE_REQUESTED
STAGE_PROPOSAL_SENT = PIPELINE_STAGE_PROPOSAL_SENT
STAGE_NEGOTIATING = PIPELINE_STAGE_NEGOTIATING
STAGE_WON = PIPELINE_STAGE_WON
STAGE_LOST = PIPELINE_STAGE_LOST

IN_FLIGHT_STAGES = {
    PIPELINE_STAGE_QUOTE_REQUESTED,
    PIPELINE_STAGE_PROPOSAL_SENT,
    PIPELINE_STAGE_NEGOTIATING,
}
TERMINAL_STAGES = {PIPELINE_STAGE_WON, PIPELINE_STAGE_LOST}

# --- Renewal.disposition enum (v6 support; unknown future values still pass through) ---
DISPOSITION_WON = "won"
DISPOSITION_REWRITTEN = "rewritten"
DISPOSITION_LOST = "lost"
DISPOSITION_DO_NOT_RENEW = "do_not_renew"
WIN_DISPOSITIONS = {DISPOSITION_WON, DISPOSITION_REWRITTEN}
LOSS_DISPOSITIONS = {DISPOSITION_LOST, DISPOSITION_DO_NOT_RENEW}

# --- Task field values (confirmed Task.json) ---
TASK_STATUS_INBOX = "Inbox"
TASK_STATUS_COMPLETED = "Completed"
TASK_TYPE_RENEWAL = "Renewal"
TASK_SOURCE_ACCOUNT = "Account"
TASK_SYNC_SOURCE = "Hermes"  # NOTE: must be added to Task.syncSource enum first (see README)

# --- Document store (confirmed hermes/documents/store.py VALID_DOC_TYPES) ---
# save_document only accepts: proposal, note, renewal, comparison, appetite,
# reference, other. The won/lost outcome is carried in the title + source, NOT
# the doc_type.
DOC_TYPE_RENEWAL = "renewal"

# --- Slack channels (RSG) — override via env if they change ---
SLACK_GRETCHEN_TASKS = os.environ.get("SLACK_GRETCHEN_TASKS", "C0AUP125PRU")  # #gretchen-tasks
SLACK_THE_BOSS = os.environ.get("SLACK_THE_BOSS", "C0ANQUENX4P")              # #the-boss
SLACK_RSG_WINS = os.environ.get("SLACK_RSG_WINS", "C0ANFKMDRUH")              # #rsg-wins
SLACK_SYSTEMS_CHECK = os.environ.get("HERMES_SYSTEMS_CHECK_CHANNEL", "C0ANSEP6SSD")

# --- Task assignee (Gretchen) ---
# Resolved live by userName unless an explicit id is provided.
GRETCHEN_USERNAME = os.environ.get("HERMES_RENEWALS_GRETCHEN_USERNAME", "gretchcoates")
GRETCHEN_USER_ID = os.environ.get("HERMES_RENEWALS_GRETCHEN_USER_ID")  # optional override

# --- Premium-change decision bands (percent), used in the card's guide ---
# premiumChange on the Renewal is an auto % delta = ((renewal - current) / current) * 100
BAND_STANDARD_MAX = 10.0   # <10%  -> standard renewal email
BAND_REVIEW_MAX = 25.0     # 10-24% -> hold + flag Lamar; >=25% -> urgent remarket

# --- Webhook auth (matches EspoCRM config: serviceWebhookSecret) ---
SERVICE_WEBHOOK_SECRET = os.environ.get("SERVICE_WEBHOOK_SECRET", "")

# --- EspoCRM base URL (for Slack deep-links to the Task / Renewal worksheet) ---
ESPO_BASE_URL = os.environ.get("ESPO_URL", "").rstrip("/")

# --- Worksheet checkbox fields ---
CHECKBOX_FIELDS = [
    "renewal_reviewed",    # Renewal declaration pulled & reviewed
    "account_confirmed",   # Account details confirmed (units / drivers)
    "renewal_email_sent",  # Renewal email sent to client
    "ams_updated",         # AMS (NowCerts) updated
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

# Optional branded Google Docs template (worksheet.fill_template). Unset => the
# generated worksheet doc is filed (v1 path).
RENEWAL_TEMPLATE_DOC_ID = os.environ.get("RENEWAL_TEMPLATE_DOC_ID")

# --- Renewal Loop v6 writeback (Momentum MCP, notes-only in v1) ---
MOMENTUM_MCP_URL = os.environ.get("MOMENTUM_MCP_URL", "https://mcp.momentumamp.com/mcp").rstrip("/")
MOMENTUM_MCP_API_KEY = os.environ.get("MOMENTUM_MCP_API_KEY", "")
MOMENTUM_MCP_TOOL_NOTES = "manage_notes"
# v1.1 planned (config/docs only, not called in v1): manage_opportunities,
# create_tasks, update_drivers, manage_vehicles, manage_policy_lifecycle_data.
WRITEBACK_RETRY_DELAYS = (30, 120, 600, 3600, 21600)
