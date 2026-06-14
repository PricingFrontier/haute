"""Tests for the SavePipelineService (routes/_save_pipeline.py).

Covers:
  - SavePipelineService.save() with simple single-file graph
  - _validate_singletons() with valid, duplicate, and missing singletons
  - _write_code() with submodel multi-file
  - _remove_stale_config_files() with stale, fresh, and empty config dirs
  - _resolve_source_file() validation
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from haute._types import GraphNode, NodeData, PipelineGraph
from haute.routes._save_pipeline import SavePipelineService
from haute.schemas import SavePipelineRequest
from tests.conftest import make_edge as _make_edge

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_node(
    nid: str,
    label: str,
    node_type: str = "polars",
    config: dict | None = None,
) -> GraphNode:
    return GraphNode(
        id=nid,
        data=NodeData(label=label, nodeType=node_type, config=config or {}),
    )


def _make_graph(*nodes: GraphNode, edges: list | None = None) -> PipelineGraph:
    return PipelineGraph(nodes=list(nodes), edges=edges or [])


# ---------------------------------------------------------------------------
# _validate_singletons
# ---------------------------------------------------------------------------


class TestValidateSingletons:
    def test_valid_graph_single_api_input(self) -> None:
        """A graph with exactly one of each singleton type passes."""
        graph = _make_graph(
            _make_node("a", "Api Input", "apiInput", {"path": "data.parquet"}),
            _make_node("o", "Output", "output", {"fields": []}),
            _make_node("t", "Transform", "polars"),
        )
        # Should not raise
        SavePipelineService._validate_singletons(graph)

    def test_duplicate_api_input_raises_400(self) -> None:
        """Two API Input nodes should raise 400."""
        graph = _make_graph(
            _make_node("a1", "Api 1", "apiInput", {"path": "d1.parquet"}),
            _make_node("a2", "Api 2", "apiInput", {"path": "d2.parquet"}),
        )
        with pytest.raises(HTTPException) as exc_info:
            SavePipelineService._validate_singletons(graph)
        assert exc_info.value.status_code == 400
        assert "API Input" in exc_info.value.detail
        assert "found 2" in exc_info.value.detail

    def test_duplicate_output_raises_400(self) -> None:
        """Two Output nodes should raise 400."""
        graph = _make_graph(
            _make_node("o1", "Out 1", "output", {"fields": []}),
            _make_node("o2", "Out 2", "output", {"fields": []}),
        )
        with pytest.raises(HTTPException) as exc_info:
            SavePipelineService._validate_singletons(graph)
        assert exc_info.value.status_code == 400
        assert "Output" in exc_info.value.detail

    def test_duplicate_live_switch_raises_400(self) -> None:
        """Two Live Switch nodes should raise 400."""
        graph = _make_graph(
            _make_node("ls1", "Switch 1", "liveSwitch", {"live": "a", "batch": "b"}),
            _make_node("ls2", "Switch 2", "liveSwitch", {"live": "c", "batch": "d"}),
        )
        with pytest.raises(HTTPException) as exc_info:
            SavePipelineService._validate_singletons(graph)
        assert exc_info.value.status_code == 400
        assert "Source Switch" in exc_info.value.detail

    def test_no_singletons_passes(self) -> None:
        """A graph with only transform nodes passes validation."""
        graph = _make_graph(
            _make_node("t1", "T1", "polars"),
            _make_node("t2", "T2", "polars"),
        )
        SavePipelineService._validate_singletons(graph)

    def test_empty_graph_passes(self) -> None:
        """An empty graph passes singleton validation."""
        graph = _make_graph()
        SavePipelineService._validate_singletons(graph)


# ---------------------------------------------------------------------------
# _validate_unique_sanitized_names
# ---------------------------------------------------------------------------


class TestValidateUniqueSanitizedNames:
    def test_distinct_labels_pass(self) -> None:
        """Nodes with distinct sanitized names pass validation."""
        graph = _make_graph(
            _make_node("a", "Alpha", "polars"),
            _make_node("b", "Beta", "polars"),
        )
        SavePipelineService._validate_unique_sanitized_names(graph)

    def test_dash_underscore_collision_raises_400(self) -> None:
        """'my-node' and 'my_node' both sanitize to 'my_node'."""
        graph = _make_graph(
            _make_node("a", "my-node", "polars"),
            _make_node("b", "my_node", "polars"),
        )
        with pytest.raises(HTTPException) as exc_info:
            SavePipelineService._validate_unique_sanitized_names(graph)
        assert exc_info.value.status_code == 400
        assert "my_node" in exc_info.value.detail

    def test_identical_labels_raises_400(self) -> None:
        """Two nodes with the exact same label collide."""
        graph = _make_graph(
            _make_node("a", "Transform", "polars"),
            _make_node("b", "Transform", "polars"),
        )
        with pytest.raises(HTTPException) as exc_info:
            SavePipelineService._validate_unique_sanitized_names(graph)
        assert exc_info.value.status_code == 400
        assert "Transform" in exc_info.value.detail

    def test_space_underscore_collision_raises_400(self) -> None:
        """'my node' and 'my_node' both sanitize to 'my_node'."""
        graph = _make_graph(
            _make_node("a", "my node", "polars"),
            _make_node("b", "my_node", "polars"),
        )
        with pytest.raises(HTTPException) as exc_info:
            SavePipelineService._validate_unique_sanitized_names(graph)
        assert exc_info.value.status_code == 400
        assert "my_node" in exc_info.value.detail

    def test_three_way_collision_raises_400(self) -> None:
        """Three labels that all sanitize to the same name."""
        graph = _make_graph(
            _make_node("a", "my-node", "polars"),
            _make_node("b", "my_node", "polars"),
            _make_node("c", "my node", "polars"),
        )
        with pytest.raises(HTTPException) as exc_info:
            SavePipelineService._validate_unique_sanitized_names(graph)
        assert exc_info.value.status_code == 400
        assert "my_node" in exc_info.value.detail

    def test_unicode_distinct_labels_do_not_collide(self) -> None:
        """Post Wave 9D #123: non-ASCII chars are reversibly encoded, so
        ``café`` and ``caf`` no longer collide.  The sanitiser maps them
        to distinct identifiers (``caf_xe9_`` vs ``caf``) so the
        save-pipeline validator accepts them both.
        """
        graph = _make_graph(
            _make_node("a", "café", "polars"),
            _make_node("b", "caf", "polars"),
        )
        # Must not raise — the labels are now distinct after sanitisation.
        SavePipelineService._validate_unique_sanitized_names(graph)

    def test_empty_labels_collide(self) -> None:
        """Multiple nodes with empty labels all sanitize to 'unnamed_node'."""
        graph = _make_graph(
            _make_node("a", "", "polars"),
            _make_node("b", "", "polars"),
        )
        with pytest.raises(HTTPException) as exc_info:
            SavePipelineService._validate_unique_sanitized_names(graph)
        assert exc_info.value.status_code == 400
        assert "unnamed_node" in exc_info.value.detail

    def test_mixed_node_types_collision_raises_400(self) -> None:
        """Collision detection works across different node types."""
        graph = _make_graph(
            _make_node("a", "transform", "polars"),
            _make_node("b", "transform", "dataSource"),
        )
        with pytest.raises(HTTPException) as exc_info:
            SavePipelineService._validate_unique_sanitized_names(graph)
        assert exc_info.value.status_code == 400

    def test_empty_graph_passes(self) -> None:
        """An empty graph has no collisions."""
        graph = _make_graph()
        SavePipelineService._validate_unique_sanitized_names(graph)


# ---------------------------------------------------------------------------
# _resolve_source_file
# ---------------------------------------------------------------------------


class TestResolveSourceFile:
    def test_empty_source_file_raises_400(self, tmp_path: Path) -> None:
        """An empty source_file string should raise 400."""
        svc = SavePipelineService(tmp_path)
        with pytest.raises(HTTPException) as exc_info:
            svc._resolve_source_file("")
        assert exc_info.value.status_code == 400
        assert "source_file" in exc_info.value.detail

    def test_valid_source_file(self, tmp_path: Path) -> None:
        """A valid relative path should resolve within the project root."""
        svc = SavePipelineService(tmp_path)
        result = svc._resolve_source_file("pipeline.py")
        assert result == (tmp_path / "pipeline.py").resolve()

    def test_traversal_raises_403(self, tmp_path: Path) -> None:
        """A path that escapes the project root should raise 403."""
        svc = SavePipelineService(tmp_path)
        with pytest.raises(HTTPException) as exc_info:
            svc._resolve_source_file("../../etc/passwd")
        assert exc_info.value.status_code == 403


# ---------------------------------------------------------------------------
# SavePipelineService.save() — end-to-end with simple graph
# ---------------------------------------------------------------------------


class TestSaveSimpleGraph:
    def test_save_single_file_graph(self, tmp_path: Path) -> None:
        """save() generates code, writes .py and .haute.json sidecar."""
        svc = SavePipelineService(tmp_path)
        graph = _make_graph(
            _make_node("src", "Source", "dataSource", {"path": "data.parquet"}),
            _make_node("t1", "Transform", "polars", {"code": "return source"}),
            edges=[_make_edge("src", "t1")],
        )
        body = SavePipelineRequest(
            name="my_pipeline",
            description="Test pipeline",
            graph=graph,
            source_file="my_pipeline.py",
            sources=["live"],
            active_source="live",
        )

        with patch.object(svc, "_validate_api_inputs_have_schemas"):
            result = svc.save(body)

        assert result.status == "saved"
        assert result.pipeline_name == "my_pipeline"
        assert "my_pipeline.py" in result.file

        # Verify .py file was created
        py_file = tmp_path / result.file
        assert py_file.exists()
        content = py_file.read_text()
        assert "Pipeline" in content

        # Verify sidecar was created
        sidecar = py_file.with_suffix(".haute.json")
        assert sidecar.exists()
        sidecar_data = json.loads(sidecar.read_text())
        assert "positions" in sidecar_data

    def test_save_repairs_single_parent_stale_inputs_by_parent_key(self, tmp_path: Path) -> None:
        """Browser saves should follow current UI edges, not stale ownership metadata."""
        svc = SavePipelineService(tmp_path)
        graph = _make_graph(
            _make_node(
                "edgeJoin_10",
                "join_premiums",
                "dataSource",
                {"path": "premiums.parquet"},
            ),
            _make_node(
                "consumer",
                "consumer",
                "polars",
                {
                    "code": "df = join_premiums.with_columns(pl.col('premium'))",
                    "contract": {
                        "inputs": ["premium", "quote_id"],
                        "outputs": [],
                        "inputs_by_parent": {
                            "join_policy_data": ["premium", "quote_id"],
                        },
                    },
                },
            ),
            edges=[_make_edge("edgeJoin_10", "consumer")],
        )
        body = SavePipelineRequest(
            name="my_pipeline",
            description="",
            graph=graph,
            source_file="my_pipeline.py",
        )

        with patch.object(svc, "_validate_api_inputs_have_schemas"):
            result = svc.save(body)

        content = (tmp_path / result.file).read_text()
        assert "'inputs_by_parent': {'join_premiums': ['premium', 'quote_id']}" in content
        assert "join_policy_data" not in content

    def test_save_returns_relative_file_path(self, tmp_path: Path) -> None:
        """The returned file path should be relative to project root."""
        svc = SavePipelineService(tmp_path)
        graph = _make_graph(
            _make_node("src", "Source", "dataSource", {"path": "data.parquet"}),
        )
        body = SavePipelineRequest(
            name="test_pipe",
            description="",
            graph=graph,
            source_file="test_pipe.py",
        )

        with patch.object(svc, "_validate_api_inputs_have_schemas"):
            result = svc.save(body)

        # Should be relative, not absolute
        assert not result.file.startswith("/")

    def test_save_rejects_load_error_nodes_before_writing_code(self, tmp_path: Path) -> None:
        svc = SavePipelineService(tmp_path)
        py_file = tmp_path / "broken.py"
        py_file.write_text("# original broken source\n")
        graph = _make_graph(
            _make_node("src", "Source", "dataSource", {"path": "data.parquet"}),
            _make_node(
                "bad",
                "Broken Transform",
                "polars",
                {"_load_error": "body could not be parsed"},
            ),
            edges=[_make_edge("src", "bad")],
        )
        body = SavePipelineRequest(
            name="broken",
            description="",
            graph=graph,
            source_file="broken.py",
        )

        with pytest.raises(HTTPException) as exc_info:
            svc.save(body)

        assert exc_info.value.status_code == 400
        assert "failed to load or parse" in exc_info.value.detail
        assert py_file.read_text() == "# original broken source\n"


# ---------------------------------------------------------------------------
# _write_code with submodels
# ---------------------------------------------------------------------------


class TestWriteCodeMultiFile:
    def test_submodel_creates_module_file(self, tmp_path: Path) -> None:
        """When graph has submodels, _write_code should create multiple files."""
        svc = SavePipelineService(tmp_path)

        main_node = _make_node("sub", "submodel__scoring", "submodel")
        graph = _make_graph(main_node)
        # Set up submodels dict so the multi-file path is taken
        graph.submodels = {
            "scoring": {
                "nodes": [
                    {"id": "s1", "data": {"label": "S1", "nodeType": "polars", "config": {}}},
                ],
                "edges": [],
            },
        }

        fake_files = {
            "main.py": "# main pipeline\nimport haute\n",
            "modules/scoring.py": "# scoring submodel\nimport haute\n",
        }

        body = SavePipelineRequest(
            name="main",
            description="",
            graph=graph,
            source_file="main.py",
        )

        with patch("haute.codegen.graph_to_code_multi", return_value=fake_files):
            svc._write_code(body, graph, tmp_path / "main.py")

        assert (tmp_path / "main.py").exists()
        assert (tmp_path / "modules" / "scoring.py").exists()
        assert "scoring submodel" in (tmp_path / "modules" / "scoring.py").read_text()

    def test_single_file_no_submodels(self, tmp_path: Path) -> None:
        """Without submodels, _write_code generates a single file via real codegen."""
        svc = SavePipelineService(tmp_path)
        graph = _make_graph(
            _make_node("src", "Source", "dataSource", {"path": "data.parquet"}),
        )
        body = SavePipelineRequest(
            name="pipe",
            description="",
            graph=graph,
            source_file="pipe.py",
        )
        py_path = tmp_path / "pipe.py"

        svc._write_code(body, graph, py_path)

        assert py_path.exists()
        code = py_path.read_text()
        assert "import haute" in code
        assert "pipe" in code  # pipeline name

    def test_submodel_path_traversal_rejected(self, tmp_path: Path) -> None:
        """Phase 1C #12: codegen outputs with traversal are rejected loudly.

        Previously the loop silently ``continue``d on paths that escaped
        the project root, which masked codegen bugs and relied on a
        post-``resolve()`` check that symlinks could bypass.  The fixed
        ``_write_code`` raises HTTP 400 before any filesystem write,
        and the surrounding save transaction rolls back.
        """
        svc = SavePipelineService(tmp_path)
        graph = _make_graph()
        graph.submodels = {"evil": {"nodes": [], "edges": []}}

        # Produce a file path that would escape the project root
        fake_files = {
            "main.py": "# ok\n",
            "../../etc/evil.py": "# evil\n",
        }

        body = SavePipelineRequest(
            name="main",
            description="",
            graph=graph,
            source_file="main.py",
        )

        with patch("haute.codegen.graph_to_code_multi", return_value=fake_files):
            with pytest.raises(HTTPException) as exc_info:
                svc._write_code(body, graph, tmp_path / "main.py")
        assert exc_info.value.status_code == 400
        # The traversal path must not have been written anywhere.
        assert not Path("/etc/evil.py").exists()


# ---------------------------------------------------------------------------
# _remove_stale_config_files
# ---------------------------------------------------------------------------


class TestWriteConfigFiles:
    def test_writes_config_files_from_embedded_submodel_graph(self, tmp_path: Path) -> None:
        """Submodel route saves still need child-node configs materialised."""
        svc = SavePipelineService(tmp_path)
        graph = _make_graph(
            _make_node("src", "source", "dataSource", {"path": "data.parquet"}),
        )
        child = _make_node("banding", "child_banding", "banding", {"bands": []})
        graph.submodels = {
            "pricing": {
                "file": "modules/pricing.py",
                "graph": {"nodes": [child.model_dump(mode="json")], "edges": []},
            }
        }

        svc._write_config_files(graph)

        assert (tmp_path / "config" / "data_source" / "source.json").exists()
        assert (tmp_path / "config" / "banding" / "child_banding.json").exists()

    def test_writes_config_files_from_nested_submodel_graph(self, tmp_path: Path) -> None:
        """Config collection follows nested submodel metadata recursively."""
        svc = SavePipelineService(tmp_path)
        deep_child = _make_node("deep", "deep_banding", "banding", {"bands": []})
        graph = _make_graph()
        graph.submodels = {
            "outer": {
                "file": "modules/outer.py",
                "graph": {
                    "nodes": [],
                    "edges": [],
                    "submodels": {
                        "inner": {
                            "file": "modules/inner.py",
                            "graph": {
                                "nodes": [deep_child.model_dump(mode="json")],
                                "edges": [],
                            },
                        }
                    },
                },
            }
        }

        svc._write_config_files(graph)

        assert (tmp_path / "config" / "banding" / "deep_banding.json").exists()

    def test_duplicate_submodel_config_path_fails_before_write(self, tmp_path: Path) -> None:
        """Parent and embedded submodels must not race for one sidecar path."""
        svc = SavePipelineService(tmp_path)
        graph = _make_graph(
            _make_node("parent", "Shared", "dataSource", {"path": "parent.csv"}),
        )
        child = _make_node("child", "Shared", "dataSource", {"path": "child.csv"})
        graph.submodels = {
            "pricing": {
                "file": "modules/pricing.py",
                "graph": {"nodes": [child.model_dump(mode="json")], "edges": []},
            }
        }

        with pytest.raises(HTTPException) as exc_info:
            svc._write_config_files(graph)

        assert exc_info.value.status_code == 400
        assert "Duplicate config sidecar path" in exc_info.value.detail
        assert not (tmp_path / "config" / "data_source" / "Shared.json").exists()

    def test_duplicate_config_path_inside_one_submodel_fails_before_write(
        self,
        tmp_path: Path,
    ) -> None:
        svc = SavePipelineService(tmp_path)
        graph = _make_graph()
        child_a = _make_node("child_a", "Shared", "dataSource", {"path": "a.csv"})
        child_b = _make_node("child_b", "Shared", "dataSource", {"path": "b.csv"})
        graph.submodels = {
            "pricing": {
                "file": "modules/pricing.py",
                "graph": {
                    "nodes": [
                        child_a.model_dump(mode="json"),
                        child_b.model_dump(mode="json"),
                    ],
                    "edges": [],
                },
            }
        }

        with pytest.raises(HTTPException) as exc_info:
            svc._write_config_files(graph)

        assert exc_info.value.status_code == 400
        assert "Duplicate config sidecar path" in exc_info.value.detail
        assert not (tmp_path / "config" / "data_source" / "Shared.json").exists()

    def test_writable_config_conflicting_with_load_error_fails_before_write(
        self,
        tmp_path: Path,
    ) -> None:
        svc = SavePipelineService(tmp_path)
        config_path = tmp_path / "config" / "data_source" / "Shared.json"
        config_path.parent.mkdir(parents=True)
        config_path.write_text("{ broken json")
        graph = _make_graph(
            _make_node("parent", "Shared", "dataSource", {"path": "parent.csv"}),
        )
        child = _make_node("child", "Shared", "dataSource", {"_load_error": "duplicate key"})
        graph.submodels = {
            "pricing": {
                "file": "modules/pricing.py",
                "graph": {"nodes": [child.model_dump(mode="json")], "edges": []},
            }
        }

        with pytest.raises(HTTPException) as exc_info:
            svc._write_config_files(graph)

        assert exc_info.value.status_code == 400
        assert "Duplicate config sidecar path" in exc_info.value.detail
        assert config_path.read_text() == "{ broken json"

    def test_stale_cleanup_removes_dropped_submodel_child_config(self, tmp_path: Path) -> None:
        """Submodel child configs are owned and stale-cleaned like parent configs."""
        svc = SavePipelineService(tmp_path)
        graph_with_child = _make_graph()
        child = _make_node("banding", "child_banding", "banding", {"bands": []})
        graph_with_child.submodels = {
            "pricing": {
                "file": "modules/pricing.py",
                "graph": {"nodes": [child.model_dump(mode="json")], "edges": []},
            }
        }
        graph_without_child = _make_graph()
        graph_without_child.submodels = {
            "pricing": {"file": "modules/pricing.py", "graph": {"nodes": [], "edges": []}}
        }

        svc._write_config_files(graph_with_child)
        child_config = tmp_path / "config" / "banding" / "child_banding.json"
        assert child_config.exists()

        svc._prev_config_files = svc._collect_node_configs_recursive(graph_with_child)
        svc._write_config_files(graph_without_child)
        svc._remove_stale_config_files(graph_without_child)

        assert not child_config.exists()

    def test_prev_config_baseline_includes_submodel_child_configs(self, tmp_path: Path) -> None:
        """The on-disk ownership baseline uses the same recursive collector."""
        svc = SavePipelineService(tmp_path)
        py_path = tmp_path / "pipeline.py"
        py_path.write_text("# parsed by patched helper\n")
        child = _make_node("banding", "child_banding", "banding", {"bands": []})
        disk_graph = _make_graph()
        disk_graph.submodels = {
            "pricing": {
                "file": "modules/pricing.py",
                "graph": {"nodes": [child.model_dump(mode="json")], "edges": []},
            }
        }

        with patch("haute.routes._helpers.parse_pipeline_to_graph", return_value=disk_graph):
            prev = svc._compute_disk_prev_config_files(py_path)

        assert "config/banding/child_banding.json" in prev

    def test_duplicate_disk_baseline_disables_stale_cleanup(self, tmp_path: Path) -> None:
        """Old duplicate on-disk graphs should not block saving a corrected graph."""
        svc = SavePipelineService(tmp_path)
        py_path = tmp_path / "pipeline.py"
        py_path.write_text("# parsed by patched helper\n")
        parent = _make_node("parent", "Shared", "dataSource", {"path": "parent.csv"})
        child = _make_node("child", "Shared", "dataSource", {"path": "child.csv"})
        disk_graph = _make_graph(parent)
        disk_graph.submodels = {
            "pricing": {
                "file": "modules/pricing.py",
                "graph": {"nodes": [child.model_dump(mode="json")], "edges": []},
            }
        }

        with patch("haute.routes._helpers.parse_pipeline_to_graph", return_value=disk_graph):
            prev = svc._compute_disk_prev_config_files(py_path)

        assert prev == {}

    def test_stale_cleanup_protects_submodel_child_config_load_errors(self, tmp_path: Path) -> None:
        """Corrupt child configs skipped on write must be protected from cleanup."""
        svc = SavePipelineService(tmp_path)
        config_dir = tmp_path / "config" / "banding"
        config_dir.mkdir(parents=True)
        child_config = config_dir / "child_banding.json"
        child_config.write_text("{ broken json")

        child = _make_node(
            "banding",
            "child_banding",
            "banding",
            {"_load_error": "duplicate key"},
        )
        graph = _make_graph()
        graph.submodels = {
            "pricing": {
                "file": "modules/pricing.py",
                "graph": {"nodes": [child.model_dump(mode="json")], "edges": []},
            }
        }

        svc._prev_config_files = {"config/banding/child_banding.json": "{ broken json"}
        svc._write_config_files(graph)
        svc._remove_stale_config_files(graph)

        assert child_config.exists()


class TestRemoveStaleConfigFiles:
    """Diff-based cleanup contract.

    Bundle 6 sub-task C reworked this code path: `_prev_config_files`
    is computed by `save()` from the on-disk graph BEFORE any writes,
    and `_remove_stale_config_files` consumes that pre-computed prev
    via plain diff (`stale = prev - current - protected`).  The
    previous full-scan fallback that deleted unknown files when prev
    was empty has been removed because it actively violated the trust
    model (`notes-haute/security/SECURITY.md` §3).

    These unit tests exercise `_remove_stale_config_files` in
    isolation by setting `_prev_config_files` directly.  End-to-end
    coverage of the "compute prev from disk" path lives in
    `tests/test_bundle6_trust_model_cleanup.py`.
    """

    def test_removes_stale_config_file(self, tmp_path: Path) -> None:
        """A file in prev but not current is deleted (the diff target)."""
        svc = SavePipelineService(tmp_path)

        stale_dir = tmp_path / "config" / "banding"
        stale_dir.mkdir(parents=True)
        stale_file = stale_dir / "old_banding.json"
        stale_file.write_text("{}")

        # Simulate "prev save wrote this file" — it's now in haute's
        # ownership claim and therefore eligible for deletion when
        # the current graph drops the corresponding node.
        svc._prev_config_files = {"config/banding/old_banding.json": "{}"}
        svc._last_config_files = {}
        svc._protected_config_files = set()

        svc._remove_stale_config_files(_make_graph())

        assert not stale_file.exists()

    def test_preserves_fresh_config_file(self, tmp_path: Path) -> None:
        """Config files that match current graph nodes should be kept."""
        svc = SavePipelineService(tmp_path)

        graph = _make_graph(
            _make_node("b1", "my_banding", "banding", {"bands": []}),
        )
        svc._write_config_files(graph)  # Writes config/banding/my_banding.json

        fresh_file = tmp_path / "config" / "banding" / "my_banding.json"
        assert fresh_file.exists(), "Config file should be written by _write_config_files"

        svc._remove_stale_config_files(graph)

        assert fresh_file.exists()

    def test_removes_empty_folder(self, tmp_path: Path) -> None:
        """Empty config-type folders are removed after the last file leaves."""
        svc = SavePipelineService(tmp_path)

        config_dir = tmp_path / "config" / "banding"
        config_dir.mkdir(parents=True)
        stale_file = config_dir / "old.json"
        stale_file.write_text("{}")

        # The stale file IS in haute's prev (haute wrote it on a
        # previous save).  Removing it leaves the folder empty,
        # which should be cleaned up too.
        svc._prev_config_files = {"config/banding/old.json": "{}"}
        svc._last_config_files = {}
        svc._protected_config_files = set()

        svc._remove_stale_config_files(_make_graph())

        assert not config_dir.exists()
        # Config dir itself should be cleaned up too
        assert not (tmp_path / "config").exists()

    def test_no_config_dir_noop(self, tmp_path: Path) -> None:
        """If config/ doesn't exist and prev is empty, the method is a no-op."""
        svc = SavePipelineService(tmp_path)
        graph = _make_graph()
        svc._write_config_files(graph)
        # _prev_config_files unset → treated as empty → diff is empty → no-op.
        svc._remove_stale_config_files(graph)

    def test_mixed_stale_and_fresh(self, tmp_path: Path) -> None:
        """Diff cleanup is selective: stale (prev∖current) removed,
        fresh (∈current) and unknown (∉prev) both preserved.

        The "unknown preserved" half of this contract is what
        distinguishes the new behaviour from the dropped full-scan
        fallback; see SECURITY.md §3 for the trust-model rationale.
        """
        svc = SavePipelineService(tmp_path)

        # Active node → fresh config (written by _write_config_files).
        graph = _make_graph(
            _make_node("b1", "current_banding", "banding", {"bands": []}),
        )
        svc._write_config_files(graph)

        config_dir = tmp_path / "config" / "banding"
        fresh = config_dir / "current_banding.json"
        assert fresh.exists()

        # Two extra files on disk: one haute previously wrote (in
        # prev → eligible for diff deletion), one unknown (not in
        # prev → preserved).
        stale_known = config_dir / "old_banding.json"
        stale_known.write_text("{}")
        unknown_orphan = config_dir / "manual_orphan.json"
        unknown_orphan.write_text(json.dumps({"manual": True}))

        svc._prev_config_files = {
            "config/banding/current_banding.json": "{}",
            "config/banding/old_banding.json": "{}",
            # NOTE: manual_orphan deliberately absent from prev.
        }
        svc._protected_config_files = set()

        svc._remove_stale_config_files(graph)

        assert fresh.exists(), "Active node's config was wrongly deleted"
        assert not stale_known.exists(), (
            "Diff cleanup failed: a file in prev∖current should be deleted"
        )
        assert unknown_orphan.exists(), (
            "Trust-model violation: a file NOT in prev (manual edit, other "
            "tool, older haute version) must be preserved — haute can only "
            "delete files it previously claimed ownership of via writing them"
        )
        assert config_dir.exists()


