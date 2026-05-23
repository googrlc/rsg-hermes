"""intake_submissions worker — drives the rsg-intake state machine.

Pipeline per row (PROJECT-CONTEXT.md):

  received → synthesizing → synthesized → drafting → awaiting_approval
                                                              ↓ (Slack APPROVE)
                                                            approved
                                                              ↓
                                                            writing → written → complete

  Any state → failed (with error_log entry + Slack alert)

This file lands incrementally across Phase 3:

  Step 2 (here): worker scaffold + ``received -> awaiting_approval`` arc.
                 Synthesizer + Slack draft post are stubbed; the arc
                 transitions states so the claim pattern is exercised
                 end-to-end. Stubs raise NotImplementedError which the
                 worker catches and routes to ``failed`` + Slack alert.
  Step 3:        actual synthesizer fill-in (commands/agency_intake.py).
  Step 4:        Slack draft post replaces the stubbed _post_draft.
  Step 5:        ``approved -> writing -> written -> complete`` arc +
                 retrieval inserts + completion post.

Single-worker assumption is fine today; the claim pattern in
``claim_next_received`` makes scale-out safe later.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any

from hermes.integrations.intake_submissions import (
    IntakeError,
    claim_next_received,
    transition,
)
from hermes.integrations.slack_notifier import SlackNotifier, SlackNotifierError
from hermes.integrations.supabase_client import SupabaseClient, SupabaseClientError

log = logging.getLogger(__name__)


DEFAULT_POLL_SECONDS = 5.0


def _intake_alert_channel() -> str:
    """Slack channel for failure alerts (#systems-check by default)."""
    return os.environ.get(
        "HERMES_INTAKE_ALERT_CHANNEL", os.environ.get("HERMES_SYSTEMS_CHECK_CHANNEL", "C0ANSEP6SSD")
    ).strip()


def _intake_draft_channel() -> str:
    """Slack channel for draft + completion posts."""
    return os.environ.get(
        "HERMES_INTAKE_DRAFT_CHANNEL",
        os.environ.get("HERMES_SENTINEL_SLACK_CHANNEL", "D0B2PJYLGQG"),
    ).strip()


def _post_alert(text: str, *, channel: str | None = None) -> None:
    """Post to Slack, swallowing errors so the worker doesn't crash on Slack failures."""
    try:
        notifier = SlackNotifier(channel=channel or _intake_alert_channel())
        notifier.post_message(text=text)
    except SlackNotifierError:
        log.exception("Slack post failed (channel=%s)", channel)
    except Exception:
        log.exception("Unexpected Slack post failure (channel=%s)", channel)


def _safe_transition_to_failed(
    supa: SupabaseClient, submission_id: str, *, exc: Exception, stage: str
) -> None:
    """Move the submission to ``failed`` and alert Slack. Swallows transition errors."""
    err = {"stage": stage, "message": str(exc), "exception_type": type(exc).__name__}
    try:
        transition(supa, submission_id, "failed", note=f"{stage} failed", error=err)
    except Exception:
        log.exception("Failed to mark submission %s as failed", submission_id)
    _post_alert(
        f":rotating_light: Hermes intake submission failed\n"
        f"- submission_id: {submission_id}\n"
        f"- stage: {stage}\n"
        f"- error: {exc}"
    )


# ---------------------------------------------------------------------------
# Pluggable hooks (filled across Steps 3-4).
# Steps 3+4 swap real implementations in via module-level assignment so the
# worker's claim/transition flow stays stable.
# ---------------------------------------------------------------------------


def _post_draft_stub(submission_id: str, payload: dict[str, Any]) -> None:
    """Step-4 hook: replaced by a real Slack post with interactive buttons."""
    log.info("[stub] would post draft for submission %s to Slack", submission_id)


# Step 3: real synthesizer + renderer from commands/agency_intake.py.
from hermes.commands.agency_intake import (  # noqa: E402  (import-after-use to keep the hook section contiguous)
    render_hermes_blocks,
    synthesize_from_payload,
)

synthesize_payload = synthesize_from_payload
render_blocks = render_hermes_blocks
post_draft = _post_draft_stub  # Step 4 fills this


# ---------------------------------------------------------------------------
# Arc 1: received -> synthesizing -> synthesized -> drafting -> awaiting_approval
# ---------------------------------------------------------------------------


def process_one_received(supa: SupabaseClient) -> bool:
    """Pull one received row through to awaiting_approval.

    Returns True if a row was processed (success OR failed), False if the
    queue was empty / lost the claim race.
    """
    claimed = claim_next_received(supa)
    if claimed is None:
        return False

    submission_id = str(claimed["id"])
    log.info("Claimed intake submission %s (synthesizing)", submission_id)

    payload = claimed.get("payload") or {}

    # Stage A: synthesize -> 'synthesized' (writes hermes_blocks + draft_summary)
    try:
        draft_summary, warnings = synthesize_payload(payload)
        hermes_blocks = render_blocks(draft_summary)
    except Exception as exc:
        _safe_transition_to_failed(supa, submission_id, exc=exc, stage="synthesize")
        return True

    if warnings:
        log.warning("Submission %s synthesis warnings: %s", submission_id, warnings)

    try:
        transition(
            supa, submission_id, "synthesized",
            note="synthesis complete",
            extra_fields={"hermes_blocks": hermes_blocks, "draft_summary": draft_summary},
        )
    except (IntakeError, SupabaseClientError) as exc:
        _safe_transition_to_failed(supa, submission_id, exc=exc, stage="synthesized-transition")
        return True

    # Stage B: drafting — pass-through. Dedup probes deferred per Phase 3
    # decision (post-Phase-5 work).
    try:
        transition(
            supa, submission_id, "drafting",
            note="dedup probes deferred — pass-through",
        )
    except (IntakeError, SupabaseClientError) as exc:
        _safe_transition_to_failed(supa, submission_id, exc=exc, stage="drafting-transition")
        return True

    # Stage C: post Slack draft and settle at awaiting_approval.
    try:
        post_draft(submission_id, draft_summary)
        transition(
            supa, submission_id, "awaiting_approval",
            note="draft posted to Slack",
        )
    except (IntakeError, SupabaseClientError) as exc:
        _safe_transition_to_failed(
            supa, submission_id, exc=exc, stage="awaiting_approval-transition"
        )
        return True
    except Exception as exc:
        # Slack post failures are non-fatal; record and continue to
        # awaiting_approval. But if the post + transition both crash here,
        # surface as failed.
        _safe_transition_to_failed(supa, submission_id, exc=exc, stage="slack-draft-post")
        return True

    log.info("Submission %s now awaiting_approval", submission_id)
    return True


# ---------------------------------------------------------------------------
# Worker loop
# ---------------------------------------------------------------------------


def tick(supa: SupabaseClient) -> dict[str, int]:
    """One worker iteration."""
    received = 1 if process_one_received(supa) else 0
    return {"received_processed": received}


def run_intake_worker_loop(
    supa: SupabaseClient,
    *,
    poll_seconds: float = DEFAULT_POLL_SECONDS,
) -> None:
    """Continuously drive the intake state machine. Designed for
    ``hermes --run-intake-worker`` and a docker-compose service."""
    interval = poll_seconds if poll_seconds > 0 else DEFAULT_POLL_SECONDS
    log.info("Starting intake worker loop: interval=%ss", interval)
    while True:
        try:
            result = tick(supa)
            if any(result.values()):
                log.info("intake tick: %s", result)
        except Exception:
            log.exception("intake worker tick crashed; continuing")
        time.sleep(interval)
