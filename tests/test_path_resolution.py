"""Direct contract tests for runtime path resolution.

The property tests cover broad invariants; these tests pin the specific
decision points that determine which file preview/trace/run touches.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from haute._path_resolution import (
    MalformedRuntimePathError,
    RuntimePathOutsideProjectError,
    _candidate_if_allowed,
    resolve_runtime_file_path,
)
from tests._project import use_project, work_elsewhere


def test_prefers_project_candidate_when_both_exist(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    pipeline_dir = project_root / "pipelines"
    project_root.mkdir()
    pipeline_dir.mkdir()
    (project_root / "data.json").write_text("{}", encoding="utf-8")
    (pipeline_dir / "data.json").write_text("{}", encoding="utf-8")

    resolved = resolve_runtime_file_path(
        "data.json",
        project_root=project_root,
        pipeline_dir=pipeline_dir,
        prefer="project",
        enforce_project_root=True,
    )

    assert resolved == (project_root / "data.json").resolve()


def test_prefers_pipeline_candidate_when_both_exist_and_prefer_pipeline(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    pipeline_dir = project_root / "pipelines"
    project_root.mkdir()
    pipeline_dir.mkdir()
    (project_root / "data.json").write_text("{}", encoding="utf-8")
    (pipeline_dir / "data.json").write_text("{}", encoding="utf-8")

    resolved = resolve_runtime_file_path(
        "data.json",
        project_root=project_root,
        pipeline_dir=pipeline_dir,
        prefer="pipeline",
        enforce_project_root=True,
    )

    assert resolved == (pipeline_dir / "data.json").resolve()


def test_prefer_uses_value_equality_for_non_interned_strings(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    pipeline_dir = project_root / "pipelines"
    project_root.mkdir()
    pipeline_dir.mkdir()
    (project_root / "data.json").write_text("project", encoding="utf-8")
    (pipeline_dir / "data.json").write_text("pipeline", encoding="utf-8")

    prefer_pipeline = "".join(["pipe", "line"])

    resolved = resolve_runtime_file_path(
        "data.json",
        project_root=project_root,
        pipeline_dir=pipeline_dir,
        prefer=prefer_pipeline,  # type: ignore[arg-type]
        enforce_project_root=True,
    )

    assert resolved == (pipeline_dir / "data.json").resolve()


@pytest.mark.parametrize("prefer", ["project", "pipeline"])
def test_a_glob_pattern_resolves_where_it_matches_a_file(tmp_path: Path, prefer: str) -> None:
    project_root = tmp_path / "project"
    pipeline_dir = project_root / "rating"
    (pipeline_dir / "files" / "data").mkdir(parents=True)
    (pipeline_dir / "files" / "data" / "a.parquet").write_bytes(b"x")

    resolved = resolve_runtime_file_path(
        "files/data/*.parquet",
        project_root=project_root,
        pipeline_dir=pipeline_dir,
        prefer=prefer,  # type: ignore[arg-type]
        enforce_project_root=True,
    )

    assert resolved == pipeline_dir / "files" / "data" / "*.parquet"


def test_missing_candidates_return_deterministic_preferred_path(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    pipeline_dir = project_root / "pipelines"
    project_root.mkdir()
    pipeline_dir.mkdir()

    project_preferred = resolve_runtime_file_path(
        "missing.json",
        project_root=project_root,
        pipeline_dir=pipeline_dir,
        prefer="project",
        enforce_project_root=True,
    )
    pipeline_preferred = resolve_runtime_file_path(
        "missing.json",
        project_root=project_root,
        pipeline_dir=pipeline_dir,
        prefer="pipeline",
        enforce_project_root=True,
    )

    assert project_preferred == (project_root / "missing.json").resolve()
    assert pipeline_preferred == (pipeline_dir / "missing.json").resolve()


def test_a_source_file_outside_the_installed_project_gives_it_no_root_of_its_own(
    tmp_path: Path,
) -> None:
    """The root is the installed project's: a pipeline file elsewhere does not widen it,
    so a relative path names the project's file, never one beside the outside pipeline."""
    project = tmp_path / "project"
    project.mkdir()
    use_project(project)
    source_file = tmp_path / "external" / "pipelines" / "pricing.py"
    source_file.parent.mkdir(parents=True)
    source_file.write_text("# pipeline", encoding="utf-8")
    (source_file.parent / "inputs").mkdir()
    (source_file.parent / "inputs" / "data.parquet").write_text("outside", encoding="utf-8")

    resolved = resolve_runtime_file_path(
        "inputs/data.parquet",
        source_file=source_file,
        prefer="pipeline",
        enforce_project_root=True,
    )

    assert resolved == (project / "inputs" / "data.parquet").resolve()


