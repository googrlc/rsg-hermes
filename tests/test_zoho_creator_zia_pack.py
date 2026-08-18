"""Zoho Creator Zia pack stays internally consistent."""

from __future__ import annotations

import runpy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_zoho_creator_zia_pack_validates():
    ns = runpy.run_path(str(ROOT / "docs/zoho-creator/scripts/validate_pack.py"))
    assert ns["main"]() == 0
