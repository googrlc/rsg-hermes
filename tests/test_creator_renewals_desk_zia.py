"""Creator Renewals Desk Zia paste pack stays aligned with Deluge and INSTALL."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DESK = ROOT / "docs" / "zoho" / "creator-renewals-desk"


def test_zia_paste_prompt_targets_existing_app_only():
    prompt = (DESK / "ZIA_PASTE_PROMPT.md").read_text()
    assert "renewals-desk" in prompt
    assert "lamar_risksolutionsgroup668" in prompt
    assert "Do **not** create a second application" in prompt or "Do **not** create a new Creator app" in prompt
    assert "Do **not** duplicate" in prompt or "Refuse to create a second application" in prompt
    assert "never calls NowCerts" in prompt or "Creator never calls NowCerts" in prompt
    for module in (
        "Accounts",
        "Deals",
        "Policies",
        "Renewal_Events",
        "Renewals",
        "AMS_Write_Queue",
        "Tasks",
    ):
        assert module in prompt


def test_zia_prompt_embeds_deluge_scripts():
    prompt = (DESK / "ZIA_PASTE_PROMPT.md").read_text()
    deluge_dir = DESK / "deluge"
    for name in (
        "stage_guard.dg",
        "window_bucket.dg",
        "task_seed.dg",
        "ams_enqueue.dg",
        "dismiss.dg",
        "approve.dg",
    ):
        body = (deluge_dir / name).read_text()
        # Skip comment header; require a distinctive executable line.
        distinctive = next(
            line.strip()
            for line in body.splitlines()
            if line.strip() and not line.strip().startswith("//")
        )
        assert distinctive in prompt, f"{name} snippet missing from ZIA_PASTE_PROMPT.md"


def test_approve_deluge_never_calls_nowcerts():
    body = (DESK / "deluge" / "approve.dg").read_text()
    compact = body.replace(" ", "").lower()
    assert "zoho.crm.updateRecord" in body
    assert "Does NOT call NowCerts" in body
    assert 'input.get("object_type")!="renewal"' in compact
    assert "zoho.loginuserid" in body
    assert 'upd.put("Status", "queued")' in body
    assert "nowcerts.com" not in compact
    assert "insert_policy" not in compact


def test_install_opens_existing_app():
    install = (DESK / "INSTALL.md").read_text()
    assert "already exists" in install
    assert "Not a new app. Not a duplicate." in install
    assert "ZIA_PASTE_PROMPT.md" in install
    assert "deluge/approve.dg" in install
    assert "deluge/cursor_api.dg" in install


def test_cursor_api_function_never_calls_nowcerts():
    body = (DESK / "deluge" / "cursor_api.dg").read_text()
    compact = body.replace(" ", "").lower()
    assert 'op=="ping"' in compact
    assert "zoho.crm.createRecord" in body
    assert "zoho.crm.updateRecord" in body
    assert "expected_result is required" in body
    assert "cannot skip desk stages" in body
    assert "insert_policy" not in compact
    assert "nowcerts.com" not in compact
