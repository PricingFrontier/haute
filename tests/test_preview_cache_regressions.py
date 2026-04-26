from __future__ import annotations

import json
from pathlib import Path

from haute._sandbox import set_project_root
from haute._types import GraphEdge, GraphNode, NodeData, NodeType, PipelineGraph
from haute.executor import execute_graph


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
