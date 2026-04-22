"""Tests for haute.executor - graph execution engine."""

from __future__ import annotations

import json

import polars as pl
import pytest

from haute._user_exec import _exec_user_code
from haute.executor import (
    PreambleError,
    _build_node_fn,
    _compile_preamble,
    _resolve_batch_scenario,
    execute_graph,
    execute_sink,
)
from tests.conftest import (
    make_edge as _edge,
)
from tests.conftest import (
    make_graph as _g,
)
from tests.conftest import (
    make_node as _n,
)
from tests.conftest import (
    make_output_node as _output_node,
)
from tests.conftest import (
    make_source_node as _source_node,
)
from tests.conftest import (
    make_transform_node as _transform_node,
)

# ---------------------------------------------------------------------------
# _exec_user_code
# ---------------------------------------------------------------------------


class TestExecUserCode:
    def test_explicit_assignment(self):
        lf = pl.DataFrame({"x": [1, 2, 3]}).lazy()
        result = _exec_user_code("df = df.with_columns(y=pl.col('x') * 2)", ["df"], (lf,))
        df = result.collect()
        assert "y" in df.columns
        assert df["y"].to_list() == [2, 4, 6]

    def test_syntax_error_reported(self):
        lf = pl.DataFrame({"x": [1]}).lazy()
        with pytest.raises(Exception):
            _exec_user_code("df = invalid syntax here !!!", ["df"], (lf,))

    def test_eager_result_converted_to_lazy(self):
        lf = pl.DataFrame({"x": [1]}).lazy()
        result = _exec_user_code("df = df.collect()", ["df"], (lf,))
        assert isinstance(result, pl.LazyFrame)

    def test_multiple_named_sources(self):
        """Multiple source names are injected as named variables."""
        lf_a = pl.DataFrame({"x": [1]}).lazy()
        lf_b = pl.DataFrame({"y": [2]}).lazy()
        code = "df = a.join(b, how='cross')"
        result = _exec_user_code(code, ["a", "b"], (lf_a, lf_b))
        df = result.collect()
        assert set(df.columns) == {"x", "y"}

    def test_extra_ns_injects_object(self):
        """extra_ns should inject additional variables (e.g. external model obj)."""
        lf = pl.DataFrame({"x": [10]}).lazy()
        code = "df = df.with_columns(y=pl.lit(val))"
        result = _exec_user_code(code, ["df"], (lf,), extra_ns={"val": 42})
        df = result.collect()
        assert df["y"].to_list() == [42]

    def test_runtime_error_preserves_message(self):
        """Runtime errors in user code should propagate with useful messages."""
        lf = pl.DataFrame({"x": [1]}).lazy()
        with pytest.raises(Exception) as exc_info:
            _exec_user_code("df = 1 / 0", ["df"], (lf,))
        assert "division" in str(exc_info.value).lower()

    def test_multiline_code(self):
        """Multi-line user code with intermediate variables works."""
        lf = pl.DataFrame({"x": [1, 2]}).lazy()
        code = "tmp = df.with_columns(y=pl.lit(7))\ndf = tmp.with_columns(z=pl.lit(8))"
        result = _exec_user_code(code, ["df"], (lf,))
        df = result.collect()
        assert "y" in df.columns
        assert "z" in df.columns

    def test_df_passthrough_when_no_assignment(self):
        """When user code doesn't assign to df, the original df is returned."""
        lf = pl.DataFrame({"x": [1]}).lazy()
        result = _exec_user_code("_ = 1 + 1", ["df"], (lf,))
        df = result.collect()
        assert df["x"].to_list() == [1]

    def test_empty_dataframe_passthrough(self):
        """Empty DataFrame (0 rows) passes through correctly."""
        lf = pl.DataFrame({"x": pl.Series([], dtype=pl.Int64)}).lazy()
        result = _exec_user_code("df = df.with_columns(y=pl.col('x') * 2)", ["df"], (lf,))
        df = result.collect()
        assert len(df) == 0
        assert set(df.columns) == {"x", "y"}


# ---------------------------------------------------------------------------
# _compile_preamble
# ---------------------------------------------------------------------------


class TestCompilePreamble:
    def test_empty_preamble_returns_empty_dict(self):
        assert _compile_preamble("") == {}
        assert _compile_preamble("   ") == {}

    def test_extracts_functions(self):
        ns = _compile_preamble("def double(x):\n    return x * 2\n")
        assert "double" in ns
        assert ns["double"](5) == 10

    def test_extracts_constants(self):
        ns = _compile_preamble("MY_MAP = {'a': 1}\nMY_LIST = [1, 2]\n")
        assert ns["MY_MAP"] == {"a": 1}
        assert ns["MY_LIST"] == [1, 2]

    def test_functions_can_call_each_other(self):
        code = "def helper(x):\n    return x + 1\n\ndef main(x):\n    return helper(x) * 2\n"
        ns = _compile_preamble(code)
        assert ns["main"](3) == 8  # (3 + 1) * 2

    def test_functions_can_use_polars(self):
        ns = _compile_preamble("def make_lit():\n    return pl.lit(42)\n")
        expr = ns["make_lit"]()
        assert isinstance(expr, pl.Expr)

    def test_filters_indirect_dangerous_os_path_helpers(self):
        ns = _compile_preamble("from os.path import join\nSAFE = 1\n")
        assert "SAFE" in ns
        assert "join" not in ns

    def test_filters_rebound_dangerous_modules(self):
        ns = _compile_preamble("import os\nmy_path = os.path\nSAFE = 1\n")
        assert "SAFE" in ns
        assert "os" not in ns
        assert "my_path" not in ns

    def test_utility_modules_evicted_between_calls(self, tmp_path, monkeypatch):
        """Ensure utility modules are re-imported fresh each call, not cached."""
        monkeypatch.chdir(tmp_path)

        util_dir = tmp_path / "utility"
        util_dir.mkdir()
        (util_dir / "__init__.py").write_text("")
        (util_dir / "helpers.py").write_text("VALUE = 1\n")

        ns1 = _compile_preamble("from utility.helpers import *\n")
        assert ns1["VALUE"] == 1

        # Simulate a GUI edit that changes the file on disk
        (util_dir / "helpers.py").write_text("VALUE = 42\n")

        ns2 = _compile_preamble("from utility.helpers import *\n")
        assert ns2["VALUE"] == 42, "utility module was served from stale cache"

    def test_raises_preamble_error_on_name_error(self, tmp_path, monkeypatch):
        """Broken utility code should raise PreambleError, not crash the server."""
        monkeypatch.chdir(tmp_path)

        util_dir = tmp_path / "utility"
        util_dir.mkdir()
        (util_dir / "__init__.py").write_text("")
        (util_dir / "helpers.py").write_text("x = 1\nf\n")

        with pytest.raises(PreambleError, match="name 'f' is not defined"):
            _compile_preamble("from utility.helpers import *\n")

    def test_raises_preamble_error_on_syntax_error(self, tmp_path, monkeypatch):
        """Utility file with syntax error should raise PreambleError."""
        monkeypatch.chdir(tmp_path)

        util_dir = tmp_path / "utility"
        util_dir.mkdir()
        (util_dir / "__init__.py").write_text("")
        (util_dir / "helpers.py").write_text("def foo(\n")

        with pytest.raises(PreambleError, match="Error in utility"):
            _compile_preamble("from utility.helpers import *\n")

    def test_preamble_error_includes_utility_file_path(self, tmp_path, monkeypatch):
        """Error message should reference the utility file, not just '<string>'."""
        monkeypatch.chdir(tmp_path)

        util_dir = tmp_path / "utility"
        util_dir.mkdir()
        (util_dir / "__init__.py").write_text("")
        (util_dir / "helpers.py").write_text("x = 1\nundefined_var\n")

        with pytest.raises(PreambleError) as exc_info:
            _compile_preamble("from utility.helpers import *\n")
        assert "utility" in str(exc_info.value)
        assert "helpers" in str(exc_info.value)


# ---------------------------------------------------------------------------
# _build_node_fn with preamble_ns
# ---------------------------------------------------------------------------


class TestBuildNodeFnWithPreamble:
    def test_transform_can_call_preamble_function(self):
        preamble_ns = _compile_preamble("def add_ten(col):\n    return pl.col(col) + 10\n")
        node = _transform_node("t", code="df = df.with_columns(y=add_ten('x'))")
        _, fn, _ = _build_node_fn(node, source_names=["df"], preamble_ns=preamble_ns)
        lf = pl.DataFrame({"x": [1, 2]}).lazy()
        df = fn(lf).collect()
        assert df["y"].to_list() == [11, 12]

    def test_transform_can_use_preamble_constant(self):
        preamble_ns = _compile_preamble("FACTOR = 3\n")
        node = _transform_node("t", code="df = df.with_columns(y=pl.col('x') * FACTOR)")
        _, fn, _ = _build_node_fn(node, source_names=["df"], preamble_ns=preamble_ns)
        lf = pl.DataFrame({"x": [5]}).lazy()
        df = fn(lf).collect()
        assert df["y"].to_list() == [15]


