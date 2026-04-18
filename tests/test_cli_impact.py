"""Tests for haute.cli._impact — the ``haute impact`` command."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import polars as pl
import pytest

from haute.cli import cli

if TYPE_CHECKING:
    from click.testing import CliRunner


def _setup_impact_project(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    target: str = "databricks",
    impact_dataset: str = "data/impact.parquet",
    staging_url: str = "",
    prod_url: str = "",
) -> None:
    """Set up a tmp project with haute.toml and impact dataset."""
    monkeypatch.chdir(tmp_path)
    toml = (
        f'[project]\nname = "t"\npipeline = "main.py"\n'
        f'[deploy]\nmodel_name = "test-model"\nendpoint_name = "test-ep"\n'
        f'target = "{target}"\n'
        f'[safety]\nimpact_dataset = "{impact_dataset}"\n'
        f'[ci]\nprovider = "github"\n'
        f'[ci.staging]\nendpoint_suffix = "-staging"\n'
        f'endpoint_url = "{staging_url}"\n'
        f'[ci.production]\nendpoint_url = "{prod_url}"\n'
    )
    (tmp_path / "haute.toml").write_text(toml)
    (tmp_path / ".git").mkdir()

    # Write impact dataset
    data_dir = tmp_path / "data"
    data_dir.mkdir(exist_ok=True)
    df = pl.DataFrame(
        {
            "VehPower": [5, 6, 7],
            "Area": ["A", "B", "C"],
            "premium": [100.0, 200.0, 300.0],
        }
    )
    df.write_parquet(data_dir / "impact.parquet")


class TestImpact:
    def test_no_toml_fails(
        self,
        runner: CliRunner,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(cli, ["impact"])
        assert result.exit_code == 1
        assert "haute.toml" in result.output.lower()

    def test_no_impact_dataset_fails(
        self,
        runner: CliRunner,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.chdir(tmp_path)
        (tmp_path / "haute.toml").write_text(
            '[project]\nname = "t"\npipeline = "main.py"\n'
            '[deploy]\nmodel_name = "m"\n'
            '[safety]\nimpact_dataset = ""\n',
        )
        (tmp_path / ".git").mkdir()
        result = runner.invoke(cli, ["impact"])
        assert result.exit_code == 1
        assert "impact_dataset" in result.output.lower()

    def test_impact_dataset_file_not_found(
        self,
        runner: CliRunner,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.chdir(tmp_path)
        (tmp_path / "haute.toml").write_text(
            '[project]\nname = "t"\npipeline = "main.py"\n'
            '[deploy]\nmodel_name = "m"\n'
            '[safety]\nimpact_dataset = "missing.parquet"\n',
        )
        (tmp_path / ".git").mkdir()
        result = runner.invoke(cli, ["impact"])
        assert result.exit_code == 1
        assert "not found" in result.output.lower()

    def test_databricks_first_deploy(
        self,
        runner: CliRunner,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """First deploy: prod endpoint not found → first_deploy report."""
        _setup_impact_project(tmp_path, monkeypatch)

        staging_preds = [{"premium": 100.0}, {"premium": 200.0}, {"premium": 300.0}]

        with (
            patch("haute.cli._impact._impact_databricks") as mock_db,
            patch("haute.deploy._impact.build_report"),
            patch("haute.deploy._impact.format_terminal", return_value="Report"),
            patch("haute.deploy._impact.format_markdown", return_value="# Report"),
        ):
            mock_db.return_value = (staging_preds, [], False)
            result = runner.invoke(cli, ["impact"])

        assert result.exit_code == 0, result.output
        assert (tmp_path / "impact_report.md").exists()

    def test_databricks_comparison(
        self,
        runner: CliRunner,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Normal deploy: both endpoints reachable → comparison report."""
        _setup_impact_project(tmp_path, monkeypatch)

        staging_preds = [{"premium": 110.0}, {"premium": 210.0}, {"premium": 310.0}]
        prod_preds = [{"premium": 100.0}, {"premium": 200.0}, {"premium": 300.0}]

        with (
            patch("haute.cli._impact._impact_databricks") as mock_db,
            patch("haute.deploy._impact.build_report") as mock_report,
            patch("haute.deploy._impact.format_terminal", return_value="Report"),
            patch("haute.deploy._impact.format_markdown", return_value="# Report"),
        ):
            mock_db.return_value = (staging_preds, prod_preds, True)
            mock_report.return_value = MagicMock()
            result = runner.invoke(cli, ["impact"])

        assert result.exit_code == 0, result.output
        mock_report.assert_called_once()

    def test_container_target_no_staging_url_fails(
        self,
        runner: CliRunner,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _setup_impact_project(tmp_path, monkeypatch, target="container", staging_url="")
        result = runner.invoke(cli, ["impact"])
        assert result.exit_code == 1
        assert "staging" in result.output.lower() and "url" in result.output.lower()

    def test_container_target_success(
        self,
        runner: CliRunner,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _setup_impact_project(
            tmp_path,
            monkeypatch,
            target="container",
            staging_url="http://staging:8080/quote",
            prod_url="http://prod:8080/quote",
        )

        staging_preds = [{"premium": 110.0}]
        prod_preds = [{"premium": 100.0}]

        with (
            patch("haute.cli._impact._impact_http") as mock_http,
            patch("haute.deploy._impact.build_report") as mock_report,
            patch("haute.deploy._impact.format_terminal", return_value="Report"),
            patch("haute.deploy._impact.format_markdown", return_value="# Report"),
        ):
            mock_http.return_value = (staging_preds, prod_preds, True)
            mock_report.return_value = MagicMock()
            result = runner.invoke(cli, ["impact"])

        assert result.exit_code == 0, result.output

    def test_github_step_summary(
        self,
        runner: CliRunner,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Should write to GITHUB_STEP_SUMMARY when env var is set."""
        _setup_impact_project(tmp_path, monkeypatch)
        summary_file = tmp_path / "summary.md"
        monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary_file))

        with (
            patch("haute.cli._impact._impact_databricks") as mock_db,
            patch("haute.deploy._impact.format_terminal", return_value="Report"),
            patch("haute.deploy._impact.format_markdown", return_value="# Markdown Report"),
        ):
            mock_db.return_value = ([], [], False)
            result = runner.invoke(cli, ["impact"])

        assert result.exit_code == 0, result.output
        assert summary_file.exists()
        assert "# Markdown Report" in summary_file.read_text()

    def test_sample_option(
        self,
        runner: CliRunner,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """--sample should downsample the dataset."""
        _setup_impact_project(tmp_path, monkeypatch)

        with (
            patch("haute.cli._impact._impact_databricks") as mock_db,
            patch("haute.deploy._impact.format_terminal", return_value="Report"),
            patch("haute.deploy._impact.format_markdown", return_value="# Report"),
        ):
            mock_db.return_value = ([], [], False)
            result = runner.invoke(cli, ["impact", "--sample", "2"])

        assert result.exit_code == 0, result.output
        # Verify records were limited
        records_arg = mock_db.call_args[0][2]  # 3rd positional arg = records
        assert len(records_arg) == 2


class TestImpactDatabricks:
    def test_prod_endpoint_not_found(self) -> None:
        """When prod endpoint doesn't exist, should return prod_exists=False."""
        from haute.cli._impact import _impact_databricks

        mock_ws = MagicMock()
        mock_ws.serving_endpoints.get.side_effect = type("NotFound", (Exception,), {})("not found")

        with (
            patch("databricks.sdk.WorkspaceClient", return_value=mock_ws),
            patch("haute.deploy._config._load_env"),
            patch("haute.deploy._impact.score_endpoint_batched", return_value=[{"p": 1.0}]),
        ):
            staging, prod, exists = _impact_databricks("stg", "prod", [{"x": 1}], 100)

        assert exists is False
        assert prod == []
        assert len(staging) == 1


class TestImpactHttp:
    def test_prod_404_marks_first_deploy(self) -> None:
        """A 404 on the prod URL is the only 'unreachable' signal that
        should be treated as 'no production yet'. Other transport errors
        (connection refused, timeout, 5xx) must propagate — see
        ``test_cli_fail_loudly.py::TestImpactProdExistsFailsLoudly`` for
        the full contract.
        """
        from haute.cli._impact import _impact_http

        with patch(
            "haute.deploy._impact.score_http_endpoint_batched",
            side_effect=[
                [{"p": 1.0}],  # staging succeeds
                RuntimeError("HTTP 404 from http://prod/quote: not found"),
            ],
        ):
            staging, prod, exists = _impact_http(
                "http://stg/quote",
                "http://prod/quote",
                [{"x": 1}],
                100,
            )

        assert exists is False
        assert prod == []

    def test_no_prod_url(self) -> None:
        """If prod_url is empty, prod_exists should be False."""
        from haute.cli._impact import _impact_http

        with patch(
            "haute.deploy._impact.score_http_endpoint_batched",
            return_value=[{"p": 1.0}],
        ):
            staging, prod, exists = _impact_http(
                "http://stg/quote",
                "",
                [{"x": 1}],
                100,
            )

        assert exists is False
        assert prod == []
