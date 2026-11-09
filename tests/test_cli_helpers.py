"""Tests for haute.cli._helpers — shared CLI utilities."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import click
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
# resolve_model_name
# ---------------------------------------------------------------------------


class TestResolveModelName:
    def test_environment_precedes_toml(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from haute.cli._helpers import resolve_model_name

        toml_path = tmp_path / "haute.toml"
        toml_path.write_text(
            '[project]\nname = "project"\n[deploy]\nmodel_name = "from-toml"\n',
            encoding="utf-8",
        )
        monkeypatch.setenv("HAUTE_MODEL_NAME", "from-env")

        assert resolve_model_name(None, toml_path) == "from-env"

    def test_cli_precedes_environment(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from haute.cli._helpers import resolve_model_name

        monkeypatch.setenv("HAUTE_MODEL_NAME", "from-env")

        assert resolve_model_name("from-cli", None) == "from-cli"


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
# Note: ``_load_deploy_config`` was removed in Phase 5 Wave 9B (#131) and
# replaced by :meth:`DeployConfig.from_toml` / :meth:`DeployConfig.from_cli_args`
# which are exercised by tests/test_cli_architecture.py.
#
# Note: ``resolve_pipeline_file`` moved to :mod:`haute._project` in Phase 5
# Wave 9B (#129) with simpler semantics:  ``None`` → ``<cwd>/main.py``,
# directory → ``<dir>/main.py``, missing path → ``FileNotFoundError``.  The
# new contract is pinned by tests/test_cli_architecture.py.
# ---------------------------------------------------------------------------


# ── D14: resolve_transport helper ──────────────────────────────────


class TestResolveTransport:
    """Tests for cli._helpers.resolve_transport()."""

    def _make_config(
        self,
        target: str = "databricks",
        staging_url: str = "",
        prod_url: str = "",
    ) -> MagicMock:
        config = MagicMock()
        config.target = target
        config.ci.staging_endpoint_url = staging_url
        config.ci.production_endpoint_url = prod_url
        return config

    def test_databricks_target(self) -> None:
        from haute.cli._helpers import resolve_transport

        config = self._make_config(target="databricks")
        info = resolve_transport(config)
        assert info.kind == "databricks"
        assert info.staging_url == ""
        assert info.prod_url == ""

    def test_container_target_with_staging_url(self) -> None:
        from haute.cli._helpers import resolve_transport

        config = self._make_config(
            target="container",
            staging_url="http://staging:8080/quote",
            prod_url="http://prod:8080/quote",
        )
        info = resolve_transport(config)
        assert info.kind == "http"
        assert info.staging_url == "http://staging:8080/quote"
        assert info.prod_url == "http://prod:8080/quote"

    def test_azure_container_apps_target(self) -> None:
        from haute.cli._helpers import resolve_transport

        config = self._make_config(
            target="azure-container-apps",
            staging_url="http://staging:8080/quote",
        )
        info = resolve_transport(config)
        assert info.kind == "http"

    def test_aws_ecs_target(self) -> None:
        from haute.cli._helpers import resolve_transport

        config = self._make_config(
            target="aws-ecs",
            staging_url="http://staging:8080/quote",
        )
        info = resolve_transport(config)
        assert info.kind == "http"

    def test_gcp_run_target(self) -> None:
        from haute.cli._helpers import resolve_transport

        config = self._make_config(
            target="gcp-run",
            staging_url="http://staging:8080/quote",
        )
        info = resolve_transport(config)
        assert info.kind == "http"

    def test_container_target_missing_staging_url_exits(self) -> None:
        from haute.cli._helpers import resolve_transport

        config = self._make_config(target="container", staging_url="")
        with pytest.raises(click.ClickException, match="Set \\[ci.staging\\] endpoint_url"):
            resolve_transport(config)

    def test_unsupported_target(self) -> None:
        from haute.cli._helpers import resolve_transport

        config = self._make_config(target="sagemaker")
        info = resolve_transport(config)
        assert info.kind == "unsupported"
