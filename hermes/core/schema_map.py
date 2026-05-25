"""Optional `schema_map.json` from `hermes --audit-fields` for custom Espo field names."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

_loaded: bool = False
_cached: dict[str, Any] | None = None


def schema_map_path() -> Path:
    return Path(os.environ.get("HERMES_SCHEMA_MAP", "schema_map.json"))


def load_schema_map() -> dict[str, Any] | None:
    """Load cached schema map if the file exists; otherwise None."""
    global _loaded, _cached
    if _loaded:
        return _cached
    _loaded = True
    path = schema_map_path()
    if not path.is_file():
        _cached = None
        return None
    try:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
    except (OSError, json.JSONDecodeError):
        _cached = None
        return None
    _cached = data if isinstance(data, dict) else None
    return _cached


def reset_schema_map_cache() -> None:
    """Call after regenerating schema_map.json so the next lookup reloads."""
    global _loaded, _cached
    _loaded = False
    _cached = None
