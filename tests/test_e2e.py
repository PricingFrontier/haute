"""End-to-end integration test: parse → execute → trace → codegen → re-parse.

Exercises the full haute pipeline lifecycle using the fixture pipeline to
verify all stages interoperate correctly.
"""

from __future__ import annotations

import json
import shutil
import textwrap
from pathlib import Path

import polars as pl
import pytest

from haute._config_io import collect_node_configs
from haute._submodel_instances import qualified_runtime_node_id
from haute._types import GraphEdge, GraphNode, NodeData, NodeType, PipelineGraph
from haute.codegen import graph_to_code, graph_to_code_multi
from haute.executor import execute_graph
from haute.graph_utils import _prune_live_switch_edges, flatten_graph
from haute.parser import parse_pipeline_file, parse_pipeline_source
from haute.routes._submodel_ops import create_submodel_graph
from haute.trace import execute_trace
from tests.conftest import (
    make_file_input_config,
    make_file_output_config,
    make_output_config,
    make_ready_file_input_config,
)

FIXTURE_DIR = Path("tests/fixtures")
PIPELINE_FILE = FIXTURE_DIR / "pipeline.py"
EXPECTED_FIXTURE_PREMIUM = 2.75


@pytest.fixture(autouse=True)
def _isolate_json_cache(tmp_path, monkeypatch, _widen_sandbox_root):
    """Redirect the JSON parquet cache to a temp dir and prewarm it.

    Without this, a stale .haute_cache/ in the working directory (from a
    previous real-data run) can poison the fixture pipeline's api-input
    node with columns from a completely different schema.

    Under v2 (post-commit-5.5) the per-port cache replaces the v1 single
    parquet. We pre-populate the cache via ``build_per_port_cache`` so
    the executor's apiInput consumer (which reads from the per-port
    cache via ``load_per_port_cache``) exercises the performance fast path.
    This mirrors the optional "Cache as Parquet" prewarm action; uncached
    execution is covered separately and reads the JSON source directly.
    """
    import json

    import haute._json_flatten as jf
    from haute._json_shred import build_per_port_cache

    # Keep the root supplied by _widen_sandbox_root: this fixture executes
    # both repository fixtures and absolute tmp_path-backed lifecycle graphs.

    cache_dir = str(tmp_path / "json_cache")
    monkeypatch.setattr(jf, "_CACHE_DIR", cache_dir)

    runtime_data_dir = tmp_path / "data"
    shutil.copytree(FIXTURE_DIR / "data", runtime_data_dir, dirs_exist_ok=True)

    # Load the v2 config the fixture pipeline references so the
    # per-port shred matches the user-visible schema mapping.
    v2_config = json.loads(
        (FIXTURE_DIR / "config/quote_input/quotes.json").read_text(),
    )

    for data_path in (
        (FIXTURE_DIR / "data/api_input.json").resolve(),
        (runtime_data_dir / "api_input.json").resolve(),
    ):
        port_cache_dir = jf._json_cache_dir(str(data_path), "working")
        port_cache_dir.mkdir(parents=True, exist_ok=True)
        build_per_port_cache(
            data_path=str(data_path),
            v2_config=v2_config,
            cache_dir=port_cache_dir,
        )
        # Mark the working layer as consulted so the dual-cache emitter
        # picks it up; otherwise it falls through to committed/ which is
        # not populated by this fixture.
        jf._mark_working_consulted(str(data_path))


