"""Carcará session authentication.

The Carcará endpoint requires a ``PHPSESSID`` session cookie on every request;
without it both ``/models`` and ``/chat/completions`` return ``403 Forbidden``.

The login is a plain HTTP POST to ``{base_url}/apps/login/action/loginAction.php``
with a JSON body ``{"action":"login","user":...,"password":...,"domain":...}`` and
browser-style headers. On success the server responds with ``{"status":"OK"}`` and
sets a ``PHPSESSID`` cookie.

The session cookie is persisted in the share dir credentials folder
(``~/.kimi/credentials/carcara.json``) so it survives restarts. Only the session
id and a few display fields are stored — never the password.
"""

from __future__ import annotations

import json
import os
import tempfile
from contextlib import suppress
from pathlib import Path
from typing import Any, cast

import httpx
from pydantic import BaseModel

from kimi_cli.share import get_share_dir

# Endpoint path (relative to the base_url) that performs the login.
LOGIN_PATH = "/apps/login/action/loginAction.php"
# Default domain used by the Carcará web app.
DEFAULT_DOMAIN = "LNCC"

# Whether the Carcara login is enabled. Disabled on the dev branch because the
# login flow is still a test/experimental feature.
CARCARA_LOGIN_ENABLED = False

# Message shown when someone tries to log in while the login is disabled.
CARCARA_LOGIN_DISABLED_MSG = "Login Carcará desabilitado nesta versão de teste (branch dev)."

# Extra browser-like headers the login endpoint expects. The base browser headers
# come from CarcaraProvider.DEFAULT_HEADERS (mirrored by _carcara_browser_headers).
LOGIN_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "pt-BR,pt;q=0.7",
    "Content-Type": "application/json;charset=UTF-8",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-origin",
}


class CarcaraLoginError(Exception):
    """Raised when the Carcará login fails."""


class CarcaraSession(BaseModel):
    """A persisted Carcará session."""

    base_url: str
    user: str
    domain: str
    phpsessid: str


def _carcara_browser_headers() -> dict[str, str]:
    """Return the browser-style headers the Carcara endpoints expect."""
    from kosong.contrib.chat_provider.carcara import CarcaraProvider

    return dict(CarcaraProvider.DEFAULT_HEADERS)


def _origin(base_url: str) -> str:
    """Return the scheme+host origin of a base URL.

    The Carcará login endpoint lives at the host root
    (``/apps/login/action/loginAction.php``), not under the service base path
    (``/service/v1``), so it is derived from the origin rather than appended to
    the full base_url.
    """
    return f"{base_url.rstrip('/').split('://')[0]}://{base_url.rstrip('/').split('://')[1].split('/')[0]}"


def _credentials_dir() -> Path:
    path = get_share_dir() / "credentials"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _credentials_path(base_url: str) -> Path:
    """Return the per-endpoint credentials file path.

    The session is stored per base_url so multiple Carcará endpoints can be used.
    A safe filename is derived from the base_url host.
    """
    host = base_url.rstrip("/").split("://")[-1].split("/")[0].replace(":", "_")
    name = host or "carcara"
    return _credentials_dir() / f"carcara-{name}.json"


def _save_to_file(base_url: str, session: CarcaraSession) -> None:
    """Atomically write the session with restrictive permissions."""
    path = _credentials_path(base_url)
    fd, tmp_path = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        data = json.dumps(session.model_dump(), ensure_ascii=False).encode("utf-8")
        written = os.write(fd, data)
        if written != len(data):
            raise OSError(f"Short write: {written}/{len(data)} bytes")
        os.fsync(fd)
        os.close(fd)
        fd = -1
        with suppress(OSError):
            os.chmod(tmp_path, 0o600)
        os.replace(tmp_path, path)
    except BaseException:
        if fd >= 0:
            with suppress(OSError):
                os.close(fd)
        with suppress(OSError):
            os.unlink(tmp_path)
        raise


def _load_from_file(base_url: str) -> CarcaraSession | None:
    path = _credentials_path(base_url)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(payload, dict):
        return None
    try:
        return CarcaraSession.model_validate(payload)
    except Exception:
        return None


