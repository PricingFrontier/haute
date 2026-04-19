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
        "result.test_rows = 200\n"
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

    def test_safety_validation_failure(self, runner: CliRunner, tmp_path: Path) -> None:
        script = tmp_path / "evil.py"
        script.write_text("import os\nos.system('rm -rf /')\njob = None\n")

        from haute._sandbox import UnsafeCodeError

        with patch("haute._sandbox.validate_user_code", side_effect=UnsafeCodeError("dangerous")):
            result = runner.invoke(cli, ["train", str(script)])
        assert result.exit_code == 1
        assert "safety" in result.output.lower() or "validation" in result.output.lower()

    def test_spec_returns_none(self, runner: CliRunner, tmp_path: Path) -> None:
        script = _write_training_script(tmp_path)

        with (
            patch("haute._sandbox.validate_user_code"),
            patch("importlib.util.spec_from_file_location", return_value=None),
        ):
            result = runner.invoke(cli, ["train", str(script)])
        assert result.exit_code == 1
        assert "cannot load" in result.output.lower()

    def test_exec_module_error(self, runner: CliRunner, tmp_path: Path) -> None:
        script = tmp_path / "bad.py"
        script.write_text("raise ValueError('boom')\n")

        with patch("haute._sandbox.validate_user_code"):
            result = runner.invoke(cli, ["train", str(script)])
        assert result.exit_code == 1
        assert "error" in result.output.lower()

    def test_no_job_variable(self, runner: CliRunner, tmp_path: Path) -> None:
        script = tmp_path / "no_job.py"
        script.write_text("x = 42\n")

        with patch("haute._sandbox.validate_user_code"):
            result = runner.invoke(cli, ["train", str(script)])
        assert result.exit_code == 1
        assert "job" in result.output.lower()

    def test_success_with_mocked_job(self, runner: CliRunner, tmp_path: Path) -> None:
        script = _write_training_script(tmp_path)

        with patch("haute._sandbox.validate_user_code"):
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

    def test_training_failure(self, runner: CliRunner, tmp_path: Path) -> None:
        script = tmp_path / "fail_train.py"
        script.write_text(
            "from unittest.mock import MagicMock\n"
            "job = MagicMock()\n"
            "job.run.side_effect = RuntimeError('CUDA out of memory')\n"
        )

        with patch("haute._sandbox.validate_user_code"):
            result = runner.invoke(cli, ["train", str(script)])
        assert result.exit_code == 1
        assert "failed" in result.output.lower() or "CUDA" in result.output

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
            "result.test_rows = 100\n"
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

        with patch("haute._sandbox.validate_user_code"):
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
            patch("haute._sandbox.validate_user_code"),
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
            "result.test_rows = 20\n"
            "result.cat_features = ['a', 'b']\n"
            "result.features = ['a', 'b', 'c', 'd']\n"
            "result.metrics = {'rmse': 0.123456789, 'r2': 0.987654321}\n"
            "job.run.return_value = result\n"
        )

        with patch("haute._sandbox.validate_user_code"):
            result = runner.invoke(cli, ["train", str(script)])
        assert result.exit_code == 0, result.output
        assert "0.1235" in result.output  # 4 decimal places rounded
        assert "0.9877" in result.output
        assert "4" in result.output  # 4 features
        assert "2 categorical" in result.output
