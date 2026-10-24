"""Tests for haute.cli._impact — the ``haute impact`` command."""

from __future__ import annotations

import json
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

    def test_no_impact_dataset_and_no_test_quotes_fails(
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

    def test_container_target_rejects_endpoint_suffix(
        self,
        runner: CliRunner,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _setup_impact_project(
            tmp_path,
            monkeypatch,
            target="container",
        )

        with patch("haute.cli._impact._impact_http") as impact_http:
            result = runner.invoke(cli, ["impact", "--endpoint-suffix", "-canary"])

        assert result.exit_code == 1
        assert "only supported for databricks" in result.output.lower()
        assert "endpoint_url" in result.output
        impact_http.assert_not_called()

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
    def test_prod_endpoint_not_found(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When prod endpoint doesn't exist, should return prod_exists=False."""
        from haute.cli._impact import _impact_databricks

        monkeypatch.setenv("DATABRICKS_RATING_HOST", "https://rating.example")
        monkeypatch.setenv("DATABRICKS_RATING_TOKEN", "rating-token")

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


class TestImpactWithoutAPortfolioSample:
    """BUG-23: a new project's ``impact_dataset`` named a file under the gitignored
    ``data/``, so CI's impact step stopped with "Impact dataset not found". Without a
    sample, impact compares the test quotes, the requests smoke sends."""

    def _project(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, quotes: list) -> None:
        _setup_impact_project(tmp_path, monkeypatch, impact_dataset="")
        toml = tmp_path / "haute.toml"
        toml.write_text(toml.read_text() + '[test_quotes]\ndir = "tests/quotes"\n')
        quotes_dir = tmp_path / "tests" / "quotes"
        quotes_dir.mkdir(parents=True)
        if quotes:
            (quotes_dir / "cases.json").write_text(json.dumps(quotes))

    def test_the_test_quotes_are_compared(
        self, runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._project(
            tmp_path,
            monkeypatch,
            [
                {"input": {"VehPower": 5, "Area": "A", "drivers": [{"age": 30}]}},
                {"_description": "a second quote", "input": {"VehPower": 9, "Area": "B"}},
            ],
        )

        with (
            patch("haute.cli._impact._impact_databricks") as mock_db,
            patch("haute.deploy._impact.format_terminal", return_value="Report"),
        ):
            mock_db.return_value = ([{"premium": 1.0}, {"premium": 2.0}], [], False)
            result = runner.invoke(cli, ["impact"])

        assert result.exit_code == 0, result.output
        records = mock_db.call_args[0][2]
        assert records == [
            {"VehPower": 5, "Area": "A", "drivers": [{"age": 30}]},
            {"VehPower": 9, "Area": "B"},
        ]
        assert "comparing the test quotes" in result.output
        assert "Dataset: test quotes (tests/quotes) (2 rows)" in result.output
        assert "test quotes (tests/quotes)" in (tmp_path / "impact_report.md").read_text()

    def test_a_comparison_segments_by_the_quotes_text_fields(
        self, runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._project(tmp_path, monkeypatch, [{"input": {"Area": "A", "n": 1}}] * 3)

        with (
            patch("haute.cli._impact._impact_databricks") as mock_db,
            patch("haute.deploy._impact.build_report") as report,
            patch("haute.deploy._impact.format_terminal", return_value="Report"),
            patch("haute.deploy._impact.format_markdown", return_value="# Report"),
        ):
            mock_db.return_value = ([{"p": 1.0}] * 3, [{"p": 1.0}] * 3, True)
            result = runner.invoke(cli, ["impact"])

        assert result.exit_code == 0, result.output
        input_df = report.call_args.kwargs["input_df"]
        assert input_df.to_dicts() == [{"Area": "A"}] * 3

    def test_no_test_quotes_to_compare_fails(
        self, runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._project(tmp_path, monkeypatch, [])

        with patch("haute.cli._impact._impact_databricks") as mock_db:
            result = runner.invoke(cli, ["impact"])

        assert result.exit_code == 1
        assert "has no test quotes to compare instead" in result.output
        mock_db.assert_not_called()


class TestImpactSendsTestQuotesAsSmokeDoes:
    """An endpoint a Workbench Input answers takes exactly one quote per request, so the
    test quotes impact compares go one per request, as smoke sends them, through the real
    transport; and a response's one-row tables are compared field by field."""

    def test_each_test_quote_is_a_request_of_its_own(
        self, runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        TestImpactWithoutAPortfolioSample()._project(
            tmp_path,
            monkeypatch,
            [{"input": {"Area": area}} for area in ("A", "B", "C")],
        )
        premiums = {"A": 100.0, "B": 200.0, "C": 300.0}

        def query(*, name: str, dataframe_records: list[dict]) -> MagicMock:
            # As a Workbench endpoint answers: one quote, or a refusal.
            if len(dataframe_records) != 1:
                raise RuntimeError("WorkbenchInputError: one quote per request")
            [record] = dataframe_records
            uplift = 1.1 if name == "test-ep-staging" else 1.0
            return MagicMock(
                predictions=[{"pricing": {"premium": premiums[record["Area"]] * uplift}}]
            )

        ws = MagicMock()
        ws.serving_endpoints.query.side_effect = query
        with (
            patch("haute.deploy._databricks_serving.serving_workspace_client", return_value=ws),
            patch("haute.deploy._config._load_env"),
        ):
            result = runner.invoke(cli, ["impact", "--endpoint-suffix", "-staging"])

        assert result.exit_code == 0, result.output
        assert ws.serving_endpoints.query.call_count == 6  # three quotes, two endpoints
        report = (tmp_path / "impact_report.md").read_text()
        assert "pricing.premium" in report
        assert "+10.0%" in report


def test_a_one_row_tables_numbers_are_compared() -> None:
    from haute.deploy._impact import build_report

    report = build_report(
        staging_preds=[{"pricing": {"premium": 200.0, "band": "A"}, "id": "q1"}],
        prod_preds=[{"pricing": {"premium": 100.0, "band": "A"}, "id": "q1"}],
        input_df=pl.DataFrame([{}]),
        pipeline_name="p",
        staging_endpoint="s",
        prod_endpoint="p",
        dataset_path="test quotes (tests/quotes)",
        total_rows=1,
    )

    [stats] = report.column_stats
    assert stats.name == "pricing.premium"
    assert stats.mean_change_pct == pytest.approx(100.0)


def test_a_field_first_answered_late_is_never_silently_dropped() -> None:
    """Haute leaves a null output field out of a response, so a field may first appear
    after the hundredth answer. It is read, not dropped by a schema sampled from the
    first answers, and the answers that leave it empty are refused by name."""
    from haute.deploy._impact import build_report

    def answers(premium: float) -> list[dict]:
        early = [{"pricing": {"base": 1.0}} for _ in range(100)]
        return [*early, {"pricing": {"base": 1.0, "premium": premium}}]

    with pytest.raises(ValueError, match=r"leave output 'pricing.premium' empty on 100 rows"):
        build_report(
            staging_preds=answers(200.0),
            prod_preds=answers(100.0),
            input_df=pl.DataFrame([{}] * 101),
            pipeline_name="p",
            staging_endpoint="s",
            prod_endpoint="p",
            dataset_path="test quotes (tests/quotes)",
            total_rows=101,
        )
