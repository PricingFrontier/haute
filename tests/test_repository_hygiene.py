from __future__ import annotations

import subprocess
import tomllib
from pathlib import Path


def _tracked_files() -> set[str]:
    result = subprocess.run(
        ["git", "ls-files"],
        check=True,
        capture_output=True,
        text=True,
    )
    return {line.strip().replace("\\", "/") for line in result.stdout.splitlines() if line.strip()}


def test_generated_and_local_agent_artifacts_are_not_tracked() -> None:
    tracked = _tracked_files()
    offenders = sorted(
        path
        for path in tracked
        if path == ".omc"
        or path.startswith(".omc/")
        or path == "graphify-out"
        or "/graphify-out/" in f"/{path}/"
        or (Path(path).name.startswith("PR23_") and path.endswith(".md"))
    )

    assert offenders == []


def test_example_pipeline_config_lives_only_under_rating() -> None:
    tracked = _tracked_files()
    root_config = sorted(path for path in tracked if path == "config" or path.startswith("config/"))

    assert root_config == []
    assert any(path.startswith("rating/config/") for path in tracked)


def test_graphify_is_not_a_runtime_dependency() -> None:
    project = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    dependencies = project["project"]["dependencies"]
    lockfile = Path("uv.lock").read_text(encoding="utf-8")

    assert not any(dep.lower().startswith("graphifyy") for dep in dependencies)
    assert 'name = "graphifyy"' not in lockfile