def login_carcara(
    base_url: str,
    user: str,
    password: str,
    domain: str = DEFAULT_DOMAIN,
    *,
    timeout: float = 30.0,
) -> CarcaraSession:
    """Perform the Carcará login and return a validated session.

    Raises:
        CarcaraLoginError: if the login or the follow-up session validation fails.
    """
    if not CARCARA_LOGIN_ENABLED:
        raise CarcaraLoginError(CARCARA_LOGIN_DISABLED_MSG)

    base_url = base_url.rstrip("/")
    origin = _origin(base_url)
    login_url = f"{origin}{LOGIN_PATH}"
    payload: dict[str, str] = {
        "action": "login",
        "user": user,
        "password": password,
        "domain": domain,
    }

    headers = dict(_carcara_browser_headers())
    headers.update(LOGIN_HEADERS)
    headers["Referer"] = f"{origin}/"
    headers["Origin"] = origin

    try:
        response = httpx.post(
            login_url,
            headers=headers,
            json=payload,
            timeout=httpx.Timeout(timeout, connect=10.0),
        )
        response.raise_for_status()
        raw = response.json()
    except httpx.HTTPStatusError as exc:
        raise CarcaraLoginError(
            f"Carcará login failed with HTTP {exc.response.status_code}: {exc.response.text[:200]}"
        ) from exc
    except httpx.HTTPError as exc:
        raise CarcaraLoginError(f"Carcará login request failed: {exc}") from exc
    except ValueError as exc:
        raise CarcaraLoginError("Carcará login returned a non-JSON response") from exc

    if str(raw.get("status", "")).lower() != "ok":
        raise CarcaraLoginError(f"Carcará login rejected: {raw}")

    phpsessid = _extract_phpsessid(response.cookies)
    if not phpsessid:
        raise CarcaraLoginError("Carcará login did not return a PHPSESSID session cookie")

    session = CarcaraSession(
        base_url=base_url,
        user=user,
        domain=domain,
        phpsessid=phpsessid,
    )

    # Validate the session by listing models (fails with 403 if the cookie is bad).
    _validate_session(session, timeout=timeout)
    return session


def _extract_phpsessid(cookies: httpx.Cookies) -> str:
    """Extract the PHPSESSID from a response's cookie jar."""
    for name, value in cookies.items():
        if name == "PHPSESSID":
            return str(value)
    return ""


def _validate_session(session: CarcaraSession, *, timeout: float) -> None:
    """Confirm the session works by hitting ``/models`` with the cookie."""
    headers = dict(_carcara_browser_headers())
    headers["Content-Type"] = "application/json"
    url = f"{session.base_url}/models"
    try:
        response = httpx.get(
            url,
            headers=headers,
            cookies={"PHPSESSID": session.phpsessid},
            timeout=httpx.Timeout(timeout, connect=10.0),
        )
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise CarcaraLoginError(
            f"Carcará session validation failed with HTTP {exc.response.status_code}"
        ) from exc
    except httpx.HTTPError as exc:
        raise CarcaraLoginError(f"Carcará session validation failed: {exc}") from exc


def save_carcara_session(session: CarcaraSession) -> None:
    """Persist a Carcará session to the credentials file."""
    try:
        _save_to_file(session.base_url, session)
    except OSError as exc:
        raise CarcaraLoginError(f"Failed to save Carcará session: {exc}") from exc


def load_carcara_session(base_url: str | None = None) -> CarcaraSession | None:
    """Load a saved Carcará session for the given base_url.

    When ``base_url`` is ``None`` the first session found in the credentials
    directory is returned (useful for the default single-endpoint setup).
    """
    if not CARCARA_LOGIN_ENABLED:
        return None
    if base_url is not None:
        return _load_from_file(base_url)
    directory = _credentials_dir()
    if not directory.exists():
        return None
    try:
        candidates = sorted(directory.glob("carcara-*.json"))
    except OSError:
        return None
    for path in candidates:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if not isinstance(payload, dict):
            continue
        try:
            return CarcaraSession.model_validate(cast(dict[str, Any], payload))
        except Exception:
            continue
    return None


def delete_carcara_session(base_url: str | None = None) -> bool:
    """Delete the saved session for ``base_url`` (or all sessions if ``None``).

    Returns ``True`` if at least one file was removed.
    """
    removed = False
    if base_url is not None:
        path = _credentials_path(base_url)
        if path.exists():
            try:
                path.unlink()
                removed = True
            except OSError as exc:
                raise CarcaraLoginError(f"Failed to delete Carcará session: {exc}") from exc
        return removed

    directory = _credentials_dir()
    if not directory.exists():
        return False
    try:
        for path in directory.glob("carcara-*.json"):
            try:
                path.unlink()
                removed = True
            except OSError:
                continue
    except OSError:
        pass
    return removed