class TestEndToEnd:
    """Full round-trip: parse → execute → trace → codegen → re-parse."""

    def test_parse_fixture_pipeline(self):
        """Fixture pipeline parses into a valid graph."""
        graph = parse_pipeline_file(PIPELINE_FILE)
        node_ids = {n.id for n in graph.nodes}
        expected_nodes = {
            "quotes",
            "batch_quotes",
            "policies",
            "area_lookup",
            "calculate_premium",
            "output",
            "results_write",
        }
        assert node_ids == expected_nodes
        assert len(graph.edges) == 6
        quotes_edge = next(edge for edge in graph.edges if edge.source == "quotes")
        assert quotes_edge.sourceHandle == "quotes"

    def test_execute_all_nodes(self):
        """All nodes execute and the fixture premium matches the oracle."""
        graph = parse_pipeline_file(PIPELINE_FILE)
        results = execute_graph(graph)
        for nid, result in results.items():
            assert result.status == "ok", f"Node {nid!r} failed: {result.error}"

        # Non-preview ancestors report schema without forcing collection, and
        # an unselected multi-frame source has no canonical flat row count.
        # The materialised output is the row-bearing end-to-end oracle.
        assert results["output"].row_count > 0

        output_row = results["output"].preview[0]
        assert output_row["area_factor"] == pytest.approx(1.1)
        assert output_row["premium"] == pytest.approx(EXPECTED_FIXTURE_PREMIUM)

    def test_execute_target_node(self):
        """Executing with a target returns results for the target and its ancestors."""
        graph = parse_pipeline_file(PIPELINE_FILE)
        results = execute_graph(graph, target_node_id="output")
        assert "output" in results
        assert results["output"].status == "ok"
        # All ancestors of output should be present
        assert "calculate_premium" in results
        assert "area_lookup" in results

    def test_trace_produces_steps(self):
        """Trace returns steps and the final traced price is exact."""
        graph = parse_pipeline_file(PIPELINE_FILE)
        trace = execute_trace(graph, row_index=0, target_node_id="output", column="premium")
        assert len(trace.steps) > 0
        step_names = [s.node_id for s in trace.steps]
        assert "output" in step_names
        assert trace.output_value == pytest.approx(EXPECTED_FIXTURE_PREMIUM)

    def test_trace_row_has_data(self):
        """Each trace step includes output values and schema diff."""
        graph = parse_pipeline_file(PIPELINE_FILE)
        trace = execute_trace(graph, row_index=0, target_node_id="output")
        for step in trace.steps:
            assert isinstance(step.output_values, dict)
            assert step.schema_diff is not None

    def test_codegen_produces_code(self):
        """Code generated from the parsed graph is non-empty and valid Python text."""
        graph = parse_pipeline_file(PIPELINE_FILE)
        code = graph_to_code(graph, pipeline_name="roundtrip")
        compile(code, "<test_codegen>", "exec")
        assert "Pipeline" in code
        assert "@pipeline." in code

    def test_codegen_roundtrip_preserves_structure(self, tmp_path):
        """parse → codegen → re-parse preserves node types."""
        from haute._config_io import collect_node_configs

        graph = parse_pipeline_file(PIPELINE_FILE)
        code = graph_to_code(graph, pipeline_name="roundtrip")

        # Write config files so the parser can resolve them
        for rel_path, content in collect_node_configs(graph).items():
            cfg_file = tmp_path / rel_path
            cfg_file.parent.mkdir(parents=True, exist_ok=True)
            cfg_file.write_text(content)

        reparsed = parse_pipeline_source(code, _base_dir=tmp_path)

        orig_types = sorted(n.data.nodeType for n in graph.nodes)
        new_types = sorted(n.data.nodeType for n in reparsed.nodes)
        assert orig_types == new_types

    def test_codegen_roundtrip_preserves_node_count(self, tmp_path):
        """Re-parsed graph has the same number of nodes."""
        graph = parse_pipeline_file(PIPELINE_FILE)
        code = graph_to_code(graph, pipeline_name="roundtrip")
        # Post Item #18: parser requires config sidecar files to exist.
        for rel_path, content in collect_node_configs(graph).items():
            cfg_file = tmp_path / rel_path
            cfg_file.parent.mkdir(parents=True, exist_ok=True)
            cfg_file.write_text(content)
        reparsed = parse_pipeline_source(code, _base_dir=tmp_path)
        assert len(reparsed.nodes) == len(graph.nodes)

    def test_codegen_roundtrip_preserves_edge_count(self, tmp_path):
        """Re-parsed graph has the same number of edges."""
        graph = parse_pipeline_file(PIPELINE_FILE)
        code = graph_to_code(graph, pipeline_name="roundtrip")
        for rel_path, content in collect_node_configs(graph).items():
            cfg_file = tmp_path / rel_path
            cfg_file.parent.mkdir(parents=True, exist_ok=True)
            cfg_file.write_text(content)
        reparsed = parse_pipeline_source(code, _base_dir=tmp_path)
        assert len(reparsed.edges) == len(graph.edges)

    def test_full_lifecycle(self, tmp_path):
        """Complete lifecycle: parse → execute → trace → codegen → re-parse → re-execute."""
        from haute._config_io import collect_node_configs

        # Step 1: Parse
        graph = parse_pipeline_file(PIPELINE_FILE)
        assert len(graph.nodes) > 0

        # Step 2: Execute
        results = execute_graph(graph)
        output_result = results["output"]
        assert output_result.status == "ok"
        orig_row_count = output_result.row_count
        assert output_result.preview[0]["premium"] == pytest.approx(EXPECTED_FIXTURE_PREMIUM)

        # Step 3: Trace
        trace = execute_trace(graph, row_index=0, target_node_id="output")
        assert len(trace.steps) > 0

        # Step 4: Codegen → re-parse
        code = graph_to_code(graph, pipeline_name="lifecycle")

        # Write config files so the parser can resolve them
        for rel_path, content in collect_node_configs(graph).items():
            cfg_file = tmp_path / rel_path
            cfg_file.parent.mkdir(parents=True, exist_ok=True)
            cfg_file.write_text(content)

        from haute._sandbox import set_project_root

        set_project_root(tmp_path)
        reparsed = parse_pipeline_source(code, _base_dir=tmp_path)
        assert len(reparsed.nodes) == len(graph.nodes)

        # Step 5: Re-execute the re-parsed graph
        results2 = execute_graph(reparsed)
        output_result2 = results2["output"]
        assert output_result2.status == "ok"
        assert output_result2.row_count == orig_row_count
        assert output_result2.preview[0]["premium"] == pytest.approx(EXPECTED_FIXTURE_PREMIUM)
        # Core input columns must survive the round-trip
        reparsed_columns = {c.name for c in output_result2.columns}
        assert "VehPower" in reparsed_columns
        assert "Area" in reparsed_columns
        assert "Exposure" in reparsed_columns


