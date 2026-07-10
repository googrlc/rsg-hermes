"""Configuration constants for the commission ingest job.

Mirrors the ``hermes/renewals/config.py`` pattern: all tunables live here and are
overridable via environment variables.
"""

from __future__ import annotations

import os

# --- Supabase tables (rsg-infrastructure project) --------------------------
LEDGER_TABLE = "commission_ledger"
RULES_TABLE = "commission_rules"

# Upsert conflict key — a unique index exists on this column. Manual (non-AMS)
# rows keep it NULL and are never touched by the ingest.
LEDGER_CONFLICT_KEY = "nowcerts_policy_id"

# --- Reconciliation statuses ----------------------------------------------
STATUS_PENDING = "pending"       # matched a rule, expected computed
STATUS_NEEDS_RULE = "needs_rule"  # no rule matched — human review queue

# Marks rows written by this job (vs. manual tracker entries or carrier statements).
STATEMENT_SOURCE = "nowcerts_ingest"

# --- Purge safety filter (HARD GATE belt-and-suspenders) -------------------
PURGE_TAG = os.environ.get("HERMES_COMMISSIONS_PURGE_TAG", "PURGE-POLICY-2026-07")

# --- Slack ----------------------------------------------------------------
# #systems-check
SLACK_SYSTEMS_CHECK = os.environ.get("HERMES_COMMISSIONS_SLACK_CHANNEL", "C0ANSEP6SSD")

# --- Watermark / incremental cursor ---------------------------------------
WATERMARK_FILE = os.environ.get(
    "HERMES_COMMISSIONS_WATERMARK_FILE", "~/.hermes/commissions_watermark.json"
)
# First-ever incremental run floor when no watermark exists.
DEFAULT_SINCE = os.environ.get(
    "HERMES_COMMISSIONS_DEFAULT_SINCE", "2026-01-01T00:00:00Z"
)

# --- Fetch tuning ---------------------------------------------------------
PAGE_SIZE = int(os.environ.get("HERMES_COMMISSIONS_PAGE_SIZE", "100"))

# --- Statement reconciliation (Phase 3) -----------------------------------
RECON_TABLE = "commission_reconciliation"
# |delta| must exceed this ($) to open a reconciliation row.
DELTA_TOLERANCE = float(os.environ.get("HERMES_COMMISSIONS_DELTA_TOLERANCE", "1"))
# Default owner for the discrepancy queue.
RECON_ASSIGNEE = os.environ.get("HERMES_COMMISSIONS_RECON_ASSIGNEE", "Gretchen")
# Priority buckets by absolute dollar delta.
PRIORITY_HIGH_ABS = float(os.environ.get("HERMES_COMMISSIONS_PRIORITY_HIGH", "500"))
PRIORITY_MED_ABS = float(os.environ.get("HERMES_COMMISSIONS_PRIORITY_MED", "100"))
