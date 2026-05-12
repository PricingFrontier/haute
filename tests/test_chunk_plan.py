"""Chunk-plan capability contract tests."""

from __future__ import annotations

from pathlib import Path

import polars as pl
import pytest

from haute.chunking import (
    ChunkCapabilityKind,
    ChunkCapabilityStatus,
    ChunkPlanRequest,
    chunk_capability_declarations,
    chunk_plan,
    validate_chunk_capability_declarations,
)
from haute.errors import ChunkPlanUnsupportedError
from haute.graph_utils import NodeType
from tests.conftest import make_edge, make_graph


def _node(node_id: str, node_type: str, config: dict[str, object] | None = None):
    return {
        "id": node_id,
        "data": {
            "label": node_id,
            "nodeType": node_type,
            "config": config or {},
        },
    }


def _write_projected_source(tmp_path: Path, *, extra_columns: int = 0) -> Path:
    path = tmp_path / f"projected_{extra_columns}.parquet"
    data: dict[str, list[object]] = {
        "quote_id": ["q1", "q2", "q3", "q4"],
        "premium": [100.0, 200.0, 300.0, 400.0],
    }
    for index in range(extra_columns):
        data[f"feature_{index}"] = [float(index)] * 4
    pl.DataFrame(data).write_parquet(path)
    return path


def _source_output_graph(path: Path, output_fields: list[str]):
    return make_graph(
        {
            "nodes": [
                _node("source", "dataSource", {"path": str(path)}),
                _node("out", "output", {"fields": output_fields}),
            ],
            "edges": [make_edge("source", "out").model_dump()],
        }
    )


def test_chunk_capability_registry_mentions_every_node_type() -> None:
    declarations = chunk_capability_declarations()

    assert set(declarations) == set(NodeType)
    validate_chunk_capability_declarations()


def test_chunk_capability_registry_is_immutable() -> None:
    declarations = chunk_capability_declarations()

    with pytest.raises(TypeError):
        declarations[NodeType.POLARS] = declarations[NodeType.DATA_SOURCE]  # type: ignore[index]


def test_chunk_capability_registry_declares_unsupported_types_explicitly() -> None:
    declarations = chunk_capability_declarations()

    unsupported = {
        node_type
        for node_type, declaration in declarations.items()
        if declaration.status == ChunkCapabilityStatus.UNSUPPORTED
    }
    assert unsupported == {
        NodeType.API_INPUT,
        NodeType.CONSTANT,
        NodeType.DATA_SINK,
        NodeType.EXTERNAL_FILE,
        NodeType.LIVE_SWITCH,
        NodeType.MODELLING,
        NodeType.OPTIMISER,
        NodeType.SUBMODEL,
        NodeType.SUBMODEL_PORT,
    }
    for node_type in unsupported:
        declaration = declarations[node_type]
        assert declaration.rules == frozenset({"unsupported_v1"})
        assert declaration.note


def test_chunk_capability_registry_validation_rejects_drift() -> None:
    declarations = dict(chunk_capability_declarations())
    declarations.pop(NodeType.POLARS)

    with pytest.raises(RuntimeError, match="every node type exactly once"):
        validate_chunk_capability_declarations(declarations)

    declarations = dict(chunk_capability_declarations())
    source_declaration = declarations[NodeType.DATA_SOURCE]
    declarations[NodeType.POLARS] = source_declaration

    with pytest.raises(RuntimeError, match="wrong node type"):
        validate_chunk_capability_declarations(declarations)


def test_chunk_plan_accepts_v1_chunk_safe_chain():
    graph = make_graph(
        {
            "nodes": [
                _node("source", "dataSource", {"path": "quotes.parquet"}),
                _node(
                    "banding",
                    "banding",
                    {"factors": [{"column": "age", "breaks": [25, 50]}]},
                ),
                _node(
                    "scenario",
                    "scenarioExpander",
                    {"column": "premium", "min": 0.9, "max": 1.1, "steps": 3},
                ),
                _node("out", "output", {"fields": ["quote_id", "premium"]}),
            ],
            "edges": [
                make_edge("source", "banding").model_dump(),
                make_edge("banding", "scenario").model_dump(),
                make_edge("scenario", "out").model_dump(),
            ],
        }
    )

    plan = chunk_plan(
        ChunkPlanRequest(
            graph=graph,
            target_node_id="out",
            chunk_size=17,
            required_columns_by_node={"out": {"quote_id", "premium"}},
        )
    )

    assert plan.chunk_size == 17
    assert plan.source_node_id == "source"
    assert plan.node_ids == ("source", "banding", "scenario", "out")
    assert plan.capabilities["source"].kind == ChunkCapabilityKind.MAP_ONLY
    assert plan.capabilities["scenario"].expands_rows is True
    assert plan.required_columns_by_node["out"] == frozenset({"quote_id", "premium"})


