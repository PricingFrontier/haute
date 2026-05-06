from __future__ import annotations

import json
from pathlib import Path

import pytest

from haute._sandbox import _get_project_root, set_project_root
from haute._types import GraphEdge, GraphNode, NodeData, NodeType, PipelineGraph
from haute.executor import _normalise_requested_preview_columns, execute_graph


@pytest.fixture(autouse=True)
def _restore_project_root():
    """Snapshot and restore the sandbox project root around each test.

    Tests in this file call ``set_project_root(tmp_path)`` to widen the
    sandbox; without this fixture the global mutation leaks into later
    tests in the session that expect the real project root.
    """
    original = _get_project_root()
    yield
    set_project_root(original)


def _make_graph(data_path: Path, artifact_path: Path) -> PipelineGraph:
    return PipelineGraph(
        nodes=[
            GraphNode(
                id="source",
                data=NodeData(
                    label="source",
                    nodeType=NodeType.DATA_SOURCE,
                    config={"path": str(data_path)},
                ),
            ),
            GraphNode(
                id="apply",
                data=NodeData(
                    label="apply",
                    nodeType=NodeType.OPTIMISER_APPLY,
                    config={
                        "sourceType": "file",
                        "artifact_path": str(artifact_path),
                        "version_column": "__optimiser_version__",
                    },
                ),
            ),
        ],
        edges=[
            GraphEdge(id="e-source-apply", source="source", target="apply"),
        ],
    )


def test_extend_path_clears_stale_errors_after_transient_optimiser_artifact_failure(
    tmp_path: Path,
) -> None:
    set_project_root(tmp_path)

    data_path = tmp_path / "scored.parquet"
    artifact_path = tmp_path / "optimiser.json"

    import polars as pl

    pl.DataFrame(
        {
            "quote_id": ["q1", "q1", "q2", "q2"],
            "scenario_index": [0, 1, 0, 1],
            "scenario_value": [0.9, 1.1, 0.9, 1.1],
            "predicted_income": [90.0, 110.0, 45.0, 55.0],
            "predicted_volume": [1.0, 0.7, 1.0, 0.8],
        }
    ).write_parquet(data_path)

    graph = _make_graph(data_path, artifact_path)

    first = execute_graph(graph, target_node_id="apply")
    assert first["apply"].status == "error"
    assert "No such file or directory" in (first["apply"].error or "")

    artifact_path.write_text(
        json.dumps(
            {
                "version": "v1",
                "created_at": "2026-04-24T00:00:00Z",
                "mode": "online",
                "lambdas": {"predicted_volume": 0.5},
                "objective": "predicted_income",
                "constraints": {"predicted_volume": {"min": 0.9}},
                "quote_id": "quote_id",
                "scenario_index": "scenario_index",
                "scenario_value": "scenario_value",
                "chunk_size": 500_000,
            }
        )
    )

    second = execute_graph(graph, target_node_id="apply")
    assert second["apply"].status == "ok"
    assert second["apply"].error is None
    assert "optimal_scenario_value" in [col.name for col in second["apply"].columns]
    assert "__optimiser_version__" in second["apply"].preview[0]


def test_preview_maps_stale_online_apply_projection_to_configured_value_column(
    tmp_path: Path,
) -> None:
    set_project_root(tmp_path)

    import polars as pl

    data_path = tmp_path / "scored.parquet"
    artifact_path = tmp_path / "optimiser.json"
    pl.DataFrame(
        {
            "quote_id": ["q1", "q1", "q2", "q2"],
            "scenario_index": [0, 1, 0, 1],
            "scenario_value": [0.9, 1.1, 0.9, 1.1],
            "predicted_income": [90.0, 110.0, 45.0, 55.0],
            "predicted_volume": [1.0, 0.7, 1.0, 0.8],
        }
    ).write_parquet(data_path)
    artifact_path.write_text(
        json.dumps(
            {
                "version": "v1",
                "created_at": "2026-04-24T00:00:00Z",
                "mode": "online",
                "lambdas": {"predicted_volume": 0.0},
                "objective": "predicted_income",
                "constraints": {"predicted_volume": {"min": 0.9}},
                "quote_id": "quote_id",
                "scenario_index": "scenario_index",
                "scenario_value": "scenario_value",
                "chunk_size": 500_000,
            }
        )
    )

    graph = _make_graph(data_path, artifact_path)
    graph.nodes[1].data.config["optimised_value_column"] = "optimised_premium"

    result = execute_graph(
        graph,
        target_node_id="apply",
        target_preview_only=True,
        requested_preview_columns=["quote_id", "optimal_scenario_value"],
    )

    assert result["apply"].status == "ok"
    assert result["apply"].preview_columns == ["quote_id", "optimised_premium"]
    assert "optimised_premium" in result["apply"].preview[0]
    assert "optimal_scenario_value" not in result["apply"].preview[0]


def _ratebook_apply_preview(tmp_path: Path) -> dict[str, object]:
    """Build a ratebook apply graph and return the preview result for ``apply``."""
    set_project_root(tmp_path)

    import polars as pl

    data_path = tmp_path / "banded.parquet"
    artifact_path = tmp_path / "ratebook.json"
    pl.DataFrame(
        {
            "quote_id": ["q1", "q2"],
            "region": ["London", "Manchester"],
        }
    ).write_parquet(data_path)
    artifact_path.write_text(
        json.dumps(
            {
                "version": "rb_v1",
                "created_at": "2026-04-24T00:00:00Z",
                "mode": "ratebook",
                "factor_tables": {
                    "region": [
                        {"__factor_group__": "London", "optimal_scenario_value": 1.05},
                        {"__factor_group__": "Manchester", "optimal_scenario_value": 0.98},
                    ],
                },
            }
        )
    )

    graph = _make_graph(data_path, artifact_path)
    graph.nodes[1].data.config["optimised_value_column"] = "optimised_premium"

    result = execute_graph(
        graph,
        target_node_id="apply",
        target_preview_only=True,
        requested_preview_columns=["quote_id", "optimised_factor"],
    )
    return {"apply": result["apply"]}


def test_preview_maps_stale_ratebook_apply_projection_to_configured_value_column(
    tmp_path: Path,
) -> None:
    """Stale ``optimised_factor`` projection is rewritten to the configured column."""
    result = _ratebook_apply_preview(tmp_path)
    apply_result = result["apply"]

    assert apply_result.status == "ok"
    assert apply_result.preview_columns == ["quote_id", "optimised_premium"]
    assert "optimised_factor" not in apply_result.preview[0]


def test_ratebook_apply_renames_factor_table_values_to_configured_value_column(
    tmp_path: Path,
) -> None:
    """The renamed column carries the actual factor-table values per quote."""
    result = _ratebook_apply_preview(tmp_path)
    apply_result = result["apply"]

    preview_by_quote = {row["quote_id"]: row for row in apply_result.preview}
    assert preview_by_quote["q1"]["optimised_premium"] == 1.05
    assert preview_by_quote["q2"]["optimised_premium"] == 0.98


def test_preview_keeps_existing_default_named_apply_projection_columns() -> None:
    import polars as pl

    node_data = NodeData(
        label="apply",
        nodeType=NodeType.OPTIMISER_APPLY,
        config={"optimised_value_column": "optimised_premium"},
    )
    requested = ["quote_id", "optimal_scenario_value"]
    df = pl.DataFrame(
        {
            "quote_id": ["q1"],
            "optimal_scenario_value": [1.1],
            "optimised_premium": [123.45],
        }
    )

    assert _normalise_requested_preview_columns(node_data, df, requested) == requested
