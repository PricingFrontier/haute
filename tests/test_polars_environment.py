"""Haute keeps Polars' spilling off, and inside its storage, in every process it starts."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from haute._polars_environment import (
    SPILL_DIR_ENV,
    polars_spill_root,
    reap_polars_spill_dirs,
)
from haute._worker_isolation import IsolatedWorkerConfig, run_isolated_worker

# Polars told the host has this little memory spills the sort below under its
# defaults (the threshold is 80% of it), so each check has a control run.
_SMALL_HOST = {"POLARS_OVERRIDE_TOTAL_MEMORY_MB": "20"}

_SORT_AND_REPORT = """
import json, os, sys
from pathlib import Path
{prelude}
import polars as pl
frame = pl.LazyFrame({{"row": pl.int_range(0, 5_000_000, eager=True)}})
frame.with_columns(key=(pl.col("row") * 7919) % 1_000_003).sort("key").collect(engine="streaming")
root = Path(os.environ.get("POLARS_OOC_SPILL_DIR", ""))
settings = {{
    k: v for k, v in os.environ.items() if k.startswith(("POLARS_OOC", "POLARS_UNKNOWN"))
}}
print(json.dumps({{"spilled": (root / str(os.getpid())).is_dir(), "settings": settings}}))
"""


def _run_fresh(prelude: str, cwd: Path, extra: dict[str, str]) -> dict[str, object]:
    environment = {key: value for key, value in os.environ.items() if not key.startswith("POLARS_")}
    environment.update(extra)
    completed = subprocess.run(
        [sys.executable, "-c", _SORT_AND_REPORT.format(prelude=prelude)],
        cwd=cwd,
        env=environment,
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(completed.stdout.strip().splitlines()[-1])


def test_the_cli_turns_spilling_off_and_owns_its_directory_before_polars_loads(
    tmp_path: Path,
) -> None:
    spill_root = polars_spill_root(tmp_path)
    control = _run_fresh("", tmp_path, {**_SMALL_HOST, SPILL_DIR_ENV: str(spill_root)})
    assert control["spilled"] is True

    report = _run_fresh("import haute.cli", tmp_path, _SMALL_HOST)

    assert report["spilled"] is False
    settings = report["settings"]
    assert isinstance(settings, dict)
    assert settings[SPILL_DIR_ENV] == str(spill_root)
    assert settings["POLARS_UNKNOWN_EXTENSION_TYPE_BEHAVIOR"] == "load_as_storage"
    assert float(settings["POLARS_OOC_MEMORY_BUDGET_FRACTION"]) >= 1000


def _sort_in_worker() -> tuple[int, bool]:
    import polars as pl

    frame = pl.LazyFrame({"row": pl.int_range(0, 5_000_000, eager=True)})
    frame.with_columns(key=(pl.col("row") * 7919) % 1_000_003).sort("key").collect(
        engine="streaming"
    )
    spill_root = Path(os.environ[SPILL_DIR_ENV])
    return os.getpid(), (spill_root / str(os.getpid())).is_dir()


def test_a_worker_inherits_the_settings_and_spills_nothing() -> None:
    pid, spilled = run_isolated_worker(
        _sort_in_worker, config=IsolatedWorkerConfig(environment=_SMALL_HOST)
    )

    assert spilled is False
    assert not (Path(os.environ[SPILL_DIR_ENV]) / str(pid)).exists()


def test_a_forced_spill_lands_in_the_spill_root_and_goes_when_its_worker_ends() -> None:
    forced = {**_SMALL_HOST, "POLARS_OOC_MEMORY_BUDGET_FRACTION": "0.8"}

    pid, spilled = run_isolated_worker(
        _sort_in_worker, config=IsolatedWorkerConfig(environment=forced)
    )

    assert spilled is True
    assert not (Path(os.environ[SPILL_DIR_ENV]) / str(pid)).exists()


def test_reaping_removes_only_the_folders_of_ended_processes(tmp_path: Path) -> None:
    ended = subprocess.run(
        [sys.executable, "-c", "import os; print(os.getpid())"],
        capture_output=True,
        text=True,
        check=True,
    )
    ended_folder = tmp_path / ended.stdout.strip()
    running_folder = tmp_path / str(os.getpid())
    other = tmp_path / "not-a-process"
    for folder in (ended_folder, running_folder, other):
        folder.mkdir()

    reap_polars_spill_dirs(tmp_path)

    assert not ended_folder.exists()
    assert running_folder.is_dir()
    assert other.is_dir()


def test_a_pipeline_run_in_a_users_process_keeps_that_process_settings(tmp_path: Path) -> None:
    script = (
        "import json, os\n"
        "import polars as pl\n"
        "from haute import Pipeline\n"
        "before = {k: v for k, v in os.environ.items() if k.startswith('POLARS_')}\n"
        "p = Pipeline('user_process')\n"
        "@p.polars\n"
        "def rows() -> pl.LazyFrame:\n"
        "    return pl.LazyFrame({'x': [1, 2]})\n"
        "assert p.run(source='batch').height == 2\n"
        "after = {k: v for k, v in os.environ.items() if k.startswith('POLARS_')}\n"
        "print(json.dumps(before == after))\n"
    )
    environment = {key: value for key, value in os.environ.items() if not key.startswith("POLARS_")}

    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        check=True,
    )

    assert completed.stdout.strip().splitlines()[-1] == "true"
