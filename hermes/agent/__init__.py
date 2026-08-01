"""The agent layer: natural-language command routing.

Sits ABOVE the domains. `dispatcher` and `nl_agent` reach into commands,
operations, integrations, the AMS book and the command center to answer a
request, so they cannot live in `hermes/core` — that is the bottom layer every
domain depends on. They used to, and because `core/__init__.py` re-exported
`Dispatcher`, importing any core utility dragged the router in with it.

The contract these produce (`DispatchResult`) stays in `hermes_core.dispatch`
so command modules can depend on the type without depending on the engine.
"""