# ---------------------------------------------------------------------------
# _build_node_fn
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("_widen_sandbox_root")
class TestBuildNodeFn:
    @pytest.mark.parametrize(
        "ext, col, values, write_method",
        [
            pytest.param("parquet", "a", [1, 2], "write_parquet", id="parquet"),
            pytest.param("csv", "b", [3, 4], "write_csv", id="csv"),
            pytest.param("json", "c", [5, 6], "write_json", id="json"),
        ],
    )
    def test_data_source_file_formats(self, tmp_path, ext, col, values, write_method):
        p = tmp_path / f"data.{ext}"
        getattr(pl.DataFrame({col: values}), write_method)(p)

        node = _source_node("src", str(p))
        name, fn, is_source = _build_node_fn(node)
        assert is_source is True
        result = fn()
        if isinstance(result, pl.LazyFrame):
            df = result.collect()
        else:
            df = result
        assert df[col].to_list() == values

    def test_data_source_databricks_is_source(self):
        node = _n(
            {
                "id": "db",
                "data": {
                    "label": "db",
                    "nodeType": "dataSource",
                    "config": {"sourceType": "databricks", "table": "cat.sch.tbl"},
                },
            }
        )
        _, fn, is_source = _build_node_fn(node)
        assert is_source is True
        # Without a cached parquet file, calling fn() raises CacheNotFoundError
        from haute._databricks_io import CacheNotFoundError

        with pytest.raises(CacheNotFoundError, match="not.*fetched"):
            fn()

    def test_data_source_with_code(self, tmp_path):
        """DataSource with user code applies code after loading data."""
        p = tmp_path / "data.parquet"
        pl.DataFrame({"x": [1, 2, 3], "y": [10, 20, 30]}).write_parquet(p)
        node = _n(
            {
                "id": "src",
                "data": {
                    "label": "src",
                    "nodeType": "dataSource",
                    "config": {"path": str(p), "code": "df = df.filter(pl.col('x') > 1)"},
                },
            }
        )
        _, fn, is_source = _build_node_fn(node)
        assert is_source is True  # still a source node
        df = fn().collect()
        assert df["x"].to_list() == [2, 3]

    def test_data_source_with_code_select(self, tmp_path):
        """DataSource code supports explicit df assignment syntax."""
        p = tmp_path / "data.parquet"
        pl.DataFrame({"a": [1, 2, 3]}).write_parquet(p)
        node = _n(
            {
                "id": "src",
                "data": {
                    "label": "src",
                    "nodeType": "dataSource",
                    "config": {"path": str(p), "code": "df = df.select('a')"},
                },
            }
        )
        _, fn, _ = _build_node_fn(node)
        df = fn().collect()
        assert df.columns == ["a"]
        assert len(df) == 3

    def test_data_source_with_empty_code_no_change(self, tmp_path):
        """DataSource with empty code behaves like no code."""
        p = tmp_path / "data.parquet"
        pl.DataFrame({"x": [1]}).write_parquet(p)
        node = _n(
            {
                "id": "src",
                "data": {
                    "label": "src",
                    "nodeType": "dataSource",
                    "config": {"path": str(p), "code": ""},
                },
            }
        )
        _, fn, is_source = _build_node_fn(node)
        assert is_source is True
        df = fn().collect()
        assert df["x"].to_list() == [1]

    def test_transform_with_code(self):
        node = _transform_node("t", code="df = df.with_columns(y=pl.col('x') + 1)")
        _, fn, is_source = _build_node_fn(node, source_names=["df"])
        assert is_source is False
        lf = pl.DataFrame({"x": [10]}).lazy()
        df = fn(lf).collect()
        assert df["y"].to_list() == [11]

    def test_transform_passthrough_without_code(self):
        node = _transform_node("t", code="")
        _, fn, is_source = _build_node_fn(node)
        lf = pl.DataFrame({"x": [5]}).lazy()
        df = fn(lf).collect()
        assert df["x"].to_list() == [5]

    def test_output_selects_fields(self):
        node = _output_node("out", fields=["a"])
        _, fn, _ = _build_node_fn(node)
        lf = pl.DataFrame({"a": [1], "b": [2]}).lazy()
        df = fn(lf).collect()
        assert df.columns == ["a"]

    def test_output_passthrough_without_fields(self):
        node = _output_node("out", fields=[])
        _, fn, _ = _build_node_fn(node)
        lf = pl.DataFrame({"a": [1], "b": [2]}).lazy()
        df = fn(lf).collect()
        assert set(df.columns) == {"a", "b"}

    def test_sink_passthrough(self):
        node = _n(
            {
                "id": "sink",
                "data": {"label": "sink", "nodeType": "dataSink", "config": {}},
            }
        )
        _, fn, is_source = _build_node_fn(node)
        assert is_source is False
        lf = pl.DataFrame({"x": [1]}).lazy()
        df = fn(lf).collect()
        assert df["x"].to_list() == [1]

    def test_external_file_with_json(self, tmp_path):
        """externalFile with JSON file type loads and injects obj."""
        import json as _json

        p = tmp_path / "data.json"
        p.write_text(_json.dumps({"multiplier": 10}))

        node = _n(
            {
                "id": "ext",
                "data": {
                    "label": "ext",
                    "nodeType": "externalFile",
                    "config": {
                        "path": str(p),
                        "fileType": "json",
                        "code": "df = df.with_columns(y=pl.lit(obj['multiplier']))",
                    },
                },
            }
        )
        _, fn, is_source = _build_node_fn(node, source_names=["df"])
        assert is_source is False
        lf = pl.DataFrame({"x": [1]}).lazy()
        df = fn(lf).collect()
        assert df["y"].to_list() == [10]

    def test_external_file_with_pickle(self, tmp_path):
        """externalFile with pickle file type loads and injects obj."""
        import pickle

        p = tmp_path / "data.pkl"
        with open(p, "wb") as f:
            pickle.dump({"factor": 5}, f)

        node = _n(
            {
                "id": "ext",
                "data": {
                    "label": "ext",
                    "nodeType": "externalFile",
                    "config": {
                        "path": str(p),
                        "fileType": "pickle",
                        "code": "df = df.with_columns(y=pl.lit(obj['factor']))",
                    },
                },
            }
        )
        _, fn, _ = _build_node_fn(node, source_names=["df"])
        lf = pl.DataFrame({"x": [1]}).lazy()
        df = fn(lf).collect()
        assert df["y"].to_list() == [5]

    def test_external_file_passthrough_without_code(self):
        """externalFile without code acts as passthrough."""
        node = _n(
            {
                "id": "ext",
                "data": {
                    "label": "ext",
                    "nodeType": "externalFile",
                    "config": {"path": "model.pkl", "fileType": "pickle", "code": ""},
                },
            }
        )
        _, fn, is_source = _build_node_fn(node)
        assert is_source is False
        lf = pl.DataFrame({"x": [7]}).lazy()
        df = fn(lf).collect()
        assert df["x"].to_list() == [7]

    def test_unknown_node_type_rejected(self):
        """Unknown nodeType should be rejected by NodeType enum validation."""
        import pytest
        from pydantic import ValidationError

        with pytest.raises(ValidationError, match="Input should be"):
            _n(
                {
                    "id": "unk",
                    "data": {"label": "unk", "nodeType": "unknownFutureType", "config": {}},
                }
            )

    def test_unhandled_node_type_passthrough(self):
        """Node type without dedicated handler falls through to passthrough."""
        node = _n(
            {
                "id": "port1",
                "data": {"label": "port1", "nodeType": "submodelPort", "config": {}},
            }
        )
        _, fn, is_source = _build_node_fn(node)
        assert is_source is False
        lf = pl.DataFrame({"x": [3]}).lazy()
        df = fn(lf).collect()
        assert df["x"].to_list() == [3]


# ---------------------------------------------------------------------------
# _prune_live_switch_edges
# ---------------------------------------------------------------------------


class TestPruneLiveSwitchEdges:
    """Verify that live_switch edge pruning excludes the inactive branch."""

    def _make_live_switch_graph(self):
        """Build a graph with a live_switch: api → feat → switch ← batch."""
        from haute._types import GraphEdge, GraphNode, NodeData, PipelineGraph

        return PipelineGraph(
            nodes=[
                GraphNode(
                    id="api",
                    data=NodeData(
                        label="quotes",
                        nodeType="apiInput",
                        config={"path": "data/q.jsonl"},
                    ),
                ),
                GraphNode(
                    id="feat",
                    data=NodeData(
                        label="feature_processing",
                        nodeType="polars",
                        config={"code": ""},
                    ),
                ),
                GraphNode(
                    id="batch",
                    data=NodeData(
                        label="batch_quotes",
                        nodeType="dataSource",
                        config={"path": "data/batch.parquet"},
                    ),
                ),
                GraphNode(
                    id="sw",
                    data=NodeData(
                        label="policies",
                        nodeType="liveSwitch",
                        config={
                            "input_scenario_map": {
                                "feature_processing": "live",
                                "batch_quotes": "nb_batch",
                            }
                        },
                    ),
                ),
                GraphNode(
                    id="down",
                    data=NodeData(
                        label="downstream",
                        nodeType="polars",
                        config={"code": ""},
                    ),
                ),
            ],
            edges=[
                GraphEdge(id="e1", source="api", target="feat"),
                GraphEdge(id="e2", source="feat", target="sw"),
                GraphEdge(id="e3", source="batch", target="sw"),
                GraphEdge(id="e4", source="sw", target="down"),
            ],
        )

    def test_live_scenario_prunes_batch_branch(self):
        from haute._execute_lazy import _prune_live_switch_edges

        g = self._make_live_switch_graph()
        pruned = _prune_live_switch_edges(g.edges, g.node_map, "live")
        edge_pairs = {(e.source, e.target) for e in pruned}
        # batch→sw edge should be removed
        assert ("batch", "sw") not in edge_pairs
        # feat→sw edge should remain
        assert ("feat", "sw") in edge_pairs
        assert ("api", "feat") in edge_pairs

    def test_nb_batch_scenario_prunes_live_branch(self):
        from haute._execute_lazy import _prune_live_switch_edges

        g = self._make_live_switch_graph()
        pruned = _prune_live_switch_edges(
            g.edges,
            g.node_map,
            "nb_batch",
        )
        edge_pairs = {(e.source, e.target) for e in pruned}
        # feat→sw edge should be removed
        assert ("feat", "sw") not in edge_pairs
        # batch→sw edge should remain
        assert ("batch", "sw") in edge_pairs

    def test_no_live_switch_returns_all_edges(self):
        """Graph without live_switch nodes should return edges unchanged."""
        from haute._execute_lazy import _prune_live_switch_edges

        g = _g(
            {
                "nodes": [
                    _source_node("src", "data.parquet"),
                    _transform_node("t", ""),
                ],
                "edges": [_edge("src", "t")],
            }
        )
        pruned = _prune_live_switch_edges(g.edges, g.node_map, "live")
        assert len(pruned) == len(g.edges)

    def test_prepare_graph_excludes_pruned_ancestors(self):
        """_prepare_graph with scenario should exclude the inactive branch
        from the topo order entirely."""
        from haute._execute_lazy import _prepare_graph

        g = self._make_live_switch_graph()
        _, order, parents_of, id_to_name = _prepare_graph(
            g,
            target_node_id="down",
            source="live",
        )
        # batch should not be in the execution order
        assert "batch" not in order
        assert "api" in order
        assert "feat" in order
        assert "sw" in order

    def test_prepare_graph_nb_batch_excludes_live_branch(self):
        from haute._execute_lazy import _prepare_graph

        g = self._make_live_switch_graph()
        _, order, parents_of, id_to_name = _prepare_graph(
            g,
            target_node_id="down",
            source="nb_batch",
        )
        # api and feat should not be in the execution order
        assert "api" not in order
        assert "feat" not in order
        assert "batch" in order
        assert "sw" in order


# ---------------------------------------------------------------------------
# execute_graph
# ---------------------------------------------------------------------------