def test_byte_budgeted_chunk_plan_shrinks_wide_projected_schema(tmp_path: Path) -> None:
    source_path = _write_projected_source(tmp_path, extra_columns=24)
    narrow_fields = ["quote_id", "premium"]
    wide_fields = narrow_fields + [f"feature_{index}" for index in range(24)]

    narrow_plan = chunk_plan(
        ChunkPlanRequest(
            graph=_source_output_graph(source_path, narrow_fields),
            target_node_id="out",
            chunk_size=None,
            target_chunk_bytes=1_024,
            required_columns_by_node={"out": narrow_fields},
        )
    )
    wide_plan = chunk_plan(
        ChunkPlanRequest(
            graph=_source_output_graph(source_path, wide_fields),
            target_node_id="out",
            chunk_size=None,
            target_chunk_bytes=1_024,
            required_columns_by_node={"out": wide_fields},
        )
    )

    assert wide_plan.source_chunk_size < narrow_plan.source_chunk_size
    assert wide_plan.chunk_size < narrow_plan.chunk_size
    assert narrow_plan.chunk_size_policy == "byte_budget"
    assert wide_plan.chunk_size_policy == "byte_budget"
    assert narrow_plan.estimated_target_row_bytes is not None
    assert wide_plan.estimated_target_row_bytes is not None
    assert wide_plan.estimated_target_row_bytes > narrow_plan.estimated_target_row_bytes


def test_byte_budgeted_chunk_plan_ignores_unused_wide_source_columns(
    tmp_path: Path,
) -> None:
    narrow_source_path = _write_projected_source(tmp_path, extra_columns=0)
    wide_source_path = _write_projected_source(tmp_path, extra_columns=24)

    narrow_source_plan = chunk_plan(
        ChunkPlanRequest(
            graph=_source_output_graph(narrow_source_path, ["quote_id", "premium"]),
            target_node_id="out",
            target_chunk_bytes=1_024,
            required_columns_by_node={"out": {"quote_id", "premium"}},
        )
    )
    wide_source_plan = chunk_plan(
        ChunkPlanRequest(
            graph=_source_output_graph(wide_source_path, ["quote_id", "premium"]),
            target_node_id="out",
            target_chunk_bytes=1_024,
            required_columns_by_node={"out": {"quote_id", "premium"}},
        )
    )

    assert wide_source_plan.chunk_size == narrow_source_plan.chunk_size
    assert wide_source_plan.source_chunk_size == narrow_source_plan.source_chunk_size
    assert (
        wide_source_plan.estimated_target_row_bytes == narrow_source_plan.estimated_target_row_bytes
    )


