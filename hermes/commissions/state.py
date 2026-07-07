"""File-based incremental watermark for the commission ingest.

Follows the repo's existing file-state convention (a small JSON marker under
``~/.hermes/``). Stores the ISO timestamp of the last successfully-processed
change so the next run only pulls policies modified since then.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from . import config

log = logging.getLogger(__name__)


def _path() -> Path:
    return Path(config.WATERMARK_FILE).expanduser()


def read_watermark() -> str | None:
    """Return the stored 'since' cursor (ISO string), or None if unset/unreadable."""
    path = _path()
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
        value = data.get("last_synced_at")
        return value or None
    except (json.JSONDecodeError, OSError) as e:
        log.warning("commissions: could not read watermark %s: %s", path, e)
        return None


def write_watermark(since: str) -> None:
    """Persist the 'since' cursor after a successful run (best-effort)."""
    path = _path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"last_synced_at": since}))
    except OSError as e:
        log.warning("commissions: could not write watermark %s: %s", path, e)
