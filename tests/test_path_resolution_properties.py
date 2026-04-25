"""Property-based tests for runtime path resolution."""

from __future__ import annotations

import string

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from haute._path_resolution import resolve_runtime_file_path

_WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}

_SAFE_PATH_PART = st.text(
    alphabet=string.ascii_letters + string.digits + "_-",
    min_size=1,
    max_size=12,
).filter(lambda value: value.upper() not in _WINDOWS_RESERVED_NAMES)
_SAFE_RELATIVE_PATH = st.lists(_SAFE_PATH_PART, min_size=1, max_size=4)


class TestPathResolutionProperties:
    @given(parts=_SAFE_RELATIVE_PATH)
    @settings(
        max_examples=80,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    def test_backslash_and_forward_slash_variants_resolve_identically(
        self,
        parts: list[str],
        tmp_path_factory: pytest.TempPathFactory,
    ) -> None:
        root = tmp_path_factory.mktemp("path_resolution_variants")
        expected = root.joinpath(*parts)
        expected.parent.mkdir(parents=True, exist_ok=True)
        expected.write_text("{}", encoding="utf-8")

        slash_path = "/".join(parts)
        backslash_path = "\\".join(parts)

        resolved_slash = resolve_runtime_file_path(
            slash_path,
            project_root=root,
            enforce_project_root=True,
        )
        resolved_backslash = resolve_runtime_file_path(
            backslash_path,
            project_root=root,
            enforce_project_root=True,
        )

        assert resolved_slash == expected.resolve()
        assert resolved_backslash == expected.resolve()

    @given(
        parts=_SAFE_RELATIVE_PATH,
        existing_target=st.sampled_from(["project", "pipeline"]),
    )
    @settings(
        max_examples=80,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    def test_single_existing_candidate_wins_regardless_of_preference(
        self,
        parts: list[str],
        existing_target: str,
        tmp_path_factory: pytest.TempPathFactory,
    ) -> None:
        root = tmp_path_factory.mktemp("path_resolution_existing")
        pipeline_dir = root / "pipelines"
        pipeline_dir.mkdir()

        project_candidate = root.joinpath(*parts)
        pipeline_candidate = pipeline_dir.joinpath(*parts)
        expected = project_candidate if existing_target == "project" else pipeline_candidate
        expected.parent.mkdir(parents=True, exist_ok=True)
        expected.write_text("{}", encoding="utf-8")

        raw_path = "/".join(parts)
        for prefer in ("project", "pipeline"):
            resolved = resolve_runtime_file_path(
                raw_path,
                project_root=root,
                pipeline_dir=pipeline_dir,
                prefer=prefer,
                enforce_project_root=True,
            )
            assert resolved == expected.resolve()

    @given(
        parts=_SAFE_RELATIVE_PATH,
        prefer=st.sampled_from(["project", "pipeline"]),
    )
    @settings(
        max_examples=80,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    def test_safe_relative_paths_resolve_within_project_root(
        self,
        parts: list[str],
        prefer: str,
        tmp_path_factory: pytest.TempPathFactory,
    ) -> None:
        root = tmp_path_factory.mktemp("path_resolution_root")
        pipeline_dir = root / "nested" / "pipelines"
        pipeline_dir.mkdir(parents=True)

        resolved = resolve_runtime_file_path(
            "/".join(parts),
            project_root=root,
            pipeline_dir=pipeline_dir,
            prefer=prefer,
            enforce_project_root=True,
        )

        assert resolved.is_relative_to(root.resolve())

    @given(
        parts=_SAFE_RELATIVE_PATH,
        prefer=st.sampled_from(["project", "pipeline"]),
    )
    @settings(
        max_examples=80,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    def test_pipeline_candidate_outside_root_is_never_selected(
        self,
        parts: list[str],
        prefer: str,
        tmp_path_factory: pytest.TempPathFactory,
    ) -> None:
        root = tmp_path_factory.mktemp("path_resolution_project")
        outside = tmp_path_factory.mktemp("path_resolution_outside") / "pipelines"
        outside.mkdir(parents=True)

        project_candidate = root.joinpath(*parts)
        project_candidate.parent.mkdir(parents=True, exist_ok=True)
        project_candidate.write_text("{}", encoding="utf-8")

        resolved = resolve_runtime_file_path(
            "/".join(parts),
            project_root=root,
            pipeline_dir=outside,
            prefer=prefer,
            enforce_project_root=True,
        )

        assert resolved == project_candidate.resolve()

    @given(parts=_SAFE_RELATIVE_PATH)
    @settings(
        max_examples=80,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    def test_relative_source_file_matches_explicit_pipeline_dir(
        self,
        parts: list[str],
        tmp_path_factory: pytest.TempPathFactory,
    ) -> None:
        root = tmp_path_factory.mktemp("path_resolution_source_file")
        pipeline_dir = root / "pipelines"
        pipeline_dir.mkdir()

        raw_path = "/".join(parts)
        resolved_from_source = resolve_runtime_file_path(
            raw_path,
            project_root=root,
            source_file="pipelines/pricing.py",
            enforce_project_root=True,
        )
        resolved_from_pipeline_dir = resolve_runtime_file_path(
            raw_path,
            project_root=root,
            pipeline_dir=pipeline_dir,
            enforce_project_root=True,
        )

        assert resolved_from_source == resolved_from_pipeline_dir
