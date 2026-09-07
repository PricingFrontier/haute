"""Property-based metamorphic relations for row-independent scoring (ENG-T11).

Metamorphic relations pinning row-independent scoring across deploy scoring
and editor preview execution:

1. Permutation invariance: row-independent scoring on a permuted input frame
   yields the unpermuted result permuted by the same index mapping.
2. Cross-path parity and oracle agreement: editor execute_graph and deploy
   score_graph agree row-for-row on generated inputs, and each row's derived
   column equals the plain-Python expectation computed from an independent
   oracle (never invoking production helpers).
3. Negative control: order-sensitive operations (e.g. cum_sum) break the
   permutation relation, documenting why the property domain is restricted
   to row-independent expressions.
4. Cold/warm cache agreement: in-process preview caching (_preview_cache in
   haute.executor) produces identical results between cold and warm executions,
   and code edits immediately invalidate the cache so stale results are never
   served.
"""

from __future__ import annotations

import json
import math
import string
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import hypothesis.strategies as st
import polars as pl
import pytest
from hypothesis import example, find, given

from haute.deploy._pruner import find_output_node, prune_for_deploy
from haute.deploy._scorer import score_graph
from haute.executor import _preview_cache, execute_graph
from tests._property_budget import pr_budget
from tests.conftest import make_graph as _g

pytestmark = pytest.mark.usefixtures("_widen_sandbox_root")

FRAME_SCHEMA: dict[str, pl.DataType] = {
    "x": pl.Float64,
    "label": pl.String,
}


@dataclass(frozen=True)
class RowIndependentTransform:
    name: str
    code: str
    column: str
    oracle: Callable[[float | None], Any]


ROW_INDEPENDENT_TRANSFORMS: tuple[RowIndependentTransform, ...] = (
    RowIndependentTransform(
        name="doubled",
        code="df = src.with_columns(doubled=pl.col('x') * 2.0)",
        column="doubled",
        oracle=lambda x: None if x is None else x * 2.0,
    ),
    RowIndependentTransform(
        name="shifted",
        code="df = src.with_columns(shifted=pl.col('x') + 1.5)",
        column="shifted",
        oracle=lambda x: None if x is None else x + 1.5,
    ),
    RowIndependentTransform(
        name="flag",
        code="df = src.with_columns(flag=pl.col('x') > 0)",
        column="flag",
        oracle=lambda x: None if x is None else x > 0,
    ),
    RowIndependentTransform(
        name="filled",
        code="df = src.with_columns(filled=pl.col('x').fill_null(0.0))",
        column="filled",
        oracle=lambda x: 0.0 if x is None else float(x),
    ),
    RowIndependentTransform(
        name="binned",
        code="df = src.with_columns(binned=pl.when(pl.col('x') > 0.0).then(1.0).otherwise(-1.0))",
        column="binned",
        oracle=lambda x: 1.0 if (x is not None and x > 0.0) else -1.0,
    ),
)


def _build_scoring_graph(parquet_path: Path, code: str):
    """Build a standard 3-node linear pipeline: apiInput -> polars -> dataOutput."""
    return _g(
        {
            "nodes": [
                {
                    "id": "src",
                    "data": {
                        "label": "src",
                        "nodeType": "apiInput",
                        "config": {"path": str(parquet_path), "sourceType": "flat_file"},
                    },
                },
                {
                    "id": "trans",
                    "data": {
                        "label": "trans",
                        "nodeType": "polars",
                        "config": {"code": code},
                    },
                },
                {
                    "id": "out",
                    "data": {
                        "label": "out",
                        "nodeType": "dataOutput",
                        "config": {"output": True},
                    },
                },
            ],
            "edges": [
                {"id": "e1", "source": "src", "target": "trans", "sourceHandle": "src"},
                {"id": "e2", "source": "trans", "target": "out"},
            ],
        }
    )


