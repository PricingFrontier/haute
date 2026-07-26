"""Regression tests for the local-only browser session boundary."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.testclient import WebSocketDenialResponse
from starlette.websockets import WebSocketDisconnect

from haute._local_security import (
    SESSION_TOKEN_COOKIE,
    local_session_token,
)
from haute.server import _serve_index_html, app


@pytest.fixture()
def local_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "main.py").write_text("", encoding="utf-8")
    return TestClient(app, base_url="http://localhost:8000", raise_server_exceptions=False)


def _bootstrap(client: TestClient):
    return client.post(
        "/api/session/bootstrap",
        headers={"host": "localhost:8000", "origin": "http://localhost:8000"},
    )


def _ws_rejection_errors() -> tuple[type[Exception], ...]:
    return (WebSocketDisconnect, WebSocketDenialResponse)


def test_bootstrap_requires_explicit_matching_origin_and_sets_http_only_cookie(
    local_client: TestClient,
) -> None:
    response = _bootstrap(local_client)

    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert response.headers["cache-control"] == "no-store"
    set_cookie = response.headers["set-cookie"]
    assert SESSION_TOKEN_COOKIE in set_cookie
    assert "HttpOnly" in set_cookie
    assert "SameSite=strict" in set_cookie
    assert local_session_token() in set_cookie
    assert local_session_token() not in response.text
    non_cookie_headers = "\n".join(
        f"{key}: {value}" for key, value in response.headers.items() if key.lower() != "set-cookie"
    )
    assert local_session_token() not in non_cookie_headers


@pytest.mark.parametrize(
    "headers",
    [
        {"host": "localhost:8000"},
        {"host": "localhost:8000", "origin": "http://127.0.0.1:8000"},
        {"host": "localhost:8000", "origin": "https://attacker.example"},
    ],
)
def test_bootstrap_rejects_absent_or_mismatched_origin_without_leaking_token(
    local_client: TestClient,
    headers: dict[str, str],
) -> None:
    response = local_client.post("/api/session/bootstrap", headers=headers)

    assert response.status_code == 403
    corpus = response.text + "\n" + "\n".join(f"{k}: {v}" for k, v in response.headers.items())
    assert local_session_token() not in corpus


@pytest.mark.parametrize(
    "header",
    [
        "forwarded",
        "x-forwarded-for",
        "x-forwarded-host",
        "x-forwarded-port",
        "x-forwarded-proto",
        "x-forwarded-custom",
    ],
)
def test_forwarded_requests_fail_closed(
    local_client: TestClient,
    header: str,
) -> None:
    response = local_client.post(
        "/api/session/bootstrap",
        headers={
            "host": "localhost:8000",
            "origin": "http://localhost:8000",
            header: "for=127.0.0.1;host=localhost",
        },
    )

    assert response.status_code == 400
    assert local_session_token() not in response.text


def test_dev_preflight_cannot_bypass_exact_authority_policy(
    local_client: TestClient,
) -> None:
    response = local_client.options(
        "/api/session/bootstrap",
        headers={
            "host": "localhost:8000",
            "origin": "http://localhost:5173",
            "access-control-request-method": "POST",
        },
    )

    assert response.status_code == 403
    assert "access-control-allow-origin" not in response.headers


def test_cookie_authenticates_api_without_exposing_token_to_javascript(
    local_client: TestClient,
) -> None:
    assert _bootstrap(local_client).status_code == 200

    response = local_client.get(
        "/api/session",
        headers={
            "host": "localhost:8000",
            "origin": "http://localhost:8000",
        },
    )

    assert response.status_code == 200
    assert response.json() == {"ok": True}


def test_absent_origin_is_allowed_only_after_cookie_authentication(
    local_client: TestClient,
) -> None:
    unauthenticated = local_client.get(
        "/api/session",
        headers={"host": "localhost:8000", "cookie": ""},
    )
    assert unauthenticated.status_code == 403

    assert _bootstrap(local_client).status_code == 200
    authenticated = local_client.get(
        "/api/session",
        headers={"host": "localhost:8000"},
    )
    assert authenticated.status_code == 200


def test_websocket_uses_cookie_and_never_needs_a_query_token(local_client: TestClient) -> None:
    assert _bootstrap(local_client).status_code == 200
    session_cookie = local_client.cookies.get(SESSION_TOKEN_COOKIE)

    with local_client.websocket_connect(
        "/ws/sync",
        headers={
            "host": "localhost:8000",
            "origin": "http://localhost:8000",
            "cookie": f"{SESSION_TOKEN_COOKIE}={session_cookie}",
        },
    ):
        pass


def test_websocket_rejects_absent_origin_even_with_valid_cookie(local_client: TestClient) -> None:
    assert _bootstrap(local_client).status_code == 200
    session_cookie = local_client.cookies.get(SESSION_TOKEN_COOKIE)

    with pytest.raises(_ws_rejection_errors()):
        with local_client.websocket_connect(
            "/ws/sync",
            headers={
                "host": "localhost:8000",
                "cookie": f"{SESSION_TOKEN_COOKIE}={session_cookie}",
            },
        ):
            pass


def test_websocket_query_token_is_not_an_authentication_transport(
    local_client: TestClient,
) -> None:
    local_client.cookies.clear()

    with pytest.raises(_ws_rejection_errors()) as exc_info:
        with local_client.websocket_connect(
            f"/ws/sync?haute_session_token={local_session_token()}",
            headers={
                "host": "localhost:8000",
                "origin": "http://localhost:8000",
                "cookie": "",
            },
        ):
            pass

    assert local_session_token() not in str(exc_info.value)


def test_served_index_contains_no_session_secret(tmp_path: Path) -> None:
    static_dir = tmp_path / "static"
    static_dir.mkdir()
    (static_dir / "index.html").write_text(
        "<!doctype html><html><head></head><body></body></html>",
        encoding="utf-8",
    )

    response = _serve_index_html(static_dir)
    corpus = (
        response.body.decode("utf-8")
        + "\n"
        + "\n".join(f"{key}: {value}" for key, value in response.headers.items())
    )

    assert local_session_token() not in corpus
    assert "__HAUTE_SESSION_TOKEN__" not in corpus
