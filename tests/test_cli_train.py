"""Tests for haute.cli._train — the ``haute train`` command."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

from haute.cli import cli

if TYPE_CHECKING:
    from click.testing import CliRunner


def _write_training_script(tmp_path: Path, *, body: str = "") -> Path:
    """Write a minimal training script and return its path."""
    script = tmp_path / "train.py"
    code = body or (
        "from unittest.mock import MagicMock\n"
        "job = MagicMock()\n"
        "result = MagicMock()\n"
        "result.model_path = '/tmp/model.cbm'\n"
        "result.train_rows = 1000\n"
        "result.validation_rows = 200\n"
        "result.cat_features = ['a']\n"
        "result.features = ['a', 'b', 'c']\n"
        "result.metrics = {'rmse': 0.1234, 'mae': 0.0567}\n"
        "job.run.return_value = result\n"
    )
    script.write_text(code)
    return script


class TestTrain:
    def test_file_not_found(self, runner: CliRunner) -> None:
        result = runner.invoke(cli, ["train", "/nonexistent/train.py"])
        assert result.exit_code == 1
        assert "not found" in result.output.lower()

    def test_a_training_script_may_read_console_input(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        """The script runs in the CLI process, where input() reads the terminal:
        the server's accident guard does not apply to it."""
        default = _write_training_script(tmp_path).read_text()
        script = _write_training_script(
            tmp_path, body="rows = int(input())\n" + default.replace("1000", "rows")
        )

        result = runner.invoke(cli, ["train", str(script)], input="1234\n")

        assert result.exit_code == 0, result.output
        assert "1,234" in result.output or "1234" in result.output

    def test_a_training_script_exit_code_is_kept(self, runner: CliRunner, tmp_path: Path) -> None:
        script = tmp_path / "stop.py"
        script.write_text("exit(7)\n", encoding="utf-8")

        result = runner.invoke(cli, ["train", str(script)])

        assert result.exit_code == 7

    def test_spec_returns_none(self, runner: CliRunner, tmp_path: Path) -> None:
        script = _write_training_script(tmp_path)

        with (
            patch("importlib.util.spec_from_file_location", return_value=None),
        ):
            result = runner.invoke(cli, ["train", str(script)])
        assert result.exit_code == 1
        assert "cannot load" in result.output.lower()

    def test_exec_module_error(self, runner: CliRunner, tmp_path: Path) -> None:
        script = tmp_path / "bad.py"
        script.write_text("raise ValueError('boom')\n")

        result = runner.invoke(cli, ["train", str(script)])
        assert result.exit_code == 1
        assert "error" in result.output.lower()

    def test_no_job_variable(self, runner: CliRunner, tmp_path: Path) -> None:
        script = tmp_path / "no_job.py"
        script.write_text("x = 42\n")

        result = runner.invoke(cli, ["train", str(script)])
        assert result.exit_code == 1
        assert "job" in result.output.lower()

    def test_success_with_mocked_job(self, runner: CliRunner, tmp_path: Path) -> None:
        script = _write_training_script(tmp_path)

        result = runner.invoke(cli, ["train", str(script)])
        assert result.exit_code == 0, result.output
        assert (
            "model saved" in result.output.lower()
            or "model.cbm" in result.output.lower()
            or "/tmp/model.cbm" in result.output
        )
        assert "1,000" in result.output
        assert "200" in result.output
        assert "rmse" in result.output.lower()

    def test_validation_rows_labelled_truthfully(self, runner: CliRunner, tmp_path: Path) -> None:
        """F541: ``validation_rows`` holds the validation count, so label it 'Validation:'.

        The old output printed 'Test:' for a value that is actually the
        validation-set row count, colliding with the train/validation/holdout
        vocabulary used everywhere else (e.g. the model card's 'Validation rows').
        """
        script = _write_training_script(tmp_path)

        result = runner.invoke(cli, ["train", str(script)])
        assert result.exit_code == 0, result.output
        assert "Validation: 200 rows" in result.output
        assert "Test:" not in result.output

    def test_training_failure(self, runner: CliRunner, tmp_path: Path) -> None:
        script = tmp_path / "fail_train.py"
        script.write_text(
            "from unittest.mock import MagicMock\n"
            "job = MagicMock()\n"
            "job.run.side_effect = RuntimeError('CUDA out of memory')\n"
        )

        result = runner.invoke(cli, ["train", str(script)])
        assert result.exit_code == 1
        assert "failed" in result.output.lower() or "CUDA" in result.output

    def test_result_formatting_failure_does_not_relabel_training(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        script = _write_training_script(
            tmp_path,
            body=(
                "from types import SimpleNamespace\n"
                "class Job:\n"
                "    def run(self, progress=None):\n"
                "        return SimpleNamespace(\n"
                "            model_path='/tmp/saved.cbm', train_rows=10,\n"
                "            validation_rows=2, cat_features=[], features=['x'],\n"
                "            metrics={'broken': object()},\n"
                "        )\n"
                "job = Job()\n"
            ),
        )

        result = runner.invoke(cli, ["train", str(script)])

        assert result.exit_code == 1
        assert "training succeeded" in result.output.lower()
        assert "reporting failed" in result.output.lower()
        assert "training failed" not in result.output.lower()

    def test_progress_callback_output(self, runner: CliRunner, tmp_path: Path) -> None:
        """Progress callback should produce bar output with percentage."""
        # Script that captures the progress callback and calls it
        script = tmp_path / "progress_train.py"
        script.write_text(
            "from unittest.mock import MagicMock\n"
            "job = MagicMock()\n"
            "result = MagicMock()\n"
            "result.model_path = '/tmp/model.cbm'\n"
            "result.train_rows = 500\n"
            "result.validation_rows = 100\n"
            "result.cat_features = []\n"
            "result.features = ['x', 'y']\n"
            "result.metrics = {'rmse': 0.5}\n"
            "\n"
            "def fake_run(progress=None):\n"
            "    if progress:\n"
            "        progress('Loading data', 0.0)\n"
            "        progress('Training', 0.5)\n"
            "        progress('Done', 1.0)\n"
            "    return result\n"
            "\n"
            "job.run = fake_run\n"
        )

        result = runner.invoke(cli, ["train", str(script)])
        assert result.exit_code == 0, result.output
        # Should show features count
        assert "2" in result.output  # 2 features
        assert "0 categorical" in result.output
        assert "500" in result.output
        assert "rmse" in result.output

    def test_spec_loader_is_none(self, runner: CliRunner, tmp_path: Path) -> None:
        """When spec is valid but spec.loader is None, should fail with cannot load."""
        script = _write_training_script(tmp_path)

        mock_spec = MagicMock()
        mock_spec.loader = None

        with (
            patch("importlib.util.spec_from_file_location", return_value=mock_spec),
        ):
            result = runner.invoke(cli, ["train", str(script)])
        assert result.exit_code == 1
        assert "cannot load" in result.output.lower()

    def test_metrics_formatting(self, runner: CliRunner, tmp_path: Path) -> None:
        """Metrics should display with 4 decimal places."""
        script = tmp_path / "metrics_train.py"
        script.write_text(
            "from unittest.mock import MagicMock\n"
            "job = MagicMock()\n"
            "result = MagicMock()\n"
            "result.model_path = '/tmp/model.cbm'\n"
            "result.train_rows = 100\n"
            "result.validation_rows = 20\n"
            "result.cat_features = ['a', 'b']\n"
            "result.features = ['a', 'b', 'c', 'd']\n"
            "result.metrics = {'rmse': 0.123456789, 'r2': 0.987654321}\n"
            "job.run.return_value = result\n"
        )

        result = runner.invoke(cli, ["train", str(script)])
        assert result.exit_code == 0, result.output
        assert "0.1235" in result.output  # 4 decimal places rounded
        assert "0.9877" in result.output
        assert "4" in result.output  # 4 features
        assert "2 categorical" in result.output


def test_haute_train_logs_an_exported_script_with_an_experiment(
    runner: CliRunner, tmp_path: Path, monkeypatch
) -> None:
    """MLF-E09: an exported script logs because it names ``mlflow_experiment``.

    ``haute train`` imports the script and runs its ``job`` without executing the
    ``__main__`` block, so logging must come from the ``TrainingJob`` argument —
    a logging call in ``__main__`` would silently never run here.
    """
    import polars as pl
    from mlflow.tracking import MlflowClient

    from haute._sandbox import set_project_root
    from haute.modelling._export import generate_training_script

    for name in ("MLFLOW_TRACKING_URI", "DATABRICKS_MLFLOW_HOST", "DATABRICKS_MLFLOW_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("MLFLOW_ALLOW_FILE_STORE", "true")
    monkeypatch.chdir(tmp_path)
    set_project_root(tmp_path)
    data = tmp_path / "data.parquet"
    pl.DataFrame(
        {
            "y": [1.0, 2.0, 1.5, 3.0, 2.5, 4.0, 1.0, 2.0],
            "x": [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8],
        }
    ).write_parquet(data)
    script = tmp_path / "train_freq.py"
    script.write_text(
        generate_training_script(
            {
                "name": "freq",
                "target": "y",
                "algorithm": "catboost",
                "task": "regression",
                "loss_function": "RMSE",
                "params": {"iterations": 2},
                "evaluation": {
                    "schema_version": 1,
                    "strategy": "random",
                    "seed": 1,
                    "validation": {"method": "none"},
                },
                "mlflow_experiment": "scripted",
                "output_dir": str(tmp_path / "outputs"),
            },
            str(data),
        ),
        encoding="utf-8",
    )

    result = runner.invoke(cli, ["train", str(script)])

    assert result.exit_code == 0, result.output
    client = MlflowClient(tracking_uri=(tmp_path / "mlruns").as_uri())
    experiment = client.get_experiment_by_name("scripted")
    assert experiment is not None
    [run] = client.search_runs([experiment.experiment_id])
    assert run.data.tags["haute.contract_version"] == "1"
    assert run.data.tags["haute.node_label"] == "freq"
