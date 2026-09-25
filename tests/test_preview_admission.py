"""Which previews read and write shared snapshots, and which inputs they prepare (CACHE-S09)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import polars as pl
import pytest

from haute._data_points import DataPointResolver
from haute._execution_context import ExecutionProfile
from haute._node_snapshots import NodeSnapshotColumns, NodeSnapshotStore
from haute._seed_plans import preview_input_node_ids, preview_lineage_admitted
from haute._types import GraphEdge, GraphNode, NodeData, NodeType, PipelineGraph


def _node(node_id: str, node_type: NodeType, config: dict[str, Any]) -> GraphNode:
    return GraphNode(id=node_id, data=NodeData(label=node_id, nodeType=node_type, config=config))


def _graph(
    project: Path,
    nodes: list[tuple[str, NodeType, dict[str, Any]]],
    edges: list[tuple[str, str]],
) -> PipelineGraph:
    return PipelineGraph(
        nodes=[_node(*spec) for spec in nodes],
        edges=[
            GraphEdge(id=f"e{i}", source=source, target=target)
            for i, (source, target) in enumerate(edges)
        ],
        preamble="import polars as pl",
        source_file=str(project / "main.py"),
    )


def _code(code: str) -> dict[str, Any]:
    return {"code": code}


@pytest.fixture()
def project(haute_scratch: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(haute_scratch)
    monkeypatch.setattr("haute._sandbox._get_project_root", lambda: haute_scratch)
    (haute_scratch / "main.py").write_text("# pipeline\n", encoding="utf-8")
    pl.DataFrame({"id": [1, 2, 3], "a": [1, 2, 3]}).write_csv(haute_scratch / "policies.csv")
    pl.DataFrame({"id": [1, 2, 3], "d": [0.1, 0.2, 0.3]}).write_parquet(
        haute_scratch / "claims.parquet"
    )
    (haute_scratch / "quotes.json").write_text('[{"id": 1}]', encoding="utf-8")
    return haute_scratch


def _joined(project: Path, policies: tuple[NodeType, dict[str, Any]]) -> PipelineGraph:
    """``policies + claims → J → B``."""
    return _graph(
        project,
        [
            ("policies", *policies),
            (
                "claims",
                NodeType.DATA_INPUT,
                {"inputType": "file", "format": "parquet", "path": str(project / "claims.parquet")},
            ),
            ("J", NodeType.POLARS, _code("df = policies.join(claims, on='id', how='left')")),
            ("B", NodeType.POLARS, _code("df = J.with_columns(pl.lit(1).alias('band'))")),
        ],
        [("policies", "J"), ("claims", "J"), ("J", "B")],
    )


_ID_TABLE: dict[str, Any] = {
    "tables": [
        {
            "label": "root",
            "path": "$[:]",
            "emit": True,
            "columns": [{"name": "id", "path": "$[:].id", "type": "int", "selected": True}],
        }
    ]
}


def _csv_api_input(project: Path, **declared: Any) -> tuple[NodeType, dict[str, Any]]:
    return NodeType.API_INPUT, {"path": str(project / "policies.csv"), **declared}


def _csv_data_input(project: Path) -> tuple[NodeType, dict[str, Any]]:
    return NodeType.DATA_INPUT, {
        "inputType": "file",
        "format": "csv",
        "path": str(project / "policies.csv"),
    }


def test_api_input_reading_undeclared_csv_is_not_admitted(project: Path) -> None:
    graph = _joined(project, _csv_api_input(project))
    assert preview_lineage_admitted(graph, "B", source="live") is False


def test_api_input_reading_declared_csv_is_admitted(project: Path) -> None:
    graph = _joined(project, _csv_api_input(project, dtypes={"id": "Int64", "a": "Int64"}))
    assert preview_lineage_admitted(graph, "B", source="live") is True


def test_data_input_reading_undeclared_csv_is_admitted(project: Path) -> None:
    # It executes from its prepared snapshot, never from the raw CSV.
    graph = _joined(project, _csv_data_input(project))
    assert preview_lineage_admitted(graph, "B", source="live") is True


def test_structured_api_input_is_admitted(project: Path) -> None:
    graph = _joined(project, (NodeType.API_INPUT, {"path": str(project / "quotes.json")}))
    assert preview_lineage_admitted(graph, "B", source="live") is True


def test_admission_probe_error_leaves_the_node_error_to_the_preview(project: Path) -> None:
    # The preview's own read reports a missing file at the node; admission
    # only declines to share snapshots for that lineage.
    graph = _joined(project, (NodeType.API_INPUT, {"path": str(project / "missing.csv")}))
    assert preview_lineage_admitted(graph, "B", source="live") is False


def test_an_api_input_outside_the_lineage_does_not_decide_admission(project: Path) -> None:
    graph = _joined(project, _csv_data_input(project))
    graph = graph.model_copy(
        update={"nodes": [*graph.nodes, _node("loose", *_csv_api_input(project))]}
    )
    assert preview_lineage_admitted(graph, "B", source="live") is True
    assert preview_lineage_admitted(graph, "loose", source="live") is False


def _publish_join(project: Path, graph: PipelineGraph) -> str:
    store = NodeSnapshotStore(project)
    resolver = DataPointResolver(graph, source="live", store=store)
    identity = resolver.node_output_slot("J").identity(resolver.node_output_signature("J"))
    artifact = store.stage_node_output(identity)
    pl.DataFrame({"id": [1, 2, 3], "a": [1, 2, 3], "d": [0.1, 0.2, 0.3]}).write_parquet(
        artifact.part_path(0)
    )
    with store.publish_node_output(
        identity,
        artifact,
        columns=NodeSnapshotColumns.all(),
        dependencies={},
        explicit=False,
        profile=ExecutionProfile.PREVIEW_EAGER,
    ) as publication:
        assert publication.generation is not None
        return publication.generation.generation_id


def test_preview_inputs_list_only_what_the_seeded_execution_reads(project: Path) -> None:
    graph = _joined(project, _csv_data_input(project))
    assert preview_input_node_ids(graph, "B", source="live") == ("policies",)

    _publish_join(project, graph)

    # The join is seeded, so nothing above it is read or prepared.
    assert preview_input_node_ids(graph, "B", source="live") == ()


def test_preview_inputs_of_an_unadmitted_lineage_are_every_input_it_reads(
    project: Path,
) -> None:
    (project / "quotes.json").write_text('[{"id": 1}]', encoding="utf-8")
    graph = _graph(
        project,
        [
            ("quotes", NodeType.API_INPUT, {"path": str(project / "quotes.json"), **_ID_TABLE}),
            ("untabled", NodeType.API_INPUT, {"path": str(project / "quotes.json")}),
            ("policies", *_csv_api_input(project)),
            ("snap", *_csv_data_input(project)),
            ("J", NodeType.POLARS, _code("df = pl.concat([quotes, untabled, policies, snap])")),
        ],
        [("quotes", "J"), ("untabled", "J"), ("policies", "J"), ("snap", "J")],
    )
    assert preview_lineage_admitted(graph, "J", source="live") is False
    # Structured API inputs (their tables are input snapshots) and
    # snapshot-backed Data Inputs; a flat-file API input, or a structured one
    # with no table schema, has nothing to prepare.
    assert preview_input_node_ids(graph, "J", source="live") == ("quotes", "snap")


def test_a_preview_that_captures_is_budgeted_as_the_cache_build_it_runs(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A capture writes a node's whole output: the preview gets the cache-build budget.

    It is still admitted as a preview, so it never reserves in-flight memory.
    """
    from haute._execution_admission import create_admitted_execution_context
    from haute._seed_plans import preview_builds_snapshots
    from haute.routes.pipeline import _preview_budget_profile
    from haute.schemas import PreviewNodeRequest

    monkeypatch.setenv("HAUTE_PREVIEW_MEMORY_LIMIT_MB", "64")
    monkeypatch.setenv("HAUTE_NODE_SNAPSHOT_MEMORY_LIMIT_MB", "256")
    graph = _joined(project, _csv_data_input(project))

    def budget_profile(node_id: str) -> ExecutionProfile | None:
        body = PreviewNodeRequest.model_construct(
            graph=None, node_id=node_id, source="live", requested_preview_columns=None
        )
        return _preview_budget_profile(graph, body)

    # ``B`` reads the join, which the preview captures; ``policies`` captures nothing.
    assert preview_builds_snapshots(graph, "B", source="live") is True
    assert budget_profile("B") is ExecutionProfile.NODE_SNAPSHOT
    assert preview_builds_snapshots(graph, "policies", source="live") is False
    assert budget_profile("policies") is None

    context = create_admitted_execution_context(
        operation="pipeline_preview",
        profile=ExecutionProfile.PREVIEW_EAGER,
        budget_profile=budget_profile("B"),
    )
    try:
        assert context.profile is ExecutionProfile.PREVIEW_EAGER
        assert context.memory_limit_bytes == 256 * 1024 * 1024
        assert context.admission is not None
        assert context.admission.config_key == "HAUTE_NODE_SNAPSHOT_MEMORY_LIMIT_MB"
    finally:
        context.release_admission()

    # Once the join is cached the preview reads it and builds nothing.
    _publish_join(project, graph)
    assert budget_profile("B") is None


def test_an_unadmitted_preview_builds_no_snapshots(project: Path) -> None:
    """A lineage the shared cache does not admit captures nothing, whatever it reads."""
    from haute._seed_plans import preview_builds_snapshots

    graph = _joined(project, _csv_api_input(project))
    assert preview_lineage_admitted(graph, "B", source="live") is False
    assert preview_builds_snapshots(graph, "B", source="live") is False
