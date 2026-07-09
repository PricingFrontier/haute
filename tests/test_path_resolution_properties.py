"""Property-based tests for runtime path resolution."""

from __future__ import annotations

import string

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from haute._config_io import is_windows_reserved_filename
from haute._path_resolution import resolve_runtime_file_path

_WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}

# The safe strategies below deliberately filter reserved names OUT, because
# these properties assume the generated paths are writable on every platform.
# ``TestWindowsReservedNameProperties`` inverts that: it deliberately
# generates reserved-stem filenames and asserts the save-time predicate
# flags every one.
_SAFE_PATH_PART = st.text(
    alphabet=string.ascii_letters + string.digits + "_-",
    min_size=1,
    max_size=12,
).filter(lambda value: value.upper() not in _WINDOWS_RESERVED_NAMES)
_SAFE_RELATIVE_PATH = st.lists(_SAFE_PATH_PART, min_size=1, max_size=4)


def _mix_case(stem: str, flags: list[bool]) -> str:
    return "".join(
        ch.upper() if flag else ch.lower()
        for ch, flag in zip(stem, flags + [False] * (len(stem) - len(flags)))
    )


_EXTENSION_PART = st.text(
    alphabet=string.ascii_letters + string.digits,
    min_size=1,
    max_size=8,
)

# A reserved stem in arbitrary casing, with zero or more dot-joined
# extensions (``NUL``, ``con.json``, ``Com1.tar.gz``, ...). Windows
# compares the stem before the FIRST dot, case-insensitively, with any
# extension.
_RESERVED_STEM_FILENAME = st.builds(
    lambda stem, flags, exts: ".".join([_mix_case(stem, flags), *exts]),
    st.sampled_from(sorted(_WINDOWS_RESERVED_NAMES)),
    st.lists(st.booleans(), min_size=1, max_size=4),
    st.lists(_EXTENSION_PART, min_size=0, max_size=2),
)


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


class TestWindowsReservedNameProperties:
    """Property coverage for the save-time reserved device-name predicate."""

    @given(filename=_RESERVED_STEM_FILENAME)
    @settings(max_examples=200)
    def test_predicate_flags_every_reserved_stem(self, filename: str) -> None:
        """Every reserved stem is flagged — any casing, any extension chain."""
        assert is_windows_reserved_filename(filename)

    @given(part=_SAFE_PATH_PART, extension=_EXTENSION_PART)
    @settings(max_examples=200)
    def test_predicate_never_flags_safe_names(self, part: str, extension: str) -> None:
        """Names the safe strategies deem writable are never flagged."""
        assert not is_windows_reserved_filename(part)
        assert not is_windows_reserved_filename(f"{part}.{extension}")
