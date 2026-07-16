"""The intake worker's approved->complete arc commits to NowCerts+Supabase
(HERMES_INTAKE_TARGET=nowcerts, the default), not the legacy EspoCRM enqueue.
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

from hermes.operations import intake_worker as w


def test_process_one_approved_commits_to_nowcerts():
    supa = MagicMock()
    claimed = {
        "id": "sub-1",
        "draft_summary": {
            "account": {"account_name": "X LLC"},
            "opportunities": [{"line_of_business": "General Liability"}],
        },
        "approved_by": "lamar",
    }
    with patch.object(w, "_claim_next_approved", return_value=claimed), \
         patch("hermes.intake.commit.commit_draft") as commit_draft, \
         patch("hermes.integrations.intake_submissions.transition") as transition, \
         patch.dict(os.environ, {"HERMES_INTAKE_TARGET": "nowcerts"}):
        commit_draft.return_value = {
            "opportunities": [{"id": "opp-1"}], "opportunity_count": 1,
            "intake_job_id": "job-1", "nextcloud_folder": None,
        }
        assert w.process_one_approved(supa) is True
        commit_draft.assert_called_once()
        # writing -> written -> complete
        assert [c.args[2] for c in transition.call_args_list] == ["written", "complete"]


def test_legacy_espocrm_path_still_available_behind_flag():
    supa = MagicMock()
    claimed = {"id": "sub-2", "draft_summary": {"account": {"account_name": "Y"}}, "approved_by": "lamar"}
    with patch.object(w, "_claim_next_approved", return_value=claimed), \
         patch("hermes.operations.agency_intake_approval._enqueue_crm_writes",
               return_value=(["q1"], {"plan": True})) as enqueue, \
         patch("hermes.intake.commit.commit_draft") as commit_draft, \
         patch.dict(os.environ, {"HERMES_INTAKE_TARGET": "espocrm"}):
        assert w.process_one_approved(supa) is True
        enqueue.assert_called_once()
        commit_draft.assert_not_called()
