"""Generic chunk-runner contract tests."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any
from unittest.mock import patch

import polars as pl
import polars.testing as plt
import pytest

from haute._execute_lazy import _execute_lazy
from haute._execution_context import (
    ExecutionCancelledError,
    ExecutionContext,
    ExecutionProfile,
)
from haute._types import GraphNode
from haute.chunking import (
    ChunkBatch,
    ChunkPlanRequest,
    ChunkRunnerRequest,
    chunk_plan,
    collect_chunked,
    is_chunk_local_polars_code,
    iter_chunked_frames,
    run_chunked_reduce,
)
from haute.errors import ChunkPlanUnsupportedError, GroupByExecutionUnsupportedError
from haute.executor import _build_node_fn
from tests.conftest import make_edge, make_graph, make_output_config

DEFAULT_OUTPUT_FIELDS = [
    "quote_id",
    "age_band",
    "premium",
    "scenario_index",
    "scenario_value",
]


def _node(node_id: str, node_type: str, config: dict[str, object] | None = None):
    config = dict(config or {})
    if node_type == "dataInput" and "path" in config:
        suffix = Path(str(config["path"])).suffix.lower().lstrip(".")
        formats = {
            "jsonl": "ndjson",
            "ndjson": "ndjson",
            "arrow": "ipc",
            "feather": "ipc",
            "ipc": "ipc",
        }
        config = {
            **config,
            "inputType": "file",
            "format": formats.get(suffix, suffix),
            "cacheMode": "direct",
        }
    return {
        "id": node_id,
        "data": {
            "label": node_id,
            "nodeType": node_type,
            "config": config,
        },
    }


def _write_source(tmp_path: Path) -> Path:
    path = tmp_path / "quotes.parquet"
    pl.DataFrame(
        {
            "quote_id": ["q1", "q2", "q3", "q4", "q5", "q6"],
            "age": [18, 24, 25, 39, 40, None],
            "premium": [100.0, 150.0, 200.0, 250.0, 300.0, 350.0],
            "unused_payload": ["drop"] * 6,
        }
    ).write_parquet(path)
    return path


def _chunk_safe_graph(path: Path, *, output_fields: list[str] | None = None):
    return make_graph(
        {
            "nodes": [
                _node("source", "dataInput", {"path": str(path)}),
                _node(
                    "age_band",
                    "banding",
                    {
                        "factors": [
                            {
                                "column": "age",
                                "outputColumn": "age_band",
                                "banding": "continuous",
                                "rules": [
                                    {
                                        "op1": ">=",
                                        "val1": 0,
                                        "op2": "<",
                                        "val2": 25,
                                        "assignment": "young",
                                    },
                                    {
                                        "op1": ">=",
                                        "val1": 25,
                                        "op2": "<",
                                        "val2": 40,
                                        "assignment": "adult",
                                    },
                                ],
                                "default": "other",
                            },
                        ],
                    },
                ),
                _node(
                    "scenario",
                    "scenarioExpander",
                    {
                        "column_name": "scenario_value",
                        "min_value": 0.9,
                        "max_value": 1.1,
                        "steps": 3,
                        "step_column": "scenario_index",
                    },
                ),
                _node(
                    "out",
                    "output",
                    make_output_config(output_fields or DEFAULT_OUTPUT_FIELDS),
                ),
            ],
            "edges": [
                make_edge("source", "age_band").model_dump(),
                make_edge("age_band", "scenario").model_dump(),
                make_edge("scenario", "out").model_dump(),
            ],
        }
    )


def _required_columns(columns: list[str] | frozenset[str] | None = None) -> frozenset[str]:
    return frozenset(columns or DEFAULT_OUTPUT_FIELDS)


def _full_lazy_output(
    graph,
    *,
    required_columns: frozenset[str] | None = None,
) -> pl.DataFrame:
    outputs, *_ = _execute_lazy(
        graph,
        _build_node_fn,
        target_node_id="out",
        source="batch",
        required_columns_by_node={"out": required_columns or _required_columns()},
    )
    return outputs["out"].collect(engine="streaming")


def _chunk_runner_plan(
    graph,
    *,
    chunk_size: int,
    required_columns: frozenset[str] | None = None,
):
    return chunk_plan(
        ChunkPlanRequest(
            graph=graph,
            target_node_id="out",
            chunk_size=chunk_size,
            required_columns_by_node={"out": required_columns or _required_columns()},
        )
    )


def _run_chunked(
    graph,
    *,
    chunk_size: int,
    checkpoint_dir: Path | None = None,
    required_columns: frozenset[str] | None = None,
    build_node_fn: Callable[..., tuple[str, Callable[..., Any], bool]] = _build_node_fn,
    execution_context: ExecutionContext | None = None,
) -> pl.DataFrame:
    plan = chunk_plan(
        ChunkPlanRequest(
            graph=graph,
            target_node_id="out",
            chunk_size=chunk_size,
            required_columns_by_node={"out": required_columns or _required_columns()},
        )
    )
    return collect_chunked(
        ChunkRunnerRequest(
            graph=graph,
            plan=plan,
            build_node_fn=build_node_fn,
            checkpoint_dir=checkpoint_dir,
            execution_context=execution_context,
        ),
        allow_unbounded=True,
    )


@pytest.mark.parametrize(
    "chunk_size",
    [
        pytest.param(1, id="one"),
        pytest.param(5, id="prime"),
        pytest.param(9, id="exact-boundary"),
        pytest.param(100, id="larger-than-input"),
    ],
)
def test_chunk_runner_matches_full_lazy_for_chunk_safe_chain(
    tmp_path: Path,
    chunk_size: int,
) -> None:
    graph = _chunk_safe_graph(_write_source(tmp_path))

    actual = _run_chunked(graph, chunk_size=chunk_size)
    expected = _full_lazy_output(graph)

    plt.assert_frame_equal(actual, expected)
    assert "unused_payload" not in actual.columns


def test_chunk_runner_derives_start_input_name_from_api_frame_edge() -> None:
    """A supplied intermediate frame must not make chunking forget its edge identity."""
    graph = make_graph(
        {
            "nodes": [
                _node("api", "apiInput"),
                _node("chunk_start", "polars"),
                _node("target", "polars"),
            ],
            "edges": [
                {
                    "id": "api_chunk_start",
                    "source": "api",
                    "target": "chunk_start",
                    "sourceHandle": "quotes",
                },
                make_edge("chunk_start", "target").model_dump(),
            ],
        }
    )
    plan = chunk_plan(
        ChunkPlanRequest(
            graph=graph,
            target_node_id="target",
            chunk_start_node_id="chunk_start",
            chunk_size=2,
            required_columns_by_node={"target": {"x"}},
        )
    )
    captured_source_names: dict[str, list[str]] = {}

    def recording_builder(
        node: GraphNode,
        *,
        source_names: list[str] | None = None,
        **_kwargs: Any,
    ) -> tuple[str, Callable[..., Any], bool]:
        captured_source_names[node.id] = list(source_names or [])
        return node.id, lambda *frames: frames[0], node.data.nodeType == "apiInput"

    batches = list(
        iter_chunked_frames(
            ChunkRunnerRequest(
                graph=graph,
                plan=plan,
                build_node_fn=recording_builder,
                start_frame=pl.DataFrame({"x": [1, 2, 3]}),
            )
        )
    )

    assert sum(batch.output_rows for batch in batches) == 3
    assert captured_source_names["chunk_start"] == ["quotes"]


def test_chunk_runner_projects_source_columns_before_first_map_node(tmp_path: Path) -> None:
    output_fields = ["quote_id", "age_band", "scenario_index"]
    required_columns = _required_columns(output_fields)
    graph = _chunk_safe_graph(_write_source(tmp_path), output_fields=output_fields)
    observed_band_inputs: list[list[str]] = []

    def recording_build_node_fn(
        node: GraphNode,
        **kwargs: Any,
    ) -> tuple[str, Callable[..., Any], bool]:
        name, fn, is_source = _build_node_fn(node, **kwargs)
        if node.id != "age_band":
            return name, fn, is_source

        def recording_band(*frames: Any) -> Any:
            observed_band_inputs.append(frames[0].collect_schema().names())
            return fn(*frames)

        return name, recording_band, is_source

    actual = _run_chunked(
        graph,
        chunk_size=5,
        required_columns=required_columns,
        build_node_fn=recording_build_node_fn,
    )
    expected = _full_lazy_output(graph, required_columns=required_columns)

    plt.assert_frame_equal(actual, expected)
    assert observed_band_inputs
    assert all("unused_payload" not in columns for columns in observed_band_inputs)
    assert all("premium" not in columns for columns in observed_band_inputs)
    assert all(set(columns) == {"quote_id", "age"} for columns in observed_band_inputs)


def test_chunk_runner_records_row_expansion_in_plan(tmp_path: Path) -> None:
    graph = _chunk_safe_graph(_write_source(tmp_path))

    plan = chunk_plan(
        ChunkPlanRequest(
            graph=graph,
            target_node_id="out",
            chunk_size=9,
            required_columns_by_node={"out": _required_columns()},
        )
    )

    assert plan.row_expansion_factor == 3
    assert plan.source_chunk_size == 3


@pytest.mark.parametrize(
    ("chunk_size", "expected_max_rows"),
    [
        pytest.param(2, 3, id="smaller-than-expansion-factor"),
        pytest.param(5, 3, id="non-divisible"),
        pytest.param(9, 9, id="exact-boundary"),
    ],
)
def test_chunk_runner_bounds_output_rows_by_expansion_adjusted_source_batches(
    tmp_path: Path,
    chunk_size: int,
    expected_max_rows: int,
) -> None:
    graph = _chunk_safe_graph(_write_source(tmp_path))
    plan = _chunk_runner_plan(graph, chunk_size=chunk_size)

    batches = list(
        iter_chunked_frames(
            ChunkRunnerRequest(graph=graph, plan=plan, build_node_fn=_build_node_fn)
        )
    )

    assert batches
    assert max(batch.output_rows for batch in batches) <= expected_max_rows
    assert sum(batch.output_rows for batch in batches) == _full_lazy_output(graph).height


def test_chunk_runner_can_start_from_proven_intermediate_frame(tmp_path: Path) -> None:
    graph = _chunk_safe_graph(_write_source(tmp_path))
    base_outputs, *_ = _execute_lazy(
        graph,
        _build_node_fn,
        target_node_id="age_band",
        source="batch",
        required_columns_by_node={"age_band": {"quote_id", "age_band", "premium"}},
    )
    plan = chunk_plan(
        ChunkPlanRequest(
            graph=graph,
            target_node_id="out",
            chunk_start_node_id="age_band",
            chunk_size=9,
            required_columns_by_node={"out": _required_columns()},
        )
    )

    actual = collect_chunked(
        ChunkRunnerRequest(
            graph=graph,
            plan=plan,
            build_node_fn=_build_node_fn,
            start_frame=base_outputs["age_band"],
        ),
        allow_unbounded=True,
    )

    assert plan.pre_chunk_node_ids == ("source",)
    assert plan.chunk_node_ids == ("age_band", "scenario", "out")
    assert plan.row_expansion_factor == 3
    plt.assert_frame_equal(actual, _full_lazy_output(graph))


def test_chunk_plan_allows_multi_source_prefix_when_start_frame_is_supplied() -> None:
    graph = make_graph(
        {
            "nodes": [
                _node("left", "dataInput", {"path": "left.parquet"}),
                _node("right", "dataInput", {"path": "right.parquet"}),
                _node(
                    "joined",
                    "polars",
                    {
                        "code": "df = left.join(right, on='quote_id')",
                        "contract": {
                            "inputs": ["quote_id", "premium"],
                            "outputs": [],
                            "inputs_by_parent": {
                                "left": ["quote_id", "premium"],
                                "right": ["quote_id"],
                            },
                        },
                    },
                ),
                _node(
                    "scenario",
                    "scenarioExpander",
                    {
                        "column_name": "scenario_value",
                        "min_value": 0.9,
                        "max_value": 1.1,
                        "steps": 2,
                        "step_column": "scenario_index",
                    },
                ),
                _node(
                    "out",
                    "output",
                    make_output_config(["quote_id", "premium", "scenario_index", "scenario_value"]),
                ),
            ],
            "edges": [
                make_edge("left", "joined").model_dump(),
                make_edge("right", "joined").model_dump(),
                make_edge("joined", "scenario").model_dump(),
                make_edge("scenario", "out").model_dump(),
            ],
        }
    )
    plan = chunk_plan(
        ChunkPlanRequest(
            graph=graph,
            target_node_id="out",
            chunk_start_node_id="joined",
            chunk_size=4,
            required_columns_by_node={
                "out": {"quote_id", "premium", "scenario_index", "scenario_value"}
            },
        )
    )

    actual = collect_chunked(
        ChunkRunnerRequest(
            graph=graph,
            plan=plan,
            build_node_fn=_build_node_fn,
            start_frame=pl.DataFrame({"quote_id": ["q1", "q2"], "premium": [100.0, 200.0]}),
        ),
        allow_unbounded=True,
    )

    assert plan.source_node_id is None
    assert plan.pre_chunk_node_ids == ("left", "right")
    assert actual.height == 4
    assert actual["scenario_index"].to_list() == [0, 1, 0, 1]


def test_chunk_runner_ignores_nested_prefix_edge_demands_with_start_frame() -> None:
    graph = make_graph(
        {
            "nodes": [
                _node("left", "dataInput", {"path": "left.parquet"}),
                _node("right", "dataInput", {"path": "right.parquet"}),
                _node(
                    "joined",
                    "polars",
                    {
                        "code": "df = left.join(right, on='quote_id')",
                        "contract": {
                            "inputs": ["quote_id", "premium", "factor"],
                            "outputs": [],
                            "inputs_by_parent": {
                                "left": ["quote_id", "premium"],
                                "right": ["quote_id", "factor"],
                            },
                        },
                    },
                ),
                _node(
                    "features",
                    "polars",
                    {
                        "code": (
                            "df = joined.with_columns("
                            "adjusted_premium=pl.col('premium') * pl.col('factor'))"
                        ),
                        "contract": {
                            "inputs": ["premium", "factor"],
                            "outputs": ["adjusted_premium"],
                        },
                    },
                ),
                _node(
                    "scenario",
                    "scenarioExpander",
                    {
                        "column_name": "scenario_value",
                        "min_value": 0.9,
                        "max_value": 1.1,
                        "steps": 2,
                        "step_column": "scenario_index",
                    },
                ),
                _node(
                    "out",
                    "output",
                    make_output_config(
                        [
                            "quote_id",
                            "adjusted_premium",
                            "scenario_index",
                            "scenario_value",
                        ]
                    ),
                ),
            ],
            "edges": [
                make_edge("left", "joined").model_dump(),
                make_edge("right", "joined").model_dump(),
                make_edge("joined", "features").model_dump(),
                make_edge("features", "scenario").model_dump(),
                make_edge("scenario", "out").model_dump(),
            ],
        }
    )
    plan = chunk_plan(
        ChunkPlanRequest(
            graph=graph,
            target_node_id="out",
            chunk_start_node_id="features",
            chunk_size=4,
            required_columns_by_node={
                "out": {
                    "quote_id",
                    "adjusted_premium",
                    "scenario_index",
                    "scenario_value",
                }
            },
        )
    )

    actual = collect_chunked(
        ChunkRunnerRequest(
            graph=graph,
            plan=plan,
            build_node_fn=_build_node_fn,
            start_frame=pl.DataFrame({"quote_id": ["q1"], "adjusted_premium": [250.0]}),
        ),
        allow_unbounded=True,
    )

    assert plan.pre_chunk_node_ids == ("left", "right", "joined")
    assert actual.height == 2
    assert actual["adjusted_premium"].to_list() == [250.0, 250.0]


def test_chunk_runner_supports_row_local_polars_transform(tmp_path: Path) -> None:
    source_path = _write_source(tmp_path)
    output_fields = ["quote_id", "scenario_index", "scenario_value", "adjusted_premium"]
    required_columns = _required_columns(output_fields)
    graph = make_graph(
        {
            "nodes": [
                _node("source", "dataInput", {"path": str(source_path)}),
                _node(
                    "scenario",
                    "scenarioExpander",
                    {
                        "column_name": "scenario_value",
                        "min_value": 0.9,
                        "max_value": 1.1,
                        "steps": 3,
                        "step_column": "scenario_index",
                    },
                ),
                _node(
                    "features",
                    "polars",
                    {
                        "code": (
                            "df = scenario.with_columns("
                            "adjusted_premium=pl.col('premium') * "
                            "pl.col('scenario_value'))"
                        ),
                        "contract": {
                            "inputs": ["premium", "scenario_value"],
                            "outputs": ["adjusted_premium"],
                        },
                    },
                ),
                _node("out", "output", make_output_config(output_fields)),
            ],
            "edges": [
                make_edge("source", "scenario").model_dump(),
                make_edge("scenario", "features").model_dump(),
                make_edge("features", "out").model_dump(),
            ],
        }
    )

    actual = _run_chunked(
        graph,
        chunk_size=5,
        required_columns=required_columns,
    )
    expected = _full_lazy_output(graph, required_columns=required_columns)

    plt.assert_frame_equal(actual, expected)


def test_chunk_runner_reuses_model_score_model_across_chunks(tmp_path: Path) -> None:
    from haute._mlflow_io import ScoringModel
    from haute.modelling._feature_contract import build_contract, save_contract

    source_path = tmp_path / "score.parquet"
    pl.DataFrame(
        {
            "quote_id": ["q1", "q2", "q3", "q4", "q5"],
            "feature": [1.0, 2.0, 3.0, 4.0, 5.0],
        }
    ).write_parquet(source_path)
    contract_path = tmp_path / "feature_contract.json"
    save_contract(
        build_contract(
            features=["feature"],
            feature_types={"feature": "Float64"},
            categorical_features=[],
            target_name="target",
            target_type="Float64",
            task="regression",
        ),
        contract_path,
    )
    output_fields = ["quote_id", "prediction"]
    graph = make_graph(
        {
            "nodes": [
                _node("source", "dataInput", {"path": str(source_path)}),
                _node(
                    "score",
                    "modelScore",
                    {
                        "sourceType": "run",
                        "run_id": "run-1",
                        "artifact_path": "model.pyfunc",
                        "task": "regression",
                        "output_column": "prediction",
                        "feature_contract_path": str(contract_path),
                        "model_reuse_lifetime": "batch",
                    },
                ),
                _node("out", "output", make_output_config(output_fields)),
            ],
            "edges": [
                make_edge("source", "score").model_dump(),
                make_edge("score", "out").model_dump(),
            ],
        }
    )
    plan = chunk_plan(
        ChunkPlanRequest(
            graph=graph,
            target_node_id="out",
            chunk_size=2,
            required_columns_by_node={"out": output_fields},
        )
    )
    scoring_model = ScoringModel(object(), ["feature"])
    scored_chunk_sizes: list[int] = []

    def fake_score_eager(scoring_model, lf, features, output_col, task):
        del scoring_model, features, task
        scored_chunk_sizes.append(lf.select(pl.len()).collect().item())
        return lf.with_columns((pl.col("feature") * 10).alias(output_col))

    with (
        patch("haute._mlflow_io.load_mlflow_model", return_value=scoring_model) as load_model,
        patch("haute._mlflow_io._score_eager", side_effect=fake_score_eager),
    ):
        actual = collect_chunked(
            ChunkRunnerRequest(graph=graph, plan=plan, build_node_fn=_build_node_fn),
            allow_unbounded=True,
        )

    assert load_model.call_count == 1
    assert scored_chunk_sizes == [2, 2, 1]
    assert actual["prediction"].to_list() == [10.0, 20.0, 30.0, 40.0, 50.0]


def test_chunk_plan_rejects_global_polars_transform(tmp_path: Path) -> None:
    source_path = _write_source(tmp_path)
    graph = make_graph(
        {
            "nodes": [
                _node("source", "dataInput", {"path": str(source_path)}),
                _node(
                    "global",
                    "polars",
                    {
                        "code": "df = source.group_by('quote_id').agg(pl.col('premium').sum())",
                        "contract": {
                            "inputs": ["quote_id", "premium"],
                            "outputs": ["premium"],
                        },
                    },
                ),
                _node("out", "output", make_output_config(["quote_id", "premium"])),
            ],
            "edges": [
                make_edge("source", "global").model_dump(),
                make_edge("global", "out").model_dump(),
            ],
        }
    )

    with pytest.raises(GroupByExecutionUnsupportedError) as exc_info:
        chunk_plan(
            ChunkPlanRequest(
                graph=graph,
                target_node_id="out",
                chunk_size=5,
                required_columns_by_node={"out": {"quote_id", "premium"}},
            )
        )
    assert exc_info.value.reason_code == "profile_requires_bounded_execution"


def test_chunk_local_polars_guard_accepts_row_local_and_rejects_global() -> None:
    assert is_chunk_local_polars_code(
        "df = source.with_columns(y=pl.col('x') * 2).filter(pl.col('x') > 0)",
        frame_names=("source",),
    )
    assert not is_chunk_local_polars_code(
        "df = source.group_by('quote_id').agg(pl.col('x').sum())",
        frame_names=("source",),
    )
    assert not is_chunk_local_polars_code(
        "df = GLOBAL_LAZY_FRAME.with_columns(y=pl.col('x') * 2)",
        frame_names=("source",),
    )
    assert not is_chunk_local_polars_code(
        "df = source.with_columns(flag=pl.col('x').is_in(source.select('y')))",
        frame_names=("source",),
    )


@pytest.mark.parametrize(
    ("graph_factory", "match", "expected_context"),
    [
        pytest.param(
            lambda tmp_path: make_graph(
                {
                    "nodes": [
                        _node("source", "dataInput", {"path": str(tmp_path / "data.json")}),
                        _node("out", "output", make_output_config(["quote_id"])),
                    ],
                    "edges": [make_edge("source", "out").model_dump()],
                }
            ),
            "bounded lazy scan",
            {"node_id": "source", "node_type": "dataInput"},
            id="unsupported-source",
        ),
        pytest.param(
            lambda tmp_path: make_graph(
                {
                    "nodes": [
                        _node("source", "dataInput", {"path": str(tmp_path / "data.parquet")}),
                        _node(
                            "left",
                            "banding",
                            {"factors": [{"column": "age", "outputColumn": "age_band"}]},
                        ),
                        _node(
                            "right",
                            "banding",
                            {"factors": [{"column": "premium", "outputColumn": "premium_band"}]},
                        ),
                        _node("join", "polars", {"code": "return left.join(right, on='id')"}),
                        _node("out", "output", make_output_config(["quote_id"])),
                    ],
                    "edges": [
                        make_edge("source", "left").model_dump(),
                        make_edge("source", "right").model_dump(),
                        make_edge("left", "join").model_dump(),
                        make_edge("right", "join").model_dump(),
                        make_edge("join", "out").model_dump(),
                    ],
                }
            ),
            "exactly one parent",
            {"node_id": "join", "node_type": "polars"},
            id="fan-in",
        ),
    ],
)
def test_unsupported_chunk_graphs_fail_during_planning(
    tmp_path: Path,
    graph_factory: Callable[[Path], Any],
    match: str,
    expected_context: dict[str, str],
) -> None:
    graph = graph_factory(tmp_path)

    with pytest.raises(ChunkPlanUnsupportedError, match=match) as exc_info:
        chunk_plan(
            ChunkPlanRequest(
                graph=graph,
                target_node_id="out",
                chunk_size=2,
                required_columns_by_node={"out": {"quote_id"}},
            )
        )
    for key, value in expected_context.items():
        assert exc_info.value.context[key] == value


def test_collect_chunked_requires_explicit_unbounded_opt_in(tmp_path: Path) -> None:
    graph = _chunk_safe_graph(_write_source(tmp_path))
    plan = _chunk_runner_plan(graph, chunk_size=5)

    with pytest.raises(ChunkPlanUnsupportedError, match="retains all rows"):
        collect_chunked(ChunkRunnerRequest(graph=graph, plan=plan, build_node_fn=_build_node_fn))


def test_run_chunked_reduce_rejects_unbounded_reducer(tmp_path: Path) -> None:
    graph = _chunk_safe_graph(_write_source(tmp_path))
    plan = _chunk_runner_plan(graph, chunk_size=5)

    class RetainingReducer:
        bounded = False

        def __init__(self) -> None:
            self.frames: list[pl.DataFrame] = []

        def add(self, batch: ChunkBatch) -> None:
            self.frames.append(batch.frame)

        def finish(self) -> list[pl.DataFrame]:
            return self.frames

    with pytest.raises(ChunkPlanUnsupportedError, match="bounded=True"):
        run_chunked_reduce(
            ChunkRunnerRequest(graph=graph, plan=plan, build_node_fn=_build_node_fn),
            RetainingReducer(),
        )


def test_run_chunked_reduce_accepts_bounded_reducer(tmp_path: Path) -> None:
    graph = _chunk_safe_graph(_write_source(tmp_path))
    plan = _chunk_runner_plan(graph, chunk_size=5)

    class RowCounter:
        bounded = True

        def __init__(self) -> None:
            self.rows = 0

        def add(self, batch: ChunkBatch) -> None:
            self.rows += batch.output_rows

        def finish(self) -> int:
            return self.rows

    assert (
        run_chunked_reduce(
            ChunkRunnerRequest(graph=graph, plan=plan, build_node_fn=_build_node_fn),
            RowCounter(),
        )
        == 18
    )


def test_chunk_runner_creates_checkpoint_directory_once_per_run(tmp_path: Path) -> None:
    graph = _chunk_safe_graph(_write_source(tmp_path))
    checkpoint_dir = tmp_path / "checkpoint-once"
    original_mkdir = Path.mkdir
    checkpoint_mkdir_calls: list[Path] = []

    def tracked_mkdir(path: Path, *args: Any, **kwargs: Any) -> None:
        if path == checkpoint_dir:
            checkpoint_mkdir_calls.append(path)
        original_mkdir(path, *args, **kwargs)

    with patch.object(Path, "mkdir", tracked_mkdir):
        result = _run_chunked(
            graph,
            chunk_size=5,
            checkpoint_dir=checkpoint_dir,
        )

    assert result.height == 18
    assert checkpoint_mkdir_calls == [checkpoint_dir]


def test_chunk_runner_cancels_before_next_chunk_and_cleans_checkpoints(tmp_path: Path) -> None:
    graph = _chunk_safe_graph(_write_source(tmp_path))
    plan = _chunk_runner_plan(graph, chunk_size=5)
    checkpoint_dir = tmp_path / "chunks"
    context = ExecutionContext(
        operation="chunk-test",
        profile=ExecutionProfile.CHUNKED_MAP_REDUCE,
        job_id="chunk-job",
    )
    iterator = iter(
        iter_chunked_frames(
            ChunkRunnerRequest(
                graph=graph,
                plan=plan,
                build_node_fn=_build_node_fn,
                execution_context=context,
                checkpoint_dir=checkpoint_dir,
            )
        )
    )

    first = next(iterator)
    assert first.checkpoint_path is not None
    assert first.checkpoint_path.exists()

    context.cancel()
    with pytest.raises(ExecutionCancelledError):
        next(iterator)

    assert not list(checkpoint_dir.glob("*.parquet"))
    assert not checkpoint_dir.exists()


def test_chunk_runner_cleans_checkpoints_when_later_chunk_fails(tmp_path: Path) -> None:
    graph = _chunk_safe_graph(_write_source(tmp_path))
    plan = _chunk_runner_plan(graph, chunk_size=5)
    checkpoint_dir = tmp_path / "failure-chunks"
    band_calls = 0

    def failing_build_node_fn(
        node: GraphNode,
        **kwargs: Any,
    ) -> tuple[str, Callable[..., Any], bool]:
        name, fn, is_source = _build_node_fn(node, **kwargs)
        if node.id != "age_band":
            return name, fn, is_source

        def failing_band(*frames: Any) -> Any:
            nonlocal band_calls
            band_calls += 1
            if band_calls == 2:
                raise RuntimeError("boom in chunk two")
            return fn(*frames)

        return name, failing_band, is_source

    with pytest.raises(RuntimeError, match="boom in chunk two"):
        collect_chunked(
            ChunkRunnerRequest(
                graph=graph,
                plan=plan,
                build_node_fn=failing_build_node_fn,
                checkpoint_dir=checkpoint_dir,
            ),
            allow_unbounded=True,
        )

    assert band_calls == 2
    assert not list(checkpoint_dir.glob("*.parquet"))
    assert not checkpoint_dir.exists()


def test_chunk_runner_cleans_partial_checkpoint_write_failure(tmp_path: Path) -> None:
    graph = _chunk_safe_graph(_write_source(tmp_path))
    plan = _chunk_runner_plan(graph, chunk_size=5)
    checkpoint_dir = tmp_path / "partial-checkpoint"

    def fail_after_partial_write(self: pl.DataFrame, path: Path, **_kwargs: Any) -> None:
        del self
        Path(path).write_bytes(b"partial parquet")
        raise RuntimeError("checkpoint write failed")

    with patch.object(pl.DataFrame, "write_parquet", fail_after_partial_write):
        with pytest.raises(RuntimeError, match="checkpoint write failed"):
            collect_chunked(
                ChunkRunnerRequest(
                    graph=graph,
                    plan=plan,
                    build_node_fn=_build_node_fn,
                    checkpoint_dir=checkpoint_dir,
                ),
                allow_unbounded=True,
            )

    assert not list(checkpoint_dir.glob("*.parquet"))
    assert not list(checkpoint_dir.glob("*.tmp"))
    assert not checkpoint_dir.exists()
