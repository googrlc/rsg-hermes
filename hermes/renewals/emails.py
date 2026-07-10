"""Renewal cadence email rendering — Jinja2 drafts, human-approved copy.

The 8 templates in ``templates/`` are the draft copy Hermes generates; a producer
(Gretchen/Lamar) approves and sends. This module only turns a template name +
context into a ``{subject, body}`` draft. It does NOT send anything — delivery is
the Slack-card layer's job, gated behind ``RENEWAL_CADENCE_ENABLED``.

Template contract: each ``.j2`` file's first rendered line is ``Subject: ...``,
then a blank line, then the plain-text body. A leading ``{# … #}`` comment (used
for the per-template variable/notes header) renders to nothing and is stripped.

Variables (BRIEF §Templates): ``first_name``, ``policy_type``, ``carrier``,
``expiration_date``, ``premium`` (where appropriate), ``sender``. Premium is
optional — templates guard it with ``{% if premium %}`` so a blank renders clean.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, StrictUndefined, TemplateNotFound

from . import cadence_config as cc

TEMPLATE_DIR = Path(__file__).parent / "templates"

# StrictUndefined so a typo'd variable fails loudly at render (during the approval
# dry-run) instead of silently emailing a client a blank where their name goes.
# `premium` is always supplied by build_context (empty string when absent), so the
# optional-premium guard never hits an undefined.
_env = Environment(
    loader=FileSystemLoader(str(TEMPLATE_DIR)),
    undefined=StrictUndefined,
    trim_blocks=True,
    lstrip_blocks=True,
    keep_trailing_newline=False,
    autoescape=False,  # plain-text email, not HTML
)

# The six approved template variables. Every context is normalized to exactly
# these keys so a template can never reference an undefined var.
TEMPLATE_VARS = ("first_name", "policy_type", "carrier", "expiration_date", "premium", "sender")

DEFAULT_SENDER = "Gretchen"


def build_context(
    *,
    first_name: str | None = None,
    policy_type: str | None = None,
    carrier: str | None = None,
    expiration_date: str | None = None,
    premium: str | None = None,
    sender: str | None = None,
) -> dict[str, str]:
    """Normalize a render context to the six approved variables.

    Missing pieces get safe, human-readable fallbacks rather than blanks or a
    render error — a first-name we don't have becomes "there", not an empty greeting.
    ``premium`` defaults to "" so the ``{% if premium %}`` guards drop cleanly.
    """
    return {
        "first_name": (first_name or "there").strip(),
        "policy_type": (policy_type or "insurance").strip(),
        "carrier": (carrier or "your carrier").strip(),
        "expiration_date": (expiration_date or "your renewal date").strip(),
        "premium": (premium or "").strip(),
        "sender": (sender or DEFAULT_SENDER).strip(),
    }


def render_email(template_name: str, context: dict[str, Any]) -> dict[str, str]:
    """Render one template to a ``{subject, body, template}`` draft.

    ``context`` should come from ``build_context`` (or supply all six keys).
    Raises ``ValueError`` for an unknown template or a file missing its
    ``Subject:`` line — both are config bugs we want to surface immediately.
    """
    if template_name not in cc.TEMPLATES:
        raise ValueError(f"unknown renewal template {template_name!r}")
    try:
        template = _env.get_template(f"{template_name}.j2")
    except TemplateNotFound as exc:  # declared in config but no file on disk
        raise ValueError(f"renewal template file missing: {template_name}.j2") from exc

    rendered = template.render(**context).strip()
    if not rendered.startswith("Subject:"):
        raise ValueError(f"template {template_name!r} must start with a 'Subject:' line")

    subject_line, _, body = rendered.partition("\n")
    subject = subject_line[len("Subject:"):].strip()
    return {"template": template_name, "subject": subject, "body": body.strip()}