# ═══════════════════════════════════════════════════════════════════════════
# Helper: build a PipelineGraph programmatically
# ═══════════════════════════════════════════════════════════════════════════


def _posix_path(p) -> str:
    """Convert a path to a forward-slash string (avoids Windows backslash
    escape issues in codegen round-trips)."""
    return str(p).replace("\\", "/")


def _make_node(nid: str, label: str, node_type: NodeType, config: dict | None = None) -> GraphNode:
    return GraphNode(
        id=nid,
        data=NodeData(
            label=label,
            nodeType=node_type,
            config=config or {},
        ),
    )


def _make_edge(src: str, tgt: str, *, source_port: str | None = None) -> GraphEdge:
    return GraphEdge(
        id=f"e_{src}_{tgt}",
        source=src,
        target=tgt,
        sourceHandle=source_port,
    )


# ═══════════════════════════════════════════════════════════════════════════
# 1. Full pipeline lifecycle: create → add nodes → connect → preview →
#    trace → save → reload → verify identical
# ═══════════════════════════════════════════════════════════════════════════


class TestFullPipelineLifecycle:
    """Create a pipeline entirely from graph objects, execute it, trace it,
    save to code, reload, and verify everything matches.
    """

    @pytest.fixture()
    def pipeline_graph(self, tmp_path):
        """Build a small but realistic pipeline graph with data files."""
        # Write test data
        data_path = tmp_path / "data.parquet"
        df = pl.DataFrame(
            {
                "id": [1, 2, 3, 4, 5],
                "value": [10.0, 20.0, 30.0, 40.0, 50.0],
                "region": ["A", "B", "A", "B", "A"],
            }
        )
        df.write_parquet(data_path)

        nodes = [
            _make_node(
                "src",
                "src",
                NodeType.DATA_INPUT,
                make_ready_file_input_config(_posix_path(data_path)),
            ),
            _make_node(
                "transform",
                "transform",
                NodeType.POLARS,
                {"code": "df = df.with_columns(doubled=pl.col('value') * 2)"},
            ),
            _make_node(
                "out",
                "out",
                NodeType.OUTPUT,
                make_output_config(["id", "value", "region", "doubled"]),
            ),
        ]
        edges = [
            _make_edge("src", "transform"),
            _make_edge("transform", "out"),
        ]
        return PipelineGraph(
            nodes=nodes,
            edges=edges,
            pipeline_name="lifecycle_test",
        )

    def test_create_execute_trace_save_reload(self, tmp_path, pipeline_graph, _widen_sandbox_root):
        # Step 1: Execute
        results = execute_graph(pipeline_graph)
        assert results["out"].status == "ok"
        assert results["out"].row_count == 5
        out_cols = {c.name for c in results["out"].columns}
        assert "doubled" in out_cols

        # Step 2: Trace
        trace = execute_trace(pipeline_graph, row_index=0, target_node_id="out")
        assert len(trace.steps) == 3
        assert trace.steps[-1].node_id == "out"
        assert "doubled" in trace.steps[-1].output_values

        # Step 3: Save (codegen + config files)
        code = graph_to_code(pipeline_graph, pipeline_name="lifecycle_test")
        for rel_path, content in collect_node_configs(pipeline_graph).items():
            cfg_file = tmp_path / rel_path
            cfg_file.parent.mkdir(parents=True, exist_ok=True)
            cfg_file.write_text(content)

        py_file = tmp_path / "lifecycle_test.py"
        py_file.write_text(code)

        # Step 4: Reload and verify structure
        reloaded = parse_pipeline_source(code, _base_dir=tmp_path)
        assert len(reloaded.nodes) == len(pipeline_graph.nodes)
        assert len(reloaded.edges) == len(pipeline_graph.edges)
        orig_types = sorted(n.data.nodeType for n in pipeline_graph.nodes)
        new_types = sorted(n.data.nodeType for n in reloaded.nodes)
        assert orig_types == new_types

        # Step 5: Re-execute reloaded graph and verify identical output
        results2 = execute_graph(reloaded)
        assert results2["out"].status == "ok"
        assert results2["out"].row_count == results["out"].row_count


