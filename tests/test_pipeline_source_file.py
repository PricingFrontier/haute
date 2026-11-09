"""A graph's source file decides where the pipeline's utility imports resolve.

Covers:
  - _ensure_source_file fills a graph's source_file from haute.toml
  - _compile_preamble resolves utility imports against the pipeline directory
  - graph_pipeline_dir derives the pipeline directory from the source file
  - the preview route fills in source_file before executing a preamble
"""

from __future__ import annotations

import polars as pl
import pytest

from haute._types import GraphEdge, GraphNode, NodeData, NodeType, PipelineGraph
from haute.executor import PreambleError, _compile_preamble
from tests._project import use_project
from tests.conftest import (
    make_file_input_config,
    make_ready_file_input_config,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _e(src: str, tgt: str) -> GraphEdge:
    return GraphEdge(id=f"e_{src}_{tgt}", source=src, target=tgt)


def _source_node(
    nid: str,
    path: str = "data.parquet",
    *,
    ready: bool = False,
) -> GraphNode:
    config = make_ready_file_input_config(path) if ready else make_file_input_config(path)
    return GraphNode(
        id=nid,
        data=NodeData(label=nid, nodeType=NodeType.DATA_INPUT, config=config),
    )


def _make_graph(
    nodes: list[GraphNode],
    edges: list[GraphEdge],
    source_file: str | None = None,
) -> PipelineGraph:
    return PipelineGraph(nodes=nodes, edges=edges, source_file=source_file)


# ---------------------------------------------------------------------------
# _ensure_source_file — pipeline route helper
# ---------------------------------------------------------------------------


class TestEnsureSourceFile:
    """Verify _ensure_source_file fills in source_file from haute.toml."""

    def test_noop_when_source_file_already_set(self, tmp_path, monkeypatch):
        """Should not overwrite an existing source_file."""
        from haute.routes.pipeline import _ensure_source_file

        use_project(tmp_path)
        (tmp_path / "haute.toml").write_text('[project]\npipeline = "other/main.py"\n')

        graph = PipelineGraph(source_file="existing/pipeline.py")
        _ensure_source_file(graph)
        assert graph.source_file == "existing/pipeline.py"

    def test_fills_from_haute_toml(self, tmp_path, monkeypatch):
        """Should read pipeline path from haute.toml when source_file is None."""
        from haute.routes.pipeline import _ensure_source_file

        use_project(tmp_path)
        (tmp_path / "haute.toml").write_text('[project]\npipeline = "rating/main.py"\n')

        graph = PipelineGraph()
        assert graph.source_file is None
        _ensure_source_file(graph)
        assert graph.source_file == "rating/main.py"

    def test_noop_when_no_haute_toml(self, tmp_path, monkeypatch):
        """Should leave source_file as None when haute.toml doesn't exist."""
        from haute.routes.pipeline import _ensure_source_file

        use_project(tmp_path)

        graph = PipelineGraph()
        _ensure_source_file(graph)
        assert graph.source_file is None

    def test_noop_when_toml_has_no_pipeline_key(self, tmp_path, monkeypatch):
        """Should leave source_file as None when [project].pipeline is missing."""
        from haute.routes.pipeline import _ensure_source_file

        use_project(tmp_path)
        (tmp_path / "haute.toml").write_text("[project]\nname = 'test'\n")

        graph = PipelineGraph()
        _ensure_source_file(graph)
        assert graph.source_file is None

    def test_handles_malformed_toml_gracefully(self, tmp_path, monkeypatch):
        """Should not crash on malformed haute.toml."""
        from haute.routes.pipeline import _ensure_source_file

        use_project(tmp_path)
        (tmp_path / "haute.toml").write_text("this is not valid toml {{{{")

        graph = PipelineGraph()
        _ensure_source_file(graph)
        assert graph.source_file is None

    def test_fills_empty_string_source_file(self, tmp_path, monkeypatch):
        """Should treat empty string source_file the same as None."""
        from haute.routes.pipeline import _ensure_source_file

        use_project(tmp_path)
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
        use_project(tmp_path)

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
        use_project(tmp_path)

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

    def test_utility_beside_the_pipeline_imports_without_editing_sys_path(
        self, tmp_path, monkeypatch
    ):
        """The utility finder reads the pipeline's folder; sys.path never gains it."""
        import sys

        from haute._project_imports import evict_utility_modules

        pipeline_dir = tmp_path / "sub"
        pipeline_dir.mkdir()
        util_dir = pipeline_dir / "utility"
        util_dir.mkdir()
        (util_dir / "__init__.py").write_text("VAL = 99\n")
        use_project(tmp_path)
        evict_utility_modules()
        before = list(sys.path)

        ns = _compile_preamble(
            "from utility import VAL\n",
            pipeline_dir=str(pipeline_dir),
        )

        assert ns["VAL"] == 99
        assert sys.path == before
        evict_utility_modules()


# ---------------------------------------------------------------------------
# _pipeline_dir — derives directory from source_file
# ---------------------------------------------------------------------------


class TestPipelineDir:
    """Verify _pipeline_dir correctly derives the pipeline directory."""

    def test_returns_none_when_no_source_file(self):
        from haute._project_context import graph_pipeline_dir

        graph = PipelineGraph()
        assert graph_pipeline_dir(graph) is None

    def test_returns_none_for_empty_source_file(self):
        from haute._project_context import graph_pipeline_dir

        graph = PipelineGraph(source_file="")
        assert graph_pipeline_dir(graph) is None

    def test_returns_parent_for_relative_path(self, monkeypatch, tmp_path):
        from haute._project_context import graph_pipeline_dir

        use_project(tmp_path)
        (tmp_path / "rating").mkdir()
        (tmp_path / "rating" / "main.py").write_text("")

        graph = PipelineGraph(source_file="rating/main.py")
        result = graph_pipeline_dir(graph)
        assert result is not None
        assert result.name == "rating"

    def test_returns_parent_for_absolute_path(self, tmp_path):
        from haute._project_context import graph_pipeline_dir

        pipeline_file = tmp_path / "myproject" / "pipeline.py"
        pipeline_file.parent.mkdir(parents=True, exist_ok=True)
        pipeline_file.write_text("")

        graph = PipelineGraph(source_file=str(pipeline_file))
        result = graph_pipeline_dir(graph)
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
        use_project(tmp_path)
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
        from haute._project_context import graph_pipeline_dir

        pdir = graph_pipeline_dir(graph)
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

        use_project(tmp_path)
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
                        config=make_ready_file_input_config("data/quotes.parquet"),
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

        use_project(tmp_path)
        data_path = tmp_path / "claims.parquet"
        pl.DataFrame({"x": [1, 2]}).write_parquet(data_path)

        graph = _make_graph(
            nodes=[
                _source_node("src", path="claims.parquet", ready=True),
                GraphNode(
                    id="calc",
                    data=NodeData(
                        label="Calc",
                        nodeType=NodeType.POLARS,
                        config={"code": "df = src.with_columns(y=pl.col('x') * 2)"},
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

        use_project(tmp_path)
        data_path = tmp_path / "claims.parquet"
        pl.DataFrame({"x": [1, 2, 3, 4]}).write_parquet(data_path)

        graph = _make_graph(
            nodes=[
                _source_node("src", path="claims.parquet", ready=True),
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
        # The row limit applies to the Explore output, so the filter sees every source row.
        assert body["preview"] == [
            {"x": 2, "scaled": 20},
            {"x": 3, "scaled": 30},
            {"x": 4, "scaled": 40},
        ]
