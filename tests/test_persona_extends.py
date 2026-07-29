"""Persona inheritance — `<!-- extends: <key> -->` composes a specialist desk
onto a shared one.

The Cases desk layers on the CRM desk rather than copying its client-lookup
rules, so there is one place to fix when those rules change. Order matters: the
parent is emitted first so the child's rules win where they disagree.
"""

import pytest

from hermes.core import identity


@pytest.fixture(autouse=True)
def _clear_cache():
    identity.load_named_persona.cache_clear()
    yield
    identity.load_named_persona.cache_clear()


def _write(tmp_path, monkeypatch, files: dict[str, str]):
    """Point the loader at a temp personas/ dir containing `files`."""
    personas = tmp_path / "personas"
    personas.mkdir()
    for key, text in files.items():
        (personas / f"{key}.md").write_text(text, encoding="utf-8")
    monkeypatch.setattr(
        identity, "_read_persona_file",
        lambda key: (personas / f"{key}.md").read_text(encoding="utf-8").strip()
        if (personas / f"{key}.md").exists() else "",
    )


def test_child_inherits_parent_parent_first(tmp_path, monkeypatch):
    _write(tmp_path, monkeypatch, {
        "base": "BASE RULES",
        "kid": "<!-- extends: base -->\nCHILD RULES",
    })
    out = identity.load_named_persona("kid")
    assert "BASE RULES" in out and "CHILD RULES" in out
    # Parent first, child last — the child gets the last word.
    assert out.index("BASE RULES") < out.index("CHILD RULES")
    # The directive itself never reaches the model.
    assert "extends:" not in out


def test_multi_level_chain(tmp_path, monkeypatch):
    _write(tmp_path, monkeypatch, {
        "a": "A",
        "b": "<!-- extends: a -->\nB",
        "c": "<!-- extends: b -->\nC",
    })
    assert identity.load_named_persona("c").split() == ["A", "B", "C"]


def test_missing_parent_degrades_to_child(tmp_path, monkeypatch):
    _write(tmp_path, monkeypatch, {"kid": "<!-- extends: ghost -->\nCHILD ONLY"})
    assert identity.load_named_persona("kid") == "CHILD ONLY"


def test_cycle_stops_at_the_repeat(tmp_path, monkeypatch):
    _write(tmp_path, monkeypatch, {
        "x": "<!-- extends: y -->\nX",
        "y": "<!-- extends: x -->\nY",
    })
    out = identity.load_named_persona("x")  # must terminate, not recurse forever
    assert "X" in out and "Y" in out


def test_plain_persona_unchanged(tmp_path, monkeypatch):
    _write(tmp_path, monkeypatch, {"solo": "JUST ME"})
    assert identity.load_named_persona("solo") == "JUST ME"


def test_unknown_key_is_empty(tmp_path, monkeypatch):
    _write(tmp_path, monkeypatch, {})
    assert identity.load_named_persona("nothing_here") == ""


def test_real_cases_persona_extends_crm():
    """The shipped Cases desk actually inherits the shipped CRM desk."""
    cases = identity.load_named_persona("cases")
    crm = identity.load_named_persona("crm")
    assert crm and cases
    assert crm in cases                       # whole parent is carried
    assert cases.index("CRM Desk") < cases.index("Cases Desk")
    # The guardrails the desk exists to enforce.
    assert "E&O claim" in cases               # coverage advice -> hard stop
    assert "Blue Ridge Dental Co-op" in cases  # seed-data hygiene