# ═══════════════════════════════════════════════════════════════════════════
# 2. Save-parse roundtrip with ALL node types
# ═══════════════════════════════════════════════════════════════════════════


class TestAllNodeTypesRoundtrip:
    """Create a graph covering every node type, save → parse → verify."""

    def test_roundtrip_all_node_types(self, tmp_path, _widen_sandbox_root):
        """Create a graph with every supported node type, codegen → parse → verify.

        SUBMODEL is a meta-node (grouping container), not emitted by codegen as
        a standalone node — it is excluded from the roundtrip assertion.
        """
        # Write test data files for nodes that need them
        data_path = tmp_path / "data.parquet"
        pl.DataFrame({"x": [1, 2], "region": ["A", "B"]}).write_parquet(data_path)
        json_path = tmp_path / "api_input.json"
        json_path.write_text(json.dumps([{"x": 1, "region": "A"}]))
        lookup_path = tmp_path / "lookup.json"
        lookup_path.write_text(json.dumps({"A": 1.0, "B": 2.0}))
        sink_path = tmp_path / "output.parquet"

        # Build the node list covering all non-meta node types.
        # Every node is connected in a linear chain so codegen handles
        # source_names correctly and the parser can reconstruct edges.
        nodes = [
            _make_node(
                "ds",
                "ds",
                NodeType.DATA_INPUT,
                make_ready_file_input_config(_posix_path(data_path)),
            ),
            _make_node(
                "api",
                "api",
                NodeType.API_INPUT,
                {
                    "path": _posix_path(json_path),
                    "tables": [
                        {
                            "path": "$[:]",
                            "label": "api",
                            "emit": True,
                            "columns": [
                                {
                                    "name": "x",
                                    "path": "$[:].x",
                                    "type": "int",
                                    "selected": True,
                                },
                                {
                                    "name": "region",
                                    "path": "$[:].region",
                                    "type": "str",
                                    "selected": True,
                                },
                            ],
                        }
                    ],
                },
            ),
            _make_node(
                "const",
                "const",
                NodeType.CONSTANT,
                {
                    "values": [{"name": "base_rate", "value": "100"}],
                },
            ),
            _make_node(
                "switch",
                "switch",
                NodeType.LIVE_SWITCH,
                {
                    "input_scenario_map": {"ds": "test_batch", "api": "live"},
                },
            ),
            # Transform has two upstreams (switch + const). Post Item #22
            # codegen forbids empty code + multiple sources, so explicitly
            # name which input to forward.
            _make_node("transform", "transform", NodeType.POLARS, {"code": "df = switch"}),
            _make_node(
                "band",
                "band",
                NodeType.BANDING,
                {
                    "factors": [
                        {
                            "banding": "continuous",
                            "column": "x",
                            "outputColumn": "x_band",
                            "rules": [
                                {"from": 0, "to": 1, "label": "low"},
                                {"from": 1, "to": 100, "label": "high"},
                            ],
                        }
                    ],
                },
            ),
            _make_node(
                "rating",
                "rating",
                NodeType.RATING_STEP,
                {
                    "tables": [
                        {
                            "name": "region_table",
                            "factors": ["region"],
                            "outputColumn": "region_factor",
                            "entries": [
                                {"region": "A", "value": 1.0},
                                {"region": "B", "value": 1.5},
                            ],
                        }
                    ],
                },
            ),
            _make_node(
                "mscore",
                "mscore",
                NodeType.MODEL_SCORE,
                {
                    "sourceType": "run",
                    "run_id": "abc123",
                    "artifact_path": "model.cbm",
                    "task": "regression",
                    "output_column": "prediction",
                },
            ),
            _make_node(
                "expander",
                "expander",
                NodeType.SCENARIO_EXPANDER,
                {
                    "quote_id": "x",
                    "column_name": "scenario_val",
                    "min_value": 0.8,
                    "max_value": 1.2,
                    "steps": 3,
                    "step_column": "scenario_idx",
                },
            ),
            _make_node(
                "opt",
                "opt",
                NodeType.OPTIMISER,
                {
                    "mode": "online",
                    "quote_id": "x",
                    "objective": "premium",
                },
            ),
            _make_node(
                "opt_apply",
                "opt_apply",
                NodeType.OPTIMISER_APPLY,
                {
                    "sourceType": "file",
                    "artifact_path": "optimiser.json",
                },
            ),
            _make_node(
                "ext",
                "ext",
                NodeType.EXTERNAL_FILE,
                {
                    "path": _posix_path(lookup_path),
                    "fileType": "json",
                    "code": "",
                },
            ),
            _make_node(
                "sink",
                "sink",
                NodeType.DATA_OUTPUT,
                make_file_output_config(sink_path),
            ),
            _make_node("out", "out", NodeType.OUTPUT, make_output_config([])),
        ]

        # Chain them linearly so every node has a source_name
        edges = [
            _make_edge("ds", "switch"),
            _make_edge("api", "switch", source_port="api"),
            _make_edge("switch", "transform"),
            _make_edge("transform", "band"),
            _make_edge("band", "rating"),
            _make_edge("rating", "mscore"),
            _make_edge("mscore", "expander"),
            _make_edge("expander", "opt"),
            _make_edge("opt", "opt_apply"),
            _make_edge("opt_apply", "ext"),
            _make_edge("ext", "sink"),
            _make_edge("ext", "out"),
            _make_edge("const", "transform"),
        ]

        graph = PipelineGraph(nodes=nodes, edges=edges, pipeline_name="all_types")

        # Generate code
        code = graph_to_code(graph, pipeline_name="all_types")

        # Write config files for re-parsing
        for rel_path, content in collect_node_configs(graph).items():
            cfg_file = tmp_path / rel_path
            cfg_file.parent.mkdir(parents=True, exist_ok=True)
            cfg_file.write_text(content)

        # Verify code compiles
        compile(code, "<all_types>", "exec")

        # Parse back
        reparsed = parse_pipeline_source(code, _base_dir=tmp_path)

        # Compare node types (sorted)
        orig_types = sorted(n.data.nodeType for n in graph.nodes)
        new_types = sorted(n.data.nodeType for n in reparsed.nodes)
        assert orig_types == new_types

        # Edge count should match
        assert len(reparsed.edges) == len(graph.edges)


