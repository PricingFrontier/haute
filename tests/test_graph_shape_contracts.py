"""Graph-shape contract pins for empty, single-node, long-text, and round-trip cases.

This file keeps the narrow graph-shape contracts in one place:

1. Empty graphs must remain valid editor state for save/parse/codegen.
   Trace-style runtime entry points should still fail loudly on an empty
   graph because there is no target to inspect.
2. A single source-only graph must round-trip through codegen and parse.
   A sink-only graph with no upstream input should surface an execution
   error instead of pretending it can run.
3. Very long descriptions are valid documentation and must round-trip.
4. Repeated graph -> codegen -> parse cycles must not drift structurally.
   The normalized graph shape should remain stable across 10 cycles.
"""

from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Any

import polars as pl
import pytest

from haute.codegen import graph_to_code
from haute.errors import ParseError
from haute.executor import execute_graph
from haute.graph_utils import GraphEdge, PipelineGraph
from haute.parser import parse_pipeline_source
from haute.trace import execute_trace
from tests.conftest import build_test_input_snapshot, compile_node_code, make_output_config
from tests.conftest import make_graph as _g
from tests.conftest import make_node as _node

pytestmark = pytest.mark.usefixtures("_widen_sandbox_root")


def _edge(src: str, tgt: str) -> GraphEdge:
    return GraphEdge(id=f"e_{src}_{tgt}", source=src, target=tgt)


def _write_configs(graph: PipelineGraph, base_dir: Path) -> None:
    """Write config sidecars for parse() round-trips."""
    from haute._config_io import collect_node_configs

    for rel_path, content in collect_node_configs(graph).items():
        abs_path = base_dir / rel_path
        abs_path.parent.mkdir(parents=True, exist_ok=True)
        abs_path.write_text(content, encoding="utf-8")


def _normalize_value(value: Any) -> Any:
    """Recursively normalize graph payloads for structural comparison."""
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        normalized: dict[str, Any] = {}
        for key in sorted(value):
            if key == "contract":
                continue
            normalized_value = _normalize_value(value[key])
            if key == "code" and normalized_value == "":
                continue
            normalized[key] = normalized_value
        return normalized
    if isinstance(value, list):
        return [_normalize_value(v) for v in value]
    return value


def _normalized_graph(graph: PipelineGraph) -> tuple[Any, ...]:
    """Return a stable structural fingerprint for a graph."""
    nodes = tuple(
        sorted(
            (
                node.id,
                node.data.label,
                _normalize_value(node.data.nodeType),
                _normalize_value(node.data.config),
            )
            for node in graph.nodes
        )
    )
    edges = tuple(sorted((edge.source, edge.target) for edge in graph.edges))
    return (
        nodes,
        edges,
        graph.preamble or "",
        tuple(_normalize_value(graph.preserved_blocks or [])),
    )


def _roundtrip(graph: PipelineGraph, base_dir: Path) -> PipelineGraph:
    """Run graph -> codegen -> parse with sidecar configs."""
    code = graph_to_code(graph, pipeline_name=graph.pipeline_name or "graph_shape")
    compile_node_code(code)
    _write_configs(graph, base_dir)
    return parse_pipeline_source(
        code,
        source_file=str(base_dir / "graph_shape_roundtrip.py"),
        _base_dir=base_dir,
    )


class TestEmptyGraphContract:
    def test_empty_graph_round_trips_as_editor_state(self, tmp_path: Path) -> None:
        graph = _g({"nodes": [], "edges": []})

        code = graph_to_code(graph, pipeline_name="empty")
        compile_node_code(code)

        parsed = parse_pipeline_source(code, source_file=str(tmp_path / "empty.py"))

        assert parsed.nodes == []
        assert parsed.edges == []
        assert parsed.pipeline_name == "empty"

    def test_empty_graph_has_no_trace_target(self) -> None:
        graph = _g({"nodes": [], "edges": []})

        with pytest.raises(ValueError, match="Empty graph"):
            execute_trace(graph, row_index=0)


