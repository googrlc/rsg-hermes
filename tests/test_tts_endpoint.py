"""Tests for the TTS endpoint in hermes-api."""

import os
from unittest import mock

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("HERMES_API_TOKEN", "")


def test_tts_empty_text_rejected():
    """Empty text should return 400."""
    from hermes.api import app

    client = TestClient(app)
    resp = client.post("/api/hermes/tts", json={"text": ""})
    assert resp.status_code in (400, 422)


def test_tts_no_longer_depends_on_slack(monkeypatch):
    """Slack is retired. The endpoint must work with no Slack env at all —
    it used to 503 here, which meant TTS was dead the moment Slack went."""
    monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)
    monkeypatch.delenv("HERMES_SENTINEL_SLACK_CHANNEL", raising=False)
    from hermes.api import app

    async def _fake_generate(*args, **kwargs):
        return b"FAKE_MP3_DATA"

    with mock.patch("hermes.api._generate_tts_audio", side_effect=_fake_generate):
        resp = TestClient(app).post("/api/hermes/tts", json={"text": "hello"})
    assert resp.status_code == 200
    assert resp.content == b"FAKE_MP3_DATA"


def test_tts_returns_the_audio(monkeypatch):
    """Happy path: the endpoint hands back the mp3 rather than uploading it."""
    from hermes.api import app

    mock_audio = b"FAKE_MP3_DATA"

    async def _fake_generate(*args, **kwargs):
        return mock_audio

    with mock.patch("hermes.api._generate_tts_audio", side_effect=_fake_generate):
        resp = TestClient(app).post("/api/hermes/tts", json={"text": "test message"})
    assert resp.status_code == 200
    assert resp.content == mock_audio
    assert resp.headers["content-type"] == "audio/mpeg"
    assert resp.headers["x-hermes-chars"] == "12"


def test_no_slack_client_helper_remains():
    """Guards the removal: nothing should reintroduce a Slack upload path."""
    import hermes.api as api_mod
    assert not hasattr(api_mod, "_get_slack_web_client")


def test_tts_generation_failure_returns_502(monkeypatch):
    """When TTS generation fails, should return 502."""
    from hermes.api import app

    async def _fail_generate(*args, **kwargs):
        return None

    with mock.patch("hermes.api._generate_tts_audio", side_effect=_fail_generate):
        client = TestClient(app)
        resp = client.post("/api/hermes/tts", json={"text": "test"})
        assert resp.status_code == 502
