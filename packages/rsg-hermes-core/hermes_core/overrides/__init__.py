"""Portal overrides — human corrections that outrank a synced source."""

from hermes_core.overrides.core import (  # noqa: F401
    ACTION_CONFLICT,
    ACTION_KEEP,
    ACTION_RETIRE,
    STATUS_ACTIVE,
    STATUS_CONFLICTED,
    STATUS_RETIRED,
    Override,
    apply_overrides,
    reconcile,
    resolve,
    same_value,
)
