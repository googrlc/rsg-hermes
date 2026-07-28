"""Dates on the pipeline, the leads list and the renewals cockpit.

Three surfaces that were all missing the one date that ranks them: a projected
close on a deal, an x-date on a lead, the policy period on a renewal.
"""
from __future__ import annotations

from hermes import leads
from hermes.intake import opportunities as opp
from hermes.operations import renewal_tracker as rt


# --- Projected close on an opportunity ---------------------------------------
def test_projected_close_prefers_the_date_a_human_set():
    date, basis = opp.projected_close(
        {"expected_close_date": "2026-09-01", "needed_by": "2026-10-01", "effective_date": "2026-11-01"}
    )
    assert (date, basis) == ("2026-09-01", "set")


def test_projected_close_falls_back_to_needed_by_then_effective():
    assert opp.projected_close({"needed_by": "2026-10-01", "effective_date": "2026-11-01"}) == (
        "2026-10-01", "needed by",
    )
    assert opp.projected_close({"effective_date": "2026-11-01"}) == ("2026-11-01", "effective")


def test_a_renewal_closes_when_the_policy_ends_not_when_it_began():
    """effective_date on a renewal is the START of the expiring term — months in
    the past. Used as a close date it renders every renewal as already slipped."""
    row = {"opportunity_type": "Renewals", "effective_date": "2025-08-18",
           "expiration_date": "2026-08-18"}
    assert opp.projected_close(row) == ("2026-08-18", "expires")


def test_new_business_still_closes_on_when_cover_starts():
    row = {"opportunity_type": "New Business", "effective_date": "2026-09-01",
           "expiration_date": "2027-09-01"}
    assert opp.projected_close(row) == ("2026-09-01", "effective")


def test_a_set_close_date_wins_on_a_renewal_too():
    row = {"opportunity_type": "Renewals", "expected_close_date": "2026-07-01",
           "expiration_date": "2026-08-18"}
    assert opp.projected_close(row) == ("2026-07-01", "set")


def test_projected_close_is_blank_rather_than_guessed():
    """No date anywhere is an honest blank — a stage is not a forecast."""
    assert opp.projected_close({"stage": "Sent Proposal", "probability": 65}) == (None, None)
    assert opp.projected_close({"expected_close_date": "", "needed_by": None}) == (None, None)


def test_projected_close_trims_a_timestamp_to_the_date():
    assert opp.projected_close({"expected_close_date": "2026-09-01T00:00:00+00:00"})[0] == "2026-09-01"


def test_with_projected_close_annotates_every_row():
    rows = opp.with_projected_close([{"needed_by": "2026-10-01"}, {"id": "x"}])
    assert rows[0]["projected_close_date"] == "2026-10-01"
    assert rows[0]["projected_close_basis"] == "needed by"
    # The key is present even when empty, so a board renders one shape.
    assert rows[1]["projected_close_date"] is None
    assert rows[1]["projected_close_basis"] is None


def test_expected_close_date_is_editable_and_creatable():
    """The forecast is CRM-owned, so both write paths have to carry it."""
    from hermes.api import OpportunityCreateRequest, OpportunityUpdateRequest, _OPP_EDITABLE

    assert "expected_close_date" in OpportunityCreateRequest.model_fields
    assert "expected_close_date" in OpportunityUpdateRequest.model_fields
    assert "expected_close_date" in _OPP_EDITABLE


def test_expected_close_date_is_never_written_by_the_ams_sync():
    """NowCerts has no estimated-close field; the sync must not clobber ours."""
    from hermes.sync.opportunity_sync import _COLS

    assert "expected_close_date" not in _COLS


# --- X-date on a lead ---------------------------------------------------------
class FakeNC:
    def __init__(self, insureds):
        self._i = insureds

    def fetch_insureds(self, *, page_size=100, since=None, max_pages=1000):
        return list(self._i)


class FakeSupa:
    def __init__(self, rows, *, boom=False):
        self.rows = rows
        self.boom = boom
        self.calls: list[dict] = []

    def select(self, table, *, columns=None, params=None, limit=None):
        self.calls.append({"table": table, "params": params})
        if self.boom:
            raise RuntimeError("supabase down")
        return list(self.rows)


def _prospect(guid, name):
    return {"id": guid, "commercialName": name, "prospectType": "Prospect", "city": "Marietta"}


def test_lead_takes_the_soonest_x_date_from_its_open_deals():
    nc = FakeNC([_prospect("g1", "Prospect Co")])
    supa = FakeSupa([
        {"insured_id": "g1", "line_of_business": "General Liability", "expiration_date": "2026-09-15"},
        {"insured_id": "g1", "line_of_business": "Commercial Auto", "expiration_date": "2026-12-01"},
    ])
    lead = leads.list_prospects(nc, supa)["leads"][0]
    assert lead["x_date"] == "2026-09-15"
    assert lead["x_date_line"] == "General Liability"


def test_lead_with_nothing_in_the_pipeline_gets_no_invented_x_date():
    nc = FakeNC([_prospect("g1", "Prospect Co")])
    lead = leads.list_prospects(nc, FakeSupa([]))["leads"][0]
    assert "x_date" not in lead