def to_json_safe(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Round-trip rows through JSON to match deploy and frontend representations."""
    return json.loads(json.dumps(rows))


def _values_agree(actual: Any, expected: Any) -> bool:
    """Compare an actual row value against a plain-Python oracle expectation."""
    if actual is None and expected is None:
        return True
    if actual is None or expected is None:
        return False
    if isinstance(actual, bool) or isinstance(expected, bool):
        return type(actual) is type(expected) and actual == expected
    if isinstance(actual, (int, float)) and isinstance(expected, (int, float)):
        return math.isclose(actual, expected, rel_tol=1e-9, abs_tol=1e-9)
    return actual == expected


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

row_strategy = st.fixed_dictionaries(
    {
        "x": st.floats(min_value=-100.0, max_value=100.0, allow_nan=False) | st.none(),
        "label": st.text(alphabet=string.ascii_letters + string.digits, min_size=1, max_size=8),
    }
)

frame_strategy = st.lists(row_strategy, min_size=1, max_size=8)


@st.composite
def frame_and_permutation_strategy(
    draw: st.DrawFn,
) -> tuple[list[dict[str, Any]], list[int]]:
    rows = draw(frame_strategy)
    perm = draw(st.permutations(range(len(rows))))
    return rows, list(perm)


@st.composite
def distinct_transform_pair_strategy(
    draw: st.DrawFn,
) -> tuple[RowIndependentTransform, RowIndependentTransform]:
    idx1 = draw(st.integers(min_value=0, max_value=len(ROW_INDEPENDENT_TRANSFORMS) - 1))
    offset = draw(st.integers(min_value=1, max_value=len(ROW_INDEPENDENT_TRANSFORMS) - 1))
    idx2 = (idx1 + offset) % len(ROW_INDEPENDENT_TRANSFORMS)
    return ROW_INDEPENDENT_TRANSFORMS[idx1], ROW_INDEPENDENT_TRANSFORMS[idx2]


@st.composite
def order_sensitive_frame_and_perm(
    draw: st.DrawFn,
) -> tuple[list[dict[str, Any]], list[int]]:
    """Two rows with distinct values and the swap: the smallest order-sensitive domain."""
    first, second = draw(
        st.lists(st.integers(min_value=-5, max_value=5), min_size=2, max_size=2, unique=True)
    )
    rows = [{"x": float(first), "label": "a"}, {"x": float(second), "label": "b"}]
    return rows, [1, 0]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pr_budget(20)
@given(data=frame_and_permutation_strategy())
@example(
    data=(
        [{"x": 1.5, "label": "r1"}, {"x": -2.0, "label": "r2"}, {"x": None, "label": "r3"}],
        [2, 0, 1],
    )
)
def test_request_permutation_preserves_per_request_answers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    data: tuple[list[dict[str, Any]], list[int]],
) -> None:
    rows, perm = data
    sub_dir = tmp_path / uuid.uuid4().hex
    sub_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(sub_dir)
    input_file = sub_dir / "input.parquet"

    input_df = pl.DataFrame(rows, schema=FRAME_SCHEMA)
    input_df.write_parquet(input_file)
    permuted_df = pl.DataFrame([rows[i] for i in perm], schema=FRAME_SCHEMA)

    for transform in ROW_INDEPENDENT_TRANSFORMS:
        graph = _build_scoring_graph(input_file, transform.code)
        out_id = find_output_node(graph)
        pruned, _kept, _removed = prune_for_deploy(graph, out_id)

        unpermuted_res = score_graph(
            graph=pruned,
            input_df=input_df,
            input_node_ids=["src"],
            output_node_id=out_id,
        )
        permuted_res = score_graph(
            graph=pruned,
            input_df=permuted_df,
            input_node_ids=["src"],
            output_node_id=out_id,
        )

        unpermuted_json = to_json_safe(unpermuted_res.to_dicts())
        permuted_json = to_json_safe(permuted_res.to_dicts())

        expected_permuted_json = [unpermuted_json[i] for i in perm]
        assert permuted_json == expected_permuted_json, (
            f"Permutation relation failed for transform '{transform.name}': "
            f"permuted={permuted_json} expected={expected_permuted_json}"
        )


@pr_budget(40)
@given(
    rows=frame_strategy,
    transform=st.sampled_from(ROW_INDEPENDENT_TRANSFORMS),
)
@example(
    rows=[{"x": 1.5, "label": "r1"}, {"x": -2.0, "label": "r2"}, {"x": None, "label": "r3"}],
    transform=ROW_INDEPENDENT_TRANSFORMS[0],
)
def test_editor_and_scorer_agree_on_generated_rows(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    rows: list[dict[str, Any]],
    transform: RowIndependentTransform,
) -> None:
    sub_dir = tmp_path / uuid.uuid4().hex
    sub_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(sub_dir)
    input_file = sub_dir / "input.parquet"

    input_df = pl.DataFrame(rows, schema=FRAME_SCHEMA)
    input_df.write_parquet(input_file)

    graph = _build_scoring_graph(input_file, transform.code)
    out_id = find_output_node(graph)
    pruned, _kept, _removed = prune_for_deploy(graph, out_id)

    # 1. Editor path: execute_graph preview
    exec_results = execute_graph(graph, target_node_id=out_id)
    assert exec_results[out_id].status == "ok", exec_results[out_id].error
    editor_rows = to_json_safe(exec_results[out_id].preview)

    # 2. Deploy path: score_graph
    score_df = score_graph(
        graph=pruned,
        input_df=input_df,
        input_node_ids=["src"],
        output_node_id=out_id,
    )
    score_rows = to_json_safe(score_df.to_dicts())

    # Parity between editor preview and deploy scorer
    assert editor_rows == score_rows, (
        f"Editor and deploy scorer disagreed for '{transform.name}': "
        f"editor={editor_rows} scorer={score_rows}"
    )

    # Independent oracle check against plain-Python expectation
    assert len(score_rows) == len(rows)
    for orig, row in zip(rows, score_rows, strict=True):
        expected_val = transform.oracle(orig["x"])
        actual_val = row[transform.column]
        assert _values_agree(actual_val, expected_val), (
            f"Derived column '{transform.column}' disagreed with oracle: "
            f"input_x={orig['x']!r}, expected={expected_val!r}, actual={actual_val!r}"
        )


def test_order_sensitive_operations_break_the_permutation_relation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sub_dir = tmp_path / "negative_control"
    sub_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(sub_dir)
    input_file = sub_dir / "cum_sum.parquet"

    order_sensitive_code = "df = src.with_columns(running=pl.col('x').cum_sum())"
    graph = _build_scoring_graph(input_file, order_sensitive_code)
    out_id = find_output_node(graph)
    pruned, _kept, _removed = prune_for_deploy(graph, out_id)

    def _permutation_relation_fails(data: tuple[list[dict[str, Any]], list[int]]) -> bool:
        rows, perm = data
        if perm == list(range(len(rows))):
            return False
        input_df = pl.DataFrame(rows, schema=FRAME_SCHEMA)
        permuted_df = pl.DataFrame([rows[i] for i in perm], schema=FRAME_SCHEMA)

        u_res = score_graph(
            graph=pruned,
            input_df=input_df,
            input_node_ids=["src"],
            output_node_id=out_id,
        )
        p_res = score_graph(
            graph=pruned,
            input_df=permuted_df,
            input_node_ids=["src"],
            output_node_id=out_id,
        )

        u_json = to_json_safe(u_res.to_dicts())
        p_json = to_json_safe(p_res.to_dicts())
        expected = [u_json[i] for i in perm]
        return p_json != expected

    found_rows, found_perm = find(
        order_sensitive_frame_and_perm(),
        _permutation_relation_fails,
        settings=pr_budget(10),
    )

    # Assert that the discovered example breaks the permutation relation
    assert _permutation_relation_fails((found_rows, found_perm)), (
        f"Expected permutation relation to fail for {found_rows} with perm {found_perm}"
    )

    # Retain the minimal failing example discovered during development:
    # 2 rows with x=[0.0, 1.0] permuted to [1, 0] yields running=[1.0, 1.0] vs expected [1.0, 0.0]
    minimal_example = (
        [{"x": 0.0, "label": "a"}, {"x": 1.0, "label": "b"}],
        [1, 0],
    )
    assert _permutation_relation_fails(minimal_example)


@pr_budget(30)
@given(
    rows=frame_strategy,
    transforms=distinct_transform_pair_strategy(),
)
@example(
    rows=[{"x": 1.5, "label": "r1"}, {"x": -2.0, "label": "r2"}, {"x": None, "label": "r3"}],
    transforms=(ROW_INDEPENDENT_TRANSFORMS[0], ROW_INDEPENDENT_TRANSFORMS[1]),
)
def test_cold_and_warm_execution_agree_and_a_code_edit_is_never_served_stale(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    rows: list[dict[str, Any]],
    transforms: tuple[RowIndependentTransform, RowIndependentTransform],
) -> None:
    t_initial, t_edited = transforms
    sub_dir = tmp_path / uuid.uuid4().hex
    sub_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(sub_dir)
    input_file = sub_dir / "input.parquet"

    input_df = pl.DataFrame(rows, schema=FRAME_SCHEMA)
    input_df.write_parquet(input_file)

    graph_initial = _build_scoring_graph(input_file, t_initial.code)
    out_id = find_output_node(graph_initial)

    # Spy on the process-wide preview cache so a warm run is proven to be a
    # cache hit, not merely an equal recomputation. LRUCache uses __slots__,
    # so the class method is patched inside a context restored per example.
    hits: list[str] = []
    cache_type = type(_preview_cache)
    class_get = cache_type.get

    def _counting_get(cache: Any, key: str) -> dict[str, Any] | None:
        value = class_get(cache, key)
        if value is not None and cache is _preview_cache:
            hits.append(key)
        return value

    with pytest.MonkeyPatch.context() as cache_patch:
        cache_patch.setattr(cache_type, "get", _counting_get)
        # 1. Cold execution (populates preview cache)
        cold_results = execute_graph(graph_initial, target_node_id=out_id)
        assert cold_results[out_id].status == "ok", cold_results[out_id].error
        cold_preview = to_json_safe(cold_results[out_id].preview)
        assert hits == [], "a fresh graph and input must not hit the preview cache"

        # 2. Warm execution (served from in-process _preview_cache)
        warm_results = execute_graph(graph_initial, target_node_id=out_id)
        assert warm_results[out_id].status == "ok", warm_results[out_id].error
        warm_preview = to_json_safe(warm_results[out_id].preview)
        assert len(hits) == 1, "the warm run must be served from the preview cache"

    # Assert cold and warm executions return identical previews
    assert cold_preview == warm_preview, (
        f"Cold and warm preview disagreed for '{t_initial.name}': "
        f"cold={cold_preview} warm={warm_preview}"
    )

    # 3. Code edit: replace transform code with a distinct transform
    graph_edited = _build_scoring_graph(input_file, t_edited.code)
    edited_results = execute_graph(graph_edited, target_node_id=out_id)
    assert edited_results[out_id].status == "ok", edited_results[out_id].error
    edited_preview = to_json_safe(edited_results[out_id].preview)

    # Verify that the edit is never served stale by checking against the new transform's oracle
    assert len(edited_preview) == len(rows)
    for orig, row in zip(rows, edited_preview, strict=True):
        expected_val = t_edited.oracle(orig["x"])
        actual_val = row[t_edited.column]
        assert _values_agree(actual_val, expected_val), (
            f"Stale or incorrect result after edit to '{t_edited.name}': "
            f"input_x={orig['x']!r}, expected={expected_val!r}, actual={actual_val!r}"
        )
