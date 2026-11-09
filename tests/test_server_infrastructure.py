"""Server infrastructure: startup, middleware, paths, sidecars and static builds.

A server start leaves the installed package alone, the request-id middleware returns JSON 500s and
logs at the right levels, ``contained_path`` refuses escapes, ``load_sidecar`` reads sidecars, and
``static_build_ready`` gates static mounting.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from haute._project_context import current_project
from haute.errors import PathOutsideProjectError
from haute.routes._helpers import _INTERNAL_ERROR_DETAIL
from tests import _server_helpers

# Fixtures shared with the other modules split from one suite.
client = _server_helpers.client
pipeline_dir = _server_helpers.pipeline_dir


# ---------------------------------------------------------------------------
# Phase 3: Server infrastructure tests
# ---------------------------------------------------------------------------


class TestStartupLeavesTheInstalledPackageAlone:
    """A server start removes nothing under the installed package: on a site-packages
    install that would edit the environment. Python's own source-timestamp check already
    keeps an edited module from running from stale bytecode."""

    def test_the_lifespan_removes_nothing_under_the_package(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import shutil

        import haute.deploy._config as deploy_config
        import haute.server as server

        package = Path(server.__file__).resolve().parent
        removed: list[Path] = []
        monkeypatch.setattr(shutil, "rmtree", lambda path, *_a, **_k: removed.append(Path(path)))
        for step in (
            "configure_logging",
            "configure_process_high_qos",
            "configure_execution_telemetry",
            "recover_json_runtime_storage",
            "_ensure_pipeline_index",
            "_follow_pipeline_settings_chunk_rows",
            "open_dedicated_workers",
            "start_interactive_worker_pool",
            "shutdown_dedicated_workers",
            "shutdown_interactive_worker_pool",
            "stop_following_chunk_rows",
        ):
            monkeypatch.setattr(server, step, lambda *_a, **_k: None)
        monkeypatch.setattr(deploy_config, "_load_env", lambda _path: None)
        monkeypatch.setattr(server, "_artifact_stale_seconds", lambda: 86_400)

        async def idle(*_args: object) -> None:
            return None

        monkeypatch.setattr(server, "_watcher_forever", idle)
        monkeypatch.setattr(server, "_reap_stale_job_artifacts_in_background", idle)

        async def start_and_stop() -> None:
            async with server._lifespan(server.create_app(current_project())):
                pass

        asyncio.run(start_and_stop())

        assert [path for path in removed if path.resolve().is_relative_to(package)] == []


class TestMiddleware500:
    """Middleware dispatch method: exception path returns JSON 500."""

    def test_dispatch_exception_returns_json_500(self):
        """Directly test _RequestIdMiddleware.dispatch when call_next raises."""
        import asyncio
        from unittest.mock import AsyncMock, MagicMock

        from haute.server import _RequestIdMiddleware

        middleware = _RequestIdMiddleware(app=MagicMock())

        # Build a fake Request
        request = MagicMock()
        request.headers = {}
        request.method = "GET"
        request.url.path = "/api/test"

        # call_next that raises
        call_next = AsyncMock(side_effect=RuntimeError("boom"))

        loop = asyncio.new_event_loop()
        try:
            resp = loop.run_until_complete(middleware.dispatch(request, call_next))
        finally:
            loop.close()

        assert resp.status_code == 500
        import json

        body = json.loads(resp.body)
        assert body == {"detail": _INTERNAL_ERROR_DETAIL}
        assert 1 <= len(resp.headers["x-request-id"]) <= 64

    def test_request_id_header_passthrough(self, client: TestClient):
        """Middleware adds x-request-id header to successful responses."""
        resp = client.get("/api/pipelines")
        assert resp.status_code == 200
        assert "x-request-id" in resp.headers

    def test_custom_request_id_preserved(self, client: TestClient):
        """Client-supplied x-request-id is preserved in the response."""
        resp = client.get("/api/pipelines", headers={"x-request-id": "custom-123"})
        assert resp.headers["x-request-id"] == "custom-123"

    @pytest.mark.parametrize(
        "request_id",
        [
            "contains a space",
            "x" * 65,
            "line\x1fseparator",
            "-cannot-start-with-punctuation",
        ],
    )
    def test_invalid_request_id_is_replaced_before_reflection(
        self,
        client: TestClient,
        request_id: str,
    ):
        """Untrusted correlation metadata never reaches a response header."""
        if "\x1f" in request_id:
            from haute.server import _select_request_id

            selected, rejection = _select_request_id(request_id)
            assert selected != request_id
            assert rejection is not None
            return

        resp = client.get("/api/pipelines", headers={"x-request-id": request_id})
        assert resp.status_code == 200
        assert resp.headers["x-request-id"] != request_id
        assert 1 <= len(resp.headers["x-request-id"]) <= 64

    @pytest.mark.parametrize("length", [1, 64])
    def test_request_id_ascii_token_boundaries_are_preserved(
        self,
        client: TestClient,
        length: int,
    ):
        request_id = "a" * length
        resp = client.get("/api/pipelines", headers={"x-request-id": request_id})
        assert resp.headers["x-request-id"] == request_id

    def test_rejected_request_id_log_does_not_contain_rejected_value(self):
        from haute.server import _select_request_id

        rejected = "secret-" + ("z" * 200)
        selected, rejection = _select_request_id(rejected)

        assert selected != rejected
        assert rejection == {"reason": "too_long", "length": len(rejected)}
        assert rejected not in repr(rejection)


# ---------------------------------------------------------------------------
# RequestIdMiddleware logging levels
# ---------------------------------------------------------------------------


class TestMiddlewareLogging:
    @pytest.mark.parametrize(
        ("url", "expected_status", "event_name", "log_level"),
        [
            ("/api/pipelines", 200, "request_ok", "info"),
            ("/api/pipeline/nonexistent_pipeline_xyz", 404, "request_client_error", "warning"),
        ],
        ids=["2xx_logs_info", "4xx_logs_warning"],
    )
    def test_status_logs_correct_level(
        self,
        client: TestClient,
        pipeline_dir: Path,
        url: str,
        expected_status: int,
        event_name: str,
        log_level: str,
    ):
        import structlog.testing

        with structlog.testing.capture_logs() as captured:
            resp = client.get(url)

        assert resp.status_code == expected_status
        matching = [e for e in captured if e.get("event") == event_name]
        assert len(matching) >= 1
        assert matching[0]["status"] == expected_status
        assert matching[0]["log_level"] == log_level

    def test_5xx_logs_error(self):
        import asyncio
        from unittest.mock import AsyncMock, MagicMock

        import structlog.testing

        from haute.server import _RequestIdMiddleware

        middleware = _RequestIdMiddleware(app=MagicMock())
        request = MagicMock()
        request.headers = {}
        request.method = "POST"
        request.url.path = "/api/test"

        call_next = AsyncMock(side_effect=RuntimeError("boom"))

        loop = asyncio.new_event_loop()
        try:
            with structlog.testing.capture_logs() as captured:
                resp = loop.run_until_complete(middleware.dispatch(request, call_next))
        finally:
            loop.close()

        assert resp.status_code == 500
        error_events = [e for e in captured if e.get("event") == "unhandled_exception"]
        assert len(error_events) >= 1
        assert "traceback" in error_events[0]
        assert error_events[0]["log_level"] == "error"

    def test_500_response_no_stack_trace(self):
        import asyncio
        from unittest.mock import AsyncMock, MagicMock

        from haute.server import _RequestIdMiddleware

        middleware = _RequestIdMiddleware(app=MagicMock())
        request = MagicMock()
        request.headers = {}
        request.method = "GET"
        request.url.path = "/api/explode"

        call_next = AsyncMock(side_effect=ValueError("secret internal detail"))

        loop = asyncio.new_event_loop()
        try:
            resp = loop.run_until_complete(middleware.dispatch(request, call_next))
        finally:
            loop.close()

        assert resp.status_code == 500
        body = json.loads(resp.body)
        assert body == {"detail": _INTERNAL_ERROR_DETAIL}
        assert "secret internal detail" not in resp.body.decode()
        assert "Traceback" not in resp.body.decode()


# ---------------------------------------------------------------------------
# contained_path unit tests
# ---------------------------------------------------------------------------


class TestValidateSafePath:
    def test_valid_path_succeeds(self, tmp_path: Path):
        from haute._sandbox import contained_path

        result = contained_path(tmp_path, "subdir/file.txt")
        assert result == (tmp_path / "subdir" / "file.txt").resolve()

    def test_traversal_raises_403(self, tmp_path: Path):
        from haute._sandbox import contained_path

        with pytest.raises(PathOutsideProjectError):
            contained_path(tmp_path, "../../etc/passwd")

    def test_symlink_escape_raises_403(self, tmp_path: Path):

        from haute._sandbox import contained_path

        outside = tmp_path.parent / "outside_target"
        outside.mkdir(exist_ok=True)
        link = tmp_path / "escape_link"
        try:
            os.symlink(outside, link)
        except OSError:
            pytest.skip("symlink creation not supported")
        with pytest.raises(PathOutsideProjectError):
            contained_path(tmp_path, "escape_link/secret.txt")


# ---------------------------------------------------------------------------
# load_sidecar unit tests
# ---------------------------------------------------------------------------


class TestLoadSidecar:
    def test_valid_json_returns_dict(self, tmp_path: Path):
        from haute.routes._helpers import load_sidecar

        py_path = tmp_path / "pipeline.py"
        py_path.write_text("")
        sidecar = py_path.with_suffix(".haute.json")
        sidecar.write_text(json.dumps({"positions": {"a": {"x": 1, "y": 2}}}))

        result = load_sidecar(py_path)
        assert result == {"positions": {"a": {"x": 1, "y": 2}}}

    def test_missing_file_returns_empty(self, tmp_path: Path):
        from haute.routes._helpers import load_sidecar

        py_path = tmp_path / "nonexistent.py"
        result = load_sidecar(py_path)
        assert result == {}

    def test_corrupt_json_returns_empty(self, tmp_path: Path):
        from haute.routes._helpers import load_sidecar

        py_path = tmp_path / "pipeline.py"
        py_path.write_text("")
        sidecar = py_path.with_suffix(".haute.json")
        sidecar.write_text("{invalid json content!!!")

        result = load_sidecar(py_path)
        assert result == {}


# ---------------------------------------------------------------------------
# mark_self_write and is_self_write timing
# ---------------------------------------------------------------------------


class TestStaticBuildReady:
    """``static_build_ready`` gates both static mounting and ``haute serve``."""

    def test_missing_dir_is_not_ready(self, tmp_path: Path) -> None:
        from haute.server import static_build_ready

        assert not static_build_ready(tmp_path / "nonexistent")

    def test_empty_dir_is_not_ready(self, tmp_path: Path) -> None:
        """A bare directory (fresh worktree, interrupted build) must not count."""
        from haute.server import static_build_ready

        static = tmp_path / "static"
        static.mkdir()
        assert not static_build_ready(static)

    def test_index_without_assets_is_not_ready(self, tmp_path: Path) -> None:
        """Mounting a missing assets/ raises at import -- require it up front."""
        from haute.server import static_build_ready

        static = tmp_path / "static"
        static.mkdir()
        (static / "index.html").write_text("<html></html>")
        assert not static_build_ready(static)

    def test_complete_build_is_ready(self, tmp_path: Path) -> None:
        from haute.server import static_build_ready

        static = tmp_path / "static"
        static.mkdir()
        (static / "index.html").write_text("<html></html>")
        (static / "assets").mkdir()
        assert static_build_ready(static)