def test_byte_budgeted_chunk_plan_accounts_for_scenario_row_expansion(
    tmp_path: Path,
) -> None:
    source_path = _write_projected_source(tmp_path)
    graph = make_graph(
        {
            "nodes": [
                _node("source", "dataSource", {"path": str(source_path)}),
                _node(
                    "scenario",
                    "scenarioExpander",
                    {
                        "column_name": "scenario_value",
                        "min_value": 0.9,
                        "max_value": 1.1,
                        "steps": 4,
                        "step_column": "scenario_index",
                    },
                ),
                _node(
                    "out",
                    "output",
                    {
                        "fields": [
                            "quote_id",
                            "premium",
                            "scenario_index",
                            "scenario_value",
                        ]
                    },
                ),
            ],
            "edges": [
                make_edge("source", "scenario").model_dump(),
                make_edge("scenario", "out").model_dump(),
            ],
        }
    )

    plan = chunk_plan(
        ChunkPlanRequest(
            graph=graph,
            target_node_id="out",
            chunk_size=None,
            target_chunk_bytes=1_024,
            required_columns_by_node={
                "out": {"quote_id", "premium", "scenario_index", "scenario_value"}
            },
        )
    )

    assert plan.row_expansion_factor == 4
    assert plan.source_chunk_size == max(1, plan.chunk_size // 4)
    assert plan.source_chunk_size < plan.chunk_size


def test_chunk_plan_explicit_row_chunk_size_preserves_legacy_semantics(
    tmp_path: Path,
) -> None:
    source_path = _write_projected_source(tmp_path, extra_columns=12)

    plan = chunk_plan(
        ChunkPlanRequest(
            graph=_source_output_graph(
                source_path,
                ["quote_id", "premium", *[f"feature_{index}" for index in range(12)]],
            ),
            target_node_id="out",
            chunk_size=17,
            required_columns_by_node={"out": {"quote_id", "premium"}},
        )
    )

    assert plan.chunk_size == 17
    assert plan.source_chunk_size == 17
    assert plan.chunk_size_policy == "explicit_rows"
    assert plan.target_chunk_bytes is None
    assert plan.estimated_source_row_bytes is None
    assert plan.estimated_target_row_bytes is None


def test_chunk_plan_rejects_ambiguous_row_and_byte_budget(
    tmp_path: Path,
) -> None:
    source_path = _write_projected_source(tmp_path)

    with pytest.raises(ValueError, match="Specify either chunk_size or target_chunk_bytes"):
        chunk_plan(
            ChunkPlanRequest(
                graph=_source_output_graph(source_path, ["quote_id", "premium"]),
                target_node_id="out",
                chunk_size=17,
                target_chunk_bytes=1_024,
                required_columns_by_node={"out": {"quote_id", "premium"}},
            )
        )


def test_chunk_plan_requires_a_row_or_byte_budget(tmp_path: Path) -> None:
    source_path = _write_projected_source(tmp_path)

    with pytest.raises(ValueError, match="chunk_size or target_chunk_bytes must be provided"):
        chunk_plan(
            ChunkPlanRequest(
                graph=_source_output_graph(source_path, ["quote_id", "premium"]),
                target_node_id="out",
                required_columns_by_node={"out": {"quote_id", "premium"}},
            )
        )


def test_chunk_plan_rejects_json_sources_for_bounded_chunking():
    graph = make_graph(
        {
            "nodes": [
                _node("source", "dataSource", {"path": "quotes.json"}),
                _node("out", "output", {"fields": ["quote_id"]}),
            ],
            "edges": [make_edge("source", "out").model_dump()],
        }
    )

    with pytest.raises(ChunkPlanUnsupportedError, match="parquet or csv"):
        chunk_plan(
            ChunkPlanRequest(
                graph=graph,
                target_node_id="out",
                chunk_size=10,
                required_columns_by_node={"out": {"quote_id"}},
            )
        )


def test_chunk_plan_requires_explicit_model_score_batch_reuse(tmp_path):
    from haute.modelling._feature_contract import build_contract, save_contract

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
    graph = make_graph(
        {
            "nodes": [
                _node("source", "dataSource", {"path": "quotes.parquet"}),
                _node(
                    "score",
                    "modelScore",
                    {
                        "sourceType": "run",
                        "run_id": "r1",
                        "output_column": "prediction",
                        "feature_contract_path": str(contract_path),
                    },
                ),
                _node("out", "output", {"fields": ["prediction"]}),
            ],
            "edges": [
                make_edge("source", "score").model_dump(),
                make_edge("score", "out").model_dump(),
            ],
        }
    )

    with pytest.raises(ChunkPlanUnsupportedError, match="model_reuse_lifetime='batch'"):
        chunk_plan(
            ChunkPlanRequest(
                graph=graph,
                target_node_id="out",
                chunk_size=10,
                required_columns_by_node={"out": {"prediction"}},
            )
        )

    graph.nodes[1].data.config["model_reuse_lifetime"] = "batch"
    plan = chunk_plan(
        ChunkPlanRequest(
            graph=graph,
            target_node_id="out",
            chunk_size=10,
            required_columns_by_node={"out": {"prediction"}},
        )
    )
    assert plan.capabilities["score"].model_reuse_lifetime == "batch"


def test_chunk_plan_rejects_opaque_rating_step_user_code():
    graph = make_graph(
        {
            "nodes": [
                _node("source", "dataSource", {"path": "quotes.csv"}),
                _node("rating", "ratingStep", {"code": "df = df.sort('quote_id')"}),
                _node("out", "output", {"fields": ["quote_id"]}),
            ],
            "edges": [
                make_edge("source", "rating").model_dump(),
                make_edge("rating", "out").model_dump(),
            ],
        }
    )

    with pytest.raises(ChunkPlanUnsupportedError, match="ratingStep user code"):
        chunk_plan(
            ChunkPlanRequest(
                graph=graph,
                target_node_id="out",
                chunk_size=10,
                required_columns_by_node={"out": {"quote_id"}},
            )
        )
