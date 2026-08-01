"""The shared bottom layer must not depend on anything above it.

`packages/rsg-hermes-core` is installed by every RSG app repo — finance, cases,
intake, renewals, carriers, the hub. That only works while it depends on no app:
the moment `hermes_core` or `hermes_integrations` imports `hermes.renewals`,
installing the core into the finance repo drags renewals in with it, and the
split stops being a split.

This is not hypothetical drift. It is exactly how the layering got tangled the
first time: `nowcerts_client` sat in `sync/`, the queue contract sat in
`renewals/executor.py`, and `dispatcher`/`nl_agent` sat in `core/` while
importing half the codebase. Thirteen bidirectional package cycles grew out of
three misfilings, and none of them looked wrong in the diff that introduced them.

The second test is the other half of the same rule: domain *logic* must not
drift in either, even without an import. A module that reads and writes one
app's table belongs to that app. `intake_submissions.py` lived in
`integrations/` for months on the strength of the word "integration" while
running the intake pipeline's state machine.
"""

from __future__ import annotations

import ast
import pathlib
import re

import pytest

CORE_ROOT = pathlib.Path("packages/rsg-hermes-core")
CORE_PACKAGES = ("hermes_core", "hermes_app", "hermes_integrations")


def _core_files() -> list[pathlib.Path]:
    files: list[pathlib.Path] = []
    for pkg in CORE_PACKAGES:
        files += [
            p for p in (CORE_ROOT / pkg).rglob("*.py") if "__pycache__" not in p.parts
        ]
    return files


def test_the_core_package_exists_where_the_build_expects_it() -> None:
    assert (CORE_ROOT / "pyproject.toml").is_file(), (
        "the shared bottom layer is not at packages/rsg-hermes-core — the "
        "Dockerfile installs it from that path before installing the app"
    )


@pytest.mark.parametrize("path", _core_files(), ids=lambda p: str(p))
def test_no_core_module_imports_an_app(path: pathlib.Path) -> None:
    """`hermes.<anything>` in here is an app import. The core may only import
    the standard library, third-party packages, and its own two packages."""
    tree = ast.parse(path.read_text(encoding="utf8"))
    offenders: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            target = node.module
        elif isinstance(node, ast.Import):
            target = node.names[0].name
        else:
            continue
        root = target.split(".")[0]
        if root == "hermes":
            offenders.append(f"line {node.lineno}: {target}")
    assert not offenders, (
        f"{path} imports the app it is supposed to sit underneath:\n  "
        + "\n  ".join(offenders)
        + "\nEvery app repo installs this package; an app import here drags that "
        "app into all of them."
    )


# Tables that belong to ONE app. A core module reading or writing one of these
# is domain logic wearing a client's name.
#
# The test is who writes it, not what it is called. `agency_crm_cases` and
# `agency_crm_tasks` were listed here and should not have been: cases, renewals,
# the hub agent, the team queue and the casework sentinel all write them — they
# are the shared agency CRM schema, which is what the module that owned them
# said in its first line. Shared persistence over shared tables belongs in the
# core, the same way `portal_overrides` does.
#
# `intake_submissions` is the contrast, and the reason this guard exists: only
# the intake app touches it, so a core module reading it means intake's schema
# has been shipped to five repos that do not want it.
APP_OWNED_TABLES = {
    "intake_submissions": "intake",
    "renewal_candidates": "renewals",
    "project_85_renewals": "renewals",
    "renewal_case_details": "renewals",
    "commission_ledger": "finance",
    "commission_transactions": "finance",
    "commission_rules": "finance",
}


# The one place a core module may name an app's table, and why.
#
# casestore.delete_case must clear every child of a case before deleting it.
# renewal_case_details is a 1:1 sidecar on agency_crm_cases, so leaving it
# behind orphans a row pointing at a case that no longer exists — worse than the
# impurity. It is not left to ON DELETE CASCADE because the constraint lives in
# the shared agency_crm schema and is not ours to assume.
#
# This is an exception, written down, not a hole. When renewals becomes its own
# repo the child list should take the sidecar as a parameter instead. Adding a
# second entry here should feel harder than fixing the design.
ALLOWED_APP_TABLE_REFERENCES = {
    ("hermes_core/casestore.py", "renewal_case_details"),
}


@pytest.mark.parametrize("path", _core_files(), ids=lambda p: str(p))
def test_no_core_module_owns_an_app_table(path: pathlib.Path) -> None:
    src = path.read_text(encoding="utf8")
    rel = path.relative_to(CORE_ROOT).as_posix()
    # Ignore prose: a docstring or comment may legitimately name a table as an
    # example. Only string literals used as values count.
    literals = {
        node.value
        for node in ast.walk(ast.parse(src))
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }
    docstrings = set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            doc = ast.get_docstring(node, clean=False)
            if doc:
                docstrings.add(doc)
    offenders = [
        f"{table!r} (owned by the {app} app)"
        for literal in literals - docstrings
        for table, app in APP_OWNED_TABLES.items()
        if re.fullmatch(rf"{re.escape(table)}", literal.strip())
        and (rel, table) not in ALLOWED_APP_TABLE_REFERENCES
    ]
    assert not offenders, (
        f"{path} reads or writes a table belonging to one app: "
        + ", ".join(sorted(set(offenders)))
        + ". That is domain logic, not a shared client — it belongs with its app."
    )
