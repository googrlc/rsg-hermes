"""Dashboard reads against an in-memory fake (no live DB)."""
import uuid

from hermes.command_center import dashboard


class FakeSupa:
    def __init__(self):
        self.tables: dict = {}

    def insert(self, table, payload):
        row = dict(payload)
        row.setdefault("id", str(uuid.uuid4()))
        self.tables.setdefault(table, {})[row["id"]] = row
        return row

    def select(self, table, columns=None, params=None, limit=None):
        rows = list(self.tables.get(table, {}).values())
        for k, v in (params or {}).items():
            if k == "order":
                continue
            if isinstance(v, str) and v.startswith("eq."):
                rows = [r for r in rows if str(r.get(k)) == v[3:]]
        return rows[: limit or len(rows)]


def _seed(supa):
    for i in range(3):
        supa.insert("canonical_clients", {"nowcerts_insured_guid": f"g{i}"})
    supa.insert("canonical_policies", {"active": True, "annualized_premium": 1000})
    supa.insert("canonical_policies", {"active": True, "current_term_amount": 500})
    supa.insert("canonical_policies", {"active": False, "premium_amount": 999})  # inactive excluded
    supa.insert("agency_snapshots", {"retention_rate": 61.2, "snapshot_date": "2026-06-01"})


def test_kpi_summary_counts_active_premium_and_book():
    supa = FakeSupa()
    _seed(supa)
    k = dashboard.kpi_summary(supa)
    assert k["client_count"] == 3
    assert k["policy_count"] == 3
    assert k["active_policy_count"] == 2
    assert k["active_premium"] == 1500       # 1000 + 500; inactive 999 excluded
    assert k["retention_rate"] == 61.2
    assert k["retention_goal"] == 75.0
    assert k["pipeline"] is None


def test_kpi_summary_empty_is_safe():
    k = dashboard.kpi_summary(FakeSupa())
    assert k["active_premium"] == 0 and k["client_count"] == 0
    assert k["retention_rate"] is None


def test_approval_queue_only_in_review_with_flag_counts():
    supa = FakeSupa()
    supa.insert("cc_submissions", {"status": "in_review", "client_name": "A",
                                   "flags": [{"severity": "blocking"}, {"severity": "warning"}]})
    supa.insert("cc_submissions", {"status": "approved", "client_name": "B", "flags": []})
    q = dashboard.approval_queue(supa)
    assert len(q) == 1 and q[0]["client_name"] == "A"
    assert q[0]["blocking"] == 1 and q[0]["warnings"] == 1


def test_activity_feed_reads_events():
    supa = FakeSupa()
    supa.insert("cc_review_events", {"action": "created", "actor": "gretchen"})
    feed = dashboard.activity_feed(supa)
    assert feed and feed[0]["action"] == "created"
