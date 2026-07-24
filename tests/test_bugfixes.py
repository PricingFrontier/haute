"""Tests for bugfixes: streaming chunk restore, source_file propagation, and config loading.

Covers:
  - Streaming chunk size restore in executor.write_data_output (ValueError on 0)
  - Streaming chunk size restore in optimiser service (ValueError on 0)
  - _ensure_source_file fills source_file from haute.toml for preview/trace/sink
  - _compile_preamble uses pipeline_dir to resolve utility imports
  - Data source configs with empty path produce empty LazyFrames
  - Data source configs with valid path load data correctly
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import polars as pl
import pytest

from haute._types import GraphEdge, GraphNode, NodeData, NodeType, PipelineGraph
from haute.executor import PreambleError, _compile_preamble
from tests.conftest import make_file_input_config, make_file_output_config

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _e(src: str, tgt: str) -> GraphEdge:
    return GraphEdge(id=f"e_{src}_{tgt}", source=src, target=tgt)


def _source_node(nid: str, path: str = "data.parquet") -> GraphNode:
    return GraphNode(
        id=nid,
        data=NodeData(label=nid, nodeType=NodeType.DATA_INPUT, config=make_file_input_config(path)),
    )


def _sink_node(nid: str, path: str = "", fmt: str = "parquet") -> GraphNode:
    return GraphNode(
        id=nid,
        data=NodeData(
            label=nid,
            nodeType=NodeType.DATA_OUTPUT,
            config=make_file_output_config(path, format_name=fmt),
        ),
    )


def _transform_node(nid: str) -> GraphNode:
    return GraphNode(
        id=nid,
        data=NodeData(label=nid, nodeType=NodeType.POLARS),
    )


def _optimiser_node(nid: str, config: dict | None = None) -> GraphNode:
    return GraphNode(
        id=nid,
        data=NodeData(
            label=nid,
            nodeType=NodeType.OPTIMISER,
            config=config or {},
        ),
    )


def _make_graph(
    nodes: list[GraphNode],
    edges: list[GraphEdge],
    source_file: str | None = None,
) -> PipelineGraph:
    return PipelineGraph(nodes=nodes, edges=edges, source_file=source_file)


# ---------------------------------------------------------------------------
# Streaming chunk size restore — executor.write_data_output
# ---------------------------------------------------------------------------


class TestStreamingChunkRestoreExecutor:
    """Verify write_data_output doesn't crash when restoring streaming chunk size."""

    def test_sink_succeeds_when_no_prior_chunk_size(self, tmp_path):
        """When POLARS_STREAMING_CHUNK_SIZE was never set, the finally block
        must not call set_streaming_chunk_size(0) — that raises ValueError."""
        from haute.executor import write_data_output

        out_path = str(tmp_path / "output.parquet")
        graph = _make_graph(
            nodes=[_source_node("s"), _sink_node("sink", path=out_path)],
            edges=[_e("s", "sink")],
        )

        lf = pl.DataFrame({"x": [1, 2, 3]}).lazy()
        mock_outputs = {"sink": lf}

        # Ensure no prior chunk size is set
        prev = pl.Config.state().get("POLARS_STREAMING_CHUNK_SIZE")

        with patch(
            "haute.executor._execute_lazy",
            return_value=(mock_outputs, ["s", "sink"], {}, {}),
        ):
            result = write_data_output(graph, "sink")

        assert result.status == "ok"
        assert result.row_count == 3
        assert Path(out_path).exists()

        # The chunk size should not have been reset to an invalid value
        current = pl.Config.state().get("POLARS_STREAMING_CHUNK_SIZE")
        # Should either be the original value or 50_000 (left from the sink)
        if prev is None:
            # The chunk size was either left at 50_000 or still unset
            assert current is None or int(current) > 0
        else:
            assert int(current) > 0

    def test_sink_succeeds_when_prior_chunk_size_exists(self, tmp_path):
        """When a prior chunk size was explicitly set, it should be restored."""
        from haute.executor import write_data_output

        out_path = str(tmp_path / "output.parquet")
        graph = _make_graph(
            nodes=[_source_node("s"), _sink_node("sink", path=out_path)],
            edges=[_e("s", "sink")],
        )

        lf = pl.DataFrame({"x": [10, 20]}).lazy()
        mock_outputs = {"sink": lf}

        # Set a known chunk size before the sink
        pl.Config.set_streaming_chunk_size(75_000)

        with patch(
            "haute.executor._execute_lazy",
            return_value=(mock_outputs, ["s", "sink"], {}, {}),
        ):
            result = write_data_output(graph, "sink")

        assert result.status == "ok"

        # The chunk size should be restored to 75_000
        restored = pl.Config.state().get("POLARS_STREAMING_CHUNK_SIZE")
        assert restored is not None
        assert int(restored) == 75_000

    def test_set_streaming_chunk_size_zero_raises(self):
        """Confirm that Polars rejects chunk size of 0 — the bug we fixed."""
        with pytest.raises(ValueError, match="number of rows per chunk must be >= 1"):
            pl.Config.set_streaming_chunk_size(0)