# ---------------------------------------------------------------------------
# Integration: save via HTTP endpoint (uses TestClient)
# ---------------------------------------------------------------------------


class TestSaveEndpointIntegration:
    @pytest.fixture()
    def client(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
        monkeypatch.chdir(tmp_path)
        from haute.server import app

        return TestClient(app)

    def test_save_via_http(self, client: TestClient, tmp_path: Path) -> None:
        """POST /api/pipeline/save produces a working .py file."""
        graph = {
            "nodes": [
                {
                    "id": "src",
                    "type": "pipelineNode",
                    "position": {"x": 0, "y": 0},
                    "data": {
                        "label": "Source",
                        "nodeType": "dataSource",
                        "config": {"path": "data.parquet"},
                    },
                },
                {
                    "id": "t1",
                    "type": "pipelineNode",
                    "position": {"x": 200, "y": 0},
                    "data": {
                        "label": "Transform",
                        "nodeType": "polars",
                        "config": {},
                    },
                },
            ],
            "edges": [{"id": "e1", "source": "src", "target": "t1"}],
        }
        resp = client.post(
            "/api/pipeline/save",
            json={
                "name": "saved_test",
                "description": "Integration test",
                "graph": graph,
                "source_file": "saved_test.py",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "saved"
        assert "saved_test.py" in data["file"]

    def test_save_duplicate_api_input_returns_400(self, client: TestClient) -> None:
        """Two API Input nodes should fail validation at 400."""
        graph = {
            "nodes": [
                {
                    "id": "a1",
                    "type": "pipelineNode",
                    "position": {"x": 0, "y": 0},
                    "data": {
                        "label": "Api1",
                        "nodeType": "apiInput",
                        "config": {"path": "d.parquet"},
                    },
                },
                {
                    "id": "a2",
                    "type": "pipelineNode",
                    "position": {"x": 200, "y": 0},
                    "data": {
                        "label": "Api2",
                        "nodeType": "apiInput",
                        "config": {"path": "d2.parquet"},
                    },
                },
            ],
            "edges": [],
        }
        resp = client.post(
            "/api/pipeline/save",
            json={
                "name": "bad_pipe",
                "description": "",
                "graph": graph,
                "source_file": "bad_pipe.py",
            },
        )
        assert resp.status_code == 400
        assert "API Input" in resp.json()["detail"]

    def test_save_empty_source_file_returns_400(self, client: TestClient) -> None:
        """Empty source_file should return 400."""
        graph = {
            "nodes": [
                {
                    "id": "s",
                    "type": "pipelineNode",
                    "position": {"x": 0, "y": 0},
                    "data": {
                        "label": "S",
                        "nodeType": "dataSource",
                        "config": {"path": "d.parquet"},
                    },
                },
            ],
            "edges": [],
        }
        resp = client.post(
            "/api/pipeline/save",
            json={
                "name": "pipe",
                "description": "",
                "graph": graph,
                "source_file": "",
            },
        )
        assert resp.status_code == 400
        assert "source_file" in resp.json()["detail"]

    def test_save_edge_join_missing_keys_returns_400(
        self,
        client: TestClient,
        tmp_path: Path,
    ) -> None:
        """edgeJoin codegen ConfigError must surface as save validation, not 500."""
        graph = {
            "nodes": [
                {
                    "id": "quotes",
                    "type": "pipelineNode",
                    "position": {"x": 0, "y": 0},
                    "data": {
                        "label": "Quotes",
                        "nodeType": "constant",
                        "config": {"values": [{"name": "region", "value": "N"}]},
                    },
                },
                {
                    "id": "lookup",
                    "type": "pipelineNode",
                    "position": {"x": 0, "y": 120},
                    "data": {
                        "label": "Lookup",
                        "nodeType": "constant",
                        "config": {"values": [{"name": "region", "value": "N"}]},
                    },
                },
                {
                    "id": "join",
                    "type": "pipelineNode",
                    "position": {"x": 240, "y": 60},
                    "data": {
                        "label": "Join Rates",
                        "nodeType": "edgeJoin",
                        "config": {
                            "baseInput": "quotes",
                            "joinInput": "lookup",
                            "how": "left",
                        },
                    },
                },
            ],
            "edges": [
                {
                    "id": "e_quotes_join",
                    "source": "quotes",
                    "target": "join",
                    "targetHandle": "base",
                },
                {
                    "id": "e_lookup_join",
                    "source": "lookup",
                    "target": "join",
                    "targetHandle": "join",
                },
            ],
        }
        resp = client.post(
            "/api/pipeline/save",
            json={
                "name": "bad_edge_join",
                "description": "",
                "graph": graph,
                "source_file": "bad_edge_join.py",
            },
        )

        assert resp.status_code == 400
        assert "edgeJoin non-cross joins require join keys" in resp.json()["detail"]
        assert not (tmp_path / "bad_edge_join.py").exists()


# ---------------------------------------------------------------------------
# _infer_flatten_schemas
# ---------------------------------------------------------------------------


class TestValidateApiInputsHaveSchemas:
    """The save hook renamed from ``_infer_flatten_schemas`` to
    ``_validate_api_inputs_have_schemas`` per the v1-removal pivot
    (commit 5.5 / D4). Behaviour inverted: no on-disk mutation; instead
    a warning string is appended to the save response for JSON apiInput
    nodes whose ``tables[]`` is empty. The user clicks Infer Tables to
    populate the schema.
    """

    def test_warns_when_json_api_input_has_no_tables(self, tmp_path: Path) -> None:
        svc = SavePipelineService(tmp_path)
        json_file = tmp_path / "input.json"
        json_file.write_text('[{"a": 1, "b": {"c": 2}}]')
        graph = _make_graph(
            _make_node("api", "api_input", "apiInput", {"path": "input.json"}),
        )
        warnings: list[str] = []
        svc._validate_api_inputs_have_schemas(graph, warnings)
        # On-disk config NOT mutated: no flattenSchema, no tables auto-written.
        cfg = graph.nodes[0].data.config
        assert "flattenSchema" not in cfg
        assert "tables" not in cfg
        # Warning surfaced for the empty-tables case.
        assert any("api_input" in w and "Infer Tables" in w for w in warnings), (
            f"expected node-label + Infer-Tables warning; got {warnings!r}"
        )

    def test_skips_non_api_input_nodes(self, tmp_path: Path) -> None:
        svc = SavePipelineService(tmp_path)
        graph = _make_graph(
            _make_node("t1", "transform", "polars", {"path": "data.json"}),
        )
        warnings: list[str] = []
        svc._validate_api_inputs_have_schemas(graph, warnings)
        assert warnings == []

    def test_skips_non_json_path(self, tmp_path: Path) -> None:
        svc = SavePipelineService(tmp_path)
        graph = _make_graph(
            _make_node("api", "api_input", "apiInput", {"path": "data.parquet"}),
        )
        warnings: list[str] = []
        svc._validate_api_inputs_have_schemas(graph, warnings)
        assert warnings == []

    def test_no_warning_when_tables_populated(self, tmp_path: Path) -> None:
        """A JSON apiInput already carrying `tables[]` is the happy path."""
        svc = SavePipelineService(tmp_path)
        graph = _make_graph(
            _make_node(
                "api",
                "api_input",
                "apiInput",
                {
                    "path": "input.json",
                    "tables": [
                        {
                            "path": "$[*]",
                            "label": "root",
                            "emit": True,
                            "columns": [],
                        }
                    ],
                },
            ),
        )
        warnings: list[str] = []
        svc._validate_api_inputs_have_schemas(graph, warnings)
        assert warnings == []

    def test_warns_when_jsonl_api_input_has_no_tables(self, tmp_path: Path) -> None:
        svc = SavePipelineService(tmp_path)
        graph = _make_graph(
            _make_node("api", "api_input", "apiInput", {"path": "input.jsonl"}),
        )
        warnings: list[str] = []
        svc._validate_api_inputs_have_schemas(graph, warnings)
        assert any("Infer Tables" in w for w in warnings)

    def test_skips_empty_path(self, tmp_path: Path) -> None:
        svc = SavePipelineService(tmp_path)
        graph = _make_graph(
            _make_node("api", "api_input", "apiInput", {"path": ""}),
        )
        warnings: list[str] = []
        svc._validate_api_inputs_have_schemas(graph, warnings)
        assert warnings == []


# ---------------------------------------------------------------------------
# _remove_stale_config_files — second-save diff path
# ---------------------------------------------------------------------------


class TestRemoveStaleConfigDiffPath:
    """Tests for the second-save path where prev config files exist."""

    def test_second_save_removes_diff(self, tmp_path: Path) -> None:
        """On second save, only files in (prev - current) are removed.

        Bundle 6 sub-task C: `_prev_config_files` is no longer rotated
        from the prior `_last_config_files` within the same
        `SavePipelineService` instance — it's snapshotted from the
        on-disk graph at the top of `save()`.  This unit test sets it
        directly to simulate the second-save case; end-to-end coverage
        of the disk-snapshot path lives in
        `tests/test_bundle6_trust_model_cleanup.py`.
        """
        svc = SavePipelineService(tmp_path)

        # First save: graph with banding node.
        graph1 = _make_graph(
            _make_node("b1", "first_banding", "banding", {"bands": []}),
        )
        svc._write_config_files(graph1)
        first_config = tmp_path / "config" / "banding" / "first_banding.json"
        assert first_config.exists()

        # Second save: graph with different banding node.  Simulate the
        # disk snapshot: prev = what the first save wrote (first_banding).
        graph2 = _make_graph(
            _make_node("b2", "second_banding", "banding", {"bands": []}),
        )
        svc._prev_config_files = {"config/banding/first_banding.json": "{}"}
        svc._write_config_files(graph2)
        svc._remove_stale_config_files(graph2)

        second_config = tmp_path / "config" / "banding" / "second_banding.json"
        assert second_config.exists()
        assert not first_config.exists()  # stale file removed

    def test_second_save_no_stale_is_noop(self, tmp_path: Path) -> None:
        """When prev and current are identical, nothing is deleted."""
        svc = SavePipelineService(tmp_path)

        graph = _make_graph(
            _make_node("b1", "stable_banding", "banding", {"bands": []}),
        )
        svc._write_config_files(graph)
        svc._remove_stale_config_files(graph)

        config_file = tmp_path / "config" / "banding" / "stable_banding.json"
        assert config_file.exists()

        # Second save with same graph
        svc._write_config_files(graph)
        svc._remove_stale_config_files(graph)

        assert config_file.exists()

    def test_stale_path_traversal_skipped(self, tmp_path: Path) -> None:
        """Stale file paths that escape project root are skipped."""
        svc = SavePipelineService(tmp_path)

        # Manually set prev_config_files with a traversal path
        svc._prev_config_files = {"../../etc/evil.json": "{}"}
        svc._last_config_files = {}
        svc._protected_config_files = set()

        graph = _make_graph()
        svc._remove_stale_config_files(graph)
        # Should not raise or delete anything outside project root

    def test_protected_config_files_preserved(self, tmp_path: Path) -> None:
        """Protected config files are not removed even when stale."""
        svc = SavePipelineService(tmp_path)

        # Create a config file
        config_dir = tmp_path / "config" / "banding"
        config_dir.mkdir(parents=True)
        protected_file = config_dir / "protected_banding.json"
        protected_file.write_text("{}")

        # Set up prev/current diff where protected file would be stale
        svc._prev_config_files = {"config/banding/protected_banding.json": "{}"}
        svc._last_config_files = {}
        svc._protected_config_files = {"config/banding/protected_banding.json"}

        graph = _make_graph()
        svc._remove_stale_config_files(graph)

        assert protected_file.exists()  # protected file survives


# ---------------------------------------------------------------------------
# _write_sidecar
# ---------------------------------------------------------------------------


class TestWriteSidecar:
    def test_writes_sidecar_with_sources(self, tmp_path: Path) -> None:
        """_write_sidecar persists source state to .haute.json."""
        graph = _make_graph(
            _make_node("src", "Source", "dataSource", {"path": "data.parquet"}),
        )
        py_path = tmp_path / "pipe.py"
        py_path.write_text("# placeholder")

        SavePipelineService._write_sidecar(
            py_path, graph, sources=["live", "batch"], active_source="live"
        )

        sidecar_path = py_path.with_suffix(".haute.json")
        assert sidecar_path.exists()
        data = json.loads(sidecar_path.read_text())
        assert "positions" in data

    def test_graph_sources_updated(self, tmp_path: Path) -> None:
        """_write_sidecar sets graph.sources and graph.active_source before saving."""
        graph = _make_graph(
            _make_node("src", "Source", "dataSource", {"path": "data.parquet"}),
        )
        py_path = tmp_path / "pipe.py"
        py_path.write_text("# placeholder")

        SavePipelineService._write_sidecar(py_path, graph, sources=["s1", "s2"], active_source="s2")

        assert graph.sources == ["s1", "s2"]
        assert graph.active_source == "s2"


# ---------------------------------------------------------------------------
# _write_code — preamble and preserved_blocks
# ---------------------------------------------------------------------------


class TestWriteCodeOptions:
    def test_preamble_passed_to_codegen(self, tmp_path: Path) -> None:
        """preamble is forwarded to graph_to_code."""
        svc = SavePipelineService(tmp_path)
        graph = _make_graph(
            _make_node("src", "Source", "dataSource", {"path": "data.parquet"}),
        )
        body = SavePipelineRequest(
            name="pipe",
            description="desc",
            graph=graph,
            source_file="pipe.py",
            preamble="# Custom preamble\n",
        )
        py_path = tmp_path / "pipe.py"

        svc._write_code(body, graph, py_path)

        assert py_path.exists()
        content = py_path.read_text()
        assert "Custom preamble" in content

    def test_none_preamble_defaults_to_empty(self, tmp_path: Path) -> None:
        """When preamble is None, it defaults to empty string."""
        svc = SavePipelineService(tmp_path)
        graph = _make_graph(
            _make_node("src", "Source", "dataSource", {"path": "data.parquet"}),
        )
        body = SavePipelineRequest(
            name="pipe",
            description="",
            graph=graph,
            source_file="pipe.py",
            preamble=None,
        )
        py_path = tmp_path / "pipe.py"

        svc._write_code(body, graph, py_path)
        assert py_path.exists()


# ---------------------------------------------------------------------------
# Full save pipeline — pipeline_root differs from project_root
# ---------------------------------------------------------------------------


class TestSaveWithPipelineRoot:
    def test_pipeline_root_defaults_to_project_root(self, tmp_path: Path) -> None:
        """When pipeline_root is not given, it defaults to project_root."""
        svc = SavePipelineService(tmp_path)
        assert svc._pipeline_root == tmp_path

    def test_pipeline_root_override(self, tmp_path: Path) -> None:
        """When pipeline_root is given, it is used instead of project_root."""
        sub = tmp_path / "subdir"
        sub.mkdir()
        svc = SavePipelineService(tmp_path, pipeline_root=sub)
        assert svc._pipeline_root == sub

    def test_pipeline_root_outside_project_root_is_rejected(self, tmp_path: Path) -> None:
        outside = tmp_path.parent / f"{tmp_path.name}_outside"
        outside.mkdir()

        with pytest.raises(ValueError, match="inside project_root"):
            SavePipelineService(tmp_path, pipeline_root=outside)
