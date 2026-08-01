"""Classify an inbound email: actionable insurance lead vs. noise.

Two-stage, cheap-first:

1. Heuristic gate — obvious newsletters / bulk / automated mail is flagged
   ``noise`` without spending a model call (List-Unsubscribe markers,
   no-reply senders, marketing subject patterns).
2. LLM decision (optional) — anything not obviously noise is sent to the
   configured OpenAI model, which returns actionable vs noise and, when
   actionable, a best-guess ``intake_kind`` for the downstream synthesizer.
   With no API key configured, the LLM stage is skipped and the message is
   treated as actionable/unknown (fail toward a human, never auto-trash).

``intake_kind`` values mirror the intake extractor skills:
commercial | personal | life | benefits | medicare | unknown.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass

log = logging.getLogger(__name__)

INTAKE_KINDS = ("commercial", "personal", "life", "benefits", "medicare", "unknown")

# Senders / subjects that are almost always bulk/automated and never a lead.
_NOISE_SENDER_RE = re.compile(
    r"(no[-_.]?reply|do[-_.]?not[-_.]?reply|newsletter|notifications?|"
    r"mailer|marketing|updates?|digest|noreply)@",
    re.IGNORECASE,
)
_NOISE_SUBJECT_RE = re.compile(
    r"\b(unsubscribe|newsletter|webinar|sale|% off|promo|coupon|"
    r"your weekly|your daily|new feature|product update)\b",
    re.IGNORECASE,
)


@dataclass
class Classification:
    """Outcome for a single message."""

    label: str  # "actionable" | "noise"
    reason: str
    intake_kind: str = "unknown"
    confidence: float = 0.0

    @property
    def is_actionable(self) -> bool:
        return self.label == "actionable"


def _openai_key() -> str:
    return os.environ.get("HERMES_OPENAI_API_KEY") or os.environ.get("OPENAI_API_KEY", "")


def _heuristic_noise(sender: str, subject: str, has_unsubscribe: bool) -> str | None:
    """Return a reason string if the message is obviously noise, else None."""
    if has_unsubscribe and _NOISE_SENDER_RE.search(sender):
        return "bulk sender + List-Unsubscribe header"
    if _NOISE_SENDER_RE.search(sender):
        return f"automated/no-reply sender ({sender})"
    if _NOISE_SUBJECT_RE.search(subject):
        return "marketing/newsletter subject pattern"
    return None


_SYSTEM_PROMPT = (
    "You triage a commercial/personal insurance agency's inbox. Decide whether an "
    "email is an actionable lead/client matter that should become a CRM record "
    "(new prospect, quote request, client question, submission, renewal, claim, "
    "carrier correspondence about a specific account) — or noise (newsletters, "
    "marketing, automated bulk mail, internal notifications). When actionable, pick "
    "the best intake_kind from: commercial, personal, life, benefits, medicare, "
    "unknown. Respond ONLY with compact JSON: "
    '{"label":"actionable|noise","intake_kind":"...","confidence":0.0,"reason":"..."}'
)


def _llm_classify(sender: str, subject: str, preview: str) -> Classification | None:
    """Ask the configured OpenAI model. Returns None if unavailable/errored."""
    from hermes_core.llm_client import get_client, resolve_model, LLMConfigError

    try:
        client = get_client()
    except LLMConfigError:
        return None
    except ImportError:
        log.warning("email_classifier: openai package not installed; skipping LLM stage")
        return None

    model = resolve_model(None)
    user = f"FROM: {sender}\nSUBJECT: {subject}\n\nPREVIEW:\n{preview[:1500]}"
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user},
            ],
            temperature=0,
            response_format={"type": "json_object"},
        )
        data = json.loads(resp.choices[0].message.content or "{}")
    except Exception as exc:  # noqa: BLE001 — fail toward human, never block the run
        log.warning("email_classifier: LLM stage failed (%s); treating as actionable", exc)
        return None

    label = "actionable" if data.get("label") == "actionable" else "noise"
    kind = data.get("intake_kind", "unknown")
    if kind not in INTAKE_KINDS:
        kind = "unknown"
    return Classification(
        label=label,
        reason=str(data.get("reason", ""))[:300] or "model decision",
        intake_kind=kind if label == "actionable" else "unknown",
        confidence=float(data.get("confidence", 0.0) or 0.0),
    )


def classify(
    *,
    sender: str,
    subject: str,
    preview: str,
    has_unsubscribe: bool = False,
) -> Classification:
    """Classify one message. Heuristic noise gate first, then LLM, then a
    safe default of ``actionable/unknown`` (so nothing is auto-quarantined on
    a model outage)."""
    sender = sender or ""
    subject = subject or ""

    noise_reason = _heuristic_noise(sender, subject, has_unsubscribe)
    if noise_reason:
        return Classification(label="noise", reason=noise_reason, confidence=0.9)

    llm = _llm_classify(sender, subject, preview)
    if llm is not None:
        return llm

    return Classification(
        label="actionable",
        reason="no classifier available — defaulting to human review",
        intake_kind="unknown",
        confidence=0.0,
    )