class TestExecuteGraph:
    def test_simple_pipeline(self, tmp_path):
        p = tmp_path / "input.parquet"
        pl.DataFrame({"x": [1, 2, 3]}).write_parquet(p)

        graph = _g(
            {
                "nodes": [
                    _source_node("src", str(p)),
                    _transform_node("t", "df = df.with_columns(y=pl.col('x') * 2)"),
                ],
                "edges": [_edge("src", "t")],
            }
        )
        results = execute_graph(graph)
        assert results["src"].status == "ok"
        assert results["t"].status == "ok"
        assert results["t"].row_count == 3
        assert any(c.name == "y" for c in results["t"].columns)

    def test_timing_ms_present(self, tmp_path):
        p = tmp_path / "t.parquet"
        pl.DataFrame({"x": [1, 2]}).write_parquet(p)

        graph = _g(
            {
                "nodes": [
                    _source_node("src", str(p)),
                    _transform_node("t", "df = df.with_columns(y=pl.col('x') + 1)"),
                ],
                "edges": [_edge("src", "t")],
            }
        )
        results = execute_graph(graph)
        for nid in ("src", "t"):
            assert isinstance(results[nid].timing_ms, float)
            assert results[nid].timing_ms >= 0

    def test_memory_bytes_present(self, tmp_path):
        p = tmp_path / "mem.parquet"
        pl.DataFrame({"x": [1, 2, 3], "y": [4, 5, 6]}).write_parquet(p)

        graph = _g(
            {
                "nodes": [
                    _source_node("src", str(p)),
                    _transform_node("t", "df = df.with_columns(z=pl.col('x') * 2)"),
                ],
                "edges": [_edge("src", "t")],
            }
        )
        results = execute_graph(graph)
        for nid in ("src", "t"):
            assert isinstance(results[nid].memory_bytes, int)
            assert results[nid].memory_bytes > 0

    def test_memory_bytes_zero_on_error(self, tmp_path):
        p = tmp_path / "ok.parquet"
        pl.DataFrame({"x": [1]}).write_parquet(p)

        graph = _g(
            {
                "nodes": [
                    _source_node("src", str(p)),
                    _transform_node("bad", "df = df.select('nonexistent_column')"),
                ],
                "edges": [_edge("src", "bad")],
            }
        )
        results = execute_graph(graph)
        assert results["bad"].status == "error"
        assert results["bad"].memory_bytes == 0

    def test_empty_graph(self):
        assert execute_graph(_g({"nodes": [], "edges": []})) == {}

    def test_row_limit(self, tmp_path):
        p = tmp_path / "big.parquet"
        pl.DataFrame({"x": list(range(100))}).write_parquet(p)

        graph = _g({"nodes": [_source_node("s", str(p))], "edges": []})
        results = execute_graph(graph, row_limit=5)
        assert results["s"].row_count == 5

    def test_target_node_id(self, tmp_path):
        p = tmp_path / "d.parquet"
        pl.DataFrame({"x": [1]}).write_parquet(p)

        graph = _g(
            {
                "nodes": [
                    _source_node("a", str(p)),
                    _transform_node("b"),
                    _transform_node("c"),
                ],
                "edges": [_edge("a", "b"), _edge("b", "c")],
            }
        )
        results = execute_graph(graph, target_node_id="b")
        assert "b" in results
        assert "c" not in results

    def test_cached_broad_preview_returns_only_narrow_target_results(self, tmp_path):
        p = tmp_path / "d.parquet"
        pl.DataFrame({"x": [1]}).write_parquet(p)

        graph = _g(
            {
                "nodes": [
                    _source_node("a", str(p)),
                    _transform_node("b", "df = df.with_columns(y=pl.col('x') + 1)"),
                    _transform_node("c", "df = df.with_columns(z=pl.col('y') + 1)"),
                ],
                "edges": [_edge("a", "b"), _edge("b", "c")],
            }
        )

        broad_results = execute_graph(graph, target_node_id="c")
        assert "c" in broad_results

        narrow_results = execute_graph(
            graph,
            target_node_id="b",
            target_preview_only=True,
        )
        assert "b" in narrow_results
        assert "c" not in narrow_results
        assert narrow_results["a"].preview == []
        assert narrow_results["b"].preview == [{"x": 1, "y": 2}]

    def test_error_node_captured(self, tmp_path):
        p = tmp_path / "d.parquet"
        pl.DataFrame({"x": [1]}).write_parquet(p)

        graph = _g(
            {
                "nodes": [
                    _source_node("src", str(p)),
                    # Select a column that doesn't exist - triggers ColumnNotFoundError at collect
                    _transform_node("bad", code="df = df.select('nonexistent_col')"),
                ],
                "edges": [_edge("src", "bad")],
            }
        )
        results = execute_graph(graph)
        assert results["bad"].status == "error"
        assert (
            "nonexistent_col" in results["bad"].error.lower()
            or "not found" in results["bad"].error.lower()
        ), f"Expected column-not-found error, got: {results['bad'].error}"
        assert results["bad"].row_count == 0
        assert results["bad"].columns == []

    def test_cascading_failure_propagates(self, tmp_path):
        """When a mid-pipeline node fails, downstream nodes also fail."""
        p = tmp_path / "d.parquet"
        pl.DataFrame({"x": [1, 2, 3]}).write_parquet(p)

        graph = _g(
            {
                "nodes": [
                    _source_node("src", str(p)),
                    _transform_node("mid", code="df = df.select('nonexistent_col')"),
                    _transform_node("leaf", code="df = df.with_columns(y=pl.col('x') * 2)"),
                ],
                "edges": [_edge("src", "mid"), _edge("mid", "leaf")],
            }
        )
        results = execute_graph(graph)
        # Mid node should fail
        assert results["mid"].status == "error"
        # Leaf node must also fail (it received None input from the failed mid node)
        assert results["leaf"].status == "error"
        assert results["leaf"].row_count == 0
        # Source should still succeed
        assert results["src"].status == "ok"

    def test_row_limit_zero_is_no_limit(self, tmp_path):
        """row_limit=0 is falsy, so it behaves the same as None (no limit)."""
        p = tmp_path / "data.parquet"
        pl.DataFrame({"x": list(range(10))}).write_parquet(p)

        graph = _g(
            {
                "nodes": [_source_node("src", str(p))],
                "edges": [],
            }
        )
        results = execute_graph(graph, row_limit=0)
        assert results["src"].status == "ok"
        assert results["src"].row_count == 10

    def test_row_limit_one(self, tmp_path):
        """row_limit=1 should produce exactly 1 row through the pipeline."""
        p = tmp_path / "data.parquet"
        pl.DataFrame({"x": list(range(10))}).write_parquet(p)

        graph = _g(
            {
                "nodes": [
                    _source_node("src", str(p)),
                    _transform_node("t", code="df = df.with_columns(y=pl.col('x') * 2)"),
                ],
                "edges": [_edge("src", "t")],
            }
        )
        results = execute_graph(graph, row_limit=1)
        assert results["src"].row_count == 1
        assert results["t"].row_count == 1
        col_names = [c.name for c in results["t"].columns]
        assert "x" in col_names
        assert "y" in col_names

    def test_empty_source_dataframe(self, tmp_path):
        """A source file with 0 rows should still have schema metadata."""
        p = tmp_path / "empty.parquet"
        pl.DataFrame(
            {"a": pl.Series([], dtype=pl.Int64), "b": pl.Series([], dtype=pl.Utf8)}
        ).write_parquet(p)

        graph = _g({"nodes": [_source_node("src", str(p))], "edges": []})
        results = execute_graph(graph)
        assert results["src"].status == "ok"
        assert results["src"].row_count == 0
        col_names = [c.name for c in results["src"].columns]
        assert "a" in col_names
        assert "b" in col_names


# ---------------------------------------------------------------------------
# Data source user code preservation
# ---------------------------------------------------------------------------


class TestDataSourceUserCode:
    """Verify user code on dataSource nodes survives the full lifecycle.

    The .py file is the source of truth.  When the parser extracts user
    code (e.g. ``df = df.limit(100)``), that code must:
    - Appear in the parsed graph's node config
    - Be executed by both the eager (preview) and lazy (sink) paths
    - NOT be written to the JSON config sidecar (code lives in .py only)
    - Survive a parse → codegen → re-parse round-trip
    """

    def test_data_source_code_applied_in_preview(self, tmp_path):
        """User code on a dataSource node is executed during preview."""
        p = tmp_path / "big.parquet"
        pl.DataFrame({"x": range(1000)}).write_parquet(p)

        graph = _g(
            {
                "nodes": [
                    _n(
                        {
                            "id": "src",
                            "data": {
                                "label": "src",
                                "nodeType": "dataSource",
                                "config": {
                                    "path": str(p),
                                    "code": "df = df.limit(10)",
                                },
                            },
                        }
                    ),
                ],
                "edges": [],
            }
        )
        results = execute_graph(graph)
        assert results["src"].status == "ok"
        assert results["src"].row_count == 10

    def test_data_source_code_applied_in_sink(self, tmp_path):
        """User code on a dataSource node is executed during sink."""
        src = tmp_path / "big.parquet"
        out = tmp_path / "out.parquet"
        pl.DataFrame({"x": range(1000)}).write_parquet(src)

        graph = _g(
            {
                "nodes": [
                    _n(
                        {
                            "id": "src",
                            "data": {
                                "label": "src",
                                "nodeType": "dataSource",
                                "config": {
                                    "path": str(src),
                                    "code": "df = df.limit(10)",
                                },
                            },
                        }
                    ),
                    _n(
                        {
                            "id": "sink",
                            "data": {
                                "label": "sink",
                                "nodeType": "dataSink",
                                "config": {"path": str(out), "format": "parquet"},
                            },
                        }
                    ),
                ],
                "edges": [_edge("src", "sink")],
            }
        )
        resp = execute_sink(graph, "sink")
        assert resp.status == "ok"
        assert resp.row_count == 10

    def test_data_source_without_code_returns_all_rows(self, tmp_path):
        """Without user code the full dataset is returned."""
        p = tmp_path / "full.parquet"
        pl.DataFrame({"x": range(500)}).write_parquet(p)

        graph = _g(
            {
                "nodes": [
                    _n(
                        {
                            "id": "src",
                            "data": {
                                "label": "src",
                                "nodeType": "dataSource",
                                "config": {"path": str(p)},
                            },
                        }
                    ),
                ],
                "edges": [],
            }
        )
        results = execute_graph(graph)
        assert results["src"].row_count == 500

    def test_config_json_excludes_code(self):
        """The JSON config sidecar must not contain 'code' — it lives in .py."""
        from haute._config_io import collect_node_configs

        graph = _g(
            {
                "nodes": [
                    _n(
                        {
                            "id": "src",
                            "data": {
                                "label": "my_source",
                                "nodeType": "dataSource",
                                "config": {
                                    "path": "data.parquet",
                                    "code": "df = df.limit(100)",
                                },
                            },
                        }
                    ),
                ],
                "edges": [],
            }
        )
        configs = collect_node_configs(graph)
        for _path, content in configs.items():
            parsed = json.loads(content)
            assert "code" not in parsed, (
                "Config JSON should not contain 'code' — it lives in the .py file"
            )

    def test_parser_extracts_data_source_code_no_sentinel(self, tmp_path):
        """Parser extracts user code from a dataSource body.

        The parser identifies user code as everything after the
        auto-generated ``df = pl.scan_parquet(...)`` boilerplate line.
        """
        py_file = tmp_path / "pipeline.py"
        parquet_path = tmp_path / "data.parquet"
        pl.DataFrame({"x": [1, 2, 3]}).write_parquet(parquet_path)

        py_file.write_text(
            f"import polars as pl\n"
            f"import haute\n"
            f'pipeline = haute.Pipeline("test")\n\n'
            f'@pipeline.data_source(config="config/data_source/my_src.json")\n'
            f"def my_src() -> pl.LazyFrame:\n"
            f'    """my_src node"""\n'
            f'    df = pl.scan_parquet("{parquet_path.as_posix()}")\n'
            f"    df = df.limit(2)\n"
            f"    return df\n"
        )
        cfg_dir = tmp_path / "config" / "data_source"
        cfg_dir.mkdir(parents=True)
        (cfg_dir / "my_src.json").write_text(json.dumps({"path": str(parquet_path)}))

        from haute.parser import parse_pipeline_file

        graph = parse_pipeline_file(py_file)
        assert len(graph.nodes) == 1
        node = graph.nodes[0]
        assert node.data.nodeType == "dataSource"
        code = node.data.config.get("code", "")
        assert "limit(2)" in code, (
            f"Parser should extract .limit(2) from the function body, got: {code!r}"
        )

    def test_parsed_data_source_code_executes_correctly(self, tmp_path):
        """Full round-trip: parse .py → execute_graph → user code applied."""
        py_file = tmp_path / "pipeline.py"
        parquet_path = tmp_path / "data.parquet"
        pl.DataFrame({"x": range(100)}).write_parquet(parquet_path)

        py_file.write_text(
            f"import polars as pl\n"
            f"import haute\n"
            f'pipeline = haute.Pipeline("test")\n\n'
            f'@pipeline.data_source(config="config/data_source/src.json")\n'
            f"def src() -> pl.LazyFrame:\n"
            f'    """src node"""\n'
            f'    df = pl.scan_parquet("{parquet_path.as_posix()}")\n'
            f"    df = df.limit(5)\n"
            f"    return df\n"
        )
        cfg_dir = tmp_path / "config" / "data_source"
        cfg_dir.mkdir(parents=True)
        (cfg_dir / "src.json").write_text(json.dumps({"path": str(parquet_path)}))

        from haute.parser import parse_pipeline_file

        graph = parse_pipeline_file(py_file)
        results = execute_graph(graph)
        assert results["src"].status == "ok"
        assert results["src"].row_count == 5, (
            f"Expected 5 rows (limit applied), got {results['src'].row_count}"
        )


