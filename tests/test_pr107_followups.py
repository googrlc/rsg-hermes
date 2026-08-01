"""Tests for the PR #107 review follow-ups (GitHub issues #108–#112)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from hermes.commands.renewal_worksheet import _candidates_by_name, escape_ilike
from hermes.core.dispatcher import Dispatcher
from hermes.operations import intake_worker as w


# ------------------------------------------------- #109: escape ilike metachars

def test_escape_ilike_neutralizes_metacharacters():
    assert escape_ilike("100%") == "100\\%"
    assert escape_ilike("a_b") == "a\\_b"
    assert escape_ilike("a\\b") == "a\\\\b"
    assert escape_ilike("a*b,c") == "ab c"


def test_candidates_by_name_uses_escaped_needle():
    supa = MagicMock()
    supa.select.return_value = []
    _candidates_by_name(supa, "Ac%me_Co")
    params = supa.select.call_args.kwargs["params"]
    assert params["client_name"] == "ilike.*Ac\\%me\\_Co*"
    assert params["eligibility_state"] == "neq.excluded"


# (#111 covered the outbound processor's nowcerts guard in hermes/sync/pipeline.py,
# deleted with the NowCerts → EspoCRM pipeline.)

# ------------------------------------------------- #112: no success log after failed transition

def test_no_success_log_after_failed_completion_transition():
    supa = MagicMock()
    claimed = {
        "id": "sub-3",
        "draft_summary": {"account": {"account_name": "Z"}, "opportunities": [{"line_of_business": "BOP"}]},
        "approved_by": "lamar",
    }
    def _transition(supa, submission_id, status, **kwargs):
        if status == "complete":
            raise RuntimeError("boom")

    with patch.object(w, "_claim_next_approved", return_value=claimed), \
         patch("hermes.intake.commit.commit_draft",
               return_value={"opportunities": [], "opportunity_count": 0,
                             "intake_job_id": "j", "nextcloud_folder": None}), \
         patch("hermes.integrations.intake_submissions.transition", side_effect=_transition), \
         patch("hermes.operations.agency_intake_approval._insert_retrieval_rows", return_value={}), \
         patch.object(w, "_safe_transition_to_failed") as stf, \
         patch.object(w, "log") as logm:
        assert w.process_one_approved(supa) is True
        stf.assert_called_once()
        assert stf.call_args.kwargs["stage"] == "complete-nowcerts-intake"
        # the success log must NOT fire after a failed transition
        assert not any("committed to NowCerts" in str(c) for c in logm.info.call_args_list)


# ------------------------------------------------- #110: shared NowCerts client reuse

def test_shared_nowcerts_created_once_and_reused():
    d = Dispatcher(use_openai=False)
    with patch("hermes.integrations.nowcerts_client.NowCertsClient") as NC:
        sentinel = object()
        NC.return_value = sentinel
        a = d._get_shared_nowcerts()
        b = d._get_shared_nowcerts()
        assert a is sentinel and b is sentinel
        assert NC.call_count == 1


def test_shared_nowcerts_failure_not_cached():
    d = Dispatcher(use_openai=False)
    with patch("hermes.integrations.nowcerts_client.NowCertsClient", side_effect=RuntimeError("no creds")):
        assert d._get_shared_nowcerts() is None
    with patch("hermes.integrations.nowcerts_client.NowCertsClient") as NC:
        NC.return_value = object()
        assert d._get_shared_nowcerts() is not None  # retried, not stuck on None