# ---------------------------------------------------------------------------
# Streaming chunk size restore — optimiser service
# ---------------------------------------------------------------------------


class TestStreamingChunkRestoreOptimiser:
    """Verify the optimiser pipeline execution doesn't crash on chunk restore."""

    def test_execute_pipeline_no_prior_chunk_size(self, haute_scratch):
        """_execute_pipeline should not raise when POLARS_STREAMING_CHUNK_SIZE
        was never set (the exact scenario that caused the production bug).

        The imports inside _execute_pipeline are lazy (inside the method body),
        so we patch at the source modules and call the method directly."""
        from haute.routes._optimiser_service import JobStore, OptimiserSolveService

        graph = _make_graph(
            nodes=[_source_node("s"), _optimiser_node("opt")],
            edges=[_e("s", "opt")],
        )

        lf = pl.DataFrame({"x": [1]}).lazy()
        mock_lazy_outputs = {"opt": lf}

        body = MagicMock()
        body.graph = graph
        body.node_id = "opt"
        body.streaming_chunk_size = None

        svc = OptimiserSolveService(store=JobStore())

        # Ensure no prior chunk size is set
        prev_chunk = pl.Config.state().get("POLARS_STREAMING_CHUNK_SIZE")

        # Patch the optimiser-service facade used by _execute_pipeline.
        with (
            patch(
                "haute.routes._optimiser_service.execute_lazy_graph",
                return_value=(mock_lazy_outputs, ["s", "opt"], {}, {}),
            ),
            patch(
                "haute.executor._compile_preamble",
                return_value={},
            ),
            patch(
                "haute.executor._pipeline_dir",
                return_value=None,
            ),
            patch(
                "haute.executor._resolve_batch_scenario",
                return_value="batch",
            ),
        ):
            checkpoint_dir = haute_scratch / "haute_test_ckpt"
            checkpoint_dir.mkdir()
            result = svc._execute_pipeline(body, "test-job", checkpoint_dir)
            assert "opt" in result

        # Verify no ValueError was raised and chunk size is valid
        current = pl.Config.state().get("POLARS_STREAMING_CHUNK_SIZE")
        if current is not None:
            assert int(current) > 0

        # Restore previous state if needed
        if prev_chunk is not None:
            pl.Config.set_streaming_chunk_size(int(prev_chunk))


# ---------------------------------------------------------------------------
# _ensure_source_file — pipeline route helper
# ---------------------------------------------------------------------------


