"""Tests for haute.cli._train — the ``haute train`` command."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest

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
