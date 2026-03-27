"""Tests for haute.cli._deploy — the ``haute deploy`` command."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import pytest

from haute.cli import cli

if TYPE_CHECKING:
    from click.testing import CliRunner


def _make_toml(tmp_path: Path) -> None:
    (tmp_path / "haute.toml").write_text(
        '[project]\nname = "t"\npipeline = "main.py"\n'
        '[deploy]\nmodel_name = "test-model"\nendpoint_name = "test-ep"\n'
        '[test_quotes]\ndir = "tests/quotes"\n',
    )


def _mock_resolved() -> MagicMock:
    """Build a mock ResolvedDeploy."""
    resolved = MagicMock()
    resolved.pruned_graph.nodes = [MagicMock(), MagicMock()]
    resolved.pruned_graph.edges = [MagicMock()]
    resolved.removed_node_ids = ["sink1"]
    resolved.artifacts = {"model.cbm": Path("model.cbm")}
    resolved.input_node_ids = ["quotes"]
    resolved.output_node_id = "output"
    resolved.input_schema = {"VehPower": "Int64"}
    resolved.output_schema = {"premium": "Float64"}
    return resolved


class TestDeploy:
    def test_non_ci_non_dry_run_blocked(
        self,
        runner: CliRunner,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Deploys must go through CI/CD unless --dry-run is used."""
        monkeypatch.chdir(tmp_path)
        _make_toml(tmp_path)
        monkeypatch.delenv("CI", raising=False)
        monkeypatch.delenv("TF_BUILD", raising=False)
        monkeypatch.delenv("GITLAB_CI", raising=False)

        result = runner.invoke(cli, ["deploy"])
        assert result.exit_code == 1
        assert "ci/cd" in result.output.lower() or "dry-run" in result.output.lower()

    def test_dry_run_skips_actual_deploy(
        self,
        runner: CliRunner,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.chdir(tmp_path)
        _make_toml(tmp_path)

        resolved = _mock_resolved()

        with (
            patch("haute.deploy._config.resolve_config", return_value=resolved),
            patch("haute.deploy._validators.validate_deploy", return_value=[]),
            patch("haute.deploy._validators.score_test_quotes", return_value=[]),
        ):
            result = runner.invoke(cli, ["deploy", "--dry-run"])

        assert result.exit_code == 0, result.output
        assert "dry run" in result.output.lower()

    def test_resolution_failure(
        self,
        runner: CliRunner,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.chdir(tmp_path)
        _make_toml(tmp_path)

        with patch("haute.deploy._config.resolve_config", side_effect=ValueError("No output node")):
            result = runner.invoke(cli, ["deploy", "--dry-run"])

        assert result.exit_code == 1
        assert "resolution failed" in result.output.lower() or "no output" in result.output.lower()

    def test_validation_failure(
        self,
        runner: CliRunner,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.chdir(tmp_path)
        _make_toml(tmp_path)

        resolved = _mock_resolved()

        with (
            patch("haute.deploy._config.resolve_config", return_value=resolved),
            patch("haute.deploy._validators.validate_deploy", return_value=["Missing artifact"]),
        ):
            result = runner.invoke(cli, ["deploy", "--dry-run"])

        assert result.exit_code == 1
        assert "validation failed" in result.output.lower()

    def test_test_quote_failure_blocks_deploy(
        self,
        runner: CliRunner,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.chdir(tmp_path)
        _make_toml(tmp_path)

        resolved = _mock_resolved()
        tq_results = [
            {"file": "ok.json", "rows": 5, "status": "ok", "time_ms": 10, "error": None},
            {
                "file": "bad.json",
                "rows": 0,
                "status": "error",
                "time_ms": 5,
                "error": "schema mismatch",
            },
        ]

        with (
            patch("haute.deploy._config.resolve_config", return_value=resolved),
            patch("haute.deploy._validators.validate_deploy", return_value=[]),
            patch("haute.deploy._validators.score_test_quotes", return_value=tq_results),
        ):
            result = runner.invoke(cli, ["deploy", "--dry-run"])

        assert result.exit_code == 1
        assert "bad.json" in result.output

    def test_deploy_success_in_ci(
        self,
        runner: CliRunner,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.chdir(tmp_path)
        _make_toml(tmp_path)
        monkeypatch.setenv("CI", "true")

        resolved = _mock_resolved()
        deploy_result = MagicMock()
        deploy_result.model_name = "test-model"
        deploy_result.model_version = 2
        deploy_result.endpoint_url = "https://host/serving-endpoints/test-ep/invocations"
        deploy_result.model_uri = None

        with (
            patch("haute.deploy._config.resolve_config", return_value=resolved),
            patch("haute.deploy._validators.validate_deploy", return_value=[]),
            patch("haute.deploy._validators.score_test_quotes", return_value=[]),
            patch("haute.deploy.deploy", return_value=deploy_result),
        ):
            result = runner.invoke(cli, ["deploy"])

        assert result.exit_code == 0, result.output
        assert "deployed" in result.output.lower() or "v2" in result.output
        assert "invocations" in result.output

    def test_deploy_import_error(
        self,
        runner: CliRunner,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.chdir(tmp_path)
        _make_toml(tmp_path)
        monkeypatch.setenv("CI", "true")

        resolved = _mock_resolved()

        with (
            patch("haute.deploy._config.resolve_config", return_value=resolved),
            patch("haute.deploy._validators.validate_deploy", return_value=[]),
            patch("haute.deploy._validators.score_test_quotes", return_value=[]),
            patch("haute.deploy.deploy", side_effect=ImportError("No module named 'mlflow'")),
        ):
            result = runner.invoke(cli, ["deploy"])

        assert result.exit_code == 1
        assert "missing dependency" in result.output.lower() or "mlflow" in result.output.lower()

    def test_deploy_not_implemented(
        self,
        runner: CliRunner,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.chdir(tmp_path)
        _make_toml(tmp_path)
        monkeypatch.setenv("CI", "true")

        resolved = _mock_resolved()

        with (
            patch("haute.deploy._config.resolve_config", return_value=resolved),
            patch("haute.deploy._validators.validate_deploy", return_value=[]),
            patch("haute.deploy._validators.score_test_quotes", return_value=[]),
            patch("haute.deploy.deploy", side_effect=NotImplementedError("sagemaker planned")),
        ):
            result = runner.invoke(cli, ["deploy"])

        assert result.exit_code == 1

    def test_endpoint_suffix_override(
        self,
        runner: CliRunner,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.chdir(tmp_path)
        _make_toml(tmp_path)

        resolved = _mock_resolved()

        with (
            patch("haute.deploy._config.resolve_config", return_value=resolved),
            patch("haute.deploy._validators.validate_deploy", return_value=[]),
            patch("haute.deploy._validators.score_test_quotes", return_value=[]),
        ):
            result = runner.invoke(cli, ["deploy", "--dry-run", "--endpoint-suffix", "-staging"])

        assert result.exit_code == 0, result.output
        assert "-staging" in result.output