class TestEnsureSourceFile:
    """Verify _ensure_source_file fills in source_file from haute.toml."""

    def test_noop_when_source_file_already_set(self, tmp_path, monkeypatch):
        """Should not overwrite an existing source_file."""
        from haute.routes.pipeline import _ensure_source_file

        monkeypatch.chdir(tmp_path)
        (tmp_path / "haute.toml").write_text('[project]\npipeline = "other/main.py"\n')

        graph = PipelineGraph(source_file="existing/pipeline.py")
        _ensure_source_file(graph)
        assert graph.source_file == "existing/pipeline.py"

    def test_fills_from_haute_toml(self, tmp_path, monkeypatch):
        """Should read pipeline path from haute.toml when source_file is None."""
        from haute.routes.pipeline import _ensure_source_file

        monkeypatch.chdir(tmp_path)
        (tmp_path / "haute.toml").write_text('[project]\npipeline = "rating/main.py"\n')

        graph = PipelineGraph()
        assert graph.source_file is None
        _ensure_source_file(graph)
        assert graph.source_file == "rating/main.py"

    def test_noop_when_no_haute_toml(self, tmp_path, monkeypatch):
        """Should leave source_file as None when haute.toml doesn't exist."""
        from haute.routes.pipeline import _ensure_source_file

        monkeypatch.chdir(tmp_path)

        graph = PipelineGraph()
        _ensure_source_file(graph)
        assert graph.source_file is None

    def test_noop_when_toml_has_no_pipeline_key(self, tmp_path, monkeypatch):
        """Should leave source_file as None when [project].pipeline is missing."""
        from haute.routes.pipeline import _ensure_source_file

        monkeypatch.chdir(tmp_path)
        (tmp_path / "haute.toml").write_text("[project]\nname = 'test'\n")

        graph = PipelineGraph()
        _ensure_source_file(graph)
        assert graph.source_file is None

    def test_handles_malformed_toml_gracefully(self, tmp_path, monkeypatch):
        """Should not crash on malformed haute.toml."""
        from haute.routes.pipeline import _ensure_source_file

        monkeypatch.chdir(tmp_path)
        (tmp_path / "haute.toml").write_text("this is not valid toml {{{{")

        graph = PipelineGraph()
        _ensure_source_file(graph)
        assert graph.source_file is None

    def test_fills_empty_string_source_file(self, tmp_path, monkeypatch):
        """Should treat empty string source_file the same as None."""
        from haute.routes.pipeline import _ensure_source_file

        monkeypatch.chdir(tmp_path)
        (tmp_path / "haute.toml").write_text('[project]\npipeline = "my/pipe.py"\n')

        graph = PipelineGraph(source_file="")
        _ensure_source_file(graph)
        assert graph.source_file == "my/pipe.py"


# ---------------------------------------------------------------------------
# _compile_preamble with pipeline_dir — utility module resolution
# ---------------------------------------------------------------------------


class TestPreamblePipelineDir:
    """Verify that _compile_preamble uses pipeline_dir to resolve utility imports."""

    def test_utility_import_resolves_with_pipeline_dir(self, tmp_path, monkeypatch):
        """When pipeline_dir is provided, utility modules in that directory
        should be importable from the preamble."""
        # Create a utility module inside a subdirectory (simulating rating/)
        pipeline_dir = tmp_path / "rating"
        pipeline_dir.mkdir()
        util_dir = pipeline_dir / "utility"
        util_dir.mkdir()
        (util_dir / "__init__.py").write_text("")
        (util_dir / "helpers.py").write_text("MAGIC = 42\n")

        # Set cwd to project root (not the pipeline dir)
        monkeypatch.chdir(tmp_path)

        ns = _compile_preamble(
            "from utility.helpers import MAGIC\n",
            pipeline_dir=str(pipeline_dir),
        )
        assert ns["MAGIC"] == 42

    def test_unique_module_fails_without_pipeline_dir(self, tmp_path, monkeypatch):
        """Without pipeline_dir, modules in a subdirectory should
        NOT be importable (they're not on sys.path)."""
        import sys

        # Use a unique module name to avoid interference from other tests
        mod_name = "_test_isolated_mod_xyz"
        pipeline_dir = tmp_path / "rating"
        pipeline_dir.mkdir()
        mod_dir = pipeline_dir / mod_name
        mod_dir.mkdir()
        (mod_dir / "__init__.py").write_text("")
        (mod_dir / "helpers.py").write_text("MAGIC = 42\n")

        # Set cwd to project root — module is in rating/, not on path
        monkeypatch.chdir(tmp_path)

        # Ensure the module is not already importable
        for key in [k for k in sys.modules if k.startswith(mod_name)]:
            del sys.modules[key]
        pdir_str = str(pipeline_dir.resolve())
        if pdir_str in sys.path:
            sys.path.remove(pdir_str)

        with pytest.raises(PreambleError, match=f"No module named '{mod_name}'"):
            _compile_preamble(
                f"from {mod_name}.helpers import MAGIC\n",
                pipeline_dir=None,
            )

    def test_pipeline_dir_added_to_sys_path(self, tmp_path, monkeypatch):
        """pipeline_dir should be added to sys.path during preamble compilation."""
        import sys

        pipeline_dir = tmp_path / "sub"
        pipeline_dir.mkdir()
        util_dir = pipeline_dir / "mymod"
        util_dir.mkdir()
        (util_dir / "__init__.py").write_text("VAL = 99\n")

        monkeypatch.chdir(tmp_path)

        pdir_str = str(pipeline_dir.resolve())
        # Remove from sys.path if already there
        if pdir_str in sys.path:
            sys.path.remove(pdir_str)

        _compile_preamble(
            "from mymod import VAL\n",
            pipeline_dir=str(pipeline_dir),
        )
        assert pdir_str in sys.path


