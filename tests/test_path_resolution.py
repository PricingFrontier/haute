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
    _infer_project_root,
    current_runtime_project_root,
    resolve_runtime_file_path,
    runtime_project_root_scoped,
)
from haute._sandbox import set_project_root
from haute._types import PipelineGraph


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


def test_absolute_source_file_outside_cwd_infers_project_root_from_source_parent(
    tmp_path: Path,
) -> None:
    source_file = tmp_path / "external" / "pipelines" / "pricing.py"
    source_file.parent.mkdir(parents=True)
    source_file.write_text("# pipeline", encoding="utf-8")

    inferred = _infer_project_root(project_root=None, source_file=source_file)
    resolved = resolve_runtime_file_path(
        "inputs/data.parquet",
        source_file=source_file,
        enforce_project_root=True,
    )

    assert inferred == source_file.parent.resolve()
    assert resolved == (source_file.parent / "inputs" / "data.parquet").resolve()


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
    set_project_root(project_root)

    resolved = resolve_runtime_file_path("inputs/data.parquet")

    assert resolved == (project_root / "inputs" / "data.parquet").resolve()


def test_source_spelled_inside_project_cannot_resolve_outside(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root = (tmp_path / "project").resolve()
    project_root.mkdir()
    set_project_root(project_root)
    source = project_root / "linked-pipeline.py"
    escaped_source = (tmp_path / "outside" / "pipeline.py").resolve()
    original_resolve = Path.resolve

    def resolve_with_symlink_escape(path: Path, *args: object, **kwargs: object) -> Path:
        if path == source:
            return escaped_source
        return original_resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", resolve_with_symlink_escape)

    with pytest.raises(ValueError, match="outside the project root"):
        _infer_project_root(project_root=None, source_file=source)


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


def test_runtime_project_root_scope_accepts_keyword_graph(tmp_path: Path) -> None:
    source = tmp_path / "external" / "pipeline.py"
    source.parent.mkdir()
    graph = PipelineGraph(source_file=str(source))

    @runtime_project_root_scoped
    def selected_root(*, graph: PipelineGraph) -> Path:
        return current_runtime_project_root()

    assert selected_root(graph=graph) == source.parent.resolve()


def test_runtime_project_root_scope_uses_first_positional_argument(tmp_path: Path) -> None:
    source = tmp_path / "external" / "pipeline.py"
    source.parent.mkdir()
    graph = PipelineGraph(source_file=str(source))
    marker = object()

    @runtime_project_root_scoped
    def selected_root(graph: PipelineGraph, value: object) -> tuple[Path, object]:
        return current_runtime_project_root(), value

    assert selected_root(graph, marker) == (source.parent.resolve(), marker)


def test_runtime_project_root_scoped_preserves_wrapped_metadata() -> None:
    def selected_root(graph: PipelineGraph) -> Path:
        """Return the execution-scoped project root."""
        return current_runtime_project_root()

    decorated = runtime_project_root_scoped(selected_root)

    assert decorated.__name__ == selected_root.__name__
    assert decorated.__doc__ == selected_root.__doc__
    assert decorated.__wrapped__ is selected_root


def test_runtime_project_root_scope_rejects_non_graph_argument() -> None:
    @runtime_project_root_scoped
    def selected_root(graph: PipelineGraph) -> Path:
        return current_runtime_project_root()

    with pytest.raises(TypeError, match="PipelineGraph"):
        selected_root("not-a-graph")  # type: ignore[arg-type]
