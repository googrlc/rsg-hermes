"""Tests for the search-before-insert dedup hardening (hermes/sync/dedup.py)
plus the integration gates in pipeline.py and bidirectional.py.

The dedup unit tests run anywhere (rapidfuzz optional, difflib fallback).
The pipeline/bidirectional integration tests import lazily so they only need
the full sync deps when actually run (i.e. in the Hermes container).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hermes.sync.dedup import best_name_match, name_score, passes_gate


# ---------------------------------------------------------------------------
# dedup unit tests (run anywhere)
# ---------------------------------------------------------------------------


def test_name_score_exact_is_one():
    assert name_score("Acme LLC", "Acme LLC") == 1.0


def test_name_score_token_sort_handles_inverted_order():
    # "Coates, Lamar" and "Lamar Coates" must score very high.
    assert name_score("Coates, Lamar", "Lamar Coates") >= 0.90


def test_name_score_case_and_whitespace_insensitive():
    assert name_score("  Acme LLC ", "acme llc") == 1.0


def test_name_score_low_for_unrelated():
    assert name_score("Acme LLC", "Totally Different Corp") < 0.6


def test_best_name_match_above_threshold():
    cands = [{"name": "Acme LLC"}, {"name": "Other Co"}]
    m = best_name_match("Acme LLC", cands, threshold=0.90)
    assert m is not None
    assert m.record["name"] == "Acme LLC"
    assert m.score == 1.0


def test_best_name_match_below_threshold_returns_none():
    cands = [{"name": "Other Co"}]
    assert best_name_match("Acme LLC", cands, threshold=0.90) is None


def test_best_name_match_picks_highest():
    cands = [{"name": "Acme LLC"}, {"name": "Acme LLC Inc"}]
    m = best_name_match("Acme LLC", cands, threshold=0.85)
    assert m is not None
    assert m.record["name"] == "Acme LLC"  # exact beats the longer near-match


def test_passes_gate_thresholds():
    assert passes_gate(1.0, "dedup_key")
    assert passes_gate(0.95, "fein")
    assert not passes_gate(0.94, "fein")
    assert passes_gate(0.90, "fuzzy_name_commercial")
    assert not passes_gate(0.89, "fuzzy_name_commercial")


# ---------------------------------------------------------------------------
# pipeline fuzzy name match (lazy import; runs in the container)
# ---------------------------------------------------------------------------


class _FakeEspo:
    def __init__(self, list_rows):
        self._rows = list_rows
        self.calls = []

    def get(self, entity, params=None):
        self.calls.append((entity, params))
        return {"list": self._rows}


def test_pipeline_fuzzy_match_links_strong_candidate():
    from hermes.sync.pipeline import _fuzzy_find_espo_account

    espo = _FakeEspo([{"id": "a1", "name": "Acme LLC"}])
    match = _fuzzy_find_espo_account(espo, "Acme LLC", threshold=0.90)
    assert match is not None
    assert match["id"] == "a1"
    assert match["_score"] == 1.0


def test_pipeline_fuzzy_match_rejects_below_threshold():
    from hermes.sync.pipeline import _fuzzy_find_espo_account

    espo = _FakeEspo([{"id": "a1", "name": "Unrelated Corp"}])
    assert _fuzzy_find_espo_account(espo, "Acme LLC", threshold=0.90) is None


def test_pipeline_fuzzy_match_handles_empty_results():
    from hermes.sync.pipeline import _fuzzy_find_espo_account

    espo = _FakeEspo([])
    assert _fuzzy_find_espo_account(espo, "Acme LLC", threshold=0.90) is None


# ---------------------------------------------------------------------------
# bidirectional search-before-insert (lazy import; runs in the container)
# ---------------------------------------------------------------------------


class _FakeNC:
    def __init__(self, results):
        self._results = results
        self.searched = False

    def search_insured(self, **kw):
        self.searched = True
        return self._results


def test_search_before_insert_links_exact_databaseid():
    from hermes.sync.bidirectional import _search_before_insert

    nc = _FakeNC([{"DatabaseId": "777", "commercialName": "Acme LLC"}])
    nc_id = _search_before_insert(nc, {"CommercialName": "Acme LLC", "DatabaseId": "777"})
    assert nc_id == "777"


def test_search_before_insert_links_strong_name_match():
    from hermes.sync.bidirectional import _search_before_insert

    nc = _FakeNC([{"DatabaseId": "42", "commercialName": "Acme LLC"}])
    assert _search_before_insert(nc, {"CommercialName": "Acme LLC"}) == "42"


def test_search_before_insert_returns_none_when_no_match():
    from hermes.sync.bidirectional import _search_before_insert

    nc = _FakeNC([{"DatabaseId": "42", "commercialName": "Unrelated Corp"}])
    assert _search_before_insert(nc, {"CommercialName": "Acme LLC"}) is None


def test_search_before_insert_returns_none_when_no_candidates():
    from hermes.sync.bidirectional import _search_before_insert

    nc = _FakeNC([])
    assert _search_before_insert(nc, {"CommercialName": "Acme LLC"}) is None


def test_search_before_insert_email_exact_match_links():
    from hermes.sync.bidirectional import _search_before_insert

    nc = _FakeNC([{"DatabaseId": "9", "commercialName": "Different", "EMail": "x@y.com"}])
    nc_id = _search_before_insert(nc, {"CommercialName": "Acme LLC", "EMail": "x@y.com"})
    assert nc_id == "9"
