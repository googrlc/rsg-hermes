"""Tests for hermes.renewals.emails — the 8 cadence email drafts.

Proves every declared template has a file, renders with a full context to a
non-empty subject + body, leaves no unrendered Jinja, and that premium-optional
templates render clean whether or not a premium is supplied.
"""
import pytest

from hermes.renewals import cadence_config as cc
from hermes.renewals import emails


def _ctx(**over):
    base = dict(
        first_name="Marcus",
        policy_type="General Liability",
        carrier="Progressive Commercial",
        expiration_date="August 15, 2026",
        premium="$1,240",
        sender="Gretchen",
    )
    base.update(over)
    return emails.build_context(**base)


def test_every_declared_template_renders():
    for name in cc.TEMPLATES:
        out = emails.render_email(name, _ctx())
        assert out["subject"], f"{name} produced an empty subject"
        assert out["body"], f"{name} produced an empty body"
        # nothing left unrendered
        assert "{{" not in out["subject"] and "{{" not in out["body"], name
        assert "{%" not in out["body"], name


def test_context_substitution_lands_in_output():
    out = emails.render_email(cc.TPL_T60_CHANGES, _ctx())
    assert "Marcus" in out["body"]
    assert "General Liability" in out["body"]
    assert "August 15, 2026" in out["body"]
    assert out["body"].rstrip().endswith("Gretchen")


def test_premium_optional_templates_render_without_premium():
    # templates that reference premium must render clean when it's absent
    for name in (cc.TPL_T30_OPTIONS, cc.TPL_T15_DECISION, cc.TPL_LIGHT_CONFIRM, cc.TPL_WELCOME):
        out = emails.render_email(name, _ctx(premium=""))
        body = out["body"]
        assert body
        assert "{%" not in body and "{{" not in body
        # no dangling fragment left where the premium clause was dropped
        assert "premium is" not in body, name          # the clause is gone, not half-rendered
        assert " at ." not in body and " at\n" not in body, name
        for artifact in (" .", " ,", "  "):             # no space-before-punct / double space
            # (paragraph breaks are "\n\n", not "  ", so this is line-local)
            for line in body.splitlines():
                assert artifact not in line, f"{name}: {artifact!r} in {line!r}"


def test_premium_included_when_supplied():
    out = emails.render_email(cc.TPL_T15_DECISION, _ctx(premium="$980"))
    assert "$980" in out["body"]


def test_build_context_fills_missing_with_fallbacks():
    ctx = emails.build_context()
    assert ctx["first_name"] == "there"
    assert ctx["premium"] == ""
    assert ctx["sender"] == emails.DEFAULT_SENDER
    # renders without error using only fallbacks
    out = emails.render_email(cc.TPL_T90_KICKOFF, ctx)
    assert "there" in out["body"]


def test_unknown_template_raises():
    with pytest.raises(ValueError):
        emails.render_email("not_a_real_template", _ctx())


def test_welcome_does_not_reference_expiration_date():
    # post-bind, the old x-date is misleading — welcome intentionally omits it
    out = emails.render_email(cc.TPL_WELCOME, _ctx(expiration_date="August 15, 2026"))
    assert "August 15, 2026" not in out["body"]


def test_every_template_name_has_a_file():
    for name in cc.TEMPLATES:
        assert (emails.TEMPLATE_DIR / f"{name}.j2").is_file(), f"missing template file for {name}"
