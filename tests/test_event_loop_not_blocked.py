"""A route handler that never awaits must not be declared `async def`.

FastAPI runs an `async def` handler ON the event loop and a plain `def` handler
in a threadpool. So an `async def` whose body is synchronous — every Supabase
read, every NowCerts call in this codebase is a blocking `requests` call —
stops the entire process for the duration. Not just that request: every other
request being served by that worker.

That is not hypothetical. 111 of the 118 handlers were declared this way, on a
single uvicorn worker, and a slow call was measured taking a 0.17s endpoint to
28.4s. It presented as "the CRM buttons don't work".

`def` is the correct declaration for a synchronous handler. This test exists so
that the next handler written does not quietly reintroduce the freeze — an
`async def` with no `await` in it is always a mistake here, and it is invisible
in review because it looks more modern, not less.

If a handler genuinely needs to await (streaming, `UploadFile.read()`), it will
contain an `await` and this test ignores it.
"""

from __future__ import annotations

import ast
import glob

import pytest

ROUTE_DECORATORS = {"get", "post", "patch", "put", "delete"}
ROUTED_OBJECTS = {"app", "router"}
API_FILES = ["hermes/api.py"] + sorted(glob.glob("hermes/routers/*.py"))


def _is_route(fn: ast.AsyncFunctionDef) -> bool:
    return any(
        isinstance(d, ast.Call)
        and isinstance(d.func, ast.Attribute)
        and d.func.attr in ROUTE_DECORATORS
        and isinstance(d.func.value, ast.Name)
        and d.func.value.id in ROUTED_OBJECTS
        for d in fn.decorator_list
    )


def _awaits_in_own_scope(fn: ast.AsyncFunctionDef) -> bool:
    """Does fn itself await, ignoring nested function scopes?

    A nested `async def` with an await inside belongs to that nested function,
    not to this handler, and must not count as evidence that the handler needs
    to be async.
    """
    found = False

    def visit(node: ast.AST) -> None:
        nonlocal found
        for child in ast.iter_child_nodes(node):
            if found:
                return
            if isinstance(child, (ast.AsyncFunctionDef, ast.FunctionDef, ast.Lambda)):
                continue
            if isinstance(child, (ast.Await, ast.AsyncWith, ast.AsyncFor)):
                found = True
                return
            visit(child)

    visit(fn)
    return found


@pytest.mark.parametrize("path", API_FILES)
def test_no_route_handler_is_async_without_awaiting(path: str) -> None:
    tree = ast.parse(open(path, encoding="utf8").read())
    offenders = [
        f"{path}:{n.lineno} {n.name}"
        for n in ast.walk(tree)
        if isinstance(n, ast.AsyncFunctionDef) and _is_route(n) and not _awaits_in_own_scope(n)
    ]
    assert not offenders, (
        "these handlers never await, so `async def` pins them to the event loop and "
        "blocks every concurrent request while they wait on network I/O — declare "
        "them `def` and FastAPI will run them in a threadpool:\n  "
        + "\n  ".join(offenders)
    )


def test_the_supabase_pool_is_at_least_as_wide_as_the_threadpool() -> None:
    """`def` handlers run in anyio's 40-slot threadpool, so up to 40 can hold a
    Supabase connection at once. A narrower pool silently churns connections."""
    import inspect

    from hermes_integrations.supabase_client import SupabaseClient

    default = inspect.signature(SupabaseClient.__init__).parameters["pool_maxsize"].default
    assert default >= 40, (
        f"pool_maxsize={default} is narrower than the 40-worker threadpool serving "
        "sync handlers; the excess opens and discards a connection per request"
    )
