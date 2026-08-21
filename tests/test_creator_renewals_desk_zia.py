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
    assert "Related_Deal or Deal_Id is not empty" in prompt
    assert "Dismissed is false" in prompt


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


PALETTE = {
    "#F6F7F9",
    "#FFFFFF",
    "#F9FAFB",
    "#1F2937",
    "#5B6472",
    "#D9DEE7",
    "#E8F0FE",
    "#DCEAF7",
    "#245A86",
    "#E3F0E7",
    "#2F6B4F",
    "#FFF1D6",
    "#8A5A12",
    "#FBE4E6",
    "#9B3A43",
}


def test_desk_palette_tokens():
    css = (DESK / "pages" / "desk.css").read_text().upper()
    html = (DESK / "pages" / "desk.html").read_text().upper()
    for token in PALETTE:
        assert token in css, token
        assert token in html, token
    desk = (DESK / "pages" / "desk.html").read_text()
    assert 'scope="col">Type</th>' in desk
    assert 'type-pill commercial' in desk
    assert 'type-pill personal' in desk
    assert "In review" in desk
    assert "Contacted" in desk
    assert "Nearing deadline" in desk
    assert "Overdue" in desk
    assert "status-pill attention" in desk
    assert "status-pill overdue" in desk


def test_catalyst_app_js_uses_desk_palette():
    app = (DESK / "catalyst" / "App.js").read_text()
    assert 'import "./desk.css"' in app
    assert 'from "./operating"' in app
    assert "type-pill" in app
    assert "Commercial" in app
    assert "Personal" in app
    assert "status-pill attention" in app
    assert "status-pill overdue" in app
    assert "NowCerts" in app
    assert "Related_Deal" in app
    assert "Deal_Id" in app
    assert "scorecard" in app
    assert "checkpoints" in app
    assert "Past due" in app
    assert "CRITICAL" in app
    assert "Needs verification" in app
    assert "Failed AMS" in app
    assert "Carrier download" in app
    assert "Enter in NowCerts" in app
    assert "Account Reviewed" in (DESK / "catalyst" / "operating.js").read_text()
    assert "Lost — Price" in (DESK / "catalyst" / "operating.js").read_text()
    assert "Lost to Competitor" not in (DESK / "catalyst" / "operating.js").read_text()
    assert "Step 1 of 5" not in app
    assert "This desk only shows the next step" not in app
    assert "nowcerts.com" not in app.lower()


def test_operating_js_checkpoint_keys_match_python():
    from hermes.renewals.operating import CHECKPOINTS

    js = (DESK / "catalyst" / "operating.js").read_text()
    for spec in CHECKPOINTS:
        assert f'key: "{spec.key}"' in js, spec.key


def test_cursor_api_function_never_calls_nowcerts():
    body = (DESK / "deluge" / "cursor_api.dg").read_text()
    compact = body.replace(" ", "").lower()
    assert 'op=="ping"' in compact
    assert "id.toLong()" in body
    assert "zoho.crm.getRecordById" in body
    assert "expected_result is required" in body
    assert "cannot skip desk stages" in body
    assert "insert_policy" not in compact
    assert "nowcerts.com" not in compact
