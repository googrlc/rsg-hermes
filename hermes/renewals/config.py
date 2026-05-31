"""Configuration for the renewals module.

All EspoCRM values below were confirmed against the live entityDefs
(Renewal.json / Task.json) and the n8n WF1 create node. Field names use the
EspoCRM API (camelCase) form, matching what WF1 POSTs to /api/v1/Renewal.
"""
import os

# --- Entities ---
RENEWAL_ENTITY = "Renewal"
TASK_ENTITY = "Task"

# --- Renewal.stage enum (confirmed Renewal.json) ---
STAGE_IDENTIFIED = "Identified"
STAGE_OUTREACH_SENT = "Outreach Sent"
STAGE_QUOTE_REQUESTED = "Quote Requested"
STAGE_PROPOSAL_SENT = "Proposal Sent"
STAGE_NEGOTIATING = "Negotiating"
STAGE_WON = "Renewed - Won"
STAGE_LOST = "Lost"

# Stages that mean "still being shopped / in flight" (task done, no outcome yet)
IN_FLIGHT_STAGES = {STAGE_QUOTE_REQUESTED, STAGE_PROPOSAL_SENT, STAGE_NEGOTIATING}
TERMINAL_STAGES = {STAGE_WON, STAGE_LOST}

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