# ═══════════════════════════════════════════════════════════════════════════
# 3. Submodel lifecycle: create → verify file → dissolve → verify restored
# ═══════════════════════════════════════════════════════════════════════════


class TestSubmodelLifecycle:
    """Create a graph, extract submodel, verify, dissolve back, verify."""

    def test_extract_and_dissolve_submodel(self, tmp_path, _widen_sandbox_root):
        data_path = tmp_path / "data.parquet"
        pl.DataFrame({"x": [1, 2, 3], "y": [10, 20, 30]}).write_parquet(data_path)

        nodes = [
            _make_node(
                "src",
                "src",
                NodeType.DATA_INPUT,
                make_ready_file_input_config(_posix_path(data_path)),
            ),
            _make_node(
                "t1",
                "step_one",
                NodeType.POLARS,
                {
                    "code": "df = df.with_columns(a=pl.col('x') + 1)",
                },
            ),
            _make_node(
                "t2",
                "step_two",
                NodeType.POLARS,
                {
                    "code": "df = df.with_columns(b=pl.col('a') + 10)",
                },
            ),
            _make_node("out", "out", NodeType.OUTPUT, make_output_config(["x", "y", "a", "b"])),
        ]
        edges = [
            _make_edge("src", "t1"),
            _make_edge("t1", "t2"),
            _make_edge("t2", "out"),
        ]
        graph = PipelineGraph(nodes=nodes, edges=edges, pipeline_name="submodel_test")
        # Step 1: Execute original
        results = execute_graph(graph)
        assert results["out"].status == "ok"
        orig_row_count = results["out"].row_count

        # Step 2: Extract submodel (t1 + t2)
        result = create_submodel_graph(graph, ["t1", "t2"], "inner_group")
        new_graph = result.graph

        # Verify the canonical occurrence and shared definition are present.
        sm_nodes = [node for node in new_graph.nodes if node.data.nodeType == NodeType.SUBMODEL]
        assert len(sm_nodes) == 1
        sm_node = sm_nodes[0]
        assert sm_node.id.startswith("submodel_instance_")
        assert sm_node.data.config == {
            "definitionId": "inner_group",
            "alias": "inner_group",
        }
        sm_definition = new_graph.submodels["inner_group"]
        assert {node.id for node in sm_definition.graph.nodes} == {"t1", "t2"}

        # Step 3: Generate code for submodel file
        files = graph_to_code_multi(
            new_graph,
            pipeline_name="submodel_test",
            source_file="main.py",
        )
        # Should have main file + submodel file
        assert len(files) == 2
        assert any("modules/" in k for k in files)

        # Step 4: Dissolve submodel back
        flat = flatten_graph(new_graph)

        # Dissolution preserves topology with occurrence-qualified child ids.
        runtime_t1 = qualified_runtime_node_id(sm_node.id, "t1")
        runtime_t2 = qualified_runtime_node_id(sm_node.id, "t2")
        flat_node_ids = {n.id for n in flat.nodes}
        assert flat_node_ids == {"src", "out", runtime_t1, runtime_t2}

        flat_edge_pairs = {(e.source, e.target) for e in flat.edges}
        assert flat_edge_pairs == {
            ("src", runtime_t1),
            (runtime_t1, runtime_t2),
            (runtime_t2, "out"),
        }

        # Step 5: Execute the dissolved graph — should produce same results
        results2 = execute_graph(flat)
        assert results2["out"].status == "ok"
        assert results2["out"].row_count == orig_row_count


