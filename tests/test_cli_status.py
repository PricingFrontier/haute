"""Tests for haute.cli._status — the ``haute status`` command."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import pytest

from haute.cli import cli

if TYPE_CHECKING:
    from click.testing import CliRunner


@pytest.fixture()
def toml_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "haute.toml").write_text(
        '[project]\nname = "t"\npipeline = "main.py"\n[deploy]\nmodel_name = "motor-pricing"\n',
    )
    return tmp_path


class TestStatus:
    def test_no_model_name_loads_from_toml(
        self,
        runner: CliRunner,
        toml_project: Path,
    ) -> None:
        mock_info = {
            "model_name": "motor-pricing",
            "latest_version": 3,
            "latest_stage": "Production",
            "status": "READY",
            "run_id": "abc123",
        }
        with patch("haute.deploy._mlflow.get_deploy_status", return_value=mock_info):
            result = runner.invoke(cli, ["status"])
        assert result.exit_code == 0, result.output
        assert "motor-pricing" in result.output
        assert "3" in result.output
        assert "Production" in result.output

    def test_explicit_model_name(self, runner: CliRunner) -> None:
        mock_info = {
            "model_name": "custom-model",
            "latest_version": 1,
            "status": "READY",
        }
        with patch("haute.deploy._mlflow.get_deploy_status", return_value=mock_info):
            result = runner.invoke(cli, ["status", "custom-model"])
        assert result.exit_code == 0, result.output
        assert "custom-model" in result.output

    def test_version_only_flag(
        self,
        runner: CliRunner,
        toml_project: Path,
    ) -> None:
        mock_info = {"latest_version": 5}
        with patch("haute.deploy._mlflow.get_deploy_status", return_value=mock_info):
            result = runner.invoke(cli, ["status", "--version-only"])
        assert result.exit_code == 0
        # Output includes config loading message + version
        assert "5" in result.output.strip().splitlines()[-1]

    def test_model_not_found(
        self,
        runner: CliRunner,
        toml_project: Path,
    ) -> None:
        mock_info = {"status": "not_found"}
        with patch("haute.deploy._mlflow.get_deploy_status", return_value=mock_info):
            result = runner.invoke(cli, ["status"])
        assert result.exit_code == 0
        assert "not found" in result.output.lower()

    def test_mlflow_import_error(
        self,
        runner: CliRunner,
        toml_project: Path,
    ) -> None:
        """Simulated ImportError when mlflow is missing."""
        with patch(
            "haute.deploy._mlflow.get_deploy_status",
            side_effect=ImportError("No module named 'mlflow'"),
        ):
            result = runner.invoke(cli, ["status"])
        assert result.exit_code == 1
        assert "mlflow" in result.output.lower()

    def test_missing_optional_fields(
        self,
        runner: CliRunner,
        toml_project: Path,
    ) -> None:
        """Status display should handle missing optional keys gracefully."""
        mock_info = {
            "model_name": "motor-pricing",
            "latest_version": 1,
            "status": "READY",
            # no latest_stage, no run_id
        }
        with patch("haute.deploy._mlflow.get_deploy_status", return_value=mock_info):
            result = runner.invoke(cli, ["status"])
        assert result.exit_code == 0, result.output
        assert "N/A" in result.output


# ---------------------------------------------------------------------------
# B9: CLI passes catalog/schema from DeployConfig to get_deploy_status
# ---------------------------------------------------------------------------


class TestStatusPassesCatalogSchema:
    """Verify the CLI loads catalog/schema from haute.toml and forwards them."""

    def test_default_catalog_schema_from_toml(
        self,
        runner: CliRunner,
        toml_project: Path,
    ) -> None:
        """Default DatabricksConfig uses catalog='main', schema='pricing'."""
        mock_info = {
            "model_name": "motor-pricing",
            "latest_version": 1,
            "status": "READY",
        }
        mock_fn = MagicMock(return_value=mock_info)
        with patch("haute.deploy._mlflow.get_deploy_status", mock_fn):
            result = runner.invoke(cli, ["status"])
        assert result.exit_code == 0, result.output
        mock_fn.assert_called_once_with(
            "motor-pricing",
            catalog="main",
            schema="pricing",
        )

    def test_custom_catalog_schema_from_toml(
        self,
        runner: CliRunner,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Custom catalog/schema from haute.toml are forwarded correctly."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / "haute.toml").write_text(
            '[project]\nname = "t"\npipeline = "main.py"\n'
            '[deploy]\nmodel_name = "home-pricing"\n'
            '[deploy.databricks]\ncatalog = "prod_catalog"\nschema = "actuarial"\n',
        )
        mock_info = {
            "model_name": "home-pricing",
            "latest_version": 2,
            "status": "READY",
        }
        mock_fn = MagicMock(return_value=mock_info)
        with patch("haute.deploy._mlflow.get_deploy_status", mock_fn):
            result = runner.invoke(cli, ["status"])
        assert result.exit_code == 0, result.output
        mock_fn.assert_called_once_with(
            "home-pricing",
            catalog="prod_catalog",
            schema="actuarial",
        )

    def test_explicit_model_name_still_uses_toml_catalog_schema(
        self,
        runner: CliRunner,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Even with an explicit model_name arg, catalog/schema come from config."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / "haute.toml").write_text(
            '[project]\nname = "t"\npipeline = "main.py"\n'
            '[deploy]\nmodel_name = "default"\n'
            '[deploy.databricks]\ncatalog = "uc_cat"\nschema = "uc_sch"\n',
        )
        mock_info = {
            "model_name": "override-model",
            "latest_version": 1,
            "status": "READY",
        }
        mock_fn = MagicMock(return_value=mock_info)
        with patch("haute.deploy._mlflow.get_deploy_status", mock_fn):
            result = runner.invoke(cli, ["status", "override-model"])
        assert result.exit_code == 0, result.output
        mock_fn.assert_called_once_with(
            "override-model",
            catalog="uc_cat",
            schema="uc_sch",
        )

    def test_no_toml_explicit_model_uses_defaults(
        self,
        runner: CliRunner,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Without haute.toml, explicit model_name should use default catalog/schema."""
        monkeypatch.chdir(tmp_path)
        # Create a minimal pipeline file so DeployConfig.from_toml can build a config
        (tmp_path / "main.py").write_text("")
        mock_info = {
            "model_name": "my-model",
            "latest_version": 1,
            "status": "READY",
        }
        mock_fn = MagicMock(return_value=mock_info)
        with patch("haute.deploy._mlflow.get_deploy_status", mock_fn):
            result = runner.invoke(cli, ["status", "my-model"])
        assert result.exit_code == 0, result.output
        # Defaults from DatabricksConfig: catalog="main", schema="pricing"
        mock_fn.assert_called_once_with(
            "my-model",
            catalog="main",
            schema="pricing",
        )
