"""Task -> Nextcloud Talk notifications + digest."""
from __future__ import annotations

from hermes.casework import notify as T


def _fake_nc(monkeypatch, sink):
    import hermes_integrations.nextcloud_client as nc

    class Fake:
        def post_talk_message(self, token, message):
            sink.append((token, message))

    monkeypatch.setattr(nc, "NextcloudClient", lambda *a, **k: Fake())


def test_skips_without_token(monkeypatch):
    monkeypatch.delenv("NEXTCLOUD_TALK_TOKEN", raising=False)
    assert T.notify_task_created({"title": "X"}) is False


def test_posts_formatted_line(monkeypatch):
    monkeypatch.setenv("NEXTCLOUD_TALK_TOKEN", "tok1")
    posts: list = []
    _fake_nc(monkeypatch, posts)
    ok = T.notify_task_created(
        {"title": "Call client", "assigned_to_email": "g@x", "insured_name": "Acme",
         "due_at": "2026-08-01T00:00:00Z"}, kind="task")
    assert ok is True and posts and posts[0][0] == "tok1"
    msg = posts[0][1]
    assert "Call client" in msg and "Acme" in msg and "g@x" in msg and "2026-08-01" in msg


def test_swallows_errors(monkeypatch):
    monkeypatch.setenv("NEXTCLOUD_TALK_TOKEN", "tok1")
    import hermes_integrations.nextcloud_client as nc

    class Boom:
        def post_talk_message(self, *a, **k):
            raise RuntimeError("chat down")

    monkeypatch.setattr(nc, "NextcloudClient", lambda *a, **k: Boom())
    assert T.notify_task_created({"title": "X"}) is False  # never raises


def test_includes_crm_link_and_priority(monkeypatch):
    monkeypatch.setenv("NEXTCLOUD_TALK_TOKEN", "tok1")
    monkeypatch.setenv("HERMES_PORTAL_URL", "https://ws.ts.net:8447")
    posts: list = []
    _fake_nc(monkeypatch, posts)
    T.notify_task_created({"title": "Bind policy", "priority": "high"})
    msg = posts[0][1]
    assert "https://ws.ts.net:8447/" in msg   # the portal — the CRM people use
    assert "🔴 high" in msg                    # priority badge


def test_the_link_never_points_at_the_retired_cockpit(monkeypatch):
    """Every task notification carried a /cockpit link. That page no longer
    exists, and this API's own origin no longer serves a UI at all."""
    monkeypatch.setenv("NEXTCLOUD_TALK_TOKEN", "tok1")
    monkeypatch.setenv("HERMES_PORTAL_URL", "https://ws.ts.net:8447")
    monkeypatch.setenv("HERMES_PUBLIC_BASE_URL", "https://ws.ts.net:8444")
    posts: list = []
    _fake_nc(monkeypatch, posts)
    T.notify_task_created({"title": "Bind policy"})
    msg = posts[0][1]
    assert "cockpit" not in msg
    assert ":8444" not in msg


def test_no_link_without_portal_url(monkeypatch):
    monkeypatch.setenv("NEXTCLOUD_TALK_TOKEN", "tok1")
    monkeypatch.delenv("HERMES_PORTAL_URL", raising=False)
    posts: list = []
    _fake_nc(monkeypatch, posts)
    T.notify_task_created({"title": "X"})
    assert "open the CRM" not in posts[0][1]  # gracefully omits the link


def test_digest_excludes_done(monkeypatch):
    monkeypatch.setenv("NEXTCLOUD_TALK_TOKEN", "tok1")
    posts: list = []
    _fake_nc(monkeypatch, posts)

    class FakeSupa:
        def select(self, table, *, columns="*", params=None, limit=100):
            return [
                {"title": "A", "assigned_to_email": "x", "status": "not_started", "due_at": "2026-08-01"},
                {"title": "B", "status": "completed"},
            ]

    res = T.daily_task_digest(FakeSupa())
    assert res["ok"] and res["count"] == 1        # completed excluded
    assert posts and "A" in posts[0][1] and "B" not in posts[0][1]
