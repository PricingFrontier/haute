"""Local-only host-binding behaviour for ``haute serve``."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest
from click import ClickException
from click.testing import CliRunner
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from haute._local_security import (
    DEFAULT_TRUSTED_HOSTS,
    TRUSTED_HOSTS_ENV,
    LocalSessionMiddleware,
    LocalTrustedHostMiddleware,
)
from haute.cli import cli
from haute.cli._serve import (
    ServeConfig,
    _configure_trusted_hosts,
    _http_url,
    _start_vite_subprocess,
    handle_serve,
)


@pytest.fixture(autouse=True)
def port_available() -> None:
    with patch("haute.cli._serve._port_is_available", return_value=True):
        yield


def test_cli_default_host_uses_browser_safe_localhost_authority() -> None:
    with patch("haute.cli._serve.handle_serve") as handle:
        result = CliRunner().invoke(cli, ["serve", "--no-browser"])
    assert result.exit_code == 0, result.output
    assert handle.call_args.args[0].host == "localhost"


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "127.0.0.42", "::1"])
def test_loopback_hosts_are_accepted(host: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("haute.cli._serve._detect_dev_frontend_dir", lambda: None)
    monkeypatch.setattr("haute.cli._serve._run_prod_mode", lambda config: None)
    handle_serve(ServeConfig(host=host, port=8000, no_browser=True))
    assert os.environ[TRUSTED_HOSTS_ENV].split(",") == list(
        dict.fromkeys((*DEFAULT_TRUSTED_HOSTS, host))
    )


@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.5", "8.8.8.8", "unresolved.example"])
def test_direct_handler_rejects_non_loopback_before_startup(
    host: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "haute.cli._serve._port_is_available", lambda *_: pytest.fail("port probed")
    )
    with pytest.raises(ClickException, match="only serves locally"):
        handle_serve(ServeConfig(host=host, port=8000, no_browser=True))


@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.5", "8.8.8.8", "unresolved.example"])
def test_cli_flag_rejects_before_server_start(host: str) -> None:
    runner = CliRunner()
    with (
        patch("uvicorn.run") as uvicorn_run,
        patch("haute.cli._serve._start_vite_subprocess") as vite,
    ):
        result = runner.invoke(cli, ["serve", "--no-browser", "--host", host])
    assert result.exit_code != 0
    assert "only serves locally" in result.output
    uvicorn_run.assert_not_called()
    vite.assert_not_called()


def test_config_host_rejects_before_server_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "haute.toml").write_text('[server]\nhost = "0.0.0.0"\n')
    with (
        patch("uvicorn.run") as uvicorn_run,
        patch("haute.cli._serve._start_vite_subprocess") as vite,
    ):
        result = CliRunner().invoke(cli, ["serve", "--no-browser"])
    assert result.exit_code != 0
    assert "only serves locally" in result.output
    uvicorn_run.assert_not_called()
    vite.assert_not_called()


def test_cli_loopback_flag_overrides_non_loopback_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "haute.toml").write_text('[server]\nhost = "0.0.0.0"\n')
    with patch("haute.cli._serve.handle_serve") as handle:
        result = CliRunner().invoke(
            cli,
            ["serve", "--no-browser", "--host", "127.0.0.42"],
        )
    assert result.exit_code == 0, result.output
    assert handle.call_args.args[0].host == "127.0.0.42"


def test_loopback_replaces_stale_trusted_hosts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(TRUSTED_HOSTS_ENV, "*")
    _configure_trusted_hosts(ServeConfig(host="127.0.0.42", port=8000, no_browser=True))
    assert os.environ[TRUSTED_HOSTS_ENV].split(",") == [
        *DEFAULT_TRUSTED_HOSTS,
        "127.0.0.42",
    ]
    assert "*" not in os.environ[TRUSTED_HOSTS_ENV]


def test_configured_bind_host_reaches_trusted_host_middleware(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def ok(_request):
        return PlainTextResponse("ok")

    monkeypatch.setenv(TRUSTED_HOSTS_ENV, "*")
    _configure_trusted_hosts(ServeConfig(host="127.0.0.42", port=8000, no_browser=True))
    restricted = Starlette(
        routes=[
            Route("/", ok),
            Route("/api/session/bootstrap", ok, methods=["POST"]),
        ]
    )
    restricted.add_middleware(LocalSessionMiddleware)
    restricted.add_middleware(LocalTrustedHostMiddleware)

    with TestClient(restricted, base_url="http://127.0.0.42:8000") as client:
        assert client.get("/", headers={"host": "127.0.0.42:8000"}).status_code == 200
        assert (
            client.post(
                "/api/session/bootstrap",
                headers={
                    "host": "localhost:5173",
                    "origin": "http://localhost:5173",
                },
            ).status_code
            == 200
        )
        assert client.get("/", headers={"host": "*"}).status_code == 400
        assert client.get("/", headers={"host": "192.168.1.5:8000"}).status_code == 400


def test_vite_does_not_expose_session_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("VITE_HAUTE_SESSION_TOKEN", "stale")
    with (
        patch("haute.cli._serve.ensure_local_session_token_env") as ensure_token,
        patch("haute.cli._serve.subprocess.Popen") as popen,
        patch("haute.cli._serve.signal.signal"),
    ):
        _start_vite_subprocess(
            tmp_path,
            ServeConfig(host="::1", port=8765, no_browser=True),
        )
    ensure_token.assert_called_once_with()
    assert "VITE_HAUTE_SESSION_TOKEN" not in popen.call_args.kwargs["env"]
    assert popen.call_args.kwargs["env"]["HAUTE_BACKEND_URL"] == "http://[::1]:8765"


@pytest.mark.parametrize(
    ("host", "expected"),
    [
        ("localhost", "http://localhost:8765"),
        ("127.0.0.42", "http://127.0.0.42:8765"),
        ("::1", "http://[::1]:8765"),
    ],
)
def test_local_backend_url_formats_supported_loopback_hosts(host: str, expected: str) -> None:
    assert _http_url(ServeConfig(host=host, port=8765, no_browser=True)) == expected