# ---------------------------------------------------------------------------
# execute_sink
# ---------------------------------------------------------------------------


def _make_sink_graph(tmp_path, *, src_data=None):
    """Build a minimal src→sink graph and return (graph, out_path)."""
    src_path = tmp_path / "in.parquet"
    out_path = tmp_path / "out.parquet"
    pl.DataFrame(src_data or {"x": [1]}).write_parquet(src_path)
    graph = _g(
        {
            "nodes": [
                _source_node("src", str(src_path)),
                _n(
                    {
                        "id": "sink",
                        "data": {
                            "label": "sink",
                            "nodeType": "dataSink",
                            "config": {"path": str(out_path), "format": "parquet"},
                        },
                    }
                ),
            ],
            "edges": [_edge("src", "sink")],
        }
    )
    return graph, out_path


class TestExecuteSink:
    def test_writes_parquet(self, tmp_path):
        src_path = tmp_path / "in.parquet"
        out_path = tmp_path / "out.parquet"
        pl.DataFrame({"x": [1, 2]}).write_parquet(src_path)

        graph = _g(
            {
                "nodes": [
                    _source_node("src", str(src_path)),
                    _n(
                        {
                            "id": "sink",
                            "data": {
                                "label": "sink",
                                "nodeType": "dataSink",
                                "config": {"path": str(out_path), "format": "parquet"},
                            },
                        }
                    ),
                ],
                "edges": [_edge("src", "sink")],
            }
        )
        result = execute_sink(graph, sink_node_id="sink")
        assert result.status == "ok"
        assert result.row_count == 2
        assert out_path.exists()
        df = pl.read_parquet(out_path)
        assert len(df) == 2

    def test_writes_csv(self, tmp_path):
        src_path = tmp_path / "in.parquet"
        out_path = tmp_path / "out.csv"
        pl.DataFrame({"a": [10]}).write_parquet(src_path)

        graph = _g(
            {
                "nodes": [
                    _source_node("src", str(src_path)),
                    _n(
                        {
                            "id": "sink",
                            "data": {
                                "label": "sink",
                                "nodeType": "dataSink",
                                "config": {"path": str(out_path), "format": "csv"},
                            },
                        }
                    ),
                ],
                "edges": [_edge("src", "sink")],
            }
        )
        result = execute_sink(graph, sink_node_id="sink")
        assert result.status == "ok"
        assert out_path.exists()

    def test_missing_sink_raises(self):
        graph = _g({"nodes": [], "edges": []})
        with pytest.raises(ValueError, match="not.*found"):
            execute_sink(graph, sink_node_id="nope")

    def test_live_scenario_coerced_to_batch(self, tmp_path):
        """Sinks are never live — scenario='live' must be coerced to 'batch'
        so model scoring uses the disk-batched path instead of OOM-prone eager."""
        graph, _ = _make_sink_graph(tmp_path, src_data={"x": [1, 2]})

        captured_sources: list[str] = []
        from unittest.mock import patch

        from haute._execute_lazy import _execute_lazy as original_execute_lazy

        def spy(*args, **kwargs):
            captured_sources.append(kwargs.get("source", "???"))
            return original_execute_lazy(*args, **kwargs)

        with patch("haute.executor._execute_lazy", side_effect=spy):
            execute_sink(graph, sink_node_id="sink", source="live")

        assert captured_sources == ["batch"]

    def test_custom_scenario_preserved_for_sink(self, tmp_path):
        """Non-live scenarios are passed through unchanged for source-switch routing."""
        graph, _ = _make_sink_graph(tmp_path)

        captured_sources: list[str] = []
        from unittest.mock import patch

        from haute._execute_lazy import _execute_lazy as original_execute_lazy

        def spy(*args, **kwargs):
            captured_sources.append(kwargs.get("source", "???"))
            return original_execute_lazy(*args, **kwargs)

        with patch("haute.executor._execute_lazy", side_effect=spy):
            execute_sink(graph, sink_node_id="sink", source="my_custom")

        assert captured_sources == ["my_custom"]

    def test_no_path_raises(self, tmp_path):
        src = tmp_path / "in.parquet"
        pl.DataFrame({"x": [1]}).write_parquet(src)
        graph = _g(
            {
                "nodes": [
                    _source_node("src", str(src)),
                    _n(
                        {
                            "id": "sink",
                            "data": {
                                "label": "sink",
                                "nodeType": "dataSink",
                                "config": {"path": "", "format": "parquet"},
                            },
                        }
                    ),
                ],
                "edges": [_edge("src", "sink")],
            }
        )
        with pytest.raises(ValueError, match="no.*output path"):
            execute_sink(graph, sink_node_id="sink")

    def test_sink_with_multi_input_join(self, tmp_path):
        """execute_sink checkpoints multi-input nodes and produces correct output."""
        src1 = tmp_path / "s1.parquet"
        src2 = tmp_path / "s2.parquet"
        out = tmp_path / "out.parquet"
        pl.DataFrame({"key": [1, 2], "a": [10, 20]}).write_parquet(src1)
        pl.DataFrame({"key": [1, 2], "b": [30, 40]}).write_parquet(src2)

        graph = _g(
            {
                "nodes": [
                    _source_node("s1", str(src1)),
                    _source_node("s2", str(src2)),
                    _n(
                        {
                            "id": "join",
                            "data": {
                                "label": "join",
                                "nodeType": "polars",
                                "config": {"code": "df = s1.join(s2, on='key', how='left')"},
                            },
                        }
                    ),
                    _n(
                        {
                            "id": "sink",
                            "data": {
                                "label": "sink",
                                "nodeType": "dataSink",
                                "config": {"path": str(out), "format": "parquet"},
                            },
                        }
                    ),
                ],
                "edges": [
                    _edge("s1", "join"),
                    _edge("s2", "join"),
                    _edge("join", "sink"),
                ],
            }
        )
        result = execute_sink(graph, sink_node_id="sink")
        assert result.status == "ok"
        assert result.row_count == 2
        df = pl.read_parquet(out)
        assert set(df.columns) >= {"key", "a", "b"}

    def test_sink_passes_checkpoint_dir(self, tmp_path):
        """execute_sink must pass a checkpoint_dir to _execute_lazy."""
        graph, _ = _make_sink_graph(tmp_path)

        from pathlib import Path
        from unittest.mock import patch

        from haute._execute_lazy import _execute_lazy as original

        captured_kwargs: list[dict] = []

        def spy(*args, **kwargs):
            captured_kwargs.append(kwargs)
            return original(*args, **kwargs)

        with patch("haute.executor._execute_lazy", side_effect=spy):
            execute_sink(graph, sink_node_id="sink")

        assert len(captured_kwargs) == 1
        cp_dir = captured_kwargs[0].get("checkpoint_dir")
        assert cp_dir is not None
        assert isinstance(cp_dir, Path)

    def test_sink_cleans_up_checkpoint_dir(self, tmp_path):
        """Checkpoint temp directory should be removed after sink completes."""
        graph, _ = _make_sink_graph(tmp_path)

        from pathlib import Path
        from unittest.mock import patch

        from haute._execute_lazy import _execute_lazy as original

        created_dirs: list[Path] = []

        def spy(*args, **kwargs):
            cp_dir = kwargs.get("checkpoint_dir")
            if cp_dir is not None:
                created_dirs.append(cp_dir)
            return original(*args, **kwargs)

        with patch("haute.executor._execute_lazy", side_effect=spy):
            execute_sink(graph, sink_node_id="sink")

        assert len(created_dirs) == 1
        assert not created_dirs[0].exists(), "checkpoint dir should be cleaned up"

    def test_live_scenario_resolves_batch_from_ism(self, tmp_path):
        """When scenario='live', execute_sink resolves the batch scenario
        from the graph's live_switch ISM instead of hardcoding 'batch'."""
        live_src = tmp_path / "live.parquet"
        batch_src = tmp_path / "batch.parquet"
        out_path = tmp_path / "out.parquet"
        pl.DataFrame({"x": [1]}).write_parquet(live_src)
        pl.DataFrame({"x": [2]}).write_parquet(batch_src)

        graph = _g(
            {
                "nodes": [
                    _source_node("live_src", str(live_src)),
                    _source_node("batch_src", str(batch_src)),
                    _n(
                        {
                            "id": "sw",
                            "data": {
                                "label": "sw",
                                "nodeType": "liveSwitch",
                                "config": {
                                    "input_scenario_map": {
                                        "live_src": "live",
                                        "batch_src": "nb_batch",
                                    },
                                },
                            },
                        }
                    ),
                    _n(
                        {
                            "id": "sink",
                            "data": {
                                "label": "sink",
                                "nodeType": "dataSink",
                                "config": {"path": str(out_path), "format": "parquet"},
                            },
                        }
                    ),
                ],
                "edges": [
                    _edge("live_src", "sw"),
                    _edge("batch_src", "sw"),
                    _edge("sw", "sink"),
                ],
            }
        )

        captured_sources: list[str] = []
        from unittest.mock import patch

        from haute._execute_lazy import _execute_lazy as original_execute_lazy

        def spy(*args, **kwargs):
            captured_sources.append(kwargs.get("source", "???"))
            return original_execute_lazy(*args, **kwargs)

        with patch("haute.executor._execute_lazy", side_effect=spy):
            execute_sink(graph, sink_node_id="sink", source="live")

        # Should resolve to "nb_batch" from the ISM, not generic "batch"
        assert captured_sources == ["nb_batch"]


# ---------------------------------------------------------------------------
# Instance node alias injection
# ---------------------------------------------------------------------------


class TestInstanceAliasInjection:
    """Regression tests for instance node input mapping."""

    def test_positional_fallback_maps_unrelated_name(self):
        """Instance input 'instance' must bind as 'claims_aggregate' via
        positional fallback when no name-based match exists."""
        lf = pl.DataFrame({"IDpol": [1, 2], "val": [10, 20]}).lazy()
        result = _exec_user_code(
            "df = claims_aggregate",
            ["instance", "policies"],
            (lf, lf),
            orig_source_names=["claims_aggregate", "policies"],
        )
        assert result.collect().columns == ["IDpol", "val"]

    def test_explicit_mapping_overrides_heuristic(self):
        """Explicit inputMapping takes priority over name matching."""
        lf_a = pl.DataFrame({"x": [1]}).lazy()
        lf_b = pl.DataFrame({"y": [2]}).lazy()
        result = _exec_user_code(
            "df = orig_input",
            ["src_a", "src_b"],
            (lf_a, lf_b),
            orig_source_names=["orig_input"],
            input_mapping={"orig_input": "src_b"},
        )
        assert result.collect().columns == ["y"]

    def test_instance_node_executes_with_different_input_names(self, tmp_path):
        """End-to-end: instance of a transform node runs correctly when its
        upstream inputs have completely different names from the original's."""
        # Write small parquet files as data sources
        src_a = tmp_path / "a.parquet"
        src_b = tmp_path / "b.parquet"
        alt_a = tmp_path / "alt_a.parquet"
        alt_b = tmp_path / "alt_b.parquet"
        pl.DataFrame({"k": [1, 2], "v": [10, 20]}).write_parquet(src_a)
        pl.DataFrame({"k": [1, 2], "w": [30, 40]}).write_parquet(src_b)
        pl.DataFrame({"k": [3], "v": [50]}).write_parquet(alt_a)
        pl.DataFrame({"k": [3], "w": [60]}).write_parquet(alt_b)

        graph = _g(
            {
                "nodes": [
                    _source_node("src_a", str(src_a)),
                    _source_node("src_b", str(src_b)),
                    _transform_node("joiner", "df = src_a.join(src_b, on='k')"),
                    _source_node("alt_a", str(alt_a)),
                    _source_node("alt_b", str(alt_b)),
                    _n(
                        {
                            "id": "joiner_inst",
                            "data": {
                                "label": "joiner_inst",
                                "nodeType": "polars",
                                "config": {"instanceOf": "joiner"},
                            },
                        }
                    ),
                ],
                "edges": [
                    _edge("src_a", "joiner"),
                    _edge("src_b", "joiner"),
                    _edge("alt_a", "joiner_inst"),
                    _edge("alt_b", "joiner_inst"),
                ],
            }
        )
        results = execute_graph(graph, target_node_id="joiner_inst")
        assert results["joiner_inst"].status == "ok"
        assert results["joiner_inst"].row_count == 1
        col_names = {c.name for c in results["joiner_inst"].columns}
        assert col_names == {"k", "v", "w"}