def test_relative_source_file_uses_explicit_project_root(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    pipeline_dir = project_root / "pipelines"
    project_root.mkdir()
    pipeline_dir.mkdir()

    resolved = resolve_runtime_file_path(
        "inputs/data.parquet",
        project_root=project_root,
        source_file="pipelines/pricing.py",
        prefer="project",
        enforce_project_root=True,
    )

    assert resolved == (project_root / "inputs" / "data.parquet").resolve()


def test_implicit_root_uses_configured_current_project(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    use_project(project_root)

    resolved = resolve_runtime_file_path("inputs/data.parquet")

    assert resolved == (project_root / "inputs" / "data.parquet").resolve()


def test_relative_source_does_not_override_selected_project_with_working_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    working_directory = tmp_path / "working-directory"
    working_directory.mkdir()
    source = working_directory / "pipelines" / "pricing.py"
    source.parent.mkdir()
    source.write_text("# pipeline", encoding="utf-8")
    selected_root = tmp_path / "selected-project"
    selected_root.mkdir()

    use_project(selected_root)
    work_elsewhere(working_directory)

    resolved = resolve_runtime_file_path(
        "inputs/data.parquet",
        source_file="pipelines/pricing.py",
        enforce_project_root=True,
    )

    assert resolved == (selected_root / "inputs" / "data.parquet").resolve()


def test_relative_source_file_pipeline_preference_uses_source_parent(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    pipeline_dir = project_root / "pipelines"
    project_root.mkdir()
    pipeline_dir.mkdir()
    expected = pipeline_dir / "inputs" / "data.parquet"
    expected.parent.mkdir()
    expected.write_text("pipeline", encoding="utf-8")

    resolved = resolve_runtime_file_path(
        "inputs/data.parquet",
        project_root=project_root,
        source_file="pipelines/pricing.py",
        prefer="pipeline",
        enforce_project_root=True,
    )

    assert resolved == expected.resolve()


def test_empty_source_file_falls_back_to_project_candidate(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()

    resolved = resolve_runtime_file_path(
        "inputs/data.parquet",
        project_root=project_root,
        source_file="",
        prefer="pipeline",
        enforce_project_root=True,
    )

    assert resolved == (project_root / "inputs" / "data.parquet").resolve()


def test_pipeline_candidate_outside_root_falls_back_to_project_candidate(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    outside_pipeline_dir = tmp_path / "outside" / "pipelines"
    outside_pipeline_dir.mkdir(parents=True)
    (project_root / "data.json").write_text("{}", encoding="utf-8")

    resolved = resolve_runtime_file_path(
        "data.json",
        project_root=project_root,
        pipeline_dir=outside_pipeline_dir,
        prefer="pipeline",
        enforce_project_root=True,
    )

    assert resolved == (project_root / "data.json").resolve()


def test_backslash_path_is_normalized_before_resolution(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    expected = project_root / "nested" / "data.json"
    expected.parent.mkdir()
    expected.write_text("{}", encoding="utf-8")

    resolved = resolve_runtime_file_path(
        r"nested\data.json",
        project_root=project_root,
        enforce_project_root=True,
    )

    assert resolved == expected.resolve()


def test_embedded_null_byte_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(MalformedRuntimePathError, match="null byte"):
        resolve_runtime_file_path(
            "bad\x00name.json",
            project_root=tmp_path,
            enforce_project_root=True,
        )


def test_absolute_raw_path_outside_root_is_rejected_when_enforced(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    outside = tmp_path / "outside.json"
    outside.write_text("{}", encoding="utf-8")

    with pytest.raises(RuntimePathOutsideProjectError, match="outside the project root"):
        resolve_runtime_file_path(
            outside,
            project_root=project_root,
            enforce_project_root=True,
        )


def test_absolute_raw_path_outside_root_is_rejected_by_default(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    outside = tmp_path / "outside.json"
    outside.write_text("{}", encoding="utf-8")

    with pytest.raises(ValueError, match="outside the project root"):
        resolve_runtime_file_path(outside, project_root=project_root)


@pytest.mark.parametrize(
    "raw_path",
    ["C:/outside.json", r"C:\outside.json", "C:outside.json"],
)
def test_windows_drive_path_never_becomes_project_relative(
    tmp_path: Path,
    raw_path: str,
) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()

    with pytest.raises(RuntimePathOutsideProjectError, match="outside the project root"):
        resolve_runtime_file_path(raw_path, project_root=project_root)


def test_absolute_raw_path_inside_root_allowed_when_enforced(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    inside = project_root / "inside.json"
    inside.write_text("{}", encoding="utf-8")

    resolved = resolve_runtime_file_path(
        inside,
        project_root=project_root,
        enforce_project_root=True,
    )

    assert resolved == inside.resolve()


def test_candidate_if_allowed_returns_outside_path_when_not_enforced(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    outside = tmp_path / "outside.json"
    outside.write_text("{}", encoding="utf-8")

    resolved = _candidate_if_allowed(
        outside,
        project_root,
        enforce_project_root=False,
    )

    assert resolved == outside.resolve()
