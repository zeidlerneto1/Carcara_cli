"""Tests for Carcará session authentication."""

from unittest.mock import MagicMock

import httpx
import pytest

from kimi_cli.auth.carcara import (
    CARCARA_LOGIN_DISABLED_MSG,
    CarcaraLoginError,
    CarcaraSession,
    _extract_phpsessid,
    _origin,
    delete_carcara_session,
    load_carcara_session,
    login_carcara,
    save_carcara_session,
)

BASE_URL = "https://carcara.sinapad.lncc.br/service/v1"


def test_origin_derives_host_root() -> None:
    assert _origin(BASE_URL) == "https://carcara.sinapad.lncc.br"
    assert _origin("http://example.com:8080/foo/bar") == "http://example.com:8080"


def _enable_login(monkeypatch) -> None:
    monkeypatch.setattr("kimi_cli.auth.carcara.CARCARA_LOGIN_ENABLED", True)


def test_session_roundtrip(tmp_path, monkeypatch) -> None:
    _enable_login(monkeypatch)
    monkeypatch.setenv("KIMI_SHARE_DIR", str(tmp_path))
    session = CarcaraSession(
        base_url=BASE_URL,
        user="alice",
        domain="LNCC",
        phpsessid="abc123",
    )
    save_carcara_session(session)

    loaded = load_carcara_session(BASE_URL)
    assert loaded is not None
    assert loaded.user == "alice"
    assert loaded.phpsessid == "abc123"
    assert loaded.domain == "LNCC"

    # Load by scanning (no explicit base_url).
    loaded_any = load_carcara_session()
    assert loaded_any is not None
    assert loaded_any.phpsessid == "abc123"


def test_delete_session(tmp_path, monkeypatch) -> None:
    _enable_login(monkeypatch)
    monkeypatch.setenv("KIMI_SHARE_DIR", str(tmp_path))
    session = CarcaraSession(
        base_url=BASE_URL,
        user="alice",
        domain="LNCC",
        phpsessid="abc123",
    )
    save_carcara_session(session)
    assert load_carcara_session(BASE_URL) is not None

    assert delete_carcara_session(BASE_URL) is True
    assert load_carcara_session(BASE_URL) is None


def test_extract_phpsessid() -> None:
    cookies = httpx.Cookies()
    cookies.set("PHPSESSID", "sess-xyz")
    cookies.set("other", "val")
    assert _extract_phpsessid(cookies) == "sess-xyz"


def test_login_carcara_success(tmp_path, monkeypatch) -> None:
    _enable_login(monkeypatch)
    monkeypatch.setenv("KIMI_SHARE_DIR", str(tmp_path))

    login_response = MagicMock()
    login_response.status_code = 200
    login_response.json.return_value = {"status": "OK"}
    cookies = httpx.Cookies()
    cookies.set("PHPSESSID", "session-42")
    login_response.cookies = cookies

    validate_response = MagicMock()
    validate_response.status_code = 200

    monkeypatch.setattr(
        "kimi_cli.auth.carcara.httpx.post",
        lambda *a, **k: login_response,
    )
    monkeypatch.setattr(
        "kimi_cli.auth.carcara.httpx.get",
        lambda *a, **k: validate_response,
    )
    session = login_carcara(BASE_URL, "alice", "secret", "LNCC")

    assert session.phpsessid == "session-42"
    assert session.user == "alice"


def test_login_carcara_bad_status(tmp_path, monkeypatch) -> None:
    _enable_login(monkeypatch)
    monkeypatch.setenv("KIMI_SHARE_DIR", str(tmp_path))

    login_response = MagicMock()
    login_response.status_code = 200
    login_response.json.return_value = {"status": "error", "message": "bad creds"}
    cookies = httpx.Cookies()
    cookies.set("PHPSESSID", "session-42")
    login_response.cookies = cookies

    monkeypatch.setattr(
        "kimi_cli.auth.carcara.httpx.post",
        lambda *a, **k: login_response,
    )
    with pytest.raises(CarcaraLoginError):
        login_carcara(BASE_URL, "alice", "wrong", "LNCC")


def test_login_carcara_missing_cookie(tmp_path, monkeypatch) -> None:
    _enable_login(monkeypatch)
    monkeypatch.setenv("KIMI_SHARE_DIR", str(tmp_path))

    login_response = MagicMock()
    login_response.status_code = 200
    login_response.json.return_value = {"status": "OK"}
    login_response.cookies = httpx.Cookies()  # no PHPSESSID

    monkeypatch.setattr(
        "kimi_cli.auth.carcara.httpx.post",
        lambda *a, **k: login_response,
    )
    with pytest.raises(CarcaraLoginError):
        login_carcara(BASE_URL, "alice", "secret", "LNCC")


# --- Disabled (dev/test) behaviour ---


def test_login_carcara_disabled_by_default(monkeypatch) -> None:
    """With the feature flag off, login must refuse without hitting the network."""
    monkeypatch.setattr(
        "kimi_cli.auth.carcara.httpx.post",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not call network")),
    )
    with pytest.raises(CarcaraLoginError) as excinfo:
        login_carcara(BASE_URL, "alice", "secret", "LNCC")
    assert CARCARA_LOGIN_DISABLED_MSG in str(excinfo.value)


def test_load_carcara_session_disabled_returns_none(tmp_path, monkeypatch) -> None:
    """Even with a persisted session, load must return None when disabled."""
    # Persist a session first (requires the flag to be on).
    _enable_login(monkeypatch)
    monkeypatch.setenv("KIMI_SHARE_DIR", str(tmp_path))
    session = CarcaraSession(
        base_url=BASE_URL,
        user="alice",
        domain="LNCC",
        phpsessid="abc123",
    )
    save_carcara_session(session)
    assert load_carcara_session(BASE_URL) is not None

    # Now turn the flag off: load must refuse to return a session.
    monkeypatch.setattr("kimi_cli.auth.carcara.CARCARA_LOGIN_ENABLED", False)
    assert load_carcara_session(BASE_URL) is None
    assert load_carcara_session() is None
