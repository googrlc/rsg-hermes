"""Morning digest configuration (Slice B - read-only)."""
import os

# Slack
SLACK_THE_BOSS = os.environ.get("SLACK_THE_BOSS", "C0ANQUENX4P")  # #the-boss

# Renewal radar: policies expiring within this many days, bucketed
RENEWAL_HORIZON_DAYS = 90
RENEWAL_BUCKETS = ((0, 30), (31, 60), (61, 90))
ACTIVE_POLICY_STATUSES = ["Active", "Up for Renewal", "Renewing"]

# Quiet pipeline: open opportunities untouched for this many days
QUIET_DAYS = int(os.environ.get("DIGEST_QUIET_DAYS", "5"))

# Opportunity stages treated as closed (matched case-insensitively by keyword
# so we never depend on exact enum spelling)
TERMINAL_KEYWORDS = ("lost", "bound", "renewed", "won", "closed", "dead")

# Task statuses that mean "still open"
OPEN_TASK_EXCLUDE = ["Completed", "Cancelled"]

# Per-section display cap in the Slack post
SECTION_CAP = 10
