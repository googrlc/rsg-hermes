"""Bottom layer: primitives and shared contracts.

Nothing here may import a domain, a command, or the agent layer. This package
is what every other package is allowed to depend on, which is what makes it
extractable as a standalone `rsg-hermes-core` (see docs/repo-split-plan.md).

This module used to re-export `Dispatcher`, so `from hermes_core import phi` —
importing any core utility at all — pulled the natural-language router and
`operations.write_gate` in behind it. The router now lives in `hermes.agent`;
import it from there.
"""
