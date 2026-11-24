"""Regression tests for the local-only browser session boundary."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import WebSocketDenialResponse
from starlette.websockets import WebSocketDisconnect

from haute._local_security import (
    DISABLE_AUTH_ENV,
    SESSION_TOKEN_COOKIE,
    LocalTrustedHostMiddleware,
    local_session_token,
    web_origin,
)
from haute.server import _serve_index_html
from tests._project import project_app, use_project


@pytest.fixture()
def local_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    use_project(tmp_path)
    (tmp_path / "main.py").write_text("", encoding="utf-8")
    return TestClient(
        project_app(), base_url="http://localhost:8000", raise_server_exceptions=False
    )


def _bootstrap(client: TestClient):
    return client.post(
        "/api/session/bootstrap",
        headers={"host": "localhost:8000", "origin": "http://localhost:8000"},
    )


def _ws_rejection_errors() -> tuple[type[Exception], ...]:
    return (WebSocketDisconnect, WebSocketDenialResponse)


def test_trusted_host_allowlist_is_enforced() -> None:
    async def ok(_request):
        return PlainTextResponse("ok")

    restricted = Starlette(routes=[Route("/", ok)])
    restricted.add_middleware(
        LocalTrustedHostMiddleware,
        allowed_hosts=["127.0.0.1"],
    )

    with TestClient(restricted, base_url="http://localhost") as client:
        assert client.get("/").status_code == 400
        assert client.get("/", headers={"host": "127.0.0.1:8123"}).status_code == 200


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


#: A POST that takes no body (an unknown job's cancel, answered by its route with 404),
#: which a page of another site can send without asking.
_NO_BODY_POST = "/api/modelling/train/cancel/no-such-job"


@pytest.fixture()
def gate_off_client(local_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv(DISABLE_AUTH_ENV, "1")
    local_client.cookies.clear()
    return local_client


def test_switching_the_session_gate_off_still_refuses_another_origins_write(
    gate_off_client: TestClient,
) -> None:
    response = gate_off_client.post(
        _NO_BODY_POST, headers={"host": "localhost:8000", "origin": "https://attacker.example"}
    )
    assert response.status_code == 403
    assert response.json() == {"detail": "Origin is not trusted for the local Haute session"}


@pytest.mark.parametrize("origin", ["http://localhost:8000", None], ids=["loopback", "absent"])
def test_switching_the_session_gate_off_drops_only_the_cookie(
    gate_off_client: TestClient, origin: str | None
) -> None:
    headers = {"host": "localhost:8000", "cookie": ""}
    if origin is not None:
        headers["origin"] = origin
    assert gate_off_client.post(_NO_BODY_POST, headers=headers).status_code == 404


def test_switching_the_session_gate_off_still_refuses_another_origins_socket(
    gate_off_client: TestClient,
) -> None:
    with pytest.raises(_ws_rejection_errors()):
        with gate_off_client.websocket_connect(
            "/ws/sync",
            headers={"host": "localhost:8000", "origin": "https://attacker.example", "cookie": ""},
        ):
            pass
    with gate_off_client.websocket_connect(
        "/ws/sync",
        headers={"host": "localhost:8000", "origin": "http://localhost:8000", "cookie": ""},
    ):
        pass


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("https://app.example", ("https", "app.example", 443)),
        ("https://APP.example:443/", ("https", "app.example", 443)),
        ("http://127.0.0.1:8000", ("http", "127.0.0.1", 8000)),
        ("null", None),
        ("https://app.example/editor", None),
        ("https://user@app.example", None),
        ("app.example", None),
        (None, None),
    ],
)
def test_web_origin_names_one_origin_however_it_is_spelled(
    value: str | None, expected: tuple[str, str, int] | None
) -> None:
    assert web_origin(value) == expected
