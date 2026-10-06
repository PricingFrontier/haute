"""The repository's default pipeline runs from a fresh checkout.

Spec: specs/reference-pipeline/low-level.md, ## Testing.
"""

from __future__ import annotations

import shutil
import subprocess
import tomllib
from pathlib import Path

import pytest
from click.testing import CliRunner

from haute._sandbox import set_project_root
from haute.cli import cli
from haute.executor import execute_graph
from haute.parser import parse_pipeline_file

ROOT = Path(__file__).resolve().parents[1]


def _tracked(prefix: str) -> list[str]:
    listed = subprocess.run(
        ["git", "ls-files", prefix], cwd=ROOT, check=True, capture_output=True, text=True
    )
    return [line.strip() for line in listed.stdout.splitlines() if line.strip()]


def test_default_pipeline_runs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """From a fresh checkout: tracked files only, nothing supplied locally.

    The short name keeps the input cache's paths under Windows' path limit.
    """
    pipeline = tomllib.loads((ROOT / "haute.toml").read_text(encoding="utf-8"))["project"][
        "pipeline"
    ]
    assert pipeline == "examples/reference/main.py"
    checkout = tmp_path
    shutil.copyfile(ROOT / "haute.toml", checkout / "haute.toml")
    for relative in _tracked("examples"):
        (checkout / relative).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, checkout / relative)
    monkeypatch.chdir(checkout)
    # The suite widens the sandbox root; a fresh checkout's root is its own directory.
    set_project_root(checkout)

    result = CliRunner().invoke(cli, ["run"])

    assert result.exit_code == 0, result.output
    assert "Pipeline: reference (3 nodes)" in result.output
    assert "priced: 6 rows \u00d7 4 cols" in result.output
    assert "\u2717" not in result.output

    # The same parse-and-execute path `haute run` takes, read back as records.
    priced = execute_graph(parse_pipeline_file(checkout / pipeline))["priced"]
    assert priced.status == "ok", priced.error
    assert priced.preview == [
        {"quote_id": "q1", "vehicle_age": 7, "driver_band": "(-inf, 25]", "sum_insured": 18000},
        {"quote_id": "q2", "vehicle_age": 11, "driver_band": "(25, 40]", "sum_insured": 12000},
        {"quote_id": "q3", "vehicle_age": 5, "driver_band": "(40, 65]", "sum_insured": 26000},
        {"quote_id": "q4", "vehicle_age": 14, "driver_band": "(65, inf]", "sum_insured": 8000},
        {"quote_id": "q5", "vehicle_age": 8, "driver_band": "(25, 40]", "sum_insured": 15000},
        {"quote_id": "q6", "vehicle_age": 6, "driver_band": "(40, 65]", "sum_insured": 22000},
    ]
