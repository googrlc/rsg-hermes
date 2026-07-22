"""End-to-end intake flow against a fake Supabase — the Phase 1 acceptance test."""
import uuid

import pytest

from hermes.command_center import service, store
from hermes.command_center.lanes import load_all_lanes
from hermes.command_center.review import ReviewError

LANES = load_all_lanes()

FULL_DEC = """
DECLARATIONS PAGE
Named Insured: Jane Roe
Carrier: Progressive Insurance Company
Effective Date: 04/16/2025    Expiration Date: 10/16/2025
Total Premium: $1,284.00
"""

NO_XDATE_DEC = """
Named Insured: Jane Roe
Carrier: Progressive Insurance Company
Total Premium: $1,284.00
"""


class FakeSupa:
    def __init__(self):
        self.tables: dict = {}

    def insert(self, table, payload):
        row = dict(payload)
        row.setdefault("id", str(uuid.uuid4()))
        self.tables.setdefault(table, {})[row["id"]] = row
        return row

    def update(self, table, record_id, payload):
        self.tables[table][record_id].update(payload)
        return self.tables[table][record_id]

    def delete(self, table, record_id):
        self.tables.get(table, {}).pop(record_id, None)

    def select(self, table, columns=None, params=None, limit=None):
        rows = list(self.tables.get(table, {}).values())
        for k, v in (params or {}).items():
            if k == "order":
                continue
            if isinstance(v, str) and v.startswith("eq."):
                rows = [r for r in rows if str(r.get(k)) == v[3:]]
        return rows[: limit or len(rows)]


def _ingest(supa, sub_id, text, filename="jane_dec_page.txt"):
    return service.ingest_files(
        supa, sub_id,
        [{"filename": filename, "text": text, "storage_path": "cc-intake-uploads/x"}],
        LANES,
    )


def test_happy_path_extract_approve_download():
    supa = FakeSupa()
    sub = service.create(supa, "gretchen-personal-lines", "Jane Roe", "gretchen", LANES)

    res = _ingest(supa, sub["id"], FULL_DEC)
    assert res["submission"]["status"] == "in_review"
    assert [f for f in res["flags"] if f["severity"] == "blocking"] == []   # xdate + name present

    service.approve(supa, sub["id"], "gretchen", LANES)
    assert store.get_submission(supa, sub["id"])["status"] == "approved"

    blob = service.download_bundle(supa, sub["id"])
    assert blob[:2] == b"PK"   # a real zip
    # audit trail captured the whole journey
    actions = [e["action"] for e in store.list_events(supa, sub["id"])]
    assert actions[0] == "created" and "approved" in actions and "downloaded" in actions


def test_missing_xdate_blocks_approval_then_fix_unblocks():
    supa = FakeSupa()
    sub = service.create(supa, "gretchen-personal-lines", "Jane Roe", "gretchen", LANES)

    res = _ingest(supa, sub["id"], NO_XDATE_DEC)
    assert any(f["field"] == "xdate" for f in res["flags"])

    with pytest.raises(ReviewError) as ei:           # blocking flag -> 422
        service.approve(supa, sub["id"], "gretchen", LANES)
    assert ei.value.status_code == 422

    service.apply_fixes(supa, sub["id"], {"xdate": "2026-07-01"}, LANES)
    service.approve(supa, sub["id"], "gretchen", LANES)
    assert store.get_submission(supa, sub["id"])["status"] == "approved"


def test_download_locked_until_approved():
    supa = FakeSupa()
    sub = service.create(supa, "gretchen-personal-lines", "Jane Roe", "gretchen", LANES)
    _ingest(supa, sub["id"], FULL_DEC)               # now in_review, not approved

    with pytest.raises(ReviewError) as d:
        service.download_bundle(supa, sub["id"])
    assert d.value.status_code == 403
