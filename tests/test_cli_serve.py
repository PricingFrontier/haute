"""Tests for haute.cli._serve — the ``haute serve`` command."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import pytest

from haute.cli import cli

if TYPE_CHECKING:
    from click.testing import CliRunner


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

    def test_prod_mode_with_static_dir(
        self,
        runner: CliRunner,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Prod mode should serve from built static directory."""
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
        static = tmp_path / "static"
        static.mkdir()

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
                ["serve", "--no-browser", "--host", "0.0.0.0", "--port", "9000"],
            )

        assert result.exit_code == 0, result.output
        call_kwargs = mock_run.call_args.kwargs
        assert call_kwargs["host"] == "0.0.0.0"
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
            import os

            npm_basename = os.path.basename(cmd[0]).lower()
            assert npm_basename.startswith("npm"), f"Expected npm as first arg, got: {cmd}"

    def test_dev_mode_opens_browser_when_flag_not_set(
        self,
        runner: CliRunner,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Dev mode without --no-browser should schedule _open_browser."""
        monkeypatch.chdir(tmp_path)
        fe = tmp_path / "frontend"
        fe.mkdir()
        (fe / "package.json").write_text("{}")
        (fe / "node_modules").mkdir()

        mock_proc = MagicMock()
        mock_timer = MagicMock()

        with (
            patch("haute.cli._serve._find_frontend_dir", return_value=fe),
            patch("subprocess.Popen", return_value=mock_proc),
            patch("uvicorn.run"),
            patch("signal.signal"),
            patch("threading.Timer", return_value=mock_timer) as timer_cls,
        ):
            result = runner.invoke(cli, ["serve"])

        assert result.exit_code == 0, result.output
        timer_cls.assert_called_once()
        # Timer should target localhost:5173 for dev mode
        call_args = timer_cls.call_args
        assert call_args[0][0] == 2.0
        assert "5173" in str(call_args)
        mock_timer.start.assert_called_once()

    def test_prod_mode_opens_browser_when_flag_not_set(
        self,
        runner: CliRunner,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Prod mode without --no-browser should schedule _open_browser."""
        monkeypatch.chdir(tmp_path)
        static = tmp_path / "static"
        static.mkdir()

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
        static = tmp_path / "static"
        static.mkdir()

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
            result = runner.invoke(cli, ["serve", "--host", "0.0.0.0", "--port", "9999"])

        assert result.exit_code == 0, result.output
        call_kwargs = timer_cls.call_args
        assert "0.0.0.0" in str(call_kwargs) and "9999" in str(call_kwargs)

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
