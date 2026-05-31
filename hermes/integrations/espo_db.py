"""Read-only direct Postgres access to the EspoCRM backend (the "read lane").

This is the *read* half of the Hermes-as-EspoCRM-brain design: fast, in-database
relational work (duplicate detection, fuzzy matching, analytics) that triggers no
business logic and therefore has nothing to bypass. It is **read-only by
construction** — see the two guards in ``connect()``.

Writes are deliberately NOT supported here. They must go through EspoCRM's
application layer (REST today, a custom Espo extension later) so hooks, ACL,
formula/workflow, and the Stream all fire. See ``docs/espocrm-read-lane.md``.

Connection is optional: if the ``ESPO_DB_*`` env vars are unset, ``is_configured()``
returns False and callers should fall back to the REST client (``EspoClient``).
The ``psycopg`` driver is an optional dependency (``pip install -e '.[db]'``) and
is imported lazily so core Hermes installs stay lean.

EspoCRM schema conventions assumed here (verified by ``verify_schema()``):
  * Entity tables are snake_case of the entity name (Account -> ``account``).
  * Every row has a ``deleted`` boolean; live rows are ``deleted = false``.
  * Email/phone live in side tables, linked via ``entity_email_address`` /
    ``entity_phone_number``; match on the lowercased ``email_address.lower`` column.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)

# Tables/columns this module depends on. verify_schema() checks these exist so an
# EspoCRM upgrade that renames the raw layout surfaces as a clear error, not silent
# corruption. The raw schema is an implementation detail; pin what we read.
REQUIRED_SCHEMA: dict[str, tuple[str, ...]] = {
    "account": ("id", "name", "fein", "deleted"),
    "contact": ("id", "name", "first_name", "last_name", "deleted"),
    "lead": ("id", "name", "deleted"),
    "email_address": ("id", "lower", "deleted"),
    "entity_email_address": ("email_address_id", "entity_id", "entity_type", "deleted"),
    "phone_number": ("id", "name", "deleted"),
    "entity_phone_number": ("phone_number_id", "entity_id", "entity_type", "deleted"),
}


class EspoDbError(Exception):
    """Raised on connection, configuration, or schema problems in the read lane."""


@dataclass
class DuplicateCandidate:
    """One possible duplicate surfaced by a fuzzy/exact match."""

    entity_type: str
    record_id: str
    name: str
    score: float  # 0..1 trigram similarity, or 1.0 for an exact email/phone hit
    matched_on: str  # "name" | "email" | "phone" | "fein"
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class DbHealth:
    ok: bool
    connected: bool
    read_only: bool
    pg_trgm: bool
    missing_schema: list[str] = field(default_factory=list)
    message: str = ""

    def format_lines(self) -> list[str]:
        check = lambda b: ":white_check_mark:" if b else ":x:"  # noqa: E731
        lines = [
            "EspoCRM read-lane doctor (direct Postgres, read-only)",
            f"  {check(self.connected)} connection",
            f"  {check(self.read_only)} session is READ ONLY (write guard)",
            f"  {check(self.pg_trgm)} pg_trgm extension available (fuzzy matching)",
            f"  {check(not self.missing_schema)} required schema present",
        ]
        if self.missing_schema:
            lines.append("    missing: " + ", ".join(self.missing_schema))
        if self.message:
            lines.append(f"  {self.message}")
        return lines


# Env var names for the read-only connection (separate from REST creds).
_ENV_HOST = "ESPO_DB_HOST"
_ENV_PORT = "ESPO_DB_PORT"
_ENV_NAME = "ESPO_DB_NAME"
_ENV_USER = "ESPO_DB_USER"
_ENV_PASSWORD = "ESPO_DB_PASSWORD"


def is_configured() -> bool:
    """True when the read-lane env vars are present. Callers fall back to REST if not."""
    return bool(os.environ.get(_ENV_HOST) and os.environ.get(_ENV_NAME) and os.environ.get(_ENV_USER))


class EspoDb:
    """Read-only connection to EspoCRM's Postgres backend.

    Two independent write guards: a SELECT-only DB role (granted server-side) and a
    session forced to ``READ ONLY``. Either alone is sufficient; both together mean a
    bug in Hermes cannot mutate the CRM through this lane.
    """

    def __init__(
        self,
        *,
        host: str | None = None,
        port: int | None = None,
        dbname: str | None = None,
        user: str | None = None,
        password: str | None = None,
        connect_timeout: float = 10.0,
    ) -> None:
        self.host = host or os.environ.get(_ENV_HOST, "")
        self.port = int(port or os.environ.get(_ENV_PORT, "5432"))
        self.dbname = dbname or os.environ.get(_ENV_NAME, "")
        self.user = user or os.environ.get(_ENV_USER, "")
        self.password = password if password is not None else os.environ.get(_ENV_PASSWORD, "")
        self.connect_timeout = connect_timeout
        if not (self.host and self.dbname and self.user):
            raise EspoDbError(
                f"{_ENV_HOST}, {_ENV_NAME}, and {_ENV_USER} must be set to use the read lane."
            )
        self._conn: Any = None

    # -- connection -----------------------------------------------------------

    def connect(self) -> Any:
        """Open (or reuse) a read-only connection. Lazy-imports psycopg."""
        if self._conn is not None and not getattr(self._conn, "closed", True):
            return self._conn
        try:
            import psycopg  # noqa: PLC0415  (optional dependency, imported lazily)
        except ImportError as e:  # pragma: no cover - depends on install extras
            raise EspoDbError(
                "psycopg is not installed. Install the DB extra: pip install -e '.[db]'"
            ) from e

        try:
            self._conn = psycopg.connect(
                host=self.host,
                port=self.port,
                dbname=self.dbname,
                user=self.user,
                password=self.password,
                connect_timeout=self.connect_timeout,
                autocommit=True,
                # Guard #2: force the whole session read-only at the server.
                options="-c default_transaction_read_only=on",
            )
        except Exception as e:  # psycopg.OperationalError etc.
            raise EspoDbError(f"Failed to connect to EspoCRM Postgres: {e}") from e
        return self._conn

    def close(self) -> None:
        if self._conn is not None and not getattr(self._conn, "closed", True):
            self._conn.close()
        self._conn = None

    def __enter__(self) -> EspoDb:
        self.connect()
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _rows(self, sql: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        """Run a SELECT and return rows as dicts. Refuses anything but SELECT/WITH."""
        head = sql.lstrip().split(None, 1)[0].lower() if sql.strip() else ""
        if head not in ("select", "with"):
            raise EspoDbError(f"Read lane only runs SELECT/WITH queries, got: {head!r}")
        conn = self.connect()
        from psycopg.rows import dict_row  # noqa: PLC0415

        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(sql, params)
            return list(cur.fetchall())

    # -- health / schema ------------------------------------------------------

    def check_health(self) -> DbHealth:
        """Verify connectivity, the read-only guard, pg_trgm, and required schema."""
        try:
            conn = self.connect()
        except EspoDbError as e:
            return DbHealth(ok=False, connected=False, read_only=False, pg_trgm=False, message=str(e))

        read_only = False
        pg_trgm = False
        try:
            ro = self._rows("SELECT current_setting('transaction_read_only') AS ro", {})
            read_only = bool(ro and ro[0]["ro"] in ("on", "true", True))
            trg = self._rows(
                "SELECT 1 AS ok FROM pg_extension WHERE extname = %(n)s", {"n": "pg_trgm"}
            )
            pg_trgm = bool(trg)
        except EspoDbError as e:
            return DbHealth(ok=False, connected=True, read_only=read_only, pg_trgm=pg_trgm, message=str(e))

        missing = self.verify_schema()
        ok = read_only and pg_trgm and not missing
        msg = "" if ok else "Run setup: SELECT-only role, default_transaction_read_only, CREATE EXTENSION pg_trgm."
        return DbHealth(
            ok=ok, connected=True, read_only=read_only, pg_trgm=pg_trgm, missing_schema=missing, message=msg
        )

    def verify_schema(self) -> list[str]:
        """Return ``table.column`` entries from REQUIRED_SCHEMA that are absent."""
        present = self._rows(
            """
            SELECT table_name, column_name
            FROM information_schema.columns
            WHERE table_schema = current_schema()
              AND table_name = ANY(%(tables)s)
            """,
            {"tables": list(REQUIRED_SCHEMA.keys())},
        )
        have: dict[str, set[str]] = {}
        for row in present:
            have.setdefault(row["table_name"], set()).add(row["column_name"])
        missing: list[str] = []
        for table, cols in REQUIRED_SCHEMA.items():
            for col in cols:
                if col not in have.get(table, set()):
                    missing.append(f"{table}.{col}")
        return missing

    # -- duplicate detection --------------------------------------------------

    def find_duplicate_contacts(
        self, *, name: str | None = None, email: str | None = None,
        phone: str | None = None, threshold: float = 0.4, limit: int = 10,
    ) -> list[DuplicateCandidate]:
        """Find likely-duplicate Contacts by exact email/phone or fuzzy name.

        Exact email/phone hits score 1.0; name hits use trigram similarity. Results
        are merged (best score per record) and sorted high-to-low.
        """
        by_id: dict[str, DuplicateCandidate] = {}

        if email:
            for r in self._exact_link_match("Contact", "contact", email=email):
                self._merge(by_id, r)
        if phone:
            for r in self._exact_link_match("Contact", "contact", phone=phone):
                self._merge(by_id, r)
        if name:
            rows = self._rows(
                """
                SELECT c.id, c.name, similarity(c.name, %(name)s) AS sim
                FROM contact c
                WHERE c.deleted = false
                  AND c.name IS NOT NULL
                  AND similarity(c.name, %(name)s) > %(threshold)s
                ORDER BY sim DESC
                LIMIT %(limit)s
                """,
                {"name": name, "threshold": threshold, "limit": limit},
            )
            for row in rows:
                self._merge(by_id, DuplicateCandidate(
                    "Contact", str(row["id"]), row.get("name") or "", float(row["sim"]), "name"
                ))

        return sorted(by_id.values(), key=lambda c: c.score, reverse=True)[:limit]

    def find_duplicate_accounts(
        self, *, name: str | None = None, fein: str | None = None,
        threshold: float = 0.4, limit: int = 10,
    ) -> list[DuplicateCandidate]:
        """Find likely-duplicate Accounts by exact FEIN or fuzzy name."""
        by_id: dict[str, DuplicateCandidate] = {}

        if fein:
            for row in self._rows(
                "SELECT id, name, fein FROM account WHERE deleted = false AND fein = %(fein)s LIMIT %(limit)s",
                {"fein": fein, "limit": limit},
            ):
                self._merge(by_id, DuplicateCandidate(
                    "Account", str(row["id"]), row.get("name") or "", 1.0, "fein",
                    {"fein": row.get("fein")},
                ))
        if name:
            for row in self._rows(
                """
                SELECT a.id, a.name, a.fein, similarity(a.name, %(name)s) AS sim
                FROM account a
                WHERE a.deleted = false
                  AND a.name IS NOT NULL
                  AND similarity(a.name, %(name)s) > %(threshold)s
                ORDER BY sim DESC
                LIMIT %(limit)s
                """,
                {"name": name, "threshold": threshold, "limit": limit},
            ):
                self._merge(by_id, DuplicateCandidate(
                    "Account", str(row["id"]), row.get("name") or "", float(row["sim"]), "name",
                    {"fein": row.get("fein")},
                ))

        return sorted(by_id.values(), key=lambda c: c.score, reverse=True)[:limit]

    def _exact_link_match(
        self, entity_type: str, table: str, *, email: str | None = None, phone: str | None = None,
    ) -> list[DuplicateCandidate]:
        """Exact match on a side-table email/phone, joined back to the entity row."""
        if email:
            rows = self._rows(
                """
                SELECT DISTINCT e.id, e.name
                FROM {table} e
                JOIN entity_email_address eea
                  ON eea.entity_id = e.id AND eea.entity_type = %(etype)s AND eea.deleted = false
                JOIN email_address ea
                  ON ea.id = eea.email_address_id AND ea.deleted = false
                WHERE e.deleted = false AND ea.lower = lower(%(email)s)
                """.format(table=table),
                {"etype": entity_type, "email": email},
            )
            return [DuplicateCandidate(entity_type, str(r["id"]), r.get("name") or "", 1.0, "email") for r in rows]
        if phone:
            digits = "".join(ch for ch in phone if ch.isdigit())
            rows = self._rows(
                """
                SELECT DISTINCT e.id, e.name
                FROM {table} e
                JOIN entity_phone_number epn
                  ON epn.entity_id = e.id AND epn.entity_type = %(etype)s AND epn.deleted = false
                JOIN phone_number pn
                  ON pn.id = epn.phone_number_id AND pn.deleted = false
                WHERE e.deleted = false
                  AND regexp_replace(pn.name, '\\D', '', 'g') = %(digits)s
                """.format(table=table),
                {"etype": entity_type, "digits": digits},
            )
            return [DuplicateCandidate(entity_type, str(r["id"]), r.get("name") or "", 1.0, "phone") for r in rows]
        return []

    @staticmethod
    def _merge(by_id: dict[str, DuplicateCandidate], cand: DuplicateCandidate) -> None:
        """Keep the highest-scoring hit per record id."""
        existing = by_id.get(cand.record_id)
        if existing is None or cand.score > existing.score:
            by_id[cand.record_id] = cand
