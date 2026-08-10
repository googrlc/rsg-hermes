"""NowCerts picklist option_id resolution."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from hermes_core import picklists as pl
from hermes_core import opportunities as opp


SEED = [
    {
        "list_key": pl.LIST_PIPELINE_NB,
        "option_id": pl.stable_option_id(pl.LIST_PIPELINE_NB, "Bound / Won"),
        "label": "Bound / Won",
        "sort_order": 0,
        "active": True,
    },
    {
        "list_key": pl.LIST_PIPELINE_NB,
        "option_id": pl.stable_option_id(pl.LIST_PIPELINE_NB, "Preparing Application"),
        "label": "Preparing Application",
        "sort_order": 1,
        "active": True,
    },
]


def test_require_option_accepts_id_and_label():
    supa = MagicMock()
    supa.select.return_value = SEED
    by_label = pl.require_option(supa, pl.LIST_PIPELINE_NB, label="Bound / Won")
    assert by_label["label"] == "Bound / Won"
    by_id = pl.require_option(supa, pl.LIST_PIPELINE_NB, option_id=by_label["option_id"])
    assert by_id["label"] == "Bound / Won"


def test_require_option_rejects_freeform():
    supa = MagicMock()
    supa.select.return_value = SEED
    with pytest.raises(ValueError, match="Unknown"):
        pl.require_option(supa, pl.LIST_PIPELINE_NB, label="Totally Made Up Stage")


def test_move_stage_rejects_unknown_when_picklist_populated():
    supa = MagicMock()
    # prior stage read
    supa.select.side_effect = [
        SEED,  # list_options for NB during resolve label miss path — actually multiple calls
    ]
    # Simpler: patch picklists.list_options / resolve
    calls = {"n": 0}

    def fake_list(supa_arg, list_key, active_only=True):
        return SEED if list_key == pl.LIST_PIPELINE_NB else []

    def fake_resolve(supa_arg, list_key, option_id=None, label=None):
        if label == "Totally Made Up Stage":
            return None
        if label == "Bound / Won" or option_id == SEED[0]["option_id"]:
            return {"option_id": SEED[0]["option_id"], "label": "Bound / Won"}
        return None

    import hermes_core.picklists as picklists_mod

    # Use monkeypatch via assignment
    orig_list = picklists_mod.list_options
    orig_resolve = picklists_mod.resolve
    picklists_mod.list_options = fake_list
    picklists_mod.resolve = fake_resolve
    try:
        with pytest.raises(ValueError, match="Unknown pipeline stage"):
            opp.advance_stage(supa, "opp-1", stage="Totally Made Up Stage", moved_by="lamar@x")
        # Known label still works
        supa.select.side_effect = [[{"stage": "Preparing Application"}]]
        supa.update.return_value = {"id": "opp-1", "stage": "Bound / Won"}
        row = opp.advance_stage(supa, "opp-1", stage="Bound / Won", moved_by="lamar@x")
        assert row["stage"] == "Bound / Won"
        assert "stage_option_id" in (supa.update.call_args.args[2] if False else supa.update.call_args[0][2])
    finally:
        picklists_mod.list_options = orig_list
        picklists_mod.resolve = orig_resolve
