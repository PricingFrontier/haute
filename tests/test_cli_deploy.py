"""Tests for haute.cli._deploy — the ``haute deploy`` command."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import pytest

from haute.cli import cli
from haute.cli._deploy import _CI_PROVIDER_ENV_VARS
from haute.errors import DeployError

if TYPE_CHECKING:
    from click.testing import CliRunner


def _make_toml(tmp_path: Path) -> None:
    (tmp_path / "haute.toml").write_text(
        '[project]\nname = "t"\npipeline = "main.py"\n'
        '[deploy]\nmodel_name = "test-model"\nendpoint_name = "test-ep"\n'
        '[test_quotes]\ndir = "tests/quotes"\n',
    )
    (tmp_path / "main.py").write_text(
        'import haute\n\npipeline = haute.Pipeline("test")\n',
        encoding="utf-8",
    )


def _mock_resolved() -> MagicMock:
    """Build a mock ResolvedDeploy."""
    resolved = MagicMock()
    resolved.close = MagicMock()
    resolved.config.target = "databricks"
    resolved.pruned_graph.nodes = [MagicMock(), MagicMock()]
    resolved.pruned_graph.edges = [MagicMock()]
    resolved.removed_node_ids = ["sink1"]
    resolved.artifacts = {"model.cbm": Path("model.cbm")}
    resolved.input_node_ids = ["quotes"]
    resolved.output_node_id = "output"
    resolved.input_schema = {"VehPower": "Int64"}
    resolved.output_schema = {"premium": "Float64"}
    return resolved


def _clear_ci_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove every CI marker recognised by the deploy guard."""
    for var in _CI_PROVIDER_ENV_VARS:
        monkeypatch.delenv(var, raising=False)


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
        _clear_ci_env(monkeypatch)

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
        resolved.close.assert_called_once_with()

    def test_toml_pipeline_uses_shared_resolver(
        self,
        runner: CliRunner,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.chdir(tmp_path)
        _make_toml(tmp_path)

        resolved = _mock_resolved()
        resolved_path = (tmp_path / "main.py").resolve()

        with (
            patch(
                "haute.cli._deploy.resolve_pipeline_file",
                return_value=resolved_path,
            ) as resolve_pipeline,
            patch("haute.deploy._config.resolve_config", return_value=resolved) as resolve_deploy,
            patch("haute.deploy._validators.validate_deploy", return_value=[]),
            patch("haute.deploy._validators.score_test_quotes", return_value=[]),
        ):
            result = runner.invoke(cli, ["deploy", "--dry-run"])

        assert result.exit_code == 0, result.output
        resolve_pipeline.assert_called_once_with(tmp_path)
        assert resolve_deploy.call_args.args[0].pipeline_file == resolved_path

    def test_no_toml_cli_passes_a_canonical_pipeline_to_resolution(
        self,
        runner: CliRunner,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.chdir(tmp_path)
        pipeline = tmp_path / "rating.py"
        pipeline.write_text('import haute\npipeline = haute.Pipeline("rating")\n')
        resolved = _mock_resolved()

        with (
            patch("haute.deploy._config.resolve_config", return_value=resolved) as resolve_deploy,
            patch("haute.deploy._validators.validate_deploy", return_value=[]),
            patch("haute.deploy._validators.score_test_quotes", return_value=[]),
        ):
            result = runner.invoke(
                cli,
                ["deploy", "rating.py", "--model-name", "rating", "--dry-run"],
            )

        assert result.exit_code == 0, result.output
        assert resolve_deploy.call_args.args[0].pipeline_file == pipeline.resolve()

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
            patch(
                "haute.deploy._validators.validate_deploy",
                side_effect=DeployError(
                    "Deploy validation failed",
                    structural_errors=["Missing artifact"],
                    test_quote_errors=[],
                ),
            ),
        ):
            result = runner.invoke(cli, ["deploy", "--dry-run"])

        assert result.exit_code == 1
        assert "validation failed" in result.output.lower()
        resolved.close.assert_called_once_with()

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
            patch("haute.deploy.deploy_resolved", return_value=deploy_result),
        ):
            result = runner.invoke(cli, ["deploy"])

        assert result.exit_code == 0, result.output
        assert "deployed" in result.output.lower() or "v2" in result.output
        assert "invocations" in result.output

    def test_non_dry_run_resolves_once_and_ships_validated_resolved_deploy(
        self,
        runner: CliRunner,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The CLI must deploy the exact ResolvedDeploy it validated and scored."""
        monkeypatch.chdir(tmp_path)
        _make_toml(tmp_path)
        monkeypatch.setenv("CI", "true")

        validated_resolved = _mock_resolved()
        divergent_resolved = _mock_resolved()
        deploy_result = MagicMock()
        deploy_result.model_name = "test-model"
        deploy_result.model_version = 7
        deploy_result.endpoint_url = "https://host/serving-endpoints/test-ep/invocations"
        deploy_result.model_uri = None

        with (
            patch(
                "haute.deploy._config.resolve_config",
                return_value=validated_resolved,
            ) as cli_resolve,
            patch(
                "haute.deploy.resolve_config",
                return_value=divergent_resolved,
            ) as public_resolve,
            patch("haute.deploy._validators.validate_deploy") as cli_validate,
            patch("haute.deploy.validate_deploy") as public_validate,
            patch("haute.deploy._validators.score_test_quotes", return_value=[]) as mock_score,
            patch("haute.deploy.deploy_to_mlflow", return_value=deploy_result) as backend,
        ):
            result = runner.invoke(cli, ["deploy"])

        assert result.exit_code == 0, result.output
        cli_resolve.assert_called_once()
        public_resolve.assert_not_called()
        cli_validate.assert_called_once_with(validated_resolved)
        public_validate.assert_not_called()
        mock_score.assert_called_once_with(validated_resolved)
        backend.assert_called_once_with(validated_resolved)

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
            patch(
                "haute.deploy.deploy_resolved",
                side_effect=ImportError("No module named 'mlflow'"),
            ),
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
            patch(
                "haute.deploy.deploy_resolved", side_effect=NotImplementedError("sagemaker planned")
            ),
        ):
            result = runner.invoke(cli, ["deploy"])

        assert result.exit_code == 1

    def test_deploy_unexpected_runtime_error_propagates(
        self,
        runner: CliRunner,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Backend programming failures retain their original exception type."""
        monkeypatch.chdir(tmp_path)
        _make_toml(tmp_path)
        monkeypatch.setenv("CI", "true")
        resolved = _mock_resolved()

        with (
            patch("haute.deploy._config.resolve_config", return_value=resolved),
            patch("haute.deploy._validators.validate_deploy", return_value=[]),
            patch("haute.deploy._validators.score_test_quotes", return_value=[]),
            patch("haute.deploy.deploy_resolved", side_effect=RuntimeError("backend bug")),
        ):
            result = runner.invoke(cli, ["deploy"])

        assert isinstance(result.exception, RuntimeError)
        assert "Deployment failed" not in result.output

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
