"""Segmentation & cadence config for the renewal communication engine.

Versioned in git — **tune the numbers here, not in code**. This module is pure
data: the segment cutoffs, term thresholds, per-segment touch schedule, and the
LOB vocabulary the classifier keys off. `hermes/renewals/cadence.py` reads this
and stays free of magic numbers.

Design contract (BRIEF — Renewal Cadence Engine v2):
- Classification and scheduling are deterministic Python. No LLM here.
- A proactive touch on a stale renewal date is worse than silence — every
  threshold is paired with a grace window so a late touch is *skipped*, never
  fired blind.
- Medicare is excluded at the classifier level (rule #1), not at send time.
"""
import os

# --- Kill switch (BRIEF §Mechanics) — default OFF until copy is approved -------
# The scheduler must treat a false/unset flag as "plan only, post nothing".
RENEWAL_CADENCE_ENABLED = os.environ.get("RENEWAL_CADENCE_ENABLED", "").lower() in {
    "1", "true", "yes", "on",
}

# --- Tunables (BRIEF §Segmentation & Cadence Config) --------------------------
# DEFAULT — Lamar may override. Account-level total active premium, NOT per-policy.
SMALL_COMMERCIAL_MAX_ACCOUNT_PREMIUM = int(
    os.environ.get("RENEWAL_SMALL_COMMERCIAL_MAX_ACCOUNT_PREMIUM", "5000")
)
# expiration - effective < 270 days => 6-month term.
TERM_6MO_MAX_DAYS = 270
# Never fire a touch more than this many days past its threshold.
BACKFILL_GRACE_DAYS = 10
# One full market review per 6-mo auto policy per ~12 months; the off-cycle term
# gets a light confirmation instead. If no full-review touch is logged within this
# window, this cycle is the full review.
FULL_REVIEW_WINDOW_DAYS = 300

# --- Segments -----------------------------------------------------------------
SEGMENT_AUTO_6MO = "auto_6mo"
SEGMENT_PERSONAL_12MO = "personal_12mo"
SEGMENT_COMMERCIAL_SMALL = "commercial_small"
SEGMENT_COMMERCIAL_MID = "commercial_mid"
SEGMENT_BENEFITS = "benefits"
# A None segment means "excluded — never touch" (Medicare).

SEGMENTS = (
    SEGMENT_AUTO_6MO,
    SEGMENT_PERSONAL_12MO,
    SEGMENT_COMMERCIAL_SMALL,
    SEGMENT_COMMERCIAL_MID,
    SEGMENT_BENEFITS,
)

# --- Cadence (BRIEF verbatim) — the human-tunable day thresholds --------------
# Tune touch timing HERE. `TOUCH_SPEC` below enriches each day with its EspoCRM
# field slot + template; `cadence.py` asserts the two stay in sync at import.
CADENCE = {
    SEGMENT_AUTO_6MO:         {"touches": [30]},        # single touch; see full-review rule
    SEGMENT_PERSONAL_12MO:    {"touches": [45, 15]},
    SEGMENT_COMMERCIAL_SMALL: {"touches": [40, 15]},
    SEGMENT_COMMERCIAL_MID:   {"touches": [90, 60, 30]},
    SEGMENT_BENEFITS:         {"touches": [90, 60, 30]},
}

# --- EspoCRM Renewal touch-tracking date fields (BRIEF §EspoCRM Changes) -------
# Four date fields, one slot per touch phase. A touch fires only if its slot is
# empty; the slot is stamped `queued` on card post and `sent` on ✅ reaction.
FIELD_TOUCH_EARLY = "touch_early_sent"        # T-90 / T-45 / T-40
FIELD_TOUCH_MID = "touch_mid_sent"            # T-60
FIELD_TOUCH_DECISION = "touch_decision_sent"  # T-30 / T-15 / light confirm
FIELD_WELCOME = "welcome_sent"                # on bound

TOUCH_FIELDS = (FIELD_TOUCH_EARLY, FIELD_TOUCH_MID, FIELD_TOUCH_DECISION, FIELD_WELCOME)

# --- Templates (BRIEF §Templates) — 8 total, one per touch-map row ------------
# Jinja2 files live in hermes/renewals/templates/. Hermes drafts them; Lamar
# approves the copy before the kill switch flips on.
TPL_T90_KICKOFF = "t90_kickoff"       # T-90  mid, benefits
TPL_T60_CHANGES = "t60_changes"       # T-60  mid, benefits
TPL_T45_HEADSUP = "t45_headsup"       # T-45  personal
TPL_T40_HEADSUP = "t40_headsup"       # T-40  small commercial
TPL_T30_OPTIONS = "t30_options"       # T-30  mid, benefits, auto_6mo (review cycle)
TPL_T15_DECISION = "t15_decision"     # T-15  personal, small commercial
TPL_LIGHT_CONFIRM = "light_confirm"   # auto_6mo (off-cycle light confirmation)
TPL_WELCOME = "welcome"               # on bound, all segments

