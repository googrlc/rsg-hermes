"""Tests for the TTS endpoint in hermes-api."""

import os
from unittest import mock

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("HERMES_API_TOKEN", "")
    monkeypatch.setenv("HERMES_TALK_ROOM_BOSS", "room-test")


def test_tts_empty_text_rejected():
    """Empty text should return 400."""
    from hermes.api import app

    client = TestClient(app)
    resp = client.post("/api/hermes/tts", json={"text": ""})
    assert resp.status_code in (400, 422)


def test_tts_no_talk_room_rejected(monkeypatch):
    """Without a Talk room the endpoint should fail before audio gen."""
    monkeypatch.delenv("HERMES_TALK_ROOM_BOSS", raising=False)
    from hermes.api import app

    client = TestClient(app)
    resp = client.post("/api/hermes/tts", json={"text": "hello"})
    assert resp.status_code in (400, 503)


def test_tts_success_files_clip_and_posts_talk_link(monkeypatch):
    """Happy path: audio is filed to Nextcloud and linked in a Talk message."""
    from hermes.api import app

    mock_audio = b"FAKE_MP3_DATA"

    async def _fake_generate(*args, **kwargs):
        return mock_audio

    nc = mock.MagicMock()
    nc.is_configured.return_value = True
    nc.file_document.return_value = {"path": "Internal/Voice/hermes_voice.mp3", "url": "https://nc/x"}

    with mock.patch("hermes.api._generate_tts_audio", side_effect=_fake_generate):
        with mock.patch("hermes.integrations.nextcloud_client.NextcloudClient", return_value=nc):
            client = TestClient(app)
            resp = client.post("/api/hermes/tts", json={"text": "test message"})
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert body["ok"] is True and body["room"] == "room-test"

    # The clip was filed as audio, and the Talk message carries the link.
    assert nc.file_document.call_args.kwargs["content"] == mock_audio
    assert nc.file_document.call_args.kwargs["content_type"] == "audio/mpeg"
    room, message = nc.post_talk_message.call_args.args
    assert room == "room-test"
    assert "https://nc/x" in message and "test message" in message


def test_tts_generation_failure_returns_502(monkeypatch):
    """When TTS generation fails, should return 502."""
    from hermes.api import app

    async def _fail_generate(*args, **kwargs):
        return None

    with mock.patch("hermes.api._generate_tts_audio", side_effect=_fail_generate):
        client = TestClient(app)
        resp = client.post("/api/hermes/tts", json={"text": "test"})
        assert resp.status_code == 502
