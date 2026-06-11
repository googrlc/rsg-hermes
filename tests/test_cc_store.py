"""Persistence layer, exercised against an in-memory fake Supabase client."""
import uuid

from hermes.command_center import store


class FakeSupa:
    """Minimal stand-in for SupabaseClient: insert/select/update/delete over dicts."""

    def __init__(self):
        self.tables: dict[str, dict[str, dict]] = {}

    def insert(self, table, payload):
        row = dict(payload)
        row.setdefault("id", str(uuid.uuid4()))
        self.tables.setdefault(table, {})[row["id"]] = row
        return row

    def update(self, table, record_id, payload):
        row = self.tables[table][record_id]
        row.update(payload)
        return row

    def delete(self, table, record_id):
        self.tables.get(table, {}).pop(record_id, None)

    def select(self, table, columns=None, params=None, limit=None):
        rows = list(self.tables.get(table, {}).values())
        params = params or {}
        for k, v in params.items():
            if k == "order":
                continue
            if isinstance(v, str) and v.startswith("eq."):
                want = v[3:]
                rows = [r for r in rows if str(r.get(k)) == want]
        return rows[: limit or len(rows)]


def test_create_logs_event_and_is_fetchable():
    supa = FakeSupa()
    sub = store.create_submission(supa, lane="gretchen-personal-lines", client_name="Jane Roe")
    assert sub["status"] == "draft"
    again = store.get_submission(supa, sub["id"])
    assert again["client_name"] == "Jane Roe"
    events = store.list_events(supa, sub["id"])
    assert [e["action"] for e in events] == ["created"]


def test_status_transitions_are_audited():
    supa = FakeSupa()
    sub = store.create_submission(supa, lane="gretchen-personal-lines", client_name="X")
    store.set_status(supa, sub["id"], "extracting", "gretchen")
    store.set_status(supa, sub["id"], "in_review", "gretchen")
    actions = [e["action"] for e in store.list_events(supa, sub["id"])]
    assert actions == ["created", "extracting", "in_review"]
    assert store.get_submission(supa, sub["id"])["status"] == "in_review"


def test_files_and_deliverables_scoped_to_submission():
    supa = FakeSupa()
    a = store.create_submission(supa, lane="l", client_name="A")
    b = store.create_submission(supa, lane="l", client_name="B")
    store.add_file(supa, a["id"], filename="dec.pdf", doc_type="dec_page", storage_path="p/1")
    store.replace_deliverables(supa, a["id"], [
        {"kind": "quote_worksheet", "title": "Quote worksheet", "content": "# Q"},
    ])
    assert len(store.list_files(supa, a["id"])) == 1
    assert len(store.list_files(supa, b["id"])) == 0
    assert len(store.list_deliverables(supa, a["id"])) == 1


def test_replace_deliverables_is_idempotent():
    supa = FakeSupa()
    s = store.create_submission(supa, lane="l", client_name="A")
    built = [{"kind": "crm_blocks", "title": "CRM", "content": "x"}]
    store.replace_deliverables(supa, s["id"], built)
    store.replace_deliverables(supa, s["id"], built)   # rebuild, not duplicate
    assert len(store.list_deliverables(supa, s["id"])) == 1
