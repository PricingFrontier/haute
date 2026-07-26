"""Tests for haute.cli._smoke — the ``haute smoke`` command."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import pytest

from haute.cli import cli

if TYPE_CHECKING:
    from click.testing import CliRunner


def _setup_smoke_project(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    target: str = "databricks",
    staging_url: str = "",
) -> Path:
    """Set up a project with haute.toml and test quotes."""
    monkeypatch.chdir(tmp_path)
    container_section = (
        '[deploy.container]\nbase_image = "python:3.11.9-slim"\n'
        if target in {"container", "azure-container-apps", "aws-ecs", "gcp-run"}
        else ""
    )
    toml = (
        f'[project]\nname = "t"\npipeline = "main.py"\n'
        f'[deploy]\nmodel_name = "test-model"\nendpoint_name = "test-ep"\n'
        f'target = "{target}"\n'
        f"{container_section}"
        f'[test_quotes]\ndir = "tests/quotes"\n'
        f'[ci]\nprovider = "github"\n'
        f'[ci.staging]\nendpoint_suffix = "-staging"\n'
        f'endpoint_url = "{staging_url}"\n'
    )
    (tmp_path / "haute.toml").write_text(toml)

    # Create test quotes
    quotes_dir = tmp_path / "tests" / "quotes"
    quotes_dir.mkdir(parents=True)
    (quotes_dir / "basic.json").write_text(json.dumps([{"input": {"VehPower": 5, "Area": "A"}}]))
    (quotes_dir / "multi.json").write_text(
        json.dumps(
            [
                {"input": {"VehPower": 5, "Area": "A"}},
                {"input": {"VehPower": 10, "Area": "B"}},
            ]
        )
    )
    return quotes_dir


def _ready_endpoint_mock() -> MagicMock:
    """Create a mock endpoint that appears ready."""
    mock_state = MagicMock()
    mock_state.ready = "EndpointStateReady.READY"
    mock_state.config_update = "EndpointStateConfigUpdate.NOT_UPDATING"
    mock_ep = MagicMock()
    mock_ep.state = mock_state
    return mock_ep


class TestSmokeDatabricks:
    """Databricks smoke tests.

    Mocking strategy:
    - ``WorkspaceClient`` — always mocked (external SDK, needs credentials)
    - ``time.sleep`` — always mocked (avoid real delays in tests)
    - ``_load_env`` — NOT mocked: no ``.env`` file in tmp_path, so it's a no-op
    - ``load_test_quote_file`` — NOT mocked: reads real test-quote JSON
      files created by ``_setup_smoke_project``
    """

    def test_databricks_success(
        self,
        runner: CliRunner,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _setup_smoke_project(tmp_path, monkeypatch)

        mock_ws = MagicMock()
        mock_ws.serving_endpoints.get.return_value = _ready_endpoint_mock()

        mock_response = MagicMock()
        mock_response.predictions = [{"premium": 100.0}]
        mock_ws.serving_endpoints.query.return_value = mock_response

        with patch("databricks.sdk.WorkspaceClient", return_value=mock_ws), patch("time.sleep"):
            result = runner.invoke(cli, ["smoke"])

        assert result.exit_code == 0, result.output
        assert "passed" in result.output.lower()

    def test_databricks_endpoint_not_ready_polls(
        self,
        runner: CliRunner,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Should poll until endpoint is ready."""
        _setup_smoke_project(tmp_path, monkeypatch)

        mock_ws = MagicMock()

        not_ready_state = MagicMock()
        not_ready_state.ready = "PENDING"
        not_ready_state.config_update = None
        not_ready_ep = MagicMock()
        not_ready_ep.state = not_ready_state

        mock_ws.serving_endpoints.get.side_effect = [not_ready_ep, _ready_endpoint_mock()]

        mock_response = MagicMock()
        mock_response.predictions = [{"premium": 100.0}]
        mock_ws.serving_endpoints.query.return_value = mock_response

        with patch("databricks.sdk.WorkspaceClient", return_value=mock_ws), patch("time.sleep"):
            result = runner.invoke(cli, ["smoke"])

        assert result.exit_code == 0, result.output
        # Polling occurred: endpoint.get() called twice (once PENDING, once READY)
        assert mock_ws.serving_endpoints.get.call_count == 2

    def test_databricks_query_failure(
        self,
        runner: CliRunner,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Endpoint query failure should fail smoke test."""
        _setup_smoke_project(tmp_path, monkeypatch)

        mock_ws = MagicMock()
        mock_ws.serving_endpoints.get.return_value = _ready_endpoint_mock()
        mock_ws.serving_endpoints.query.side_effect = RuntimeError("500 Internal Server Error")

        with patch("databricks.sdk.WorkspaceClient", return_value=mock_ws), patch("time.sleep"):
            result = runner.invoke(cli, ["smoke"])

        assert result.exit_code == 1
        assert "failed" in result.output.lower()

    def test_databricks_null_predictions(
        self,
        runner: CliRunner,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Endpoint returns no predictions -> failure."""
        _setup_smoke_project(tmp_path, monkeypatch)

        mock_ws = MagicMock()
        mock_ws.serving_endpoints.get.return_value = _ready_endpoint_mock()

        mock_response = MagicMock()
        mock_response.predictions = None
        mock_ws.serving_endpoints.query.return_value = mock_response

        with patch("databricks.sdk.WorkspaceClient", return_value=mock_ws), patch("time.sleep"):
            result = runner.invoke(cli, ["smoke"])

        assert result.exit_code == 1

    def test_endpoint_suffix_override(
        self,
        runner: CliRunner,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _setup_smoke_project(tmp_path, monkeypatch)

        mock_ws = MagicMock()
        mock_ws.serving_endpoints.get.return_value = _ready_endpoint_mock()

        mock_response = MagicMock()
        mock_response.predictions = [{"premium": 100.0}]
        mock_ws.serving_endpoints.query.return_value = mock_response

        with patch("databricks.sdk.WorkspaceClient", return_value=mock_ws), patch("time.sleep"):
            result = runner.invoke(cli, ["smoke", "--endpoint-suffix", "-canary"])

        assert result.exit_code == 0, result.output
        # Verify the suffix was used in the endpoint name lookup
        call_args = mock_ws.serving_endpoints.get.call_args
        assert "-canary" in str(call_args)


class TestSmokeHttp:
    def test_http_rejects_endpoint_suffix(
        self,
        runner: CliRunner,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _setup_smoke_project(
            tmp_path,
            monkeypatch,
            target="container",
            staging_url="http://localhost:8080/quote",
        )

        with patch("haute.cli._smoke._smoke_http") as smoke_http:
            result = runner.invoke(cli, ["smoke", "--endpoint-suffix", "-canary"])

        assert result.exit_code == 1
        assert "only supported for databricks" in result.output.lower()
        assert "endpoint_url" in result.output
        smoke_http.assert_not_called()

    def test_http_success(
        self,
        runner: CliRunner,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _setup_smoke_project(
            tmp_path, monkeypatch, target="container", staging_url="http://localhost:8080/quote"
        )

        with patch("haute.cli._smoke._smoke_http", return_value=True):
            result = runner.invoke(cli, ["smoke"])

        assert result.exit_code == 0, result.output
        assert "passed" in result.output.lower()

    def test_http_health_failure(
        self,
        runner: CliRunner,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _setup_smoke_project(
            tmp_path, monkeypatch, target="container", staging_url="http://localhost:8080/quote"
        )

        with patch("haute.cli._smoke._smoke_http", return_value=False):
            result = runner.invoke(cli, ["smoke"])

        assert result.exit_code == 1
        assert "failed" in result.output.lower()

    def test_http_no_staging_url_fails(
        self,
        runner: CliRunner,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _setup_smoke_project(tmp_path, monkeypatch, target="container", staging_url="")
        result = runner.invoke(cli, ["smoke"])
        assert result.exit_code == 1
        assert "staging" in result.output.lower()


class TestSmokeDatabricksEdgeCases:
    """Edge cases for Databricks smoke tests."""

    def test_databricks_sdk_import_error(
        self,
        runner: CliRunner,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Missing databricks-sdk should fail with install instructions."""
        _setup_smoke_project(tmp_path, monkeypatch)

        with patch(
            "databricks.sdk.WorkspaceClient",
            side_effect=ImportError("No module named 'databricks.sdk'"),
        ):
            # Need to also make the import inside _smoke_databricks fail
            import builtins

            real_import = builtins.__import__

            def mock_import(name, *args, **kwargs):
                if name == "databricks.sdk":
                    raise ImportError("No module named 'databricks.sdk'")
                return real_import(name, *args, **kwargs)

            monkeypatch.setattr(builtins, "__import__", mock_import)
            result = runner.invoke(cli, ["smoke"])

        assert result.exit_code == 1
        assert "databricks-sdk" in result.output.lower() or "databricks" in result.output.lower()

    def test_databricks_endpoint_timeout(
        self,
        runner: CliRunner,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Endpoint that never becomes ready should timeout."""
        _setup_smoke_project(tmp_path, monkeypatch)

        mock_ws = MagicMock()

        not_ready_state = MagicMock()
        not_ready_state.ready = "PENDING"
        not_ready_state.config_update = "IN_PROGRESS"
        not_ready_ep = MagicMock()
        not_ready_ep.state = not_ready_state

        # Always return not-ready — the function loops max_wait/poll_interval = 60 times
        mock_ws.serving_endpoints.get.return_value = not_ready_ep

        with patch("databricks.sdk.WorkspaceClient", return_value=mock_ws), patch("time.sleep"):
            result = runner.invoke(cli, ["smoke"])

        assert result.exit_code == 1
        assert "not ready" in result.output.lower() or "minutes" in result.output.lower()

    def test_databricks_endpoint_get_error_fails_without_retry(
        self,
        runner: CliRunner,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Only the SDK's NotFound signal is retryable."""
        _setup_smoke_project(tmp_path, monkeypatch)

        mock_ws = MagicMock()

        # A transport/auth-style error must surface immediately.
        mock_ws.serving_endpoints.get.side_effect = [
            RuntimeError("endpoint not found"),
            _ready_endpoint_mock(),
        ]

        mock_response = MagicMock()
        mock_response.predictions = [{"premium": 200.0}]
        mock_ws.serving_endpoints.query.return_value = mock_response

        with patch("databricks.sdk.WorkspaceClient", return_value=mock_ws), patch("time.sleep"):
            result = runner.invoke(cli, ["smoke"])

        assert result.exit_code == 1, result.output
        assert mock_ws.serving_endpoints.get.call_count == 1
        assert "could not check databricks endpoint" in result.output.lower()

    def test_databricks_multiple_predictions(
        self,
        runner: CliRunner,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Multiple predictions should report count correctly."""
        _setup_smoke_project(tmp_path, monkeypatch)

        mock_ws = MagicMock()
        mock_ws.serving_endpoints.get.return_value = _ready_endpoint_mock()

        mock_response = MagicMock()
        mock_response.predictions = [{"premium": 100.0}, {"premium": 200.0}]
        mock_ws.serving_endpoints.query.return_value = mock_response

        with patch("databricks.sdk.WorkspaceClient", return_value=mock_ws), patch("time.sleep"):
            result = runner.invoke(cli, ["smoke"])

        assert result.exit_code == 0, result.output
        assert "2 predictions" in result.output


class TestSmokeUnsupportedTarget:
    def test_unsupported_target_exits_non_zero(
        self,
        runner: CliRunner,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.chdir(tmp_path)
        (tmp_path / "haute.toml").write_text(
            '[project]\nname = "t"\npipeline = "main.py"\n'
            '[deploy]\nmodel_name = "m"\nendpoint_name = "e"\ntarget = "sagemaker"\n'
            '[test_quotes]\ndir = "tests/quotes"\n',
        )
        quotes_dir = tmp_path / "tests" / "quotes"
        quotes_dir.mkdir(parents=True)
        (quotes_dir / "test.json").write_text(json.dumps([{"input": {"x": 1}}]))

        result = runner.invoke(cli, ["smoke"])
        assert result.exit_code == 1
        assert "error" in result.output.lower()
        assert "smoke" in result.output.lower()
        assert "sagemaker" in result.output.lower()
        assert "not supported" in result.output.lower()