# ---------------------------------------------------------------------------
# Data Input config — empty path produces empty frame
# ---------------------------------------------------------------------------


class TestDataInputConfigPath:
    """Verify that _build_node_fn for data sources uses config.path correctly."""

    def test_unconfigured_preview_source_produces_empty_frame(self):
        """A freshly dropped editor node may preview empty before its first save."""
        from haute.executor import _build_node_fn

        node = GraphNode(
            id="src",
            data=NodeData(
                label="src",
                nodeType=NodeType.DATA_INPUT,
                config={"path": ""},
            ),
        )
        _, fn, is_source = _build_node_fn(node, source_names=[])
        assert is_source is True

        result = fn()
        df = result.collect()
        assert df.shape == (0, 0), "Empty path should produce zero-column frame"

    def test_valid_config_path_loads_data(self, tmp_path):
        """A data source with a valid parquet path should load data."""
        from haute.executor import _build_node_fn

        path = tmp_path / "test.parquet"
        pl.DataFrame({"quote_id": ["q1", "q2"], "value": [10, 20]}).write_parquet(path)

        node = GraphNode(
            id="src",
            data=NodeData(
                label="src",
                nodeType=NodeType.DATA_INPUT,
                config=make_file_input_config(path),
            ),
        )
        _, fn, is_source = _build_node_fn(node, source_names=[])
        assert is_source is True

        result = fn()
        df = result.collect()
        assert "quote_id" in df.columns
        assert df.shape == (2, 2)

    def test_empty_config_breaks_downstream_join(self):
        """Demonstrates the exact failure mode: empty data source joined
        on quote_id fails with 'unable to find column'."""
        empty_lf = pl.LazyFrame()  # zero columns
        main_lf = pl.DataFrame({"quote_id": ["q1"], "val": [1]}).lazy()

        with pytest.raises(Exception, match="quote_id"):
            main_lf.join(empty_lf, on="quote_id", how="left").collect()

    def test_selected_columns_preserved_in_config(self, tmp_path):
        """Config's selected_columns should be preserved on the node — the
        builder layer uses this to filter columns before downstream joins,
        preventing column collisions (e.g. inception_premium vs premium)."""
        node = GraphNode(
            id="src",
            data=NodeData(
                label="src",
                nodeType=NodeType.DATA_INPUT,
                config=make_file_input_config(
                    tmp_path / "dummy.parquet",
                    selected_columns=["policy_id", "quote_id"],
                ),
            ),
        )
        config = node.data.config
        assert config["selected_columns"] == ["policy_id", "quote_id"]

    def test_manual_column_selection_prevents_collision(self, tmp_path):
        """Demonstrates why selected_columns matters: without filtering,
        extra columns from a data source can collide with pipeline columns."""
        # Simulate policy_data with extra columns
        policy_all = pl.DataFrame(
            {
                "policy_id": ["p1"],
                "quote_id": ["q1"],
                "inception_premium": [500.0],
            }
        ).lazy()

        # Simulate pipeline with its own premium column
        pipeline = pl.DataFrame(
            {
                "quote_id": ["q1"],
                "premium": [400.0],
            }
        ).lazy()

        # Join without column filtering — both inception_premium and premium present
        joined_all = pipeline.join(policy_all, on="quote_id", how="left").collect()
        assert "inception_premium" in joined_all.columns  # unwanted column

        # Join with column filtering — only policy_id and quote_id
        policy_filtered = policy_all.select(["policy_id", "quote_id"])
        joined_filtered = pipeline.join(policy_filtered, on="quote_id", how="left").collect()
        assert "inception_premium" not in joined_filtered.columns
        assert "policy_id" in joined_filtered.columns


# ---------------------------------------------------------------------------
# _pipeline_dir — derives directory from source_file
# ---------------------------------------------------------------------------