def test_leads_survive_an_x_date_lookup_failure():
    nc = FakeNC([_prospect("g1", "Prospect Co")])
    out = leads.list_prospects(nc, FakeSupa([], boom=True))
    assert out["count"] == 1 and "x_date" not in out["leads"][0]


def test_lead_carries_the_city_its_column_asks_for():
    lead = leads.list_prospects(FakeNC([_prospect("g1", "Prospect Co")]))["leads"][0]
    assert lead["city"] == "Marietta"


def test_x_date_lookup_asks_only_for_open_deals_with_a_date():
    supa = FakeSupa([])
    leads.list_prospects(FakeNC([_prospect("g1", "Prospect Co")]), supa)
    params = supa.calls[0]["params"]
    assert params["status"] == "eq.open"
    assert params["expiration_date"] == "not.is.null"
    assert params["insured_id"] == "in.(g1)"


# --- Policy dates on a renewal ------------------------------------------------
class FakeBook:
    def __init__(self, policies, *, boom=False):
        self.policies = policies
        self.boom = boom
        self.params: list[dict] = []

    def select(self, table, *, columns=None, params=None, limit=None):
        self.params.append(params or {})
        if self.boom:
            raise RuntimeError("supabase down")
        return list(self.policies)


def test_renewal_gets_the_policy_period_off_the_book():
    rows = [{"policy_number": "POL-1", "expiration_date": "2026-10-01"}]
    book = FakeBook([
        {"policy_number": "POL-1", "effective_date": "2025-10-01", "expiration_date": "2026-10-01",
         "carrier": "Progressive", "lines_of_business": "Commercial Auto"},
    ])
    out = rt.attach_policy_dates(book, rows)[0]
    assert out["effective_date"] == "2025-10-01"
    assert out["carrier"] == "Progressive"
    assert out["lines_of_business"] == "Commercial Auto"


def test_renewal_picks_the_term_whose_expiration_matches():
    """One policy number, several terms — the renewal is about one of them."""
    rows = [{"policy_number": "POL-1", "expiration_date": "2026-10-01"}]
    book = FakeBook([
        {"policy_number": "POL-1", "effective_date": "2024-10-01", "expiration_date": "2025-10-01"},
        {"policy_number": "POL-1", "effective_date": "2025-10-01", "expiration_date": "2026-10-01"},
        {"policy_number": "POL-1", "effective_date": "2026-10-01", "expiration_date": "2027-10-01"},
    ])
    assert rt.attach_policy_dates(book, rows)[0]["effective_date"] == "2025-10-01"


def test_renewal_falls_back_to_the_latest_term_when_none_matches():
    rows = [{"policy_number": "POL-1", "expiration_date": "2026-11-15"}]   # corrected date
    book = FakeBook([
        {"policy_number": "POL-1", "effective_date": "2024-10-01", "expiration_date": "2025-10-01"},
        {"policy_number": "POL-1", "effective_date": "2025-10-01", "expiration_date": "2026-10-01"},
    ])
    assert rt.attach_policy_dates(book, rows)[0]["effective_date"] == "2025-10-01"


def test_policy_numbers_are_quoted_in_the_filter():
    """Policy numbers carry commas and spaces; unquoted they split the filter."""
    rows = [{"policy_number": "POL 1, A", "expiration_date": "2026-10-01"}]
    book = FakeBook([])
    rt.attach_policy_dates(book, rows)
    assert book.params[0]["policy_number"] == 'in.("POL 1, A")'


def test_renewals_survive_a_book_read_failure():
    rows = [{"policy_number": "POL-1", "expiration_date": "2026-10-01"}]
    out = rt.attach_policy_dates(FakeBook([], boom=True), rows)
    assert out == [{"policy_number": "POL-1", "expiration_date": "2026-10-01"}]


def test_attach_policy_dates_leaves_no_bookkeeping_behind():
    rows = [{"policy_number": "POL-1", "expiration_date": "2026-10-01"}]
    book = FakeBook([{"policy_number": "POL-1", "effective_date": "2025-10-01",
                      "expiration_date": "2026-10-01"}])
    assert "_policy_exact" not in rt.attach_policy_dates(book, rows)[0]


def test_summary_carries_the_policy_period_to_the_cockpit():
    from datetime import date

    out = rt.summarize_renewals(
        [{"id": "r1", "policy_number": "POL-1", "client_name": "Acme",
          "expiration_date": "2026-08-01", "effective_date": "2025-08-01",
          "carrier": "Progressive", "lines_of_business": "BOP", "premium_current": 1000}],
        today=date(2026, 7, 1),
    )
    row = out["upcoming"][0]
    assert row["effective_date"] == "2025-08-01"
    assert row["carrier"] == "Progressive"
    assert row["lines_of_business"] == "BOP"


def test_summary_shape_is_the_same_without_the_book():
    """A row that never went through attach_policy_dates still has the keys."""
    from datetime import date

    out = rt.summarize_renewals(
        [{"id": "r1", "policy_number": "POL-1", "expiration_date": "2026-08-01"}],
        today=date(2026, 7, 1),
    )
    assert out["upcoming"][0]["effective_date"] is None
    assert out["upcoming"][0]["carrier"] is None
