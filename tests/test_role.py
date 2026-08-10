"""HERMES_ROLE credential + service gating."""

from __future__ import annotations

import pytest

from hermes_app.role import (
    ROLE_FINANCE_READOUT,
    ROLE_MIRROR_READER,
    ROLE_WRITE_IN,
    assert_role_config,
    assert_role_credentials,
    current_role,
    default_role_for_service,
    modules_for,
    nowcerts_creds_present,
)


def test_default_roles_by_service() -> None:
    assert default_role_for_service("all") == ROLE_WRITE_IN
    assert default_role_for_service("hub") == ROLE_WRITE_IN
    assert default_role_for_service("finance") == ROLE_FINANCE_READOUT
    assert default_role_for_service("intake") == ROLE_MIRROR_READER
    assert default_role_for_service("renewals") == ROLE_MIRROR_READER
    assert default_role_for_service("carriers") == ROLE_MIRROR_READER


def test_explicit_role_wins(monkeypatch) -> None:
    monkeypatch.setenv("HERMES_ROLE", "finance_readout")
    monkeypatch.setenv("HERMES_SERVICE", "finance")
    assert current_role("finance") == ROLE_FINANCE_READOUT


def test_unknown_role_exits(monkeypatch) -> None:
    monkeypatch.setenv("HERMES_ROLE", "superuser")
    with pytest.raises(SystemExit, match="unknown HERMES_ROLE"):
        current_role("all")


def test_nowcerts_creds_api_key() -> None:
    assert nowcerts_creds_present({"NOWCERTS_API_KEY": "k"}) is True
    assert nowcerts_creds_present({"NOWCERTS_USERNAME": "u", "NOWCERTS_PASSWORD": "p"}) is True
    assert nowcerts_creds_present({"NOWCERTS_USERNAME": "u"}) is False
    assert nowcerts_creds_present({}) is False


def test_write_in_requires_creds() -> None:
    with pytest.raises(SystemExit, match="requires NowCerts"):
        assert_role_credentials(ROLE_WRITE_IN, env={})


def test_finance_forbids_creds() -> None:
    with pytest.raises(SystemExit, match="must not hold NowCerts"):
        assert_role_credentials(
            ROLE_FINANCE_READOUT,
            env={"NOWCERTS_USERNAME": "u", "NOWCERTS_PASSWORD": "p"},
        )


def test_mirror_reader_forbids_creds() -> None:
    with pytest.raises(SystemExit, match="must not hold NowCerts"):
        assert_role_credentials(ROLE_MIRROR_READER, env={"NOWCERTS_API_KEY": "k"})


def test_illegal_role_service_combo() -> None:
    with pytest.raises(SystemExit, match="cannot serve"):
        assert_role_config(
            "hub",
            env={"HERMES_ROLE": "finance_readout"},
            enforce_credentials=False,
        )


def test_modules_for_mirror_reader_are_service_scoped() -> None:
    assert modules_for(ROLE_MIRROR_READER, "renewals") == ("hermes.routers.renewals",)
    assert modules_for(ROLE_FINANCE_READOUT, "finance") == ("hermes.routers.finance",)


def test_finance_app_has_no_ams_push_routes(monkeypatch) -> None:
    monkeypatch.setenv("HERMES_ROLE", "finance_readout")
    monkeypatch.setenv("HERMES_SERVICE", "finance")
    monkeypatch.delenv("NOWCERTS_API_KEY", raising=False)
    monkeypatch.delenv("NOWCERTS_USERNAME", raising=False)
    monkeypatch.delenv("NOWCERTS_PASSWORD", raising=False)

    from hermes.services import create_app

    paths = {r.path for r in create_app("finance").routes}
    assert any(p.startswith("/api/commission") for p in paths)
    assert not any("push-to-ams" in p for p in paths)
    assert "/api/hermes/sync-health" not in paths


def test_healthz_on_finance(monkeypatch) -> None:
    monkeypatch.setenv("HERMES_ROLE", "finance_readout")
    monkeypatch.setenv("HERMES_SERVICE", "finance")
    monkeypatch.delenv("NOWCERTS_API_KEY", raising=False)
    monkeypatch.delenv("NOWCERTS_USERNAME", raising=False)
    monkeypatch.delenv("NOWCERTS_PASSWORD", raising=False)

    from fastapi.testclient import TestClient

    from hermes.services import create_app

    r = TestClient(create_app("finance")).get("/healthz")
    assert r.status_code == 200
    body = r.json()
    assert body["role"] == ROLE_FINANCE_READOUT
    assert body["nowcerts"] is False
    assert body["db_user"] == "hermes_finance"
    assert body["mirror_lag_seconds"] is None
    assert "finance" in body["modules_loaded"]