class TestPipelineDir:
    """Verify _pipeline_dir correctly derives the pipeline directory."""

    def test_returns_none_when_no_source_file(self):
        from haute.executor import _pipeline_dir

        graph = PipelineGraph()
        assert _pipeline_dir(graph) is None

    def test_returns_none_for_empty_source_file(self):
        from haute.executor import _pipeline_dir

        graph = PipelineGraph(source_file="")
        assert _pipeline_dir(graph) is None

    def test_returns_parent_for_relative_path(self, monkeypatch, tmp_path):
        from haute.executor import _pipeline_dir

        monkeypatch.chdir(tmp_path)
        (tmp_path / "rating").mkdir()
        (tmp_path / "rating" / "main.py").write_text("")

        graph = PipelineGraph(source_file="rating/main.py")
        result = _pipeline_dir(graph)
        assert result is not None
        assert result.name == "rating"

    def test_returns_parent_for_absolute_path(self, tmp_path):
        from haute.executor import _pipeline_dir

        pipeline_file = tmp_path / "myproject" / "pipeline.py"
        pipeline_file.parent.mkdir(parents=True, exist_ok=True)
        pipeline_file.write_text("")

        graph = PipelineGraph(source_file=str(pipeline_file))
        result = _pipeline_dir(graph)
        assert result is not None
        assert result == pipeline_file.parent.resolve()


# ---------------------------------------------------------------------------
# Integration: preview route injects source_file
# ---------------------------------------------------------------------------


class TestPreviewRouteSourceFile:
    """Verify the preview route fills in source_file before execution."""

    def test_preview_with_utility_preamble(self, tmp_path, monkeypatch):
        """End-to-end: a preview request with a preamble that imports from
        a utility module should succeed when haute.toml is configured."""
        from haute.routes.pipeline import _ensure_source_file

        # Set up project structure
        monkeypatch.chdir(tmp_path)
        pipeline_dir = tmp_path / "rating"
        pipeline_dir.mkdir()
        util_dir = pipeline_dir / "utility"
        util_dir.mkdir()
        (util_dir / "__init__.py").write_text("")
        (util_dir / "helpers.py").write_text("FACTOR = 2\n")

        (tmp_path / "haute.toml").write_text('[project]\npipeline = "rating/main.py"\n')

        # Create a graph without source_file (simulating frontend payload)
        graph = PipelineGraph(
            source_file=None,
            preamble="from utility.helpers import FACTOR\n",
        )

        # _ensure_source_file should fill it from haute.toml
        _ensure_source_file(graph)
        assert graph.source_file == "rating/main.py"

        # Now the preamble should compile with the correct pipeline_dir
        from haute.executor import _pipeline_dir

        pdir = _pipeline_dir(graph)
        assert pdir is not None
        assert pdir.name == "rating"

        ns = _compile_preamble(
            graph.preamble,
            pipeline_dir=pdir,
        )
        assert ns["FACTOR"] == 2

    def test_preview_uses_project_root_relative_data_path(
        self,
        tmp_path,
        monkeypatch,
    ):
        """GUI-selected data paths resolve from the project root.

        The file browser and schema endpoints expose paths relative to cwd
        (the Haute project root).  A pipeline can live below that root, but
        preview must still read ``data/...`` from the project root when that
        file exists there.
        """
        from haute.executor import execute_graph

        monkeypatch.chdir(tmp_path)
        pipeline_dir = tmp_path / "rating"
        pipeline_dir.mkdir()
        (pipeline_dir / "main.py").write_text("", encoding="utf-8")

        data_dir = tmp_path / "data"
        data_dir.mkdir()
        pl.DataFrame({"quote_id": ["q-1"], "premium": [100.0]}).write_parquet(
            data_dir / "quotes.parquet"
        )

        graph = PipelineGraph(
            source_file="rating/main.py",
            nodes=[
                GraphNode(
                    id="quotes",
                    data=NodeData(
                        label="quotes",
                        nodeType=NodeType.DATA_INPUT,
                        config=make_file_input_config("data/quotes.parquet"),
                    ),
                ),
            ],
        )

        results = execute_graph(graph, target_node_id="quotes")

        assert results["quotes"].status == "ok"
        assert results["quotes"].row_count == 1
        assert results["quotes"].preview[0]["quote_id"] == "q-1"

    def test_preview_ignores_disconnected_explore_outside_target_lineage(
        self,
        client,
        tmp_path,
        monkeypatch,
    ):
        """A draft Explore node must not break previews for unrelated nodes."""
        from haute._sandbox import set_project_root

        monkeypatch.chdir(tmp_path)
        set_project_root(tmp_path)
        data_path = tmp_path / "claims.parquet"
        pl.DataFrame({"x": [1, 2]}).write_parquet(data_path)

        graph = _make_graph(
            nodes=[
                _source_node("src", path="claims.parquet"),
                GraphNode(
                    id="calc",
                    data=NodeData(
                        label="Calc",
                        nodeType=NodeType.POLARS,
                        config={"code": "df = df.with_columns(y=pl.col('x') * 2)"},
                    ),
                ),
                GraphNode(
                    id="explore",
                    data=NodeData(
                        label="Explore",
                        nodeType=NodeType.EXPLORE,
                        config={},
                    ),
                ),
            ],
            edges=[_e("src", "calc")],
        )

        resp = client.post(
            "/api/pipeline/preview",
            json={
                "graph": graph.model_dump(mode="json"),
                "node_id": "calc",
                "row_limit": 100,
                "source": "live",
            },
        )

        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert [column["name"] for column in body["columns"]] == ["x", "y"]
        assert body["preview"][0] == {"x": 1, "y": 2}

    def test_preview_returns_node_error_for_unconnected_explore_target(
        self,
        client,
    ):
        """Selecting an unconnected draft Explore node should not 500."""
        graph = _make_graph(
            nodes=[
                GraphNode(
                    id="explore_8",
                    data=NodeData(
                        label="Explore 8",
                        nodeType=NodeType.EXPLORE,
                        config={},
                    ),
                )
            ],
            edges=[],
        )

        resp = client.post(
            "/api/pipeline/preview",
            json={
                "graph": graph.model_dump(mode="json"),
                "node_id": "explore_8",
                "row_limit": 100,
                "source": "live",
            },
        )

        assert resp.status_code == 200
        body = resp.json()
        assert body["node_id"] == "explore_8"
        assert body["status"] == "error"
        assert "exactly one incoming edge" in body["error"]

    def test_preview_explore_target_applies_polars_code(
        self,
        client,
        tmp_path,
        monkeypatch,
    ):
        """Explore preview should show the dataframe after its own code runs."""
        from haute._sandbox import set_project_root

        monkeypatch.chdir(tmp_path)
        set_project_root(tmp_path)
        data_path = tmp_path / "claims.parquet"
        pl.DataFrame({"x": [1, 2, 3, 4]}).write_parquet(data_path)

        graph = _make_graph(
            nodes=[
                _source_node("src", path="claims.parquet"),
                GraphNode(
                    id="explore",
                    data=NodeData(
                        label="Explore",
                        nodeType=NodeType.EXPLORE,
                        config={
                            "code": (
                                "df = df.filter(pl.col('x') > 1)"
                                ".with_columns((pl.col('x') * 10).alias('scaled'))"
                            )
                        },
                    ),
                ),
            ],
            edges=[_e("src", "explore")],
        )

        resp = client.post(
            "/api/pipeline/preview",
            json={
                "graph": graph.model_dump(mode="json"),
                "node_id": "explore",
                "row_limit": 3,
                "source": "live",
            },
        )

        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert [column["name"] for column in body["columns"]] == ["x", "scaled"]
        assert body["preview"] == [{"x": 2, "scaled": 20}, {"x": 3, "scaled": 30}]


