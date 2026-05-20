"""Round-trip tests for the Explore node's ``overview`` config block.

Builds a minimal graph containing a ``dataSource`` and an ``explore`` node
carrying ``config={"overview": {"dataset_snapshot": True}}``, runs it through
``graph_to_code`` and back through the real ``parse_pipeline_source``, and
asserts the overview config survives end-to-end.

This is the contract the UI relies on: codegen must emit the kwarg, and the
parser must read it back into ``config["overview"]``.
"""

from __future__ import annotations

from pathlib import Path

from haute._config_io import collect_node_configs
from haute._types import GraphEdge, GraphNode, NodeData, PipelineGraph
from haute.codegen import graph_to_code
from haute.parser import parse_pipeline_source


def _write_configs(graph: PipelineGraph, base_dir: Path) -> None:
    """Write any node-config JSON sidecar files produced for *graph*."""
    for rel_path, content in collect_node_configs(graph).items():
        abs_path = base_dir / rel_path
        abs_path.parent.mkdir(parents=True, exist_ok=True)
        abs_path.write_text(content)


def _explore_graph(overview: dict) -> PipelineGraph:
    """Build a minimal source -> explore graph with the given overview block."""
    return PipelineGraph(
        nodes=[
            GraphNode(
                id="source",
                data=NodeData(
                    label="source",
                    nodeType="dataSource",
                    config={"path": "data.parquet", "sourceType": "flat_file"},
                ),
            ),
            GraphNode(
                id="inspect_claims",
                data=NodeData(
                    label="inspect_claims",
                    nodeType="explore",
                    config={"overview": overview},
                ),
            ),
        ],
        edges=[GraphEdge(id="e1", source="source", target="inspect_claims")],
    )


def test_overview_config_round_trips_through_codegen_and_parse(tmp_path: Path) -> None:
    """Explore overview block must survive ``graph_to_code`` -> parse."""
    graph = _explore_graph({"dataset_snapshot": True})

    code = graph_to_code(graph, pipeline_name="round_trip_overview")
    _write_configs(graph, tmp_path)

    parsed = parse_pipeline_source(
        code,
        source_file=str(tmp_path / "pipeline.py"),
        _base_dir=tmp_path,
    )

    node_map = {n.id: n for n in parsed.nodes}
    explore_node = node_map["inspect_claims"]
    assert explore_node.data.nodeType == "explore"
    assert explore_node.data.config.get("overview") == {"dataset_snapshot": True}


def test_schema_overview_config_round_trips_through_codegen_and_parse(tmp_path: Path) -> None:
    """Explore ``overview={"schema": True}`` must survive codegen -> parse."""
    graph = _explore_graph({"schema": True})

    code = graph_to_code(graph, pipeline_name="round_trip_overview_schema")
    _write_configs(graph, tmp_path)

    parsed = parse_pipeline_source(
        code,
        source_file=str(tmp_path / "pipeline.py"),
        _base_dir=tmp_path,
    )

    node_map = {n.id: n for n in parsed.nodes}
    explore_node = node_map["inspect_claims"]
    assert explore_node.data.nodeType == "explore"
    assert explore_node.data.config.get("overview") == {"schema": True}


def test_both_overview_toggles_round_trip(tmp_path: Path) -> None:
    """Both ``dataset_snapshot`` and ``schema`` must survive a full round-trip."""
    graph = _explore_graph({"dataset_snapshot": True, "schema": True})

    code = graph_to_code(graph, pipeline_name="round_trip_overview_both")
    _write_configs(graph, tmp_path)

    parsed = parse_pipeline_source(
        code,
        source_file=str(tmp_path / "pipeline.py"),
        _base_dir=tmp_path,
    )

    node_map = {n.id: n for n in parsed.nodes}
    explore_node = node_map["inspect_claims"]
    assert explore_node.data.nodeType == "explore"
    assert explore_node.data.config.get("overview") == {
        "dataset_snapshot": True,
        "schema": True,
    }


def test_explicit_false_overview_values_round_trip(tmp_path: Path) -> None:
    """A hand-edited explore .py with ``False`` toggles must round-trip exactly.

    The UI currently drops keys on disable rather than writing ``False``, but
    a literal ``False`` written by hand into the .py is load-bearing — it
    must parse correctly and survive a re-codegen without being collapsed
    or converted into a missing key.
    """
    graph = _explore_graph({"dataset_snapshot": False, "schema": True})

    code = graph_to_code(graph, pipeline_name="round_trip_overview_false")
    _write_configs(graph, tmp_path)

    parsed = parse_pipeline_source(
        code,
        source_file=str(tmp_path / "pipeline.py"),
        _base_dir=tmp_path,
    )

    node_map = {n.id: n for n in parsed.nodes}
    explore_node = node_map["inspect_claims"]
    assert explore_node.data.nodeType == "explore"
    assert explore_node.data.config.get("overview") == {
        "dataset_snapshot": False,
        "schema": True,
    }


def test_unknown_sane_overview_values_round_trip(tmp_path: Path) -> None:
    """Unknown overview keys with simple literal values must be preserved."""
    graph = _explore_graph(
        {
            "schema": True,
            "custom_card": {
                "label": "Loss ratio",
                "columns": ["premium", "claims"],
                "enabled": False,
                "empty": None,
            },
        }
    )

    code = graph_to_code(graph, pipeline_name="round_trip_overview_unknown")
    _write_configs(graph, tmp_path)

    parsed = parse_pipeline_source(
        code,
        source_file=str(tmp_path / "pipeline.py"),
        _base_dir=tmp_path,
    )

    node_map = {n.id: n for n in parsed.nodes}
    explore_node = node_map["inspect_claims"]
    assert explore_node.data.config.get("overview") == {
        "schema": True,
        "custom_card": {
            "label": "Loss ratio",
            "columns": ["premium", "claims"],
            "enabled": False,
            "empty": None,
        },
    }


def test_empty_overview_does_not_round_trip_into_config(tmp_path: Path) -> None:
    """An empty ``overview`` dict must be dropped, not emitted into the .py file."""
    graph = _explore_graph({})

    code = graph_to_code(graph, pipeline_name="round_trip_overview_empty")
    _write_configs(graph, tmp_path)

    # Sanity: the generated source must not carry an ``overview=`` kwarg.
    assert "overview=" not in code

    parsed = parse_pipeline_source(
        code,
        source_file=str(tmp_path / "pipeline.py"),
        _base_dir=tmp_path,
    )

    node_map = {n.id: n for n in parsed.nodes}
    explore_node = node_map["inspect_claims"]
    assert explore_node.data.nodeType == "explore"
    assert "overview" not in explore_node.data.config
