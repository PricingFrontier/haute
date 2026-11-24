"""Contract tests for hosted mode (haute.hosted).

Pins the boundary from both sides: stock haute must keep rejecting
platform-proxied traffic (the local posture), and the hosted boundary
must adapt exactly that traffic — without itself granting anything the
local session gate would refuse. See
specs/hosted-databricks-app/high-level.md.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.testclient import WebSocketDenialResponse
from starlette.websockets import WebSocketDisconnect

from haute._git_state import read_working_branch
from haute._local_security import DISABLE_AUTH_ENV
from haute._project_storage import PROJECT_DIR_ENV
from haute._storage_types import StorageUnavailableError
from haute.hosted import (
    DATABRICKS_APP_ENV_VARS,
    FORWARDED_USER_SCOPE_KEY,
    DatabricksAppEnvironment,
    PlatformProxyBoundary,
    app_origin,
    create_app,
    databricks_app_environment,
)
from tests._project import project_app, use_project

_APP_DIR = str(Path(__file__).resolve().parent.parent / "databricks_app")
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)
import bootstrap  # noqa: E402

# Header set observed from the real Databricks Apps proxy (see
# databricks_app/LEARNINGS.md).
_APP_HOST = "haute-demo-1234567890123456.aws.databricksapps.com"
_APP_URL = f"https://{_APP_HOST}"
_PROXY_HEADERS = {
    "host": _APP_HOST,
    "x-forwarded-for": "10.1.2.3",
    "x-forwarded-host": _APP_HOST,
    "x-forwarded-proto": "https",
    "x-forwarded-email": "someone@example.com",
}

_CONTRACT = {
    "DATABRICKS_APP_NAME": "haute-spike",
    "DATABRICKS_APP_URL": _APP_URL,
    "DATABRICKS_WORKSPACE_ID": "2112915975510064",
}
_OWN_ORIGIN = app_origin(_APP_URL)


@pytest.fixture()
def project_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    use_project(tmp_path)
    return tmp_path


@pytest.fixture()
def hosted_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    # setenv first so monkeypatch restores the pre-test state even though
    # create_app() mutates this variable directly.
    monkeypatch.setenv(DISABLE_AUTH_ENV, "0")
    for name, value in _CONTRACT.items():
        monkeypatch.setenv(name, value)


class TestEnvironmentDetection:
    def test_absent_contract_returns_none(self) -> None:
        assert databricks_app_environment(environ={}) is None

    def test_full_contract_is_parsed(self) -> None:
        environment = databricks_app_environment(environ=_CONTRACT)
        assert environment == DatabricksAppEnvironment(
            app_name="haute-spike",
            app_url=_APP_URL,
            workspace_id="2112915975510064",
        )

    @pytest.mark.parametrize("missing", DATABRICKS_APP_ENV_VARS)
    def test_partial_contract_fails_loud(self, missing: str) -> None:
        partial = {k: v for k, v in _CONTRACT.items() if k != missing}
        with pytest.raises(RuntimeError, match=missing):
            databricks_app_environment(environ=partial)

    def test_whitespace_values_count_as_absent(self) -> None:
        assert databricks_app_environment(environ=dict.fromkeys(_CONTRACT, "  ")) is None


class TestStockAppRejectsProxiedTraffic:
    """The failure mode that makes the boundary necessary at all."""

    def test_forwarded_metadata_is_rejected(self, project_dir: Path) -> None:
        client = TestClient(project_app(), raise_server_exceptions=False)
        response = client.get(
            "/api/session",
            headers={"host": "localhost", "x-forwarded-for": "10.1.2.3"},
        )
        assert response.status_code == 400
        assert "Forwarded headers are not supported" in response.text

    def test_databricksapps_host_is_rejected(self, project_dir: Path) -> None:
        client = TestClient(project_app(), raise_server_exceptions=False)
        response = client.get("/api/session", headers={"host": _PROXY_HEADERS["host"]})
        assert response.status_code == 400
        assert "Invalid host header" in response.text


def _capture_scope_app(captured: dict):
    async def capture(scope, receive, send) -> None:
        captured["scope"] = scope
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    return capture


class TestPlatformProxyBoundary:
    def test_scope_reaches_inner_app_with_loopback_identity(self) -> None:
        captured: dict = {}
        client = TestClient(
            PlatformProxyBoundary(_capture_scope_app(captured), own_origin=_OWN_ORIGIN)
        )
        assert client.get("/", headers=_PROXY_HEADERS).status_code == 204

        names = [name for name, _ in captured["scope"]["headers"]]
        assert b"forwarded" not in names
        assert not any(name.startswith(b"x-forwarded-") for name in names)
        hosts = [value for name, value in captured["scope"]["headers"] if name == b"host"]
        assert len(hosts) == 1
        host, port = hosts[0].split(b":")
        assert host == b"127.0.0.1" and port.isdigit()

    def test_forwarded_email_is_recorded_before_stripping(self) -> None:
        captured: dict = {}
        client = TestClient(
            PlatformProxyBoundary(_capture_scope_app(captured), own_origin=_OWN_ORIGIN)
        )
        client.get("/", headers=_PROXY_HEADERS)
        assert captured["scope"][FORWARDED_USER_SCOPE_KEY] == "someone@example.com"

    def test_no_forwarded_email_leaves_scope_key_absent(self) -> None:
        captured: dict = {}
        client = TestClient(
            PlatformProxyBoundary(_capture_scope_app(captured), own_origin=_OWN_ORIGIN)
        )
        client.get("/", headers={"host": "localhost"})
        assert FORWARDED_USER_SCOPE_KEY not in captured["scope"]

    def test_empty_forwarded_email_leaves_scope_key_absent(self) -> None:
        captured: dict = {}
        headers = {**_PROXY_HEADERS, "x-forwarded-email": ""}
        client = TestClient(
            PlatformProxyBoundary(_capture_scope_app(captured), own_origin=_OWN_ORIGIN)
        )
        client.get("/", headers=headers)
        assert FORWARDED_USER_SCOPE_KEY not in captured["scope"]

    def test_whitespace_forwarded_email_leaves_scope_key_absent(self) -> None:
        captured: dict = {}
        headers = {**_PROXY_HEADERS, "x-forwarded-email": "   \t  "}
        client = TestClient(
            PlatformProxyBoundary(_capture_scope_app(captured), own_origin=_OWN_ORIGIN)
        )
        client.get("/", headers=headers)
        assert FORWARDED_USER_SCOPE_KEY not in captured["scope"]

    def test_padded_forwarded_email_is_recorded_stripped(self) -> None:
        captured: dict = {}
        headers = {**_PROXY_HEADERS, "x-forwarded-email": "  someone@example.com  \t"}
        client = TestClient(
            PlatformProxyBoundary(_capture_scope_app(captured), own_origin=_OWN_ORIGIN)
        )
        client.get("/", headers=headers)
        assert captured["scope"][FORWARDED_USER_SCOPE_KEY] == "someone@example.com"

    def test_non_http_scopes_pass_through_untouched(self) -> None:
        seen: dict = {}

        async def inner(scope, receive, send) -> None:
            seen["scope"] = scope

        boundary = PlatformProxyBoundary(inner, own_origin=_OWN_ORIGIN)
        lifespan_scope = {"type": "lifespan"}

        import anyio

        async def run() -> None:
            await boundary(lifespan_scope, None, None)  # type: ignore[arg-type]

        anyio.run(run)
        assert seen["scope"] is lifespan_scope


class TestCreateApp:
    def test_refuses_to_run_outside_hosted_environment(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        for name in DATABRICKS_APP_ENV_VARS:
            monkeypatch.delenv(name, raising=False)
        with pytest.raises(RuntimeError, match="outside a recognised hosted environment"):
            create_app(Path.cwd())

    def test_serves_api_through_platform_proxy_headers(
        self,
        hosted_contract: None,
        project_dir: Path,
    ) -> None:
        client = TestClient(create_app(project_dir), raise_server_exceptions=False)
        response = client.get("/api/session", headers=_PROXY_HEADERS)
        assert response.status_code == 200
        assert response.json() == {"ok": True}

    def test_serves_websocket_through_platform_proxy_headers(
        self,
        hosted_contract: None,
        project_dir: Path,
    ) -> None:
        client = TestClient(create_app(project_dir), raise_server_exceptions=False)
        with client.websocket_connect(
            "/ws/sync",
            headers={**_PROXY_HEADERS, "origin": _APP_URL, "sec-fetch-site": "same-origin"},
        ):
            pass

    def test_a_url_that_is_not_an_origin_stops_the_app(
        self, hosted_contract: None, project_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("DATABRICKS_APP_URL", f"{_APP_URL}/editor")
        with pytest.raises(RuntimeError, match="DATABRICKS_APP_URL is not the app's origin"):
            create_app(project_dir)
        # Refused before the trust decision is recorded.
        assert os.environ[DISABLE_AUTH_ENV] == "0"


#: A POST that takes no body, which a page of another site can send without asking: an
#: unknown job's cancel, answered by its route with 404.
_NO_BODY_POST = "/api/modelling/train/cancel/no-such-job"
_CROSS_SITE_DETAIL = "A change to this Haute app comes from the app's own page."


class TestCrossSiteRefusal:
    """The hosted editor's cross-site rule (specs/sandbox-security, Trust boundary)."""

    @pytest.mark.parametrize(
        "browser_headers",
        [
            {"origin": "https://attacker.example", "sec-fetch-site": "cross-site"},
            {"origin": "https://other-app-1234567890123456.aws.databricksapps.com"},
            {"origin": "null"},
            {"origin": "http://127.0.0.1:80"},
            {"sec-fetch-site": "same-site"},
            {"origin": _APP_URL, "sec-fetch-site": "cross-site"},
        ],
        ids=["other-site", "same-site-app", "opaque", "loopback", "same-site-only", "mixed"],
    )
    def test_another_sites_write_is_refused_before_any_route(
        self, browser_headers: dict[str, str], hosted_contract: None, project_dir: Path
    ) -> None:
        client = TestClient(create_app(project_dir), raise_server_exceptions=False)
        response = client.post(_NO_BODY_POST, headers={**_PROXY_HEADERS, **browser_headers})
        assert response.status_code == 403
        assert response.json() == {"detail": _CROSS_SITE_DETAIL}

    def test_the_refused_write_never_reaches_the_wrapped_app(self) -> None:
        captured: dict = {}
        client = TestClient(
            PlatformProxyBoundary(_capture_scope_app(captured), own_origin=_OWN_ORIGIN)
        )
        response = client.delete(
            "/api/anything", headers={**_PROXY_HEADERS, "origin": "https://attacker.example"}
        )
        assert response.status_code == 403
        assert "scope" not in captured

    def test_the_apps_own_page_still_writes_and_reaches_the_route(
        self, hosted_contract: None, project_dir: Path
    ) -> None:
        client = TestClient(create_app(project_dir), raise_server_exceptions=False)
        response = client.post(
            _NO_BODY_POST,
            headers={**_PROXY_HEADERS, "origin": _APP_URL, "sec-fetch-site": "same-origin"},
        )
        assert response.status_code == 404
        assert response.json() != {"detail": _CROSS_SITE_DETAIL}

    def test_a_client_that_is_not_a_browser_passes(
        self, hosted_contract: None, project_dir: Path
    ) -> None:
        client = TestClient(create_app(project_dir), raise_server_exceptions=False)
        assert client.post(_NO_BODY_POST, headers=_PROXY_HEADERS).status_code == 404

    def test_another_sites_read_meets_the_local_origin_check(
        self, hosted_contract: None, project_dir: Path
    ) -> None:
        client = TestClient(create_app(project_dir), raise_server_exceptions=False)
        response = client.get(
            "/api/session", headers={**_PROXY_HEADERS, "origin": "https://attacker.example"}
        )
        assert response.status_code == 403
        assert response.json() == {"detail": "Origin is not trusted for the local Haute session"}

    def test_a_navigation_from_another_site_is_not_refused(self) -> None:
        captured: dict = {}
        client = TestClient(
            PlatformProxyBoundary(_capture_scope_app(captured), own_origin=_OWN_ORIGIN)
        )
        response = client.get("/", headers={**_PROXY_HEADERS, "sec-fetch-site": "cross-site"})
        assert response.status_code == 204

    def test_the_apps_own_origin_reaches_the_server_as_the_loopback_origin(self) -> None:
        captured: dict = {}
        client = TestClient(
            PlatformProxyBoundary(_capture_scope_app(captured), own_origin=_OWN_ORIGIN)
        )
        client.post("/", headers={**_PROXY_HEADERS, "origin": f"{_APP_URL}:443"})
        headers = captured["scope"]["headers"]
        host = next(value for name, value in headers if name == b"host")
        assert [value for name, value in headers if name == b"origin"] == [b"http://" + host]

    @pytest.mark.parametrize(
        "browser_headers",
        [
            {"origin": "https://attacker.example"},
            {"origin": "https://other-app-1234567890123456.aws.databricksapps.com"},
            {"origin": _APP_URL, "sec-fetch-site": "same-site"},
        ],
        ids=["other-site", "same-site-app", "same-site-header"],
    )
    def test_another_sites_socket_is_closed_before_accept(
        self, browser_headers: dict[str, str], hosted_contract: None, project_dir: Path
    ) -> None:
        client = TestClient(create_app(project_dir), raise_server_exceptions=False)
        with pytest.raises((WebSocketDisconnect, WebSocketDenialResponse)) as refused:
            with client.websocket_connect(
                "/ws/sync", headers={**_PROXY_HEADERS, **browser_headers}
            ):
                pass
        if isinstance(refused.value, WebSocketDisconnect):
            assert refused.value.code == 1008