class TestSingleNodeVariants:
    def test_source_only_graph_round_trips(self, tmp_path: Path) -> None:
        graph = _g(
            {
                "nodes": [
                    _node(
                        {
                            "id": "src",
                            "data": {
                                "label": "src",
                                "nodeType": "dataInput",
                                "config": {
                                    "inputType": "file",
                                    "format": "parquet",
                                    "mode": "scan",
                                    "path": "data/input.parquet",
                                    "arguments": {},
                                },
                            },
                        }
                    )
                ],
                "edges": [],
            }
        )

        code = graph_to_code(graph, pipeline_name="source_only")
        compile_node_code(code)
        _write_configs(graph, tmp_path)

        parsed = parse_pipeline_source(
            code,
            source_file=str(tmp_path / "source_only.py"),
            _base_dir=tmp_path,
        )

        assert _normalized_graph(parsed) == _normalized_graph(graph)

    def test_sink_only_graph_fails_loudly(self) -> None:
        graph = _g(
            {
                "nodes": [
                    _node(
                        {
                            "id": "sink",
                            "data": {
                                "label": "sink",
                                "nodeType": "dataOutput",
                                "config": {
                                    "outputType": "file",
                                    "format": "parquet",
                                    "mode": "sink",
                                    "path": "outputs/result",
                                    "arguments": {},
                                },
                            },
                        }
                    )
                ],
                "edges": [],
            }
        )

        results = execute_graph(graph)

        assert results["sink"].status == "error"
        assert "No input data available" in results["sink"].error


