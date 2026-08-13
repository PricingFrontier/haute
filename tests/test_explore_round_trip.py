"""Round-trip tests for the Explore node's display-config blocks.

Builds minimal ``dataInput`` -> ``explore`` graphs, runs them through
``graph_to_code`` and back through the real ``parse_pipeline_source``, and
asserts their display config survives end-to-end.

This is the contract the UI relies on: codegen must emit the kwarg, and the
parser must read it back into the Explore node config.
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


def _explore_graph_with_config(config: dict) -> PipelineGraph:
    """Build a minimal source -> explore graph with the given config."""
    return PipelineGraph(
        nodes=[
            GraphNode(
                id="source",
                data=NodeData(
                    label="source",
                    nodeType="dataInput",
                    config={
                        "inputType": "file",
                        "format": "parquet",
                        "mode": "scan",
                        "path": "data.parquet",
                        "arguments": {},
                    },
                ),
            ),
            GraphNode(
                id="inspect_claims",
                data=NodeData(
                    label="inspect_claims",
                    nodeType="explore",
                    config=config,
                ),
            ),
        ],
        edges=[GraphEdge(id="e1", source="source", target="inspect_claims")],
    )


def _explore_graph(overview: dict) -> PipelineGraph:
    """Build a minimal source -> explore graph with the given overview block."""
    return _explore_graph_with_config({"overview": overview})


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


def test_pivot_chart_cards_and_overview_round_trip_together(tmp_path: Path) -> None:
    pivots = [
        {
            "version": 1,
            "id": "pivot_1",
            "name": "Claims by region",
            "enabled": True,
            "filters": [
                {
                    "id": "filter_1",
                    "field": "status",
                    "members": [{"kind": "string", "value": "Open"}],
                }
            ],
            "columns": [{"id": "column_1", "field": "year"}],
            "rows": [{"id": "row_1", "field": "region", "sort": "descending"}],
            "values": [
                {
                    "id": "value_1",
                    "field": "paid",
                    "aggregation": "sum",
                    "display_name": "Paid claims",
                    "sort_rows": "descending",
                    "color_scale": "low_red_high_green",
                    "future_value_setting": {"precision": 2},
                }
            ],
            "options": {
                "row_grand_totals": True,
                "column_grand_totals": False,
                "sort_by": "value_1",
                "future_option": "compact",
            },
            "future_setting": {"palette": "warm"},
        },
        {
            "version": 1,
            "id": "pivot_2",
            "name": "Pivot 2",
            "enabled": False,
            "filters": [],
            "columns": [],
            "rows": [],
            "values": [],
            "options": {
                "row_grand_totals": True,
                "column_grand_totals": True,
                "sort_by": None,
            },
        },
    ]
    charts = [
        {
            "version": 1,
            "id": "chart_1",
            "name": "Claims chart",
            "enabled": True,
            "pivot_id": "pivot_1",
            "kind": "combo",
            "category": {
                "source": "rows",
                "include_grand_total": False,
                "label_rotation": 0,
                "future": {"nested": ["literal"]},
            },
            "value_encodings": [
                {
                    "id": "encoding_1",
                    "value_id": "value_1",
                    "mark": "column",
                    "axis": "primary",
                    "stack_group": None,
                    "color": "#AABBCC",
                    "data_labels": False,
                    "markers": False,
                }
            ],
            "series_overrides": [],
            "axes": {
                "primary": {
                    "title": "Claims",
                    "minimum": None,
                    "maximum": None,
                    "number_format": "number",
                },
                "secondary": {
                    "title": "",
                    "minimum": None,
                    "maximum": None,
                    "number_format": "inherit",
                },
            },
            "legend": {"visible": True, "position": "bottom"},
            "future_setting": {"palette": "warm", "columns": ["premium"]},
        }
    ]
    graph = _explore_graph_with_config(
        {"overview": {"schema": True}, "pivots": pivots, "charts": charts}
    )

    code = graph_to_code(graph, pipeline_name="round_trip_display_cards")
    _write_configs(graph, tmp_path)

    assert "pivots=" in code
    assert "charts=" in code
    assert code.index("overview=") < code.index("pivots=") < code.index("charts=")
    parsed = parse_pipeline_source(
        code,
        source_file=str(tmp_path / "pipeline.py"),
        _base_dir=tmp_path,
    )

    node_map = {n.id: n for n in parsed.nodes}
    assert node_map["inspect_claims"].data.config.get("overview") == {"schema": True}
    assert node_map["inspect_claims"].data.config.get("pivots") == pivots
    assert node_map["inspect_claims"].data.config.get("charts") == charts


def test_empty_charts_do_not_round_trip_into_config(tmp_path: Path) -> None:
    graph = _explore_graph_with_config({"charts": []})

    code = graph_to_code(graph, pipeline_name="round_trip_charts_empty")
    _write_configs(graph, tmp_path)

    assert "charts=" not in code
    parsed = parse_pipeline_source(
        code,
        source_file=str(tmp_path / "pipeline.py"),
        _base_dir=tmp_path,
    )

    node_map = {n.id: n for n in parsed.nodes}
    assert "charts" not in node_map["inspect_claims"].data.config


def test_empty_pivots_do_not_round_trip_into_config(tmp_path: Path) -> None:
    graph = _explore_graph_with_config({"pivots": []})

    code = graph_to_code(graph, pipeline_name="round_trip_pivots_empty")
    _write_configs(graph, tmp_path)

    assert "pivots=" not in code
    parsed = parse_pipeline_source(
        code,
        source_file=str(tmp_path / "pipeline.py"),
        _base_dir=tmp_path,
    )

    node_map = {n.id: n for n in parsed.nodes}
    assert "pivots" not in node_map["inspect_claims"].data.config