# ---------------------------------------------------------------------------
# liveSwitch node
# ---------------------------------------------------------------------------


class TestLiveSwitch:
    def _switch_graph(self, tmp_path, scenario_map=None, reverse_edges=False):
        """Build a graph with two sources feeding a liveSwitch."""
        if scenario_map is None:
            scenario_map = {"live_src": "live", "batch_src": "test_batch"}
        p1 = tmp_path / "live.parquet"
        p2 = tmp_path / "batch.parquet"
        pl.DataFrame({"x": [1, 2, 3]}).write_parquet(p1)
        pl.DataFrame({"x": [10, 20, 30, 40]}).write_parquet(p2)
        edges = [
            _edge("live_src", "switch"),
            _edge("batch_src", "switch"),
        ]
        if reverse_edges:
            edges = list(reversed(edges))
        return _g(
            {
                "nodes": [
                    _source_node("live_src", str(p1)),
                    _source_node("batch_src", str(p2)),
                    _n(
                        {
                            "id": "switch",
                            "type": "liveSwitch",
                            "data": {
                                "label": "switch",
                                "nodeType": "liveSwitch",
                                "config": {
                                    "input_scenario_map": scenario_map,
                                    "inputs": ["live_src", "batch_src"],
                                },
                            },
                            "position": {"x": 0, "y": 0},
                        }
                    ),
                ],
                "edges": edges,
            }
        )

    def test_live_scenario_selects_mapped_input(self, tmp_path):
        graph = self._switch_graph(tmp_path)
        results = execute_graph(graph, target_node_id="switch", source="live")
        assert results["switch"].status == "ok"
        assert results["switch"].row_count == 3

    def test_live_scenario_works_regardless_of_edge_order(self, tmp_path):
        """Edge order in the graph JSON is arbitrary — live must still pick the correct input."""
        graph = self._switch_graph(tmp_path, reverse_edges=True)
        results = execute_graph(graph, target_node_id="switch", source="live")
        assert results["switch"].status == "ok"
        assert results["switch"].row_count == 3

    def test_batch_scenario_selects_mapped_input(self, tmp_path):
        graph = self._switch_graph(tmp_path)
        results = execute_graph(graph, target_node_id="switch", source="test_batch")
        assert results["switch"].status == "ok"
        assert results["switch"].row_count == 4

    def test_unmapped_scenario_falls_back_to_first_input(self, tmp_path):
        """An unmapped scenario should fall back to the first input."""
        graph = self._switch_graph(tmp_path)
        results = execute_graph(graph, target_node_id="switch", source="unknown_scenario")
        assert results["switch"].status == "ok"
        assert results["switch"].row_count == 3

    def test_empty_scenario_map_falls_back_to_first_input(self, tmp_path):
        """Empty input_scenario_map {} should fall back to the first input."""
        graph = self._switch_graph(tmp_path, scenario_map={})
        results = execute_graph(graph, target_node_id="switch", source="live")
        assert results["switch"].status == "ok"
        # With empty map, should fall back to first input (live_src, 3 rows)
        assert results["switch"].row_count == 3


# ---------------------------------------------------------------------------
# API Input large-file gating
# ---------------------------------------------------------------------------


def _api_input_node(nid: str, path: str) -> _n:
    """Build a minimal apiInput node."""
    return _n(
        {
            "id": nid,
            "data": {
                "label": nid,
                "nodeType": "apiInput",
                "config": {"path": path},
            },
        }
    )


