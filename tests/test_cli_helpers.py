"""Tests for haute.cli._helpers — shared CLI utilities."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# _open_browser
# ---------------------------------------------------------------------------


class TestOpenBrowser:
    """:func:`_open_browser` delegates to :mod:`webbrowser` once.

    Post codebase-review #79, the platform-dispatched cascade
    (``xdg-open`` -> ``open`` -> ``webbrowser`` -> ``webbrowser``) was
    collapsed into a single :func:`webbrowser.open` call. When the
    browser can't be launched the URL is printed to the user so they
    can click it themselves.
    """

    def test_delegates_to_webbrowser_open(self) -> None:
        """The only path is :func:`webbrowser.open` — no subprocess."""
        with (
            patch("haute.cli._helpers.webbrowser") as mock_wb,
        ):
            mock_wb.open.return_value = True

            from haute.cli._helpers import _open_browser

            _open_browser("http://localhost:8000")

            mock_wb.open.assert_called_once_with("http://localhost:8000")

    def test_failure_prints_url(self, capsys: pytest.CaptureFixture[str]) -> None:
        """When :func:`webbrowser.open` raises, the URL is printed so the
        user can paste it manually — and the exception does not escape."""
        url = "http://localhost:8000"
        with patch("haute.cli._helpers.webbrowser") as mock_wb:
            mock_wb.open.side_effect = RuntimeError("no display")

            from haute.cli._helpers import _open_browser

            _open_browser(url)  # must not raise

        captured = capsys.readouterr()
        combined = captured.out + captured.err
        assert url in combined, f"Expected URL in output. out={captured.out!r} err={captured.err!r}"

    def test_failure_returning_false_also_prints_url(
        self,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """:func:`webbrowser.open` returns ``False`` on silent failure — the
        URL must still be surfaced so the user sees it."""
        url = "http://localhost:8000"
        with patch("haute.cli._helpers.webbrowser") as mock_wb:
            mock_wb.open.return_value = False

            from haute.cli._helpers import _open_browser

            _open_browser(url)

        captured = capsys.readouterr()
        combined = captured.out + captured.err
        assert url in combined, f"Expected URL in output. out={captured.out!r} err={captured.err!r}"


# ---------------------------------------------------------------------------
# _find_frontend_dir
# ---------------------------------------------------------------------------


class TestFindFrontendDir:
    def test_found_in_cwd(self, tmp_path: Path) -> None:
        fe = tmp_path / "frontend"
        fe.mkdir()
        (fe / "package.json").write_text("{}")

        with patch("haute.cli._helpers.Path") as mock_path:
            mock_path.cwd.return_value = tmp_path

            from haute.cli._helpers import _find_frontend_dir

            result = _find_frontend_dir()

        assert result == fe

    def test_found_in_parent(self, tmp_path: Path) -> None:
        fe = tmp_path / "frontend"
        fe.mkdir()
        (fe / "package.json").write_text("{}")
        child = tmp_path / "subdir"
        child.mkdir()

        with patch("haute.cli._helpers.Path") as mock_path:
            mock_path.cwd.return_value = child

            from haute.cli._helpers import _find_frontend_dir

            result = _find_frontend_dir()

        assert result == fe

    def test_not_found_raises(self, tmp_path: Path) -> None:
        """Post codebase-review #80, a missing frontend/ raises
        :class:`FileNotFoundError` so callers make an explicit choice about
        dev-vs-prod rather than treating ``None`` as an implicit signal.
        """
        with patch("haute.cli._helpers.Path") as mock_path:
            mock_path.cwd.return_value = tmp_path

            from haute.cli._helpers import _find_frontend_dir

            with pytest.raises(FileNotFoundError):
                _find_frontend_dir()


# ---------------------------------------------------------------------------
# _load_deploy_config
# ---------------------------------------------------------------------------


class TestLoadDeployConfig:
    def test_loads_from_toml(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        (tmp_path / "haute.toml").write_text(
            '[project]\nname = "t"\npipeline = "main.py"\n[deploy]\nmodel_name = "m"\n',
        )

        from haute.cli._helpers import _load_deploy_config

        config = _load_deploy_config()
        assert config.model_name == "m"

    def test_require_toml_missing_raises(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.chdir(tmp_path)

        from haute.cli._helpers import _load_deploy_config

        with pytest.raises(SystemExit):
            _load_deploy_config(require_toml=True)

    def test_fallback_to_pipeline_file(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.chdir(tmp_path)
        (tmp_path / "my_pipeline.py").write_text("# placeholder\n")

        from haute.cli._helpers import _load_deploy_config

        config = _load_deploy_config(pipeline_file="my_pipeline.py")
        assert config.pipeline_file == Path("my_pipeline.py")
        assert config.model_name == "my_pipeline"

    def test_fallback_with_model_name(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.chdir(tmp_path)
        (tmp_path / "p.py").write_text("# placeholder\n")

        from haute.cli._helpers import _load_deploy_config

        config = _load_deploy_config(pipeline_file="p.py", model_name="custom")
        assert config.model_name == "custom"

    def test_no_toml_no_pipeline_file_raises(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.chdir(tmp_path)

        from haute.cli._helpers import _load_deploy_config

        with pytest.raises(SystemExit):
            _load_deploy_config()

    def test_no_toml_auto_discovers_pipeline(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Without toml or explicit file, _load_deploy_config should use discovery."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / "pricing.py").write_text(
            "import haute\npipeline = haute.Pipeline('p')\n",
        )

        from haute.cli._helpers import _load_deploy_config

        config = _load_deploy_config()
        assert config.pipeline_file.name == "pricing.py"
        assert config.model_name == "pricing"


# ---------------------------------------------------------------------------
# resolve_pipeline_file
# ---------------------------------------------------------------------------


class TestResolvePipelineFile:
    def test_explicit_path_returned(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.chdir(tmp_path)
        target = tmp_path / "my_pipeline.py"
        target.write_text("# pipeline\n")

        from haute.cli._helpers import resolve_pipeline_file

        result = resolve_pipeline_file(str(target))
        assert result == target

    def test_explicit_path_missing_raises(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.chdir(tmp_path)

        from haute.cli._helpers import resolve_pipeline_file

        with pytest.raises(SystemExit):
            resolve_pipeline_file("/nonexistent/pipeline.py")

    def test_toml_pipeline_key_used(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.chdir(tmp_path)
        (tmp_path / "haute.toml").write_text(
            '[project]\nname = "t"\npipeline = "custom.py"\n',
        )
        (tmp_path / "custom.py").write_text("# pipeline\n")

        from haute.cli._helpers import resolve_pipeline_file

        result = resolve_pipeline_file()
        assert result == Path("custom.py")

    def test_toml_without_pipeline_key_falls_through_to_discovery(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """haute.toml exists but has no [project].pipeline — use discovery."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / "haute.toml").write_text('[project]\nname = "t"\n')
        (tmp_path / "pricing.py").write_text(
            "import haute\npipeline = haute.Pipeline('p')\n",
        )

        from haute.cli._helpers import resolve_pipeline_file

        result = resolve_pipeline_file()
        assert result.name == "pricing.py"

    def test_discovery_finds_pipeline(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """No toml, no explicit path — discover_pipelines finds a .py file."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / "motor.py").write_text(
            "import haute\npipeline = haute.Pipeline('m')\n",
        )

        from haute.cli._helpers import resolve_pipeline_file

        result = resolve_pipeline_file()
        assert result.name == "motor.py"

    def test_no_toml_no_discovery_defaults_to_main(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """No toml, no discoverable pipelines, main.py exists — use main.py."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / "main.py").write_text("# placeholder\n")

        from haute.cli._helpers import resolve_pipeline_file

        result = resolve_pipeline_file()
        assert result == Path("main.py")

    def test_no_toml_no_discovery_no_main_raises(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """No toml, no discoverable pipelines, no main.py — SystemExit."""
        monkeypatch.chdir(tmp_path)

        from haute.cli._helpers import resolve_pipeline_file

        with pytest.raises(SystemExit):
            resolve_pipeline_file()

    def test_toml_pipeline_key_missing_file_raises(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """haute.toml points to a file that doesn't exist — SystemExit."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / "haute.toml").write_text(
            '[project]\nname = "t"\npipeline = "missing.py"\n',
        )

        from haute.cli._helpers import resolve_pipeline_file

        with pytest.raises(SystemExit):
            resolve_pipeline_file()

    def test_explicit_path_takes_priority_over_toml(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Even if haute.toml exists, explicit path wins."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / "haute.toml").write_text(
            '[project]\nname = "t"\npipeline = "toml_pipeline.py"\n',
        )
        (tmp_path / "toml_pipeline.py").write_text("# toml\n")
        (tmp_path / "explicit.py").write_text("# explicit\n")

        from haute.cli._helpers import resolve_pipeline_file

        result = resolve_pipeline_file("explicit.py")
        assert result == Path("explicit.py")
