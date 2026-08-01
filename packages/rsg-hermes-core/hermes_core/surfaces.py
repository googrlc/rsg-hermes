"""Where the humans actually look.

Hermes serves no screen. The RSG Agency Portal is the agency's one CRM, and it
runs as its own service on its own port — so anything Hermes sends a person
(a Talk notification, a root-URL response) has to name an address it does not
own. That address lives here, in one place, because it appeared in two and the
one that was wrong pointed at a page that no longer exists.

``HERMES_PORTAL_URL`` is deliberately distinct from ``HERMES_PUBLIC_BASE_URL``:
the latter is this API's own origin, used for intake status URLs that people
never see. They were the same host while the cockpit was served from this
process. They are not the same thing, and conflating them is what made every
task notification link into a dead page the day the cockpit came down.
"""

from __future__ import annotations

import os


def portal_url() -> str:
    """Base URL of the RSG Agency Portal, or "" if nobody configured one.

    Empty is a real answer: callers drop the link rather than guess a host.
    """
    return os.environ.get("HERMES_PORTAL_URL", "").strip().rstrip("/")