class TestExploreGraphShape:
    def test_valid_explore_graph_round_trips(self, tmp_path: Path) -> None:
        graph = _g(
            {
                "nodes": [
                    _node(
                        {
                            "id": "Source",
                            "data": {
                                "label": "Source",
                                "nodeType": "dataInput",
                                "config": {
                                    "inputType": "file",
                                    "format": "parquet",
                                    "mode": "scan",
                                    "path": "data/input.parquet",
                                    "arguments": {},
                                },
                            },
                        }
                    ),
                    _node(
                        {
                            "id": "Explore",
                            "data": {"label": "Explore", "nodeType": "explore", "config": {}},
                        }
                    ),
                ],
                "edges": [_edge("Source", "Explore")],
            }
        )

        parsed = _roundtrip(graph, tmp_path)

        assert _normalized_graph(parsed) == _normalized_graph(graph)

    def test_codegen_rejects_explore_without_input(self) -> None:
        graph = _g(
            {
                "nodes": [
                    _node(
                        {
                            "id": "explore",
                            "data": {"label": "Explore", "nodeType": "explore", "config": {}},
                        }
                    )
                ],
                "edges": [],
            }
        )

        with pytest.raises(ParseError, match="exactly one incoming edge"):
            graph_to_code(graph, pipeline_name="bad_explore")

    def test_codegen_rejects_explore_with_multiple_inputs(self) -> None:
        graph = _g(
            {
                "nodes": [
                    _node(
                        {
                            "id": "a",
                            "data": {
                                "label": "A",
                                "nodeType": "dataInput",
                                "config": {
                                    "inputType": "file",
                                    "format": "parquet",
                                    "mode": "scan",
                                    "path": "a.parquet",
                                    "arguments": {},
                                },
                            },
                        }
                    ),
                    _node(
                        {
                            "id": "b",
                            "data": {
                                "label": "B",
                                "nodeType": "dataInput",
                                "config": {
                                    "inputType": "file",
                                    "format": "parquet",
                                    "mode": "scan",
                                    "path": "b.parquet",
                                    "arguments": {},
                                },
                            },
                        }
                    ),
                    _node(
                        {
                            "id": "explore",
                            "data": {"label": "Explore", "nodeType": "explore", "config": {}},
                        }
                    ),
                ],
                "edges": [_edge("a", "explore"), _edge("b", "explore")],
            }
        )

        with pytest.raises(ParseError, match="exactly one incoming edge"):
            graph_to_code(graph, pipeline_name="bad_explore")

    def test_codegen_rejects_explore_outgoing_edge(self) -> None:
        graph = _g(
            {
                "nodes": [
                    _node(
                        {
                            "id": "src",
                            "data": {
                                "label": "Source",
                                "nodeType": "dataInput",
                                "config": {
                                    "inputType": "file",
                                    "format": "parquet",
                                    "mode": "scan",
                                    "path": "data.parquet",
                                    "arguments": {},
                                },
                            },
                        }
                    ),
                    _node(
                        {
                            "id": "explore",
                            "data": {"label": "Explore", "nodeType": "explore", "config": {}},
                        }
                    ),
                    _node(
                        {
                            "id": "out",
                            "data": {
                                "label": "Output",
                                "nodeType": "output",
                                "config": make_output_config([]),
                            },
                        }
                    ),
                ],
                "edges": [_edge("src", "explore"), _edge("explore", "out")],
            }
        )

        with pytest.raises(ParseError, match="cannot have outgoing edges"):
            graph_to_code(graph, pipeline_name="bad_explore")

    def test_executor_rejects_raw_explore_outgoing_edge(self, tmp_path: Path) -> None:
        path = tmp_path / "data.parquet"

        pl.DataFrame({"x": [1]}).write_parquet(path)
        graph = _g(
            {
                "nodes": [
                    _node(
                        {
                            "id": "src",
                            "data": {
                                "label": "Source",
                                "nodeType": "dataInput",
                                "config": {
                                    "inputType": "file",
                                    "format": "parquet",
                                    "mode": "scan",
                                    "path": str(path),
                                    "arguments": {},
                                },
                            },
                        }
                    ),
                    _node(
                        {
                            "id": "explore",
                            "data": {"label": "Explore", "nodeType": "explore", "config": {}},
                        }
                    ),
                    _node(
                        {
                            "id": "out",
                            "data": {
                                "label": "Output",
                                "nodeType": "output",
                                "config": make_output_config(["x"]),
                            },
                        }
                    ),
                ],
                "edges": [_edge("src", "explore"), _edge("explore", "out")],
            }
        )

        with pytest.raises(ParseError, match="cannot have outgoing edges"):
            execute_graph(graph, target_node_id="out")

    def test_executor_rejects_raw_explore_without_input(self) -> None:
        graph = _g(
            {
                "nodes": [
                    _node(
                        {
                            "id": "explore",
                            "data": {"label": "Explore", "nodeType": "explore", "config": {}},
                        }
                    )
                ],
                "edges": [],
            }
        )

        with pytest.raises(ParseError, match="exactly one incoming edge"):
            execute_graph(graph, target_node_id="explore")

    def test_target_preview_ignores_disconnected_explore_outside_lineage(
        self,
        tmp_path: Path,
    ) -> None:
        data_path = tmp_path / "claims.parquet"
        pl.DataFrame({"x": [1, 2]}).write_parquet(data_path)
        graph = _g(
            {
                "nodes": [
                    _node(
                        {
                            "id": "src",
                            "data": {
                                "label": "Source",
                                "nodeType": "dataInput",
                                "config": {
                                    "inputType": "file",
                                    "format": "parquet",
                                    "mode": "scan",
                                    "path": str(data_path),
                                    "arguments": {},
                                },
                            },
                        }
                    ),
                    _node(
                        {
                            "id": "calc",
                            "data": {
                                "label": "Calc",
                                "nodeType": "polars",
                                "config": {"code": "df = df.with_columns(y=pl.col('x') * 2)"},
                            },
                        }
                    ),
                    _node(
                        {
                            "id": "explore",
                            "data": {"label": "Explore", "nodeType": "explore", "config": {}},
                        }
                    ),
                ],
                "edges": [_edge("src", "calc")],
            }
        )
        build_test_input_snapshot(graph.node_map["src"].data.config)

        results = execute_graph(
            graph,
            target_node_id="calc",
            target_preview_only=True,
        )

        assert results["calc"].status == "ok"
        assert [(col.name, col.dtype) for col in results["calc"].columns] == [
            ("x", "Int64"),
            ("y", "Int64"),
        ]

    def test_executor_rejects_raw_explore_with_multiple_inputs(self, tmp_path: Path) -> None:
        first = tmp_path / "first.parquet"
        second = tmp_path / "second.parquet"

        pl.DataFrame({"x": [1]}).write_parquet(first)
        pl.DataFrame({"x": [2]}).write_parquet(second)
        graph = _g(
            {
                "nodes": [
                    _node(
                        {
                            "id": "first",
                            "data": {
                                "label": "First",
                                "nodeType": "dataInput",
                                "config": {
                                    "inputType": "file",
                                    "format": "parquet",
                                    "mode": "scan",
                                    "path": str(first),
                                    "arguments": {},
                                },
                            },
                        }
                    ),
                    _node(
                        {
                            "id": "second",
                            "data": {
                                "label": "Second",
                                "nodeType": "dataInput",
                                "config": {
                                    "inputType": "file",
                                    "format": "parquet",
                                    "mode": "scan",
                                    "path": str(second),
                                    "arguments": {},
                                },
                            },
                        }
                    ),
                    _node(
                        {
                            "id": "explore",
                            "data": {"label": "Explore", "nodeType": "explore", "config": {}},
                        }
                    ),
                ],
                "edges": [_edge("first", "explore"), _edge("second", "explore")],
            }
        )

        with pytest.raises(ParseError, match="exactly one incoming edge"):
            execute_graph(graph, target_node_id="explore")