class TestBoundaryDoesNotBypassLocalAuth:
    def test_header_rewriting_alone_grants_nothing(
        self,
        project_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Without the explicit hosted trust decision, the boundary must not
        defeat the session token gate — an anonymous proxied request stays
        403 even though its headers now look local."""
        monkeypatch.delenv(DISABLE_AUTH_ENV, raising=False)
        client = TestClient(
            PlatformProxyBoundary(project_app(), own_origin=_OWN_ORIGIN),
            raise_server_exceptions=False,
        )
        # Override the valid session cookie the suite-wide conftest fixture
        # bakes into every TestClient, so this request is truly anonymous.
        response = client.get(
            "/api/session",
            headers={**_PROXY_HEADERS, "cookie": "unrelated=1"},
        )
        assert response.status_code == 403


class TestEnsureProject:
    def test_unbound_boot_seeds_a_volatile_project_and_chdirs_into_it(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        project_dir = tmp_path / "project"
        monkeypatch.setenv(PROJECT_DIR_ENV, str(project_dir))
        monkeypatch.delenv("HAUTE_STATE_VOLUME", raising=False)

        configured_creds: list[Path] = []
        monkeypatch.setattr(
            "haute._project_storage.configure_git_credentials",
            lambda path: configured_creds.append(path),
        )
        # The bootstrap changes directory itself; monkeypatch restores it.
        monkeypatch.chdir(tmp_path)

        returned_path = bootstrap.ensure_project()

        assert returned_path == project_dir
        assert Path.cwd() == project_dir
        assert (project_dir / "haute.toml").is_file()
        assert (project_dir / "rating" / "main.py").is_file()

        assert (project_dir / ".git").is_dir()
        proc_status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=project_dir,
            capture_output=True,
            text=True,
            check=True,
        )
        assert proc_status.stdout.strip() == ""

        proc_log = subprocess.run(
            ["git", "log", "-1", "--format=%s"],
            cwd=project_dir,
            capture_output=True,
            text=True,
            check=True,
        )
        assert proc_log.stdout.strip().startswith("Seed haute project")

        # A volatile seed commits on `main` but records no working branch: the
        # session opens in the first-run chooser, exactly like a fresh local
        # project (frontend/e2e/core-flows.spec.ts "first-run chooser ...").
        assert read_working_branch(project_dir) is None
        assert configured_creds == [Path.home() / ".haute-runtime"]

    @pytest.mark.parametrize("outcome", ["restored", "present"])
    def test_a_restored_or_present_boot_never_reseeds(
        self, outcome: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        project_dir = tmp_path / "project"
        project_dir.mkdir(parents=True, exist_ok=True)
        sentinel = project_dir / "sentinel.txt"
        sentinel.write_text("survives", encoding="utf-8")

        monkeypatch.setenv(PROJECT_DIR_ENV, str(project_dir))
        monkeypatch.setattr(
            "haute._project_storage.restore_if_bound",
            lambda _dir: outcome,
        )
        monkeypatch.setattr(
            "haute._project_storage.configure_git_credentials",
            lambda _path: None,
        )
        # The bootstrap changes directory itself; monkeypatch restores it.
        monkeypatch.chdir(tmp_path)

        returned_path = bootstrap.ensure_project()

        assert returned_path == project_dir
        assert Path.cwd() == project_dir
        assert sentinel.is_file()
        assert sentinel.read_text(encoding="utf-8") == "survives"
        assert not (project_dir / "haute.toml").exists()
        assert not (project_dir / "rating").exists()
        assert not (project_dir / ".git").exists()

    def test_a_failed_restore_gates_the_boot_without_seeding(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        project_dir = tmp_path / "project"
        monkeypatch.setenv(PROJECT_DIR_ENV, str(project_dir))

        def _failing_restore(_dir: Path) -> str:
            raise StorageUnavailableError("volume down")

        monkeypatch.setattr(
            "haute._project_storage.restore_if_bound",
            _failing_restore,
        )
        monkeypatch.setattr(
            "haute._project_storage.configure_git_credentials",
            lambda _path: None,
        )
        # The bootstrap changes directory itself; monkeypatch restores it.
        monkeypatch.chdir(tmp_path)

        with pytest.raises(StorageUnavailableError, match="volume down"):
            bootstrap.ensure_project()

        if project_dir.exists():
            assert list(project_dir.iterdir()) == []
        assert not (project_dir / "haute.toml").exists()
        assert not (project_dir / ".git").exists()
