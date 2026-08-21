"""Mutation witnesses for unit-callable helpers in ``src/haute/executor.py``.

These pin the behaviour of the executor's pure/near-pure helpers so a Cosmic Ray
mutation flips an assertion. The big ``execute_graph`` body (and its internal
branch survivors) is exercised by the integration suites (``test_executor.py``
et al.) rather than these unit witnesses; this file targets the helpers that can
be driven directly with crafted inputs.
"""

from __future__ import annotations

import os
import types
from dataclasses import FrozenInstanceError
from pathlib import Path

import polars as pl
import pytest

import haute.executor as executor_module
from haute.executor import (
    PREVIEW_MAX_CELLS,
    PreparedDataOutput,
    _cache_has_required_materialization,
    _contain_output_path,
    _is_dangerous_preamble_binding,
    _pipeline_dir,
    _preview_row_limit_for_width,
)


def test_executor_resource_defaults_are_explicit_contracts() -> None:
    assert executor_module._MAX_PREVIEW_ROWS == 10_000
    assert executor_module.PREVIEW_CACHE_MAX_BYTES == 64 * 1024 * 1024
    assert PREVIEW_MAX_CELLS == 50_000
    assert executor_module.PREVIEW_INITIAL_COLUMN_LIMIT == 200
    assert executor_module._preview_cache._max_size == 8
    assert executor_module._WINDOWS_OUTPUT_SYNC_RETRY_DELAYS == (0.05, 0.1, 0.2, 0.4, 0.8)


def test_executor_platform_flag_matches_the_runtime() -> None:
    assert executor_module._IS_WINDOWS is (os.name == "nt")


def test_prepared_output_is_frozen_slotted_and_non_transactional_by_default() -> None:
    prepared = PreparedDataOutput(
        response=object(),  # type: ignore[arg-type]
        project_root="root",
        display_path="output.parquet",
        final_path=None,
        staging_path=None,
        overwrite=False,
    )
    assert prepared.transactional is False
    assert not hasattr(prepared, "__dict__")
    with pytest.raises(FrozenInstanceError):
        prepared.transactional = True  # type: ignore[misc]


# ── L150: ``p = Path.cwd() / p`` is a path-join, not arithmetic ───────


def test_pipeline_dir_relative_source_uses_path_join() -> None:
    """A relative ``source_file`` drives ``Path.cwd() / p`` (L150). Any
    ``ReplaceBinaryOperator`` mutation of ``/`` (-> ``+``/``*``/... on Path
    operands) raises TypeError at runtime, so simply reaching this branch and
    getting a Path back kills all of them.
    """
    graph = types.SimpleNamespace(source_file="rel/sub/pipeline.py")
    result = _pipeline_dir(graph)
    assert isinstance(result, Path)
    assert result.is_absolute()


def test_contain_output_path_anchors_relative_pipeline_to_project_root(tmp_path: Path) -> None:
    """A relative graph source is resolved below the explicit project root.

    This pins the path join in ``_contain_output_path`` and prevents output
    resolution from accidentally falling back to the process working directory.
    """
    graph = types.SimpleNamespace(source_file="pipelines/pricing.py")

    result = _contain_output_path(
        graph,
        "outputs/result.parquet",
        project_root=tmp_path,
    )

    assert result == (tmp_path / "pipelines" / "outputs" / "result.parquet").resolve()


# ── L215 / L225: dangerous-import sandbox check (``or`` chain) ────────


def test_dangerous_binding_submodule_of_dangerous_top_level_module() -> None:
    """A module whose name is a SUBmodule of a dangerous top-level (e.g.
    ``subprocess.fake``) is dangerous solely via L215
    ``module_name.split(".", 1)[0] in _DANGEROUS_MODULES``. Mutating that
    ``or`` to ``and`` (the first two clauses are False for this fake) would
    wrongly clear it — a sandbox-escape regression.
    """
    fake = types.ModuleType("subprocess.fake")
    assert _is_dangerous_preamble_binding(fake) is True


def test_dangerous_binding_value_from_dangerous_submodule() -> None:
    """Non-module value whose ``__module__`` is a dangerous submodule exercises
    the second ``or`` chain (L225). Same kill: ``or``->``and`` clears a value
    that should be flagged dangerous.
    """

    def _f() -> None:  # pragma: no cover - never called
        return None

    _f.__module__ = "ctypes.fake"
    assert _is_dangerous_preamble_binding(_f) is True


# ── L517 / L525: preview row-limit sizing ────────────────────────────


def test_preview_row_limit_zero_rows_not_rejected() -> None:
    """``if max_preview_rows < 0`` (L517) must admit 0. ``<`` -> ``<=`` and the
    ``0`` -> ``1`` NumberReplacer both turn the boundary into ``<= 0`` / ``< 1``,
    which would (wrongly) raise on a legitimate 0-row request.
    """
    assert _preview_row_limit_for_width(0, 5) == 0


def test_preview_row_limit_cell_budget_floor_division() -> None:
    """``min(max_preview_rows, PREVIEW_MAX_CELLS // column_count)`` (L525) caps
    rows by the cell budget. With a huge row request the cap binds, so any
    ``ReplaceBinaryOperator`` swap of ``//`` (-> ``*``/``+``/``%``/...) changes
    the returned cap. The expected value is recomputed from the real constant so
    the test pins the operator, not a magic number.
    """
    expected = PREVIEW_MAX_CELLS // 10
    assert _preview_row_limit_for_width(10**9, 10) == expected


def test_preview_row_limit_negative_rejected() -> None:
    """Companion: a negative request must raise (pins the guard fires at all)."""
    with pytest.raises(ValueError, match="max_preview_rows"):
        _preview_row_limit_for_width(-1, 5)


# ── L724: required-columns subset gate in cache reuse ────────────────


def test_cache_materialization_rejects_missing_required_column() -> None:
    """``if not set(required_columns) <= set(df.columns): return False`` (L724).
    The cached frame is missing a required column ('b'), so a correct subset
    check reports the cache as NOT satisfying materialisation (False). Mutating
    ``<=`` to ``>=``/``is not`` inverts the gate and would treat an incomplete
    cache as complete (True) — a stale-result hazard.
    """
    res = _cache_has_required_materialization(
        graph=types.SimpleNamespace(node_map={}),
        target_node_id=None,
        requested_preview_columns=None,
        required_materialized_nodes={"n1"},
        materialize_column_limits_by_node=None,
        cached_outputs={"n1": pl.DataFrame({"a": [1]})},
        cached_output_columns={"n1": [("a", "Int64"), ("b", "Int64")]},
    )
    assert res is False