class TestLongDescriptions:
    def test_very_long_node_description_roundtrips(self, tmp_path: Path) -> None:
        long_description = "Long actuarial modelling note. " + ("premium factor " * 500)
        graph = _g(
            {
                "nodes": [
                    _node(
                        {
                            "id": "calc",
                            "data": {
                                "label": "calc",
                                "description": long_description,
                                "nodeType": "polars",
                                "config": {
                                    "code": "df = df.with_columns(total=pl.col('x') * 2)",
                                },
                            },
                        }
                    )
                ],
                "edges": [],
            }
        )

        code = graph_to_code(graph, pipeline_name="long_description")
        compile_node_code(code)
        parsed = parse_pipeline_source(
            code,
            source_file=str(tmp_path / "long_description.py"),
            _base_dir=tmp_path,
        )

        assert parsed.nodes[0].data.description == long_description


class TestRoundTripDrift:
    def test_ten_cycle_roundtrip_is_structurally_stable(self, tmp_path: Path) -> None:
        graph = _g(
            {
                "nodes": [
                    _node(
                        {
                            "id": "src",
                            "data": {
                                "label": "src",
                                "nodeType": "dataInput",
                                "config": {
                                    "inputType": "file",
                                    "format": "parquet",
                                    "mode": "scan",
                                    "path": "data/input.parquet",
                                    "arguments": {},
                                },
                            },
                        }
                    ),
                    _node(
                        {
                            "id": "calc",
                            "data": {
                                "label": "calc",
                                "nodeType": "polars",
                                "config": {
                                    "code": "df = df.with_columns(total=pl.col('x') * 2)",
                                },
                            },
                        }
                    ),
                ],
                "edges": [_edge("src", "calc")],
            }
        )

        baseline = _normalized_graph(graph)
        current = graph

        for cycle in range(10):
            cycle_dir = tmp_path / f"cycle_{cycle}"
            cycle_dir.mkdir()
            current = _roundtrip(current, cycle_dir)
            assert _normalized_graph(current) == baseline