TEMPLATES = (
    TPL_T90_KICKOFF,
    TPL_T60_CHANGES,
    TPL_T45_HEADSUP,
    TPL_T40_HEADSUP,
    TPL_T30_OPTIONS,
    TPL_T15_DECISION,
    TPL_LIGHT_CONFIRM,
    TPL_WELCOME,
)

# Full-review vs. light-confirmation rotation for a 6-mo auto policy resolves to
# one of these two templates at scheduling time (see cadence.auto_6mo_cycle).
AUTO_6MO_TEMPLATE_BY_CYCLE = {
    "full_review": TPL_T30_OPTIONS,
    "light_confirm": TPL_LIGHT_CONFIRM,
}

# --- Touch schedule — enriched (day → field slot + template + label) ----------
# Source of truth for what actually fires. Kept in sync with CADENCE by a guard
# in cadence.py (drift between the tunable day list and this table is a bug).
# For auto_6mo the template is chosen per cycle, so `template` is a dict there.
TOUCH_SPEC = {
    SEGMENT_AUTO_6MO: [
        {"days": 30, "field": FIELD_TOUCH_DECISION,
         "label": "T-30 decision", "template": AUTO_6MO_TEMPLATE_BY_CYCLE},
    ],
    SEGMENT_PERSONAL_12MO: [
        {"days": 45, "field": FIELD_TOUCH_EARLY,
         "label": "T-45 heads-up + changes check", "template": TPL_T45_HEADSUP},
        {"days": 15, "field": FIELD_TOUCH_DECISION,
         "label": "T-15 decision / confirm", "template": TPL_T15_DECISION},
    ],
    SEGMENT_COMMERCIAL_SMALL: [
        {"days": 40, "field": FIELD_TOUCH_EARLY,
         "label": "T-40 heads-up + changes check", "template": TPL_T40_HEADSUP},
        {"days": 15, "field": FIELD_TOUCH_DECISION,
         "label": "T-15 decision / confirm", "template": TPL_T15_DECISION},
    ],
    SEGMENT_COMMERCIAL_MID: [
        {"days": 90, "field": FIELD_TOUCH_EARLY,
         "label": "T-90 market-review kickoff", "template": TPL_T90_KICKOFF},
        {"days": 60, "field": FIELD_TOUCH_MID,
         "label": "T-60 changes check", "template": TPL_T60_CHANGES},
        {"days": 30, "field": FIELD_TOUCH_DECISION,
         "label": "T-30 options / decision", "template": TPL_T30_OPTIONS},
    ],
    SEGMENT_BENEFITS: [
        {"days": 90, "field": FIELD_TOUCH_EARLY,
         "label": "T-90 market-review kickoff", "template": TPL_T90_KICKOFF},
        {"days": 60, "field": FIELD_TOUCH_MID,
         "label": "T-60 changes check", "template": TPL_T60_CHANGES},
        {"days": 30, "field": FIELD_TOUCH_DECISION,
         "label": "T-30 options / decision", "template": TPL_T30_OPTIONS},
    ],
}

# --- LOB vocabulary (evaluated by the classifier, compared lowercased) --------
# Rule #1: Medicare is excluded entirely — no touches, no cards, ever.
MEDICARE_LOB_KEYWORDS = (
    "medicare",
    "medigap",
    "med supp",
    "pdp",
    "prescription drug plan",
)

# Rule #2: group benefits win regardless of premium size. Individual life/DI is
# NOT benefits (no "group"/employee-benefits marker), so it won't match here.
BENEFITS_LOB_KEYWORDS = (
    "group health",
    "group dental",
    "group vision",
    "group life",
    "group disability",
    "group std",
    "group ltd",
    "employee benefit",
    "employee benefits",
    "aflac",
    "supplemental health",
    "voluntary benefit",
)

# Personal lines markers. A LOB containing "commercial" is never personal, so
# "Commercial Auto" / "Commercial Umbrella" fall through to the commercial rule.
PERSONAL_LOB_KEYWORDS = (
    "auto",
    "automobile",
    "home",
    "homeowner",
    "dwelling",
    "renter",
    "umbrella",
    "boat",
    "watercraft",
    "rv",
    "motorcycle",
    "personal",
    "ho3",
    "ho-3",
    "ho6",
    "ho-6",
)

# Auto markers for the 6-month-term rule (personal auto only — "commercial" in
# the LOB disqualifies).
AUTO_LOB_KEYWORDS = ("auto", "automobile")

COMMERCIAL_MARKER = "commercial"