# ═══════════════════════════════════════════════════════════════════════════
# 4. Config roundtrip: set configs → save → reload → verify configs preserved
# ═══════════════════════════════════════════════════════════════════════════


class TestConfigRoundtrip:
    """Set node configs, save, reload, verify configs are preserved."""

    def test_data_input_config_roundtrip(self, tmp_path, _widen_sandbox_root):
        data_path = tmp_path / "data.parquet"
        pl.DataFrame({"x": [1]}).write_parquet(data_path)

        node = _make_node(
            "ds",
            "ds",
            NodeType.DATA_INPUT,
            make_ready_file_input_config(_posix_path(data_path)),
        )
        graph = PipelineGraph(
            nodes=[node],
            edges=[],
            pipeline_name="cfg_test",
        )
        code = graph_to_code(graph, pipeline_name="cfg_test")
        for rel, content in collect_node_configs(graph).items():
            (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
            (tmp_path / rel).write_text(content)

        reparsed = parse_pipeline_source(code, _base_dir=tmp_path)
        assert len(reparsed.nodes) == 1
        assert reparsed.nodes[0].data.config.get("path") == _posix_path(data_path)

    def test_banding_config_roundtrip(self, tmp_path, _widen_sandbox_root):
        banding_config = {
            "factors": [
                {
                    "banding": "discrete",
                    "column": "region",
                    "outputColumn": "region_band",
                    "rules": [
                        {"value": "urban", "label": "city"},
                        {"value": "rural", "label": "country"},
                    ],
                    "default": "other",
                }
            ],
        }
        nodes = [
            _make_node("ds", "ds", NodeType.DATA_INPUT, make_file_input_config("data.parquet")),
            _make_node("band", "band", NodeType.BANDING, banding_config),
        ]
        edges = [_make_edge("ds", "band")]
        graph = PipelineGraph(nodes=nodes, edges=edges, pipeline_name="band_cfg")

        code = graph_to_code(graph, pipeline_name="band_cfg")
        for rel, content in collect_node_configs(graph).items():
            (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
            (tmp_path / rel).write_text(content)

        reparsed = parse_pipeline_source(code, _base_dir=tmp_path)
        band_node = [n for n in reparsed.nodes if n.data.nodeType == NodeType.BANDING]
        assert len(band_node) == 1
        factors = band_node[0].data.config.get("factors", [])
        assert len(factors) == 1
        assert factors[0]["column"] == "region"
        assert factors[0]["outputColumn"] == "region_band"

    def test_rating_step_config_roundtrip(self, tmp_path, _widen_sandbox_root):
        rating_config = {
            "tables": [
                {
                    "factors": ["age_band"],
                    "outputColumn": "age_factor",
                    "entries": [
                        {"age_band": "young", "value": 1.5},
                        {"age_band": "middle", "value": 1.0},
                        {"age_band": "senior", "value": 1.2},
                    ],
                    "defaultValue": "1.0",
                }
            ],
            "combinedOutputs": [
                {
                    "outputColumn": "total_factor",
                    "operation": "multiply",
                    "baseValue": 1,
                }
            ],
        }
        nodes = [
            _make_node("ds", "ds", NodeType.DATA_INPUT, make_file_input_config("data.parquet")),
            _make_node("rt", "rt", NodeType.RATING_STEP, rating_config),
        ]
        edges = [_make_edge("ds", "rt")]
        graph = PipelineGraph(nodes=nodes, edges=edges, pipeline_name="rt_cfg")

        code = graph_to_code(graph, pipeline_name="rt_cfg")
        for rel, content in collect_node_configs(graph).items():
            (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
            (tmp_path / rel).write_text(content)

        reparsed = parse_pipeline_source(code, _base_dir=tmp_path)
        rt_node = [n for n in reparsed.nodes if n.data.nodeType == NodeType.RATING_STEP]
        assert len(rt_node) == 1
        tables = rt_node[0].data.config.get("tables", [])
        assert len(tables) == 1
        assert tables[0]["outputColumn"] == "age_factor"
        entries = tables[0].get("entries", [])
        assert len(entries) == 3
        assert rt_node[0].data.config["combinedOutputs"] == rating_config["combinedOutputs"]

    def test_model_score_config_roundtrip(self, tmp_path, _widen_sandbox_root):
        ms_config = {
            "sourceType": "run",
            "run_id": "run_abc123",
            "artifact_path": "model.cbm",
            "task": "regression",
            "output_column": "pred",
        }
        nodes = [
            _make_node("ds", "ds", NodeType.DATA_INPUT, make_file_input_config("data.parquet")),
            _make_node("ms", "ms", NodeType.MODEL_SCORE, ms_config),
        ]
        edges = [_make_edge("ds", "ms")]
        graph = PipelineGraph(nodes=nodes, edges=edges, pipeline_name="ms_cfg")

        code = graph_to_code(graph, pipeline_name="ms_cfg")
        for rel, content in collect_node_configs(graph).items():
            (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
            (tmp_path / rel).write_text(content)

        reparsed = parse_pipeline_source(code, _base_dir=tmp_path)
        ms_node = [n for n in reparsed.nodes if n.data.nodeType == NodeType.MODEL_SCORE]
        assert len(ms_node) == 1
        cfg = ms_node[0].data.config
        assert cfg.get("task") == "regression"
        assert cfg.get("output_column") == "pred"

    def test_optimiser_config_roundtrip(self, tmp_path, _widen_sandbox_root):
        opt_config = {
            "mode": "online",
            "quote_id": "policy_id",
            "objective": "premium",
            "constraints": {"volume": {"min": 0.9}},
            "max_iter": 50,
        }
        nodes = [
            _make_node("ds", "ds", NodeType.DATA_INPUT, make_file_input_config("data.parquet")),
            _make_node("opt", "opt", NodeType.OPTIMISER, opt_config),
        ]
        edges = [_make_edge("ds", "opt")]
        graph = PipelineGraph(nodes=nodes, edges=edges, pipeline_name="opt_cfg")

        code = graph_to_code(graph, pipeline_name="opt_cfg")
        for rel, content in collect_node_configs(graph).items():
            (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
            (tmp_path / rel).write_text(content)

        reparsed = parse_pipeline_source(code, _base_dir=tmp_path)
        opt_node = [n for n in reparsed.nodes if n.data.nodeType == NodeType.OPTIMISER]
        assert len(opt_node) == 1
        cfg = opt_node[0].data.config
        assert cfg.get("mode") == "online"
        assert cfg.get("quote_id") == "policy_id"


# ═══════════════════════════════════════════════════════════════════════════
# 5. Git workflow: create branch → make changes → save → commit → revert
# ═══════════════════════════════════════════════════════════════════════════


# ═══════════════════════════════════════════════════════════════════════════
# 6. Utility module lifecycle: create → use in preamble → preview →
#    update → preview again → delete
# ═══════════════════════════════════════════════════════════════════════════


class TestUtilityModuleLifecycle:
    """Test creating, using, updating, and deleting utility modules."""

    def test_utility_lifecycle(self, tmp_path, monkeypatch, _widen_sandbox_root):
        import sys

        # Set up the project root in tmp_path so utility imports resolve
        monkeypatch.chdir(tmp_path)
        if str(tmp_path) not in sys.path:
            sys.path.insert(0, str(tmp_path))

        # Step 1: Create utility module
        util_dir = tmp_path / "utility"
        util_dir.mkdir()
        (util_dir / "__init__.py").write_text("")
        (util_dir / "helpers.py").write_text(
            textwrap.dedent("""\
            MAGIC_CONSTANT = 42

            def double_it(x):
                return x * 2
        """)
        )

        # Step 2: Write test data
        data_path = tmp_path / "data.parquet"
        pl.DataFrame({"x": [1, 2, 3]}).write_parquet(data_path)

        # Step 3: Create pipeline with preamble that uses the utility
        preamble = "from utility.helpers import MAGIC_CONSTANT, double_it"
        nodes = [
            _make_node(
                "src",
                "src",
                NodeType.DATA_INPUT,
                make_ready_file_input_config(_posix_path(data_path)),
            ),
            _make_node(
                "t",
                "transform",
                NodeType.POLARS,
                {
                    "code": "df = df.with_columns(result=pl.lit(double_it(MAGIC_CONSTANT)))",
                },
            ),
        ]
        edges = [_make_edge("src", "t")]
        graph = PipelineGraph(
            nodes=nodes,
            edges=edges,
            pipeline_name="util_test",
            preamble=preamble,
        )

        # Step 4: Preview (execute) — verify utility works
        results = execute_graph(graph)
        assert results["t"].status == "ok"
        # double_it(42) = 84
        preview = results["t"].preview
        assert all(row["result"] == 84 for row in preview)

        # Step 5: Update the utility module
        (util_dir / "helpers.py").write_text(
            textwrap.dedent("""\
            MAGIC_CONSTANT = 100

            def double_it(x):
                return x * 3  # changed to triple!
        """)
        )

        # Evict utility from sys.modules so the new version is picked up
        for mod_name in [k for k in sys.modules if k.startswith("utility")]:
            del sys.modules[mod_name]

        # Step 6: Preview again — verify updated utility is used.
        # Reuse the same graph structure; utility content invalidates the cache.
        results2 = execute_graph(graph)
        assert results2["t"].status == "ok"
        # double_it(100) = 300 now (triple)
        preview2 = results2["t"].preview
        assert all(row["result"] == 300 for row in preview2)

        # Step 7: Delete utility
        (util_dir / "helpers.py").unlink()
        assert not (util_dir / "helpers.py").exists()

        # Clean up sys.path
        if str(tmp_path) in sys.path:
            sys.path.remove(str(tmp_path))


# ═══════════════════════════════════════════════════════════════════════════
# 7. Multi-scenario execution: live_switch with batch/live scenarios
# ═══════════════════════════════════════════════════════════════════════════


class TestMultiScenarioExecution:
    """Verify live_switch edge pruning for different scenarios."""

    @pytest.fixture()
    def scenario_graph(self, tmp_path):
        """Build a graph with two source paths routing through a live_switch."""
        live_path = tmp_path / "live.parquet"
        batch_path = tmp_path / "batch.parquet"
        pl.DataFrame({"id": [1], "source": ["live"], "val": [100.0]}).write_parquet(live_path)
        pl.DataFrame(
            {"id": [1, 2, 3], "source": ["batch"] * 3, "val": [10.0, 20.0, 30.0]}
        ).write_parquet(batch_path)

        nodes = [
            _make_node(
                "live_src",
                "live_src",
                NodeType.DATA_INPUT,
                make_ready_file_input_config(_posix_path(live_path)),
            ),
            _make_node(
                "batch_src",
                "batch_src",
                NodeType.DATA_INPUT,
                make_ready_file_input_config(_posix_path(batch_path)),
            ),
            _make_node(
                "switch",
                "switch",
                NodeType.LIVE_SWITCH,
                {
                    "input_scenario_map": {"live_src": "live", "batch_src": "test_batch"},
                },
            ),
            _make_node("out", "out", NodeType.OUTPUT, make_output_config(["id", "source", "val"])),
        ]
        edges = [
            _make_edge("live_src", "switch"),
            _make_edge("batch_src", "switch"),
            _make_edge("switch", "out"),
        ]
        return PipelineGraph(
            nodes=nodes,
            edges=edges,
            pipeline_name="scenario_test",
            sources=["live", "test_batch"],
        )

    def test_live_scenario_prunes_batch_edge(self, scenario_graph, _widen_sandbox_root):
        """In 'live' scenario, the batch_src edge should be pruned."""
        node_map = scenario_graph.node_map
        pruned = _prune_live_switch_edges(scenario_graph.edges, node_map, "live")

        # The batch_src -> switch edge should be removed
        pruned_pairs = {(e.source, e.target) for e in pruned}
        assert ("live_src", "switch") in pruned_pairs
        assert ("batch_src", "switch") not in pruned_pairs
        assert ("switch", "out") in pruned_pairs

    def test_batch_scenario_prunes_live_edge(self, scenario_graph, _widen_sandbox_root):
        """In 'test_batch' scenario, the live_src edge should be pruned."""
        node_map = scenario_graph.node_map
        pruned = _prune_live_switch_edges(scenario_graph.edges, node_map, "test_batch")

        pruned_pairs = {(e.source, e.target) for e in pruned}
        assert ("batch_src", "switch") in pruned_pairs
        assert ("live_src", "switch") not in pruned_pairs

    def test_execute_live_scenario_uses_live_data(self, scenario_graph, _widen_sandbox_root):
        """Executing in 'live' scenario should use live data (1 row)."""
        results = execute_graph(scenario_graph, source="live")
        assert results["out"].status == "ok"
        assert results["out"].row_count == 1
        assert results["out"].preview[0]["source"] == "live"

    def test_execute_batch_scenario_uses_batch_data(self, scenario_graph, _widen_sandbox_root):
        """Executing in 'test_batch' scenario should use batch data (3 rows)."""
        results = execute_graph(scenario_graph, source="test_batch")
        assert results["out"].status == "ok"
        assert results["out"].row_count == 3
        assert all(row["source"] == "batch" for row in results["out"].preview)

    def test_trace_respects_scenario(self, scenario_graph, _widen_sandbox_root):
        """Trace should only include nodes from the active scenario path."""
        # Live trace: should include live_src but not batch_src
        trace_live = execute_trace(
            scenario_graph,
            row_index=0,
            target_node_id="out",
            source="live",
        )
        live_step_ids = {s.node_id for s in trace_live.steps}
        assert "live_src" in live_step_ids
        assert "batch_src" not in live_step_ids

        # Batch trace: should include batch_src but not live_src
        trace_batch = execute_trace(
            scenario_graph,
            row_index=0,
            target_node_id="out",
            source="test_batch",
        )
        batch_step_ids = {s.node_id for s in trace_batch.steps}
        assert "batch_src" in batch_step_ids
        assert "live_src" not in batch_step_ids
