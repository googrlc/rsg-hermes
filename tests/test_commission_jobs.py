"""The runner: what it does unattended, and — more importantly — what it won't.

Two properties matter more than the happy paths. The poller must never commit
money, and the watchdog must complain when the pipeline goes quiet, because a
job that silently stops looks identical to a slow week.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from hermes.commissions import jobs
from hermes.commissions import statements as st

NOW = datetime(2026, 8, 2, 12, 0, tzinfo=timezone.utc)


def _hours_ago(hours: float) -> str:
    return (NOW - timedelta(hours=hours)).isoformat()


class FakeSupa:
    """Table -> rows, with inserts and updates recorded for assertions."""

    def __init__(self, tables: dict[str, list[dict]] | None = None):
        self.tables = tables or {}
        self.inserted: list[tuple[str, dict]] = []
        self.updated: list[tuple[str, str, dict]] = []

    def select(self, table, columns="*", params=None, limit=None):
        rows = list(self.tables.get(table, []))
        for key, value in (params or {}).items():
            if key in {"order", "select"} or not str(value).startswith("eq."):
                continue
            wanted = str(value)[3:]
            rows = [r for r in rows if str(r.get(key)) == wanted]
        return rows

    def insert(self, table, payload):
        self.inserted.append((table, payload))
        row = {"id": f"{table}-{len(self.inserted)}", **payload}
        self.tables.setdefault(table, []).append(row)
        return row

    def update(self, table, record_id, payload):
        self.updated.append((table, record_id, payload))
        return {"id": record_id, **payload}


class FakeNextcloud:
    def __init__(self, files: dict[str, bytes], *, configured: bool = True,
                 folder_exists: bool = True):
        self.files = files
        self._configured = configured
        self._folder_exists = folder_exists
        self.reads: list[str] = []

    def is_configured(self):
        return self._configured

    def path_exists(self, path):
        return self._folder_exists

    def list_dir(self, path):
        return [
            {"name": name, "path": f"{path}/{name}", "is_dir": False, "size": len(body)}
            for name, body in self.files.items()
        ]

    def read_file(self, path):
        self.reads.append(path)
        return self.files[path.rsplit("/", 1)[-1]]


STATEMENT_CSV = (
    b"Policy Number,Insured,Gross Premium,Gross Comm,Tran Code,Tran Date\n"
    b"P-1,Acme LLC,1000.00,150.00,New Business,01/15/2026\n"
)


# --- the inbox ----------------------------------------------------------------

def test_the_poller_stages_and_never_commits(monkeypatch):
    """The one property worth protecting: a dropped file is not an approval."""
    supa = FakeSupa()
    monkeypatch.setattr(st, "_match_preview", lambda *a, **k: {})
    nextcloud = FakeNextcloud({"june.csv": STATEMENT_CSV})

    report = jobs.poll_inbox(supa, nextcloud=nextcloud)

    assert report.staged == 1
    written = {table for table, _payload in supa.inserted}
    assert written == {st.BATCHES_TABLE, st.STAGING_TABLE}
    assert st.STATEMENTS_TABLE not in written, "a poller must not create a statement"
    assert st.TRANSACTIONS_TABLE not in written, "a poller must not book money"

    batch = next(p for t, p in supa.inserted if t == st.BATCHES_TABLE)
    assert batch["ingest_status"] == st.STATUS_PENDING_REVIEW
    assert batch["uploaded_by"] == jobs.POLLER_IDENTITY


def test_a_file_already_staged_is_not_staged_again(monkeypatch):
    """Re-polling is free, so the folder never has to be cleared for correctness."""
    from hermes.commissions.statements import content_hash

    supa = FakeSupa({st.BATCHES_TABLE: [{
        "id": "b-old", "content_hash": content_hash(STATEMENT_CSV),
        "ingest_status": st.STATUS_COMMITTED, "source_file": "june.csv",
    }]})
    monkeypatch.setattr(st, "_match_preview", lambda *a, **k: {})

    report = jobs.poll_inbox(supa, nextcloud=FakeNextcloud({"june.csv": STATEMENT_CSV}))

    assert (report.staged, report.duplicates) == (0, 1)
    assert supa.inserted == []


def test_an_unsupported_file_is_reported_not_swallowed():
    """A statement that quietly went unread is worse than one that was refused."""
    nextcloud = FakeNextcloud({"notes.docx": b"x", "logo.png": b"y"})

    report = jobs.poll_inbox(FakeSupa(), nextcloud=nextcloud)

    assert report.seen == 0
    assert sorted(report.ignored) == ["logo.png", "notes.docx"]


def test_one_undownloadable_file_does_not_stop_the_folder(monkeypatch):
    """The rest of the drop folder still gets staged, and the failure is named."""
    supa = FakeSupa()
    monkeypatch.setattr(st, "_match_preview", lambda *a, **k: {})
    nextcloud = FakeNextcloud({"locked.csv": b"", "june.csv": STATEMENT_CSV})

    original = nextcloud.read_file

    def read(path):
        if path.endswith("locked.csv"):
            raise RuntimeError("423 Locked")
        return original(path)

    nextcloud.read_file = read

    report = jobs.poll_inbox(supa, nextcloud=nextcloud)

    assert (report.staged, report.failed) == (1, 1)
    assert any("locked.csv" in e for e in report.errors)


def test_a_file_that_parses_to_nothing_is_staged_as_an_error(monkeypatch):
    """Not dropped on the floor: an unparseable statement is a batch to look at."""
    supa = FakeSupa()
    monkeypatch.setattr(st, "_match_preview", lambda *a, **k: {})

    report = jobs.poll_inbox(supa, nextcloud=FakeNextcloud({"empty.csv": b""}))

    assert report.staged == 1
    batch = next(p for t, p in supa.inserted if t == st.BATCHES_TABLE)
    assert batch["ingest_status"] == st.STATUS_ERROR
    assert batch["row_count"] == 0


def test_an_unconfigured_share_says_so_rather_than_reporting_zero():
    """"Nothing to poll" and "no share configured" are different answers."""
    report = jobs.poll_inbox(FakeSupa(), nextcloud=FakeNextcloud({}, configured=False))

    assert report.configured is False
    assert "not configured" in report.message


def test_a_missing_drop_folder_is_not_an_empty_one():
    """Caught on the first live run: the folder had never been created.

    WebDAV answers a missing folder with the same empty listing as an empty one,
    so without this check the poller reports "0 files" every night forever and
    reads exactly like a quiet month. Nobody would learn that no statement could
    ever arrive.
    """
    report = jobs.poll_inbox(FakeSupa(), nextcloud=FakeNextcloud({}, folder_exists=False))

    assert report.folder_missing is True
    assert report.seen == 0
    assert "does not exist" in report.message
    assert "no statement can ever arrive" in report.message


def test_an_empty_folder_that_exists_reports_plainly():
    report = jobs.poll_inbox(FakeSupa(), nextcloud=FakeNextcloud({}))

    assert report.folder_missing is False
    assert "seen=0" in report.message


def test_a_dry_run_reads_nothing():
    nextcloud = FakeNextcloud({"june.csv": STATEMENT_CSV})

    report = jobs.poll_inbox(FakeSupa(), nextcloud=nextcloud, dry_run=True)

    assert report.staged == 0 and report.seen == 1
    assert nextcloud.reads == []


# --- the nightly reconcile ----------------------------------------------------

def test_reconcile_links_before_it_rolls_up(monkeypatch):
    """A line linked this pass must be counted this pass, not next."""
    order: list[str] = []

    class Link:
        exact = normalized = created = 1
        ledger_rows_created = 1
        unmatched = 0
        errors: list[str] = []
        linked = 3

    class Roll:
        examined, changed = 4, 2
        by_status = {"underpaid": 1, "reconciled": 3}
        details = [{"policy_number": "P-1", "from": "missing_statement", "to": "underpaid"}]

    monkeypatch.setattr("hermes.commissions.matching.relink_unmatched",
                        lambda *a, **k: (order.append("link"), Link())[1])
    monkeypatch.setattr("hermes.commissions.reconcile.run_rollup",
                        lambda *a, **k: (order.append("roll"), Roll())[1])

    report = jobs.nightly_reconcile(FakeSupa())

    assert order == ["link", "roll"]
    assert (report.linked, report.ledger_rows_created) == (3, 1)
    assert report.by_status == {"underpaid": 1, "reconciled": 3}


def test_reconcile_reports_the_transitions_that_needed_no_new_money(monkeypatch):
    """The reason it runs on a clock: a term ending changes a status by itself."""
    class Link:
        exact = normalized = created = ledger_rows_created = unmatched = 0
        errors: list[str] = []
        linked = 0

    class Roll:
        examined, changed = 1, 1
        by_status = {"underpaid": 1}
        details = [{"policy_number": "P-9", "from": "missing_statement",
                    "to": "underpaid", "severity": "high"}]

    monkeypatch.setattr("hermes.commissions.matching.relink_unmatched", lambda *a, **k: Link())
    monkeypatch.setattr("hermes.commissions.reconcile.run_rollup", lambda *a, **k: Roll())

    report = jobs.nightly_reconcile(FakeSupa())

    assert report.transitions[0]["to"] == "underpaid"
    assert "changed=1" in report.message


# --- the watchdog -------------------------------------------------------------

BOOK = [
    {"policy_number": "P-1", "status": "Active", "active": True,
     "effective_date": "2026-03-01", "created_at": _hours_ago(10),
     "annualized_premium": 1000},
    {"policy_number": "P-2", "status": "Active", "active": True,
     "effective_date": "2026-04-01", "created_at": _hours_ago(10),
     "annualized_premium": 2000},
]
LEDGER = [{"id": "l1", "policy_number": "P-1", "expected_commission": 150,
           "updated_at": _hours_ago(9), "created_at": _hours_ago(40)}]


def _watch(book=BOOK, ledger=LEDGER, extra=None, monkeypatch=None):
    supa = FakeSupa({"commission_ledger": ledger, **(extra or {})})
    monkeypatch.setattr("hermes_core.book.select_policies", lambda *a, **k: book)
    return jobs.watchdog(supa, now=NOW)


def test_a_fresh_pipeline_is_quiet_apart_from_a_real_gap(monkeypatch):
    """P-2 qualifies and has no ledger row — that IS the gap worth reporting."""
    report = _watch(monkeypatch=monkeypatch)

    assert [p.kind for p in report.problems] == ["missing_from_ledger"]
    assert "P-2" not in report.message           # counts, not a row dump
    assert report.coverage["balanced"] is True


def test_a_stale_ledger_reads_as_a_job_that_stopped_running(monkeypatch):
    """The failure this exists for: a dead cron looks like a quiet week.

    The ledger is the signal because the 2:25 seed rewrites every commissionable
    row nightly off the book the 2:20 sync just refreshed. A fresh ledger proves
    the chain ran; a stale one proves it did not.
    """
    stale = [{**LEDGER[0], "updated_at": _hours_ago(90), "created_at": _hours_ago(90)}]

    report = _watch(ledger=stale, monkeypatch=monkeypatch)

    stale_problems = [p for p in report.problems if p.kind == "stale"]
    assert stale_problems, "a 90h-old ledger must be flagged"
    assert "commission_ledger" in stale_problems[0].detail
    assert "may not be running" in stale_problems[0].detail


def test_a_book_that_simply_gained_no_policies_is_not_an_alarm(monkeypatch):
    """canonical_policies has no updated_at — only created_at, which moves when a
    NEW policy appears, not when the sync runs. Several days this month added
    none at all. Alarming on it would fire on a quiet fortnight, and a watchdog
    that cries wolf on a quiet fortnight is one nobody reads.
    """
    quiet_book = [{**row, "created_at": _hours_ago(400)} for row in BOOK]

    report = _watch(book=quiet_book, monkeypatch=monkeypatch)

    assert not [p for p in report.problems if p.kind == "stale"]
    # Still reported, as context rather than as a verdict.
    assert report.book_newest_policy is not None


def test_the_watchdog_counts_the_work_piling_up(monkeypatch):
    extra = {
        "commission_ingest_batches": [
            {"id": "b1", "ingest_status": "pending_review"},
            {"id": "b2", "ingest_status": "committed"},
        ],
        "commission_transactions": [
            {"id": "t1", "ledger_id": None}, {"id": "t2", "ledger_id": "l1"},
        ],
    }
    report = _watch(extra=extra, monkeypatch=monkeypatch)

    assert report.pending_batches == 1
    assert report.unmatched_transactions == 1


def test_rows_with_no_expected_commission_are_counted(monkeypatch):
    ledger = [*LEDGER, {"id": "l2", "policy_number": "P-2",
                        "expected_commission": None, "updated_at": _hours_ago(9)}]

    report = _watch(ledger=ledger, monkeypatch=monkeypatch)

    assert report.rows_without_expected == 1


def test_an_unreadable_book_is_a_problem_not_an_empty_report(monkeypatch):
    """Zero policies and an unreachable AMS must not look the same."""
    def boom(*a, **k):
        raise RuntimeError("AMS down")

    monkeypatch.setattr("hermes_core.book.select_policies", boom)

    report = jobs.watchdog(FakeSupa({"commission_ledger": LEDGER}), now=NOW)

    assert any(p.kind == "book_unreadable" for p in report.problems)
    assert report.ok is False


# --- alerting -----------------------------------------------------------------

class FakeNotifier:
    def __init__(self):
        self.posts: list[str] = []

    def post_message(self, *, text, blocks=None):
        self.posts.append(text)
        return {"ok": True}


def test_a_healthy_run_says_nothing():
    """A channel that only speaks when something is wrong stays readable."""
    notifier = FakeNotifier()

    assert jobs.alert(jobs.HealthReport(), notifier=notifier) is False
    assert notifier.posts == []


def test_problems_are_posted_with_their_detail():
    report = jobs.HealthReport(problems=[jobs.Problem("stale", "book is 80h old")])
    notifier = FakeNotifier()

    assert jobs.alert(report, notifier=notifier) is True
    assert "book is 80h old" in notifier.posts[0]


def test_a_failed_alert_does_not_fail_the_job():
    class Broken:
        def post_message(self, **kw):
            raise RuntimeError("slack down")

    report = jobs.HealthReport(problems=[jobs.Problem("stale", "x")])
    assert jobs.alert(report, notifier=Broken()) is False


@pytest.mark.parametrize("value,expected_hour", [
    ("2026-08-02 02:25:01.482788+00", 2),
    ("2026-08-02T02:25:01.482788+00:00", 2),
    ("2026-08-02T02:25:01Z", 2),
])
def test_postgres_timestamps_parse(value, expected_hour):
    """Supabase prints a +00 offset; fromisoformat wants +00:00."""
    stamp = jobs._parse_stamp(value)
    assert stamp is not None and stamp.hour == expected_hour
    assert stamp.tzinfo is not None
