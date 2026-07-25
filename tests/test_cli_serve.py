"""Tests for haute.cli._serve — the ``haute serve`` command."""

from __future__ import annotations

import socket
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import pytest

from haute.cli import _serve as serve_mod
from haute.cli import cli

if TYPE_CHECKING:
    from click.testing import CliRunner


@pytest.fixture(autouse=True)
def _serve_port_available():
    """Keep serve-mode tests independent of the developer's local port 8000."""
    with patch("haute.cli._serve._port_is_available", return_value=True):
        yield


def _built_static_dir(tmp_path: Path) -> Path:
    """Create a complete fake frontend build (index.html + assets/).

    Prod mode now checks build completeness, not bare directory
    existence, so tests that want to reach ``uvicorn.run`` must fake
    both artefacts.
    """
    static = tmp_path / "static"
    static.mkdir()
    (static / "index.html").write_text("<html></html>")
    (static / "assets").mkdir()
    return static


class TestServe:
    def test_prod_mode_no_static_fails(
        self,
        runner: CliRunner,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Prod mode without static dir should fail."""
        monkeypatch.chdir(tmp_path)

        with (
            patch(
                "haute.cli._serve._find_frontend_dir",
                side_effect=FileNotFoundError("no frontend/ anywhere"),
            ),
            patch("haute.server.STATIC_DIR", tmp_path / "nonexistent"),
        ):
            result = runner.invoke(cli, ["serve", "--no-browser"])

        assert result.exit_code == 1
        assert "frontend" in result.output.lower() or "npm" in result.output.lower()

    def test_prod_mode_empty_static_dir_fails(
        self,
        runner: CliRunner,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """An empty static/ (fresh worktree, interrupted build) must fail fast.

        Before the completeness check, a bare directory passed the
        ``exists()`` gate and uvicorn crashed with an opaque
        ``RuntimeError`` mounting the missing ``assets/`` subdirectory.
        """
        monkeypatch.chdir(tmp_path)
        static = tmp_path / "static"
        static.mkdir()

        with (
            patch(
                "haute.cli._serve._find_frontend_dir",
                side_effect=FileNotFoundError("no frontend/ anywhere"),
            ),
            patch("haute.server.STATIC_DIR", static),
            patch("uvicorn.run") as mock_run,
        ):
            result = runner.invoke(cli, ["serve", "--no-browser"])

        assert result.exit_code == 1
        mock_run.assert_not_called()

    def test_prod_mode_source_checkout_error_names_build_steps(
        self,
        runner: CliRunner,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """In a source checkout the error must be actionable: npm steps + script."""
        monkeypatch.chdir(tmp_path)
        repo_root = tmp_path / "checkout"
        (repo_root / "frontend").mkdir(parents=True)
        (repo_root / "frontend" / "package.json").write_text("{}")

        with (
            patch(
                "haute.cli._serve._find_frontend_dir",
                side_effect=FileNotFoundError("no frontend/ anywhere"),
            ),
            patch("haute.cli._serve._source_checkout_root", return_value=repo_root),
            patch("haute.server.STATIC_DIR", tmp_path / "nonexistent"),
        ):
            result = runner.invoke(cli, ["serve", "--no-browser"])

        assert result.exit_code == 1
        assert "npm install" in result.output
        assert "npm run build" in result.output
        assert "setup-worktree.sh" in result.output
        assert str(repo_root / "frontend") in result.output

    def test_prod_mode_wheel_install_error_omits_build_steps(
        self,
        runner: CliRunner,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A wheel install with broken assets should say reinstall, not npm."""
        monkeypatch.chdir(tmp_path)

        with (
            patch(
                "haute.cli._serve._find_frontend_dir",
                side_effect=FileNotFoundError("no frontend/ anywhere"),
            ),
            patch("haute.cli._serve._source_checkout_root", return_value=None),
            patch("haute.server.STATIC_DIR", tmp_path / "nonexistent"),
        ):
            result = runner.invoke(cli, ["serve", "--no-browser"])

        assert result.exit_code == 1
        assert "reinstall" in result.output.lower()
        assert "npm" not in result.output

    def test_source_checkout_root_detection(self, tmp_path: Path) -> None:
        """Detection keys off frontend/package.json two levels above the package."""
        pkg = tmp_path / "src" / "haute"
        pkg.mkdir(parents=True)
        (pkg / "__init__.py").write_text("")
        fake_haute = MagicMock(__file__=str(pkg / "__init__.py"))

        with patch.dict("sys.modules", {"haute": fake_haute}):
            assert serve_mod._source_checkout_root() is None
            frontend = tmp_path / "frontend"
            frontend.mkdir()
            (frontend / "package.json").write_text("{}")
            assert serve_mod._source_checkout_root() == tmp_path

    def test_ipv6_loopback_uses_ipv6_probe_socket(self) -> None:
        """The port probe must match IPv6 hosts such as ``::1``."""
        assert serve_mod._socket_family_for_host("::1") == socket.AF_INET6
        assert serve_mod._socket_family_for_host("127.0.0.1") == socket.AF_INET

    def test_backend_probe_host_maps_wildcard_binds_to_loopback(self) -> None:
        """Readiness probes need a connectable host when uvicorn binds all interfaces."""
        assert serve_mod._backend_probe_host("0.0.0.0") == "127.0.0.1"
        assert serve_mod._backend_probe_host("::") == "::1"
        assert serve_mod._backend_probe_host("127.0.0.42") == "127.0.0.42"

    def test_wait_for_tcp_ready_retries_refused_connections(self) -> None:
        """The backend readiness probe should poll until a connection is accepted."""
        attempts: list[tuple[tuple[str, int], float]] = []
        sleeps: list[float] = []
        now = 0.0

        class _Connection:
            def close(self) -> None:
                pass

        def fake_connect(address: tuple[str, int], timeout: float) -> _Connection:
            attempts.append((address, timeout))
            if len(attempts) < 3:
                raise ConnectionRefusedError("not ready")
            return _Connection()

        def fake_monotonic() -> float:
            return now

        def fake_sleep(duration: float) -> None:
            nonlocal now
            sleeps.append(duration)
            now += duration

        serve_mod._wait_for_tcp_ready(
            "127.0.0.1",
            8000,
            timeout=1.0,
            poll_interval=0.1,
            connect=fake_connect,
            sleep=fake_sleep,
            monotonic=fake_monotonic,
        )

        assert [attempt[0] for attempt in attempts] == [
            ("127.0.0.1", 8000),
            ("127.0.0.1", 8000),
            ("127.0.0.1", 8000),
        ]
        assert sleeps == [0.1, 0.1]

    def test_wait_for_tcp_ready_times_out_without_fallback(self) -> None:
        """A backend that never accepts connections should not trigger a blind browser open."""
        attempts = 0
        now = 0.0

        def fake_connect(address: tuple[str, int], timeout: float) -> object:
            nonlocal attempts
            attempts += 1
            raise ConnectionRefusedError("not ready")

        def fake_monotonic() -> float:
            return now

        def fake_sleep(duration: float) -> None:
            nonlocal now
            now += duration

        with pytest.raises(TimeoutError, match="127.0.0.1:8000"):
            serve_mod._wait_for_tcp_ready(
                "127.0.0.1",
                8000,
                timeout=0.25,
                poll_interval=0.1,
                connect=fake_connect,
                sleep=fake_sleep,
                monotonic=fake_monotonic,
            )

        assert attempts > 1

    def test_wait_for_tcp_ready_fails_loudly_on_unexpected_socket_error(self) -> None:
        """Unexpected connect errors should surface instead of being treated as not-ready."""
        sleeps: list[float] = []

        def fake_connect(address: tuple[str, int], timeout: float) -> object:
            raise socket.gaierror("bad host")

        with pytest.raises(socket.gaierror):
            serve_mod._wait_for_tcp_ready(
                "bad host",
                8000,
                timeout=1.0,
                poll_interval=0.1,
                connect=fake_connect,
                sleep=sleeps.append,
                monotonic=lambda: 0.0,
            )

        assert sleeps == []

    def test_open_browser_after_backend_ready_waits_then_opens(self) -> None:
        """The dev-mode browser opener should open only after the backend probe succeeds."""
        events: list[tuple[str, object]] = []

        def fake_wait(host: str, port: int) -> None:
            events.append(("wait", (host, port)))

        def fake_open(url: str) -> None:
            events.append(("open", url))

        with (
            patch("haute.cli._serve._wait_for_tcp_ready", side_effect=fake_wait),
            patch("haute.cli._serve._open_browser", side_effect=fake_open),
        ):
            serve_mod._wait_for_backend_then_open_browser(
                "http://localhost:5173",
                "0.0.0.0",
                8000,
            )

        assert events == [
            ("wait", ("127.0.0.1", 8000)),
            ("open", "http://localhost:5173"),
        ]

    def test_open_browser_after_backend_timeout_reports_without_opening(
        self,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Expected readiness timeouts should be actionable, not raw thread tracebacks."""
        with (
            patch(
                "haute.cli._serve._wait_for_tcp_ready",
                side_effect=TimeoutError("timed out"),
            ),
            patch("haute.cli._serve._open_browser") as mock_open,
        ):
            serve_mod._wait_for_backend_then_open_browser(
                "http://localhost:5173",
                "127.0.0.1",
                8000,
            )

        mock_open.assert_not_called()
        assert "browser was not opened automatically" in capsys.readouterr().err

    def test_prod_mode_with_static_dir(
        self,
        runner: CliRunner,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Prod mode should serve from built static directory."""
        monkeypatch.chdir(tmp_path)
        static = _built_static_dir(tmp_path)

        with (
            patch(
                "haute.cli._serve._find_frontend_dir",
                side_effect=FileNotFoundError("no frontend/ anywhere"),
            ),
            patch("haute.server.STATIC_DIR", static),
            patch("uvicorn.run") as mock_run,
        ):
            result = runner.invoke(cli, ["serve", "--no-browser"])

        assert result.exit_code == 0, result.output
        mock_run.assert_called_once()

    def test_custom_host_and_port(
        self,
        runner: CliRunner,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Custom ``--host`` / ``--port`` flow through to uvicorn.

        The pre-flight port-availability check is patched to always
        succeed so the test doesn't depend on port 9000 being free on
        the host machine — we only want to verify the CLI flags reach
        :func:`uvicorn.run`, not exercise real socket binding.
        """
        monkeypatch.chdir(tmp_path)
        static = _built_static_dir(tmp_path)

        with (
            patch(
                "haute.cli._serve._find_frontend_dir",
                side_effect=FileNotFoundError("no frontend/ anywhere"),
            ),
            patch("haute.cli._serve._port_is_available", return_value=True),
            patch("haute.server.STATIC_DIR", static),
            patch("uvicorn.run") as mock_run,
        ):
            result = runner.invoke(
                cli,
                ["serve", "--no-browser", "--host", "127.0.0.42", "--port", "9000"],
            )

        assert result.exit_code == 0, result.output
        call_kwargs = mock_run.call_args.kwargs
        assert call_kwargs["host"] == "127.0.0.42"
        assert call_kwargs["port"] == 9000

    def test_dev_mode_starts_vite_and_uvicorn(
        self,
        runner: CliRunner,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Dev mode should start Vite subprocess + uvicorn."""
        monkeypatch.chdir(tmp_path)
        fe = tmp_path / "frontend"
        fe.mkdir()
        (fe / "package.json").write_text("{}")
        (fe / "node_modules").mkdir()

        mock_proc = MagicMock()

        with (
            patch("haute.cli._serve._find_frontend_dir", return_value=fe),
            patch("subprocess.Popen", return_value=mock_proc) as mock_popen,
            patch("uvicorn.run"),
            patch("signal.signal"),
        ):
            runner.invoke(cli, ["serve", "--no-browser"])

        mock_popen.assert_called_once()
        cmd = mock_popen.call_args[0][0]
        # cmd may be a string (shell=True) or list (shell=False)
        if isinstance(cmd, str):
            assert "npm" in cmd, f"Expected 'npm' in command string: {cmd}"
        else:
            # On Windows, shutil.which("npm") returns the full path
            # e.g. "C:\Program Files\nodejs\npm.cmd"
            npm_basename = Path(cmd[0]).name.lower()
            assert npm_basename.startswith("npm"), f"Expected npm as first arg, got: {cmd}"

    def test_dev_mode_opens_browser_after_backend_is_ready_when_flag_not_set(
        self,
        runner: CliRunner,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Dev mode should not use a blind timer before opening the browser."""
        monkeypatch.chdir(tmp_path)
        fe = tmp_path / "frontend"
        fe.mkdir()
        (fe / "package.json").write_text("{}")
        (fe / "node_modules").mkdir()

        mock_proc = MagicMock()

        with (
            patch("haute.cli._serve._find_frontend_dir", return_value=fe),
            patch("subprocess.Popen", return_value=mock_proc),
            patch("uvicorn.run"),
            patch("signal.signal"),
            patch("threading.Timer") as timer_cls,
            patch("haute.cli._serve._open_browser_after_backend_ready") as ready_open,
        ):
            result = runner.invoke(cli, ["serve", "--host", "127.0.0.1", "--port", "8765"])

        assert result.exit_code == 0, result.output
        ready_open.assert_called_once_with("http://localhost:5173", "127.0.0.1", 8765)
        timer_cls.assert_not_called()

    def test_prod_mode_opens_browser_when_flag_not_set(
        self,
        runner: CliRunner,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Prod mode without --no-browser should schedule _open_browser."""
        monkeypatch.chdir(tmp_path)
        static = _built_static_dir(tmp_path)

        mock_timer = MagicMock()

        with (
            patch(
                "haute.cli._serve._find_frontend_dir",
                side_effect=FileNotFoundError("no frontend/ anywhere"),
            ),
            patch("haute.server.STATIC_DIR", static),
            patch("uvicorn.run"),
            patch("threading.Timer", return_value=mock_timer) as timer_cls,
        ):
            result = runner.invoke(cli, ["serve"])

        assert result.exit_code == 0, result.output
        timer_cls.assert_called_once()
        # Timer should target host:port for prod mode
        call_args = timer_cls.call_args
        assert call_args[0][0] == 1.5
        assert "8000" in str(call_args)
        mock_timer.start.assert_called_once()

    def test_prod_mode_custom_host_port_browser_url(
        self,
        runner: CliRunner,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Prod mode browser URL should reflect custom host/port."""
        monkeypatch.chdir(tmp_path)
        static = _built_static_dir(tmp_path)

        mock_timer = MagicMock()

        with (
            patch(
                "haute.cli._serve._find_frontend_dir",
                side_effect=FileNotFoundError("no frontend/ anywhere"),
            ),
            patch("haute.server.STATIC_DIR", static),
            patch("uvicorn.run"),
            patch("threading.Timer", return_value=mock_timer) as timer_cls,
        ):
            result = runner.invoke(cli, ["serve", "--host", "127.0.0.42", "--port", "9999"])

        assert result.exit_code == 0, result.output
        call_kwargs = timer_cls.call_args
        assert "127.0.0.42" in str(call_kwargs) and "9999" in str(call_kwargs)

    def test_dev_mode_cleanup_terminates_vite(
        self,
        runner: CliRunner,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When uvicorn.run raises, the vite process should be terminated in the finally block."""
        monkeypatch.chdir(tmp_path)
        fe = tmp_path / "frontend"
        fe.mkdir()
        (fe / "package.json").write_text("{}")
        (fe / "node_modules").mkdir()

        mock_proc = MagicMock()

        with (
            patch("haute.cli._serve._find_frontend_dir", return_value=fe),
            patch("subprocess.Popen", return_value=mock_proc),
            patch("uvicorn.run", side_effect=KeyboardInterrupt),
            patch("signal.signal"),
        ):
            runner.invoke(cli, ["serve", "--no-browser"])

        # The finally block should have called terminate on the vite process
        mock_proc.terminate.assert_called()

    def test_find_frontend_dir_found(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """_find_frontend_dir returns a path when frontend/package.json exists."""
        from haute.cli._helpers import _find_frontend_dir

        fe = tmp_path / "frontend"
        fe.mkdir()
        (fe / "package.json").write_text("{}")
        monkeypatch.chdir(tmp_path)

        result = _find_frontend_dir()
        assert result is not None
        assert result.name == "frontend"

    def test_find_frontend_dir_not_found(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """_find_frontend_dir raises FileNotFoundError when no frontend/ exists.

        Per codebase-review #80 the "missing frontend" signal is made
        explicit via an exception rather than a silent ``None`` return,
        so each caller decides whether a missing frontend is an error
        (dev-only commands) or a fall-through (``serve`` → prod mode).
        """
        from haute.cli._helpers import _find_frontend_dir

        monkeypatch.chdir(tmp_path)

        with pytest.raises(FileNotFoundError):
            _find_frontend_dir()

    def test_dev_mode_echoes_dev_info(
        self,
        runner: CliRunner,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Dev mode should print helpful dev mode information."""
        monkeypatch.chdir(tmp_path)
        fe = tmp_path / "frontend"
        fe.mkdir()
        (fe / "package.json").write_text("{}")
        (fe / "node_modules").mkdir()

        mock_proc = MagicMock()

        with (
            patch("haute.cli._serve._find_frontend_dir", return_value=fe),
            patch("subprocess.Popen", return_value=mock_proc),
            patch("uvicorn.run"),
            patch("signal.signal"),
        ):
            result = runner.invoke(cli, ["serve", "--no-browser"])

        assert "[dev]" in result.output.lower()
        assert "vite" in result.output.lower()
        assert "5173" in result.output
        assert "8000" in result.output