class TestApiInputLargeFileGating:
    def test_large_file_no_cache_raises_error(self, tmp_path, monkeypatch):
        """Large JSONL files without a cache should produce a descriptive error."""
        from haute._json_flatten import _LARGE_FILE_THRESHOLD

        monkeypatch.chdir(tmp_path)

        # Create a "large" JSONL file that exceeds the threshold
        data_file = tmp_path / "large.jsonl"
        # Write enough valid JSONL lines to exceed threshold
        line = json.dumps({"x": "a" * 1000}) + "\n"
        lines_needed = (_LARGE_FILE_THRESHOLD // len(line)) + 1
        data_file.write_text(line * lines_needed)
        assert data_file.stat().st_size >= _LARGE_FILE_THRESHOLD

        node = _api_input_node("api", str(data_file))
        _, fn, is_source = _build_node_fn(node)
        assert is_source is True

        with pytest.raises(RuntimeError, match="not been cached yet"):
            fn()

    def test_large_file_with_cache_succeeds(self, tmp_path, monkeypatch):
        """Large JSONL files with a valid cache should use the cache directly."""
        from haute._json_flatten import _LARGE_FILE_THRESHOLD, _json_cache_path

        monkeypatch.chdir(tmp_path)

        # Create a "large" JSONL file
        data_file = tmp_path / "large.jsonl"
        line = json.dumps({"x": 1}) + "\n"
        lines_needed = (_LARGE_FILE_THRESHOLD // len(line)) + 1
        data_file.write_text(line * lines_needed)
        assert data_file.stat().st_size >= _LARGE_FILE_THRESHOLD

        # Pre-build the cache manually
        cache_path = _json_cache_path(str(data_file))
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        pl.DataFrame({"x": [1, 2, 3]}).write_parquet(cache_path)

        node = _api_input_node("api", str(data_file))
        _, fn, _ = _build_node_fn(node)
        result = fn()
        df = result.collect()
        assert df["x"].to_list() == [1, 2, 3]

    def test_uncached_file_raises(self, tmp_path, monkeypatch):
        """Any uncached JSONL file should raise, regardless of size."""
        monkeypatch.chdir(tmp_path)

        data_file = tmp_path / "small.jsonl"
        data_file.write_text('{"x": 10}\n{"x": 20}\n')

        node = _api_input_node("api", str(data_file))
        _, fn, _ = _build_node_fn(node)
        with pytest.raises(RuntimeError, match="not been cached"):
            fn()


# ---------------------------------------------------------------------------
# Error path tests
# ---------------------------------------------------------------------------


class TestExecUserCodeErrors:
    """Error paths in _exec_user_code — syntax errors, runtime errors,
    missing sources, and sandboxing."""

    def test_syntax_error_in_chain_code(self):
        """Syntax error in chain-style code should raise SyntaxError."""
        lf = pl.DataFrame({"x": [1]}).lazy()
        with pytest.raises(SyntaxError):
            _exec_user_code("df = df.filter(pl.col('x') > )", ["df"], (lf,))

    def test_syntax_error_in_assignment_code(self):
        """Syntax error in assignment-style code should raise SyntaxError."""
        lf = pl.DataFrame({"x": [1]}).lazy()
        with pytest.raises(SyntaxError):
            _exec_user_code("df = df.with_columns(", ["df"], (lf,))

    def test_division_by_zero_in_user_code(self):
        """Runtime ZeroDivisionError should propagate from user code."""
        lf = pl.DataFrame({"x": [1]}).lazy()
        with pytest.raises(ZeroDivisionError):
            _exec_user_code("df = 1 / 0", ["df"], (lf,))

    def test_name_error_referencing_undefined_variable(self):
        """Referencing an undefined variable should raise NameError."""
        lf = pl.DataFrame({"x": [1]}).lazy()
        with pytest.raises(NameError, match="nonexistent_var"):
            _exec_user_code("df = nonexistent_var", ["df"], (lf,))

    def test_attribute_error_on_wrong_method(self):
        """Calling a non-existent method on the DataFrame raises at exec/collect."""
        lf = pl.DataFrame({"x": [1]}).lazy()
        with pytest.raises(AttributeError):
            _exec_user_code("df = df.totally_fake_method()", ["df"], (lf,))

    def test_empty_code_returns_input_unchanged(self):
        """Empty string code should pass through the input DataFrame."""
        lf = pl.DataFrame({"x": [1, 2, 3]}).lazy()
        # Empty code hits the chain branch since it doesn't start with "."
        # and doesn't contain "df =", so wraps as df = (\n\n)
        # Actually, empty code won't start with "." and won't contain "df =",
        # so it wraps as df = (\n    \n). That's a syntax error.
        # The hook is that _build_node_fn with empty code returns passthrough.
        # Let's verify the passthrough path instead:
        node = _transform_node("t", code="")
        _, fn, _ = _build_node_fn(node)
        result = fn(lf).collect()
        assert result["x"].to_list() == [1, 2, 3]


class TestBuildNodeFnErrorPaths:
    """Error paths in _build_node_fn — missing config, bad source refs, etc."""

    def test_data_source_missing_path(self):
        """Data source with empty path returns an empty LazyFrame."""
        node = _source_node("src", "")
        _, fn, is_source = _build_node_fn(node)
        assert is_source is True
        result = fn()
        assert isinstance(result, pl.LazyFrame)
        assert len(result.collect()) == 0

    def test_data_source_nonexistent_file(self):
        """Data source pointing to a file that doesn't exist should raise at collect."""
        node = _source_node("src", "/nonexistent/path/data.parquet")
        _, fn, _ = _build_node_fn(node)
        # scan_parquet is lazy — the error occurs at collect() time
        with pytest.raises(FileNotFoundError):
            fn().collect()

    def test_transform_with_syntax_error_in_code(self):
        """Transform node whose code has a syntax error should raise when invoked."""
        node = _transform_node("bad", code="df = df.filter(pl.col('x') >")
        _, fn, _ = _build_node_fn(node, source_names=["df"])
        lf = pl.DataFrame({"x": [1]}).lazy()
        with pytest.raises(SyntaxError):
            fn(lf)

    def test_transform_with_runtime_error(self):
        """Transform node whose code divides by zero should raise at execution."""
        node = _transform_node("bad", code="df = 1 / 0")
        _, fn, _ = _build_node_fn(node, source_names=["df"])
        lf = pl.DataFrame({"x": [1]}).lazy()
        with pytest.raises(ZeroDivisionError):
            fn(lf)

    def test_external_file_missing_path(self, tmp_path):
        """External file node with non-existent path should fail when invoked."""
        node = _n(
            {
                "id": "ext",
                "data": {
                    "label": "ext",
                    "nodeType": "externalFile",
                    "config": {
                        "path": str(tmp_path / "does_not_exist.pkl"),
                        "fileType": "pickle",
                        "code": "df = obj",
                    },
                },
            }
        )
        _, fn, _ = _build_node_fn(node, source_names=["df"])
        lf = pl.DataFrame({"x": [1]}).lazy()
        with pytest.raises((ValueError, FileNotFoundError)):
            fn(lf)


class TestSelectedColumns:
    """Tests for the selected_columns column-filter feature."""

    def test_selected_columns_filters_output(self, tmp_path):
        """selected_columns in config filters the node's output DataFrame."""
        p = tmp_path / "sel.parquet"
        pl.DataFrame({"a": [1], "b": [2], "c": [3]}).write_parquet(p)

        graph = _g(
            {
                "nodes": [
                    _source_node("src", str(p)),
                    _n(
                        {
                            "id": "t",
                            "data": {
                                "label": "t",
                                "nodeType": "polars",
                                "config": {
                                    "code": "df = df.with_columns(d=pl.col('a') + pl.col('b'))",
                                    "selected_columns": ["a", "d"],
                                },
                            },
                        }
                    ),
                ],
                "edges": [_edge("src", "t")],
            }
        )
        results = execute_graph(graph)
        assert results["t"].status == "ok"
        col_names = [c.name for c in results["t"].columns]
        assert col_names == ["a", "d"]
        # available_columns should have the full pre-filter set
        avail_names = [c.name for c in results["t"].available_columns]
        assert set(avail_names) == {"a", "b", "c", "d"}

    def test_selected_columns_empty_keeps_all(self, tmp_path):
        """Empty selected_columns means keep all columns (default)."""
        p = tmp_path / "all.parquet"
        pl.DataFrame({"x": [1], "y": [2]}).write_parquet(p)

        graph = _g(
            {
                "nodes": [
                    _source_node("src", str(p)),
                    _n(
                        {
                            "id": "t",
                            "data": {
                                "label": "t",
                                "nodeType": "polars",
                                "config": {"code": "", "selected_columns": []},
                            },
                        }
                    ),
                ],
                "edges": [_edge("src", "t")],
            }
        )
        results = execute_graph(graph)
        col_names = [c.name for c in results["t"].columns]
        assert set(col_names) == {"x", "y"}

    def test_selected_columns_on_source_node(self, tmp_path):
        """selected_columns works on source nodes too."""
        p = tmp_path / "src_sel.parquet"
        pl.DataFrame({"a": [1], "b": [2], "c": [3]}).write_parquet(p)

        src = _source_node("src", str(p))
        src.data.config["selected_columns"] = ["a", "c"]

        graph = _g({"nodes": [src], "edges": []})
        results = execute_graph(graph)
        col_names = [c.name for c in results["src"].columns]
        assert col_names == ["a", "c"]
        avail_names = [c.name for c in results["src"].available_columns]
        assert set(avail_names) == {"a", "b", "c"}

    def test_selected_columns_propagates_downstream(self, tmp_path):
        """Downstream nodes only see the filtered columns."""
        p = tmp_path / "prop.parquet"
        pl.DataFrame({"a": [1], "b": [2], "c": [3]}).write_parquet(p)

        src = _source_node("src", str(p))
        src.data.config["selected_columns"] = ["a"]

        graph = _g(
            {
                "nodes": [
                    src,
                    _transform_node("t", "df = df.with_columns(x=pl.col('a') * 10)"),
                ],
                "edges": [_edge("src", "t")],
            }
        )
        results = execute_graph(graph)
        assert results["t"].status == "ok"
        col_names = [c.name for c in results["t"].columns]
        # Transform sees only 'a' from upstream, adds 'x'
        assert set(col_names) == {"a", "x"}

    def test_selected_columns_invalid_column_ignored(self, tmp_path):
        """Columns in selected_columns that don't exist are silently ignored."""
        p = tmp_path / "inv.parquet"
        pl.DataFrame({"a": [1], "b": [2]}).write_parquet(p)

        src = _source_node("src", str(p))
        src.data.config["selected_columns"] = ["a", "nonexistent"]

        graph = _g({"nodes": [src], "edges": []})
        results = execute_graph(graph)
        col_names = [c.name for c in results["src"].columns]
        assert col_names == ["a"]


class TestExecuteGraphErrorPaths:
    """Error paths in execute_graph — cascading failures, bad node references,
    missing sources, and circular dependencies."""

    def test_node_referencing_nonexistent_column(self, tmp_path):
        """A transform that selects a non-existent column should produce an error result."""
        p = tmp_path / "d.parquet"
        pl.DataFrame({"x": [1, 2]}).write_parquet(p)

        graph = _g(
            {
                "nodes": [
                    _source_node("src", str(p)),
                    _transform_node("bad", code="df = df.select('no_such_column')"),
                ],
                "edges": [_edge("src", "bad")],
            }
        )
        results = execute_graph(graph)
        assert results["bad"].status == "error"
        assert results["bad"].row_count == 0

    def test_chain_of_errors_propagates(self, tmp_path):
        """A chain of 3 transforms where the first errors should fail all downstream."""
        p = tmp_path / "d.parquet"
        pl.DataFrame({"x": [1]}).write_parquet(p)

        graph = _g(
            {
                "nodes": [
                    _source_node("src", str(p)),
                    _transform_node("t1", code="df = 1 / 0"),
                    _transform_node("t2"),
                    _transform_node("t3"),
                ],
                "edges": [
                    _edge("src", "t1"),
                    _edge("t1", "t2"),
                    _edge("t2", "t3"),
                ],
            }
        )
        results = execute_graph(graph)
        assert results["src"].status == "ok"
        assert results["t1"].status == "error"
        assert results["t2"].status == "error"
        assert results["t3"].status == "error"

    def test_runtime_error_captured_in_user_code(self, tmp_path):
        """Runtime error (division by zero) should be captured as error result."""
        p = tmp_path / "d.parquet"
        pl.DataFrame({"x": [1]}).write_parquet(p)

        graph = _g(
            {
                "nodes": [
                    _source_node("src", str(p)),
                    _transform_node("bad", code="df = 1 / 0"),
                ],
                "edges": [_edge("src", "bad")],
            }
        )
        results = execute_graph(graph)
        assert results["bad"].status == "error"
        assert "division" in results["bad"].error.lower() or "zero" in results["bad"].error.lower()

    def test_syntax_error_captured_in_user_code(self, tmp_path):
        """Syntax error in user code should be captured as error result."""
        p = tmp_path / "d.parquet"
        pl.DataFrame({"x": [1]}).write_parquet(p)

        graph = _g(
            {
                "nodes": [
                    _source_node("src", str(p)),
                    _transform_node("bad", code="df = df.filter(pl.col('x') >"),
                ],
                "edges": [_edge("src", "bad")],
            }
        )
        results = execute_graph(graph)
        assert results["bad"].status == "error"

    def test_circular_dependency_raises_cycle_error(self, tmp_path):
        """Circular edges raise CycleError with the node names involved."""
        from haute._topo import CycleError

        p = tmp_path / "d.parquet"
        pl.DataFrame({"x": [1]}).write_parquet(p)

        graph = _g(
            {
                "nodes": [
                    _source_node("src", str(p)),
                    _transform_node("a"),
                    _transform_node("b"),
                ],
                "edges": [
                    _edge("src", "a"),
                    _edge("a", "b"),
                    _edge("b", "a"),  # circular
                ],
            }
        )
        with pytest.raises(CycleError, match="Cycle detected") as exc_info:
            execute_graph(graph)
        assert set(exc_info.value.cycle_nodes) == {"a", "b"}

    def test_disconnected_nodes_still_execute(self, tmp_path):
        """Nodes with no edges (disconnected) should still be executed."""
        p1 = tmp_path / "d1.parquet"
        p2 = tmp_path / "d2.parquet"
        pl.DataFrame({"x": [1]}).write_parquet(p1)
        pl.DataFrame({"y": [2]}).write_parquet(p2)

        graph = _g(
            {
                "nodes": [
                    _source_node("src1", str(p1)),
                    _source_node("src2", str(p2)),
                ],
                "edges": [],
            }
        )
        results = execute_graph(graph)
        assert results["src1"].status == "ok"
        assert results["src2"].status == "ok"

    def test_edge_referencing_nonexistent_source_node(self, tmp_path):
        """Edge with a source that doesn't exist in nodes should not crash."""
        p = tmp_path / "d.parquet"
        pl.DataFrame({"x": [1]}).write_parquet(p)

        graph = _g(
            {
                "nodes": [
                    _source_node("src", str(p)),
                    _transform_node("t"),
                ],
                "edges": [
                    _edge("src", "t"),
                    _edge("ghost", "t"),  # ghost doesn't exist
                ],
            }
        )
        # Should not crash — the ghost edge is simply ignored by topo sort
        results = execute_graph(graph)
        assert results["src"].status == "ok"
        assert results["t"].status == "ok"

    def test_broken_preamble_only_errors_preamble_nodes(self, tmp_path, monkeypatch):
        """A broken preamble should only error transform/live-switch nodes,
        not data sources that don't use preamble bindings."""
        monkeypatch.chdir(tmp_path)

        # Create a utility module with a NameError
        util_dir = tmp_path / "utility"
        util_dir.mkdir()
        (util_dir / "__init__.py").write_text("")
        (util_dir / "bad.py").write_text("x = 1\nundefined_var\n")

        p = tmp_path / "d.parquet"
        pl.DataFrame({"x": [1]}).write_parquet(p)

        graph = _g(
            {
                "nodes": [
                    _source_node("src", str(p)),
                    _transform_node("t"),
                ],
                "edges": [_edge("src", "t")],
                "preamble": "from utility.bad import *\n",
            }
        )
        results = execute_graph(graph)
        # Data source should succeed — it doesn't need the preamble
        assert results["src"].status == "ok"
        # Transform should show the preamble error
        assert results["t"].status == "error"
        assert "undefined_var" in results["t"].error


# ---------------------------------------------------------------------------
# _resolve_batch_scenario
# ---------------------------------------------------------------------------


class TestResolveBatchScenario:
    """Edge-case tests for _resolve_batch_scenario."""

    def test_no_live_switch_returns_none(self):
        """Graph with no live_switch nodes returns None."""
        graph = _g(
            {
                "nodes": [
                    _source_node("src", "data.parquet"),
                    _transform_node("t"),
                ],
                "edges": [_edge("src", "t")],
            }
        )
        assert _resolve_batch_scenario(graph) is None

    def test_all_live_returns_none(self):
        """live_switch with ISM where all values are 'live' returns None."""
        graph = _g(
            {
                "nodes": [
                    _source_node("src", "data.parquet"),
                    _n(
                        {
                            "id": "sw",
                            "data": {
                                "label": "sw",
                                "nodeType": "liveSwitch",
                                "config": {
                                    "input_scenario_map": {"a": "live", "b": "live"},
                                },
                            },
                        }
                    ),
                ],
                "edges": [_edge("src", "sw")],
            }
        )
        assert _resolve_batch_scenario(graph) is None

    def test_returns_first_non_live(self):
        """live_switch with ISM containing a non-live value returns it."""
        graph = _g(
            {
                "nodes": [
                    _source_node("src", "data.parquet"),
                    _n(
                        {
                            "id": "sw",
                            "data": {
                                "label": "sw",
                                "nodeType": "liveSwitch",
                                "config": {
                                    "input_scenario_map": {"a": "live", "b": "nb_batch"},
                                },
                            },
                        }
                    ),
                ],
                "edges": [_edge("src", "sw")],
            }
        )
        assert _resolve_batch_scenario(graph) == "nb_batch"

    def test_conflicting_non_live_raises(self):
        """Two live_switch nodes with different non-live scenarios raise ValueError."""
        graph = _g(
            {
                "nodes": [
                    _source_node("src", "data.parquet"),
                    _n(
                        {
                            "id": "sw1",
                            "data": {
                                "label": "sw1",
                                "nodeType": "liveSwitch",
                                "config": {
                                    "input_scenario_map": {"a": "live", "b": "batch_A"},
                                },
                            },
                        }
                    ),
                    _n(
                        {
                            "id": "sw2",
                            "data": {
                                "label": "sw2",
                                "nodeType": "liveSwitch",
                                "config": {
                                    "input_scenario_map": {"c": "live", "d": "batch_B"},
                                },
                            },
                        }
                    ),
                ],
                "edges": [_edge("src", "sw1"), _edge("src", "sw2")],
            }
        )
        with pytest.raises(ValueError, match="Conflicting batch scenarios"):
            _resolve_batch_scenario(graph)


# ===========================================================================
# GAP ANALYSIS TESTS
# ===========================================================================
#
# Each test below targets a specific gap in the existing test suite.
# The docstring of each test explains what real production failure it catches.
# ===========================================================================


# ---------------------------------------------------------------------------
# GAP 1: FingerprintCache partial-hit path
# Production failure: User clicks node A, cache populates.  Then clicks
# node B (deeper in the graph).  The cache has the same fingerprint but
# node B isn't in the cached outputs.  The executor must re-execute for
# the new target, merge results, and store the merged cache — not silently
# return incomplete results.
# ---------------------------------------------------------------------------


class TestPreviewCachePartialHit:
    """Verify the cache-extend (partial-hit) path in execute_graph."""

    def test_partial_hit_extends_cache(self, tmp_path):
        """When a cached graph fingerprint exists but the target node is
        not yet materialized, execute_graph should re-execute for the new
        target and merge the results.

        Real failure: clicking a deeper node after an initial preview
        returns 'No output' because the cache returned a hit without
        the requested node.
        """
        from haute.executor import _preview_cache

        _preview_cache.invalidate()

        p = tmp_path / "d.parquet"
        pl.DataFrame({"x": [1, 2, 3]}).write_parquet(p)

        graph = _g(
            {
                "nodes": [
                    _source_node("src", str(p)),
                    _transform_node("mid", "df = df.with_columns(y=pl.col('x') + 1)"),
                    _transform_node("leaf", "df = df.with_columns(z=pl.col('y') * 10)"),
                ],
                "edges": [_edge("src", "mid"), _edge("mid", "leaf")],
            }
        )

        # First call: only up to "mid"
        results1 = execute_graph(graph, target_node_id="mid")
        assert results1["mid"].status == "ok"
        assert "leaf" not in results1

        # Second call: same graph, but now requesting "leaf" — partial hit
        results2 = execute_graph(graph, target_node_id="leaf")
        assert "leaf" in results2
        assert results2["leaf"].status == "ok"
        assert results2["leaf"].row_count == 3
        # The merged cache should also still contain "mid" and "src"
        assert "mid" in results2
        assert results2["mid"].status == "ok"

        _preview_cache.invalidate()

    def test_full_cache_hit_returns_instantly(self, tmp_path):
        """When the target node is already in the cached outputs, no
        re-execution should happen.

        Real failure: every click re-executes the full pipeline even when
        the result is already cached.
        """
        from unittest.mock import patch

        from haute.executor import _preview_cache

        _preview_cache.invalidate()

        p = tmp_path / "d.parquet"
        pl.DataFrame({"x": [1]}).write_parquet(p)

        graph = _g(
            {
                "nodes": [_source_node("src", str(p))],
                "edges": [],
            }
        )

        # Populate cache
        execute_graph(graph, target_node_id="src")

        # Second call — should hit cache, no _eager_execute call
        with patch("haute.executor._eager_execute") as mock_exec:
            results = execute_graph(graph, target_node_id="src")
            mock_exec.assert_not_called()
        assert results["src"].status == "ok"

        _preview_cache.invalidate()


# ---------------------------------------------------------------------------
# GAP 2: Preview cache invalidation
# Production failure: User edits a node's code, but the preview still
# shows stale results because _preview_cache.invalidate() was not called
# or didn't actually clear the cache.
# ---------------------------------------------------------------------------


class TestPreviewCacheInvalidation:
    """Verify _preview_cache.invalidate() actually clears cached results."""

    def test_invalidate_forces_re_execution(self, tmp_path):
        """After invalidation, the same graph fingerprint triggers a
        full re-execution instead of returning stale cached results.

        Real failure: user edits utility code (same graph structure),
        invalidation fires, but stale results are still served.
        """
        from haute.executor import _preview_cache

        _preview_cache.invalidate()

        p = tmp_path / "d.parquet"
        pl.DataFrame({"x": [1, 2]}).write_parquet(p)

        graph = _g(
            {
                "nodes": [_source_node("src", str(p))],
                "edges": [],
            }
        )

        results1 = execute_graph(graph)
        assert results1["src"].status == "ok"

        # Invalidate the cache
        _preview_cache.invalidate()

        # After invalidation, try_get should return None for any fingerprint
        from haute.graph_utils import graph_fingerprint

        fp = graph_fingerprint(graph, "None:live")
        assert _preview_cache.try_get(fp) is None

        # Re-execute should still work
        results2 = execute_graph(graph)
        assert results2["src"].status == "ok"
        assert results2["src"].row_count == 2

        _preview_cache.invalidate()


# ---------------------------------------------------------------------------
# GAP 3: Preamble lock under concurrent access
# Production failure: Two concurrent requests (e.g. preview + estimate)
# both call _compile_preamble.  One evicts "utility" from sys.modules
# while the other is mid-import, causing KeyError inside
# importlib._bootstrap._load_unlocked.
# ---------------------------------------------------------------------------


class TestPreambleLockConcurrency:
    """Verify _preamble_lock prevents race conditions during preamble compilation."""

    def test_concurrent_preamble_compilation_no_crash(self, tmp_path, monkeypatch):
        """Two threads compiling the same preamble concurrently should not
        crash with KeyError from sys.modules eviction race.

        Real failure: intermittent KeyError in importlib._bootstrap when
        two requests hit _compile_preamble simultaneously.
        """
        import threading

        monkeypatch.chdir(tmp_path)
        util_dir = tmp_path / "utility"
        util_dir.mkdir()
        (util_dir / "__init__.py").write_text("")
        (util_dir / "helpers.py").write_text("VALUE = 42\n")

        errors: list[Exception] = []
        results: list[dict] = []

        def compile_worker():
            try:
                ns = _compile_preamble("from utility.helpers import *\n")
                results.append(ns)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=compile_worker) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert not errors, f"Concurrent preamble compilation crashed: {errors}"
        assert len(results) == 5
        for ns in results:
            assert ns["VALUE"] == 42

    def test_sys_path_insert_is_idempotent(self, tmp_path, monkeypatch):
        """Multiple _compile_preamble calls should not keep appending the
        same cwd to sys.path.

        Real failure: sys.path grows unboundedly on every preview click,
        slowing down all subsequent imports.
        """
        import sys

        monkeypatch.chdir(tmp_path)

        cwd = str(tmp_path)
        # Remove cwd from sys.path first to get a clean state
        while cwd in sys.path:
            sys.path.remove(cwd)

        _compile_preamble("X = 1\n")
        count_after_first = sys.path.count(cwd)

        _compile_preamble("Y = 2\n")
        count_after_second = sys.path.count(cwd)

        # Should not have added cwd again (it was already there from first call)
        assert count_after_second == count_after_first, (
            f"sys.path.insert duplicated cwd: {count_after_first} -> {count_after_second}"
        )


# ---------------------------------------------------------------------------
# GAP 5: max_preview_rows truncation
# Production failure: A pipeline with 50K rows sends a 50K-row JSON
# preview payload over the WebSocket, causing the browser to freeze
# and the server to OOM on JSON serialization.
# ---------------------------------------------------------------------------


class TestMaxPreviewRowsTruncation:
    """Verify max_preview_rows limits the preview payload size."""

    def test_preview_truncated_to_max_rows(self, tmp_path):
        """execute_graph should cap the preview list at max_preview_rows
        even when the full DataFrame has more rows.

        Real failure: browser tab crashes when receiving a 100K-row
        JSON preview payload.
        """
        from haute.executor import _preview_cache

        _preview_cache.invalidate()

        p = tmp_path / "big.parquet"
        pl.DataFrame({"x": list(range(500))}).write_parquet(p)

        graph = _g(
            {
                "nodes": [_source_node("src", str(p))],
                "edges": [],
            }
        )

        results = execute_graph(graph, max_preview_rows=10)
        assert results["src"].status == "ok"
        # row_count should reflect the full data
        assert results["src"].row_count == 500
        # preview should be truncated
        assert len(results["src"].preview) == 10

        _preview_cache.invalidate()

    def test_max_preview_rows_smaller_than_data(self, tmp_path):
        """When max_preview_rows < actual rows, preview is capped but
        row_count stays accurate.

        Real failure: row_count and len(preview) are conflated — the UI
        shows 'showing 50 of 50' instead of 'showing 50 of 10000'.
        """
        from haute.executor import _preview_cache

        _preview_cache.invalidate()

        p = tmp_path / "data.parquet"
        pl.DataFrame({"x": list(range(200))}).write_parquet(p)

        graph = _g(
            {
                "nodes": [
                    _source_node("src", str(p)),
                    _transform_node("t", "df = df.with_columns(y=pl.col('x') * 2)"),
                ],
                "edges": [_edge("src", "t")],
            }
        )

        results = execute_graph(graph, max_preview_rows=5)
        assert results["t"].row_count == 200
        assert len(results["t"].preview) == 5
        # Verify the preview rows have the correct columns
        assert "x" in results["t"].preview[0]
        assert "y" in results["t"].preview[0]

        _preview_cache.invalidate()

    def test_max_preview_rows_default_caps_at_10k(self, tmp_path):
        """Default max_preview_rows=10_000 should cap large DataFrames.

        Real failure: the default value is ignored or not applied.
        """
        from haute.executor import _MAX_PREVIEW_ROWS, _preview_cache

        _preview_cache.invalidate()

        p = tmp_path / "huge.parquet"
        n_rows = _MAX_PREVIEW_ROWS + 100
        pl.DataFrame({"x": list(range(n_rows))}).write_parquet(p)

        graph = _g(
            {
                "nodes": [_source_node("src", str(p))],
                "edges": [],
            }
        )

        results = execute_graph(graph)  # uses default max_preview_rows
        assert results["src"].row_count == n_rows
        assert len(results["src"].preview) == _MAX_PREVIEW_ROWS

        _preview_cache.invalidate()


# ---------------------------------------------------------------------------
# GAP 6: Empty DataFrame (0 rows) through full pipeline
# Production failure: A source with 0 rows (e.g. filtered to nothing)
# crashes downstream joins or transforms that assume at least 1 row.
# ---------------------------------------------------------------------------


class TestEmptyDataFrameFullPipeline:
    """Verify 0-row DataFrames propagate through transforms and joins."""

    def test_empty_source_through_transform_chain(self, tmp_path):
        """0-row source → transform → transform should produce 0-row
        results with correct schema at each step.

        Real failure: transforms crash with 'empty sequence' or
        'invalid schema' when receiving 0-row input.
        """
        from haute.executor import _preview_cache

        _preview_cache.invalidate()

        p = tmp_path / "empty.parquet"
        pl.DataFrame(
            {
                "x": pl.Series([], dtype=pl.Int64),
                "y": pl.Series([], dtype=pl.Float64),
            }
        ).write_parquet(p)

        graph = _g(
            {
                "nodes": [
                    _source_node("src", str(p)),
                    _transform_node("t1", "df = df.with_columns(z=pl.col('x') + 1)"),
                    _transform_node("t2", "df = df.with_columns(w=pl.col('z') * pl.col('y'))"),
                ],
                "edges": [_edge("src", "t1"), _edge("t1", "t2")],
            }
        )

        results = execute_graph(graph)
        for nid in ("src", "t1", "t2"):
            assert results[nid].status == "ok", f"Node {nid} failed: {results[nid].error}"
            assert results[nid].row_count == 0

        # Schema should be correct even with 0 rows
        t2_cols = {c.name for c in results["t2"].columns}
        assert {"x", "y", "z", "w"} <= t2_cols

        _preview_cache.invalidate()

    def test_empty_source_through_join(self, tmp_path):
        """0-row left source joined with non-empty right should produce
        0-row output (left join semantics).

        Real failure: join logic crashes or produces NaN-filled rows
        when one side is empty.
        """
        from haute.executor import _preview_cache

        _preview_cache.invalidate()

        p_empty = tmp_path / "empty.parquet"
        p_full = tmp_path / "full.parquet"
        pl.DataFrame(
            {
                "key": pl.Series([], dtype=pl.Int64),
                "a": pl.Series([], dtype=pl.Int64),
            }
        ).write_parquet(p_empty)
        pl.DataFrame({"key": [1, 2], "b": [10, 20]}).write_parquet(p_full)

        graph = _g(
            {
                "nodes": [
                    _source_node("empty_src", str(p_empty)),
                    _source_node("full_src", str(p_full)),
                    _n(
                        {
                            "id": "join",
                            "data": {
                                "label": "join",
                                "nodeType": "polars",
                                "config": {
                                    "code": "df = empty_src.join(full_src, on='key', how='left')",
                                },
                            },
                        }
                    ),
                ],
                "edges": [
                    _edge("empty_src", "join"),
                    _edge("full_src", "join"),
                ],
            }
        )

        results = execute_graph(graph)
        assert results["join"].status == "ok"
        assert results["join"].row_count == 0
        join_cols = {c.name for c in results["join"].columns}
        assert {"key", "a", "b"} <= join_cols

        _preview_cache.invalidate()

    def test_empty_source_sink_writes_empty_file(self, tmp_path):
        """execute_sink with 0-row input should write a valid empty
        parquet file (schema only, no rows).

        Real failure: sink crashes on empty input or writes a corrupt
        file that can't be read back.
        """
        p_src = tmp_path / "empty.parquet"
        p_out = tmp_path / "out.parquet"
        pl.DataFrame(
            {
                "x": pl.Series([], dtype=pl.Int64),
            }
        ).write_parquet(p_src)

        graph = _g(
            {
                "nodes": [
                    _source_node("src", str(p_src)),
                    _n(
                        {
                            "id": "sink",
                            "data": {
                                "label": "sink",
                                "nodeType": "dataSink",
                                "config": {"path": str(p_out), "format": "parquet"},
                            },
                        }
                    ),
                ],
                "edges": [_edge("src", "sink")],
            }
        )

        result = execute_sink(graph, sink_node_id="sink")
        assert result.status == "ok"
        assert result.row_count == 0
        # The file should be readable with correct schema
        df = pl.read_parquet(p_out)
        assert len(df) == 0
        assert "x" in df.columns


# ---------------------------------------------------------------------------
# GAP 7: Conflicting batch scenarios
# Production failure: Two live_switch nodes define different batch
# scenario names (e.g. "nb_batch" and "monthly_batch").  execute_sink
# silently picks one, routing half the pipeline to the wrong data source.
# ---------------------------------------------------------------------------


class TestConflictingBatchScenarios:
    """Verify _resolve_batch_scenario raises on conflicting non-live names."""

    def test_same_non_live_across_switches_ok(self):
        """Multiple live_switch nodes with the SAME non-live scenario
        should return that scenario (no conflict).

        Real failure: false positive conflict detection when multiple
        switches agree on the same batch scenario name.
        """
        graph = _g(
            {
                "nodes": [
                    _source_node("src1", "d1.parquet"),
                    _source_node("src2", "d2.parquet"),
                    _n(
                        {
                            "id": "sw1",
                            "data": {
                                "label": "sw1",
                                "nodeType": "liveSwitch",
                                "config": {
                                    "input_scenario_map": {"a": "live", "b": "nb_batch"},
                                },
                            },
                        }
                    ),
                    _n(
                        {
                            "id": "sw2",
                            "data": {
                                "label": "sw2",
                                "nodeType": "liveSwitch",
                                "config": {
                                    "input_scenario_map": {"c": "live", "d": "nb_batch"},
                                },
                            },
                        }
                    ),
                ],
                "edges": [_edge("src1", "sw1"), _edge("src2", "sw2")],
            }
        )
        assert _resolve_batch_scenario(graph) == "nb_batch"

    def test_conflicting_scenarios_error_message_informative(self):
        """The error message should name both conflicting scenario values.

        Real failure: generic error message makes it impossible for the
        user to identify which live_switch nodes are misconfigured.
        """
        graph = _g(
            {
                "nodes": [
                    _source_node("src", "data.parquet"),
                    _n(
                        {
                            "id": "sw1",
                            "data": {
                                "label": "sw1",
                                "nodeType": "liveSwitch",
                                "config": {
                                    "input_scenario_map": {"a": "live", "b": "alpha_batch"},
                                },
                            },
                        }
                    ),
                    _n(
                        {
                            "id": "sw2",
                            "data": {
                                "label": "sw2",
                                "nodeType": "liveSwitch",
                                "config": {
                                    "input_scenario_map": {"c": "live", "d": "beta_batch"},
                                },
                            },
                        }
                    ),
                ],
                "edges": [_edge("src", "sw1"), _edge("src", "sw2")],
            }
        )
        with pytest.raises(ValueError, match="Conflicting batch scenarios") as exc_info:
            _resolve_batch_scenario(graph)
        msg = str(exc_info.value)
        assert "alpha_batch" in msg
        assert "beta_batch" in msg

    def test_single_switch_multiple_non_live_same_value_ok(self):
        """A single live_switch with multiple inputs mapping to the same
        non-live scenario should not raise.

        Real failure: the duplicate-detection logic incorrectly flags
        the same value appearing twice within a single ISM.
        """
        graph = _g(
            {
                "nodes": [
                    _source_node("src", "data.parquet"),
                    _n(
                        {
                            "id": "sw",
                            "data": {
                                "label": "sw",
                                "nodeType": "liveSwitch",
                                "config": {
                                    "input_scenario_map": {
                                        "a": "live",
                                        "b": "nb_batch",
                                        "c": "nb_batch",
                                    },
                                },
                            },
                        }
                    ),
                ],
                "edges": [_edge("src", "sw")],
            }
        )
        assert _resolve_batch_scenario(graph) == "nb_batch"


# ---------------------------------------------------------------------------
# GAP 8: Preamble failure isolation
# Production failure: A broken preamble (e.g. bad utility import) causes
# ALL nodes to fail, including data sources that don't use preamble
# bindings.  The user can't even see their data to debug the issue.
# ---------------------------------------------------------------------------


class TestPreambleFailureIsolation:
    """Verify preamble errors inject only into POLARS and LIVE_SWITCH nodes."""

    def test_broken_preamble_data_source_succeeds(self, tmp_path, monkeypatch):
        """Data source nodes should execute normally even when the preamble
        is broken.

        Real failure: user breaks utility code, entire pipeline shows
        errors including data sources, blocking all debugging.
        """
        monkeypatch.chdir(tmp_path)

        util_dir = tmp_path / "utility"
        util_dir.mkdir()
        (util_dir / "__init__.py").write_text("")
        (util_dir / "broken.py").write_text("raise RuntimeError('compile fail')\n")

        p = tmp_path / "d.parquet"
        pl.DataFrame({"x": [1, 2, 3]}).write_parquet(p)

        from haute.executor import _preview_cache

        _preview_cache.invalidate()

        graph = _g(
            {
                "nodes": [
                    _source_node("src", str(p)),
                    _transform_node("t1", "df = df.with_columns(y=pl.col('x') + 1)"),
                    _transform_node("t2", "df = df.filter(pl.col('x') > 0)"),
                ],
                "edges": [_edge("src", "t1"), _edge("t1", "t2")],
                "preamble": "from utility.broken import *\n",
            }
        )

        results = execute_graph(graph)
        # Data source should succeed
        assert results["src"].status == "ok"
        assert results["src"].row_count == 3
        # Transform nodes should fail with the preamble error
        assert results["t1"].status == "error"
        assert results["t2"].status == "error"

        _preview_cache.invalidate()

    def test_broken_preamble_live_switch_gets_error(self, tmp_path, monkeypatch):
        """liveSwitch nodes should also receive the preamble error.

        Real failure: liveSwitch nodes silently pass through with no
        error, hiding the preamble failure from the user.
        """
        monkeypatch.chdir(tmp_path)

        util_dir = tmp_path / "utility"
        util_dir.mkdir()
        (util_dir / "__init__.py").write_text("")
        (util_dir / "broken.py").write_text("undefined_name\n")

        p1 = tmp_path / "live.parquet"
        p2 = tmp_path / "batch.parquet"
        pl.DataFrame({"x": [1]}).write_parquet(p1)
        pl.DataFrame({"x": [2]}).write_parquet(p2)

        from haute.executor import _preview_cache

        _preview_cache.invalidate()

        graph = _g(
            {
                "nodes": [
                    _source_node("live_src", str(p1)),
                    _source_node("batch_src", str(p2)),
                    _n(
                        {
                            "id": "sw",
                            "data": {
                                "label": "sw",
                                "nodeType": "liveSwitch",
                                "config": {
                                    "input_scenario_map": {
                                        "live_src": "live",
                                        "batch_src": "nb_batch",
                                    },
                                },
                            },
                        }
                    ),
                ],
                "edges": [
                    _edge("live_src", "sw"),
                    _edge("batch_src", "sw"),
                ],
                "preamble": "from utility.broken import *\n",
            }
        )

        results = execute_graph(graph, source="live")
        # Data sources should succeed
        assert results["live_src"].status == "ok"
        # liveSwitch should get the preamble error
        assert results["sw"].status == "error"
        assert "undefined_name" in results["sw"].error

        _preview_cache.invalidate()

    def test_broken_preamble_does_not_error_model_score_or_sink(self, tmp_path, monkeypatch):
        """Non-preamble node types (dataSink, etc.) should not receive
        the preamble error.

        Real failure: sink nodes display 'preamble error' when they
        have nothing to do with preamble code, confusing the user.
        """
        monkeypatch.chdir(tmp_path)

        util_dir = tmp_path / "utility"
        util_dir.mkdir()
        (util_dir / "__init__.py").write_text("")
        (util_dir / "broken.py").write_text("bad_name\n")

        p = tmp_path / "d.parquet"
        pl.DataFrame({"x": [1]}).write_parquet(p)

        from haute.executor import _eager_execute, _preview_cache

        _preview_cache.invalidate()

        graph = _g(
            {
                "nodes": [
                    _source_node("src", str(p)),
                    _n(
                        {
                            "id": "sink",
                            "data": {
                                "label": "sink",
                                "nodeType": "dataSink",
                                "config": {
                                    "path": str(tmp_path / "out.parquet"),
                                    "format": "parquet",
                                },
                            },
                        }
                    ),
                ],
                "edges": [_edge("src", "sink")],
                "preamble": "from utility.broken import *\n",
            }
        )

        # Use _eager_execute directly to check error injection logic
        outputs, order, errors, *_ = _eager_execute(graph, None, None, source="live")
        # dataSink is NOT in the preamble_types set, so it should not
        # have the preamble error injected
        assert "sink" not in errors or "bad_name" not in errors.get("sink", "")

        _preview_cache.invalidate()