# ---------------------------------------------------------------------------
# Polars Config safety — general guard against invalid chunk sizes
# ---------------------------------------------------------------------------


class TestPolarsConfigSafety:
    """Guard against regressions in Polars config handling."""

    def test_chunk_size_zero_is_invalid(self):
        """Polars rejects chunk_size=0 — this is the invariant our fix depends on."""
        with pytest.raises(ValueError, match="number of rows per chunk must be >= 1"):
            pl.Config.set_streaming_chunk_size(0)

    def test_chunk_size_negative_is_invalid(self):
        """Negative chunk sizes should also be rejected."""
        with pytest.raises((ValueError, OverflowError)):
            pl.Config.set_streaming_chunk_size(-1)

    def test_chunk_size_one_is_valid(self):
        """Minimum valid chunk size is 1."""
        pl.Config.set_streaming_chunk_size(1)
        val = pl.Config.state().get("POLARS_STREAMING_CHUNK_SIZE")
        assert val is not None
        assert int(val) == 1

    def test_default_chunk_size_is_none(self):
        """By default, POLARS_STREAMING_CHUNK_SIZE is not in Config.state."""
        pl.Config.restore_defaults()
        val = pl.Config.state().get("POLARS_STREAMING_CHUNK_SIZE")
        assert val is None

    def test_set_and_read_round_trips(self):
        """Setting a chunk size should be readable back from state."""
        pl.Config.set_streaming_chunk_size(99_999)
        val = pl.Config.state().get("POLARS_STREAMING_CHUNK_SIZE")
        assert val is not None
        assert int(val) == 99_999


# ---------------------------------------------------------------------------
# Parser warning for config load errors
# ---------------------------------------------------------------------------


class TestParserConfigLoadWarning:
    """Verify the parser surfaces config load errors via graph.warning."""

    def test_raises_when_config_missing(self, tmp_path, monkeypatch):
        """Parsing a pipeline with a missing config file raises ``ConfigError``
        (post Item #18 fail-loudly contract)."""
        monkeypatch.chdir(tmp_path)
        pipeline_dir = tmp_path / "rating"
        pipeline_dir.mkdir()
        (pipeline_dir / "main.py").write_text(
            "import haute\nimport polars as pl\n\n"
            'pipeline = haute.Pipeline("test")\n\n'
            '@pipeline.data_input(config="config/data_input/missing.json")\n'
            "def missing() -> pl.LazyFrame:\n"
            '    return pl.scan_parquet("")\n'
        )

        from haute.errors import ConfigError
        from haute.parser import parse_pipeline_file

        with pytest.raises(ConfigError):
            parse_pipeline_file(pipeline_dir / "main.py")

    def test_no_warning_when_config_exists(self, tmp_path, monkeypatch):
        """Parsing a pipeline with a valid config file should not set graph.warning."""
        monkeypatch.chdir(tmp_path)
        pipeline_dir = tmp_path / "rating"
        pipeline_dir.mkdir()
        config_dir = pipeline_dir / "config" / "data_input"
        config_dir.mkdir(parents=True)
        (config_dir / "src.json").write_text(
            '{"inputType":"file","format":"parquet","mode":"scan",'
            '"cacheMode":"direct","path":"d.parquet","arguments":{}}'
        )
        (pipeline_dir / "main.py").write_text(
            "import haute\nimport polars as pl\n\n"
            'pipeline = haute.Pipeline("test")\n\n'
            '@pipeline.data_input(config="config/data_input/src.json")\n'
            "def src() -> pl.LazyFrame:\n"
            '    return pl.scan_parquet("d.parquet")\n'
        )

        from haute.parser import parse_pipeline_file

        graph = parse_pipeline_file(pipeline_dir / "main.py")
        assert graph.warning is None

    def test_warning_set_when_config_has_invalid_utf8(self, tmp_path, monkeypatch):
        """A config file with non-UTF-8 bytes (e.g. Windows-1252 en-dash)
        should be read with replacement chars and still parse successfully."""
        monkeypatch.chdir(tmp_path)
        pipeline_dir = tmp_path / "rating"
        pipeline_dir.mkdir()
        config_dir = pipeline_dir / "config" / "banding"
        config_dir.mkdir(parents=True)
        # Write a JSON file with a Windows-1252 en-dash (0x96) — invalid UTF-8
        # With errors="replace", the 0x96 becomes U+FFFD and JSON stays valid
        (config_dir / "bands.json").write_bytes(b'{"bands": [{"label": "20\x9627"}]}')
        (pipeline_dir / "main.py").write_text(
            "import haute\nimport polars as pl\n\n"
            'pipeline = haute.Pipeline("test")\n\n'
            '@pipeline.banding(config="config/banding/bands.json")\n'
            "def bands(df: pl.LazyFrame) -> pl.LazyFrame:\n"
            "    return df\n"
        )

        from haute.parser import parse_pipeline_file

        graph = parse_pipeline_file(pipeline_dir / "main.py")
        # Pipeline parses successfully — replacement chars keep JSON valid
        assert len(graph.nodes) == 1
        config = graph.nodes[0].data.config
        assert "_load_error" not in config
        # The label contains the replacement character instead of the raw byte
        assert "\ufffd" in config["bands"][0]["label"]

    def test_format_load_error_warning_empty(self):
        """No labels should produce None."""
        from haute.parser import _format_load_error_warning

        assert _format_load_error_warning([]) is None

    def test_format_load_error_warning_truncates(self):
        """More than 3 labels should be truncated."""
        from haute.parser import _format_load_error_warning

        result = _format_load_error_warning(["a", "b", "c", "d", "e"])
        assert result is not None
        assert "a, b, c" in result
        assert "and 2 more" in result
