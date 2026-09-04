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

from haute._types import GraphNode, NodeData, PipelineGraph, SubmodelDefinition
from haute.routes._save_pipeline import SavePipelineService
from haute.schemas import SavePipelineRequest
from tests.conftest import make_edge as _make_edge
from tests.conftest import make_output_config

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _file_input_config(path: str) -> dict:
    format_name, mode = {
        ".csv": ("csv", "scan"),
        ".parquet": ("parquet", "scan"),
        ".json": ("json", "read"),
        ".jsonl": ("ndjson", "scan"),
        ".ndjson": ("ndjson", "scan"),
        ".arrow": ("ipc", "scan"),
        ".feather": ("ipc", "scan"),
        ".ipc": ("ipc", "scan"),
    }[Path(path).suffix.lower()]
    return {
        "inputType": "file",
        "format": format_name,
        "mode": mode,
        "path": path,
        "arguments": {},
    }


def _make_node(
    nid: str,
    label: str,
    node_type: str = "polars",
    config: dict | None = None,
) -> GraphNode:
    if node_type == "dataInput" and config and set(config) == {"path"}:
        config = _file_input_config(config["path"])
    return GraphNode(
        id=nid,
        data=NodeData(label=label, nodeType=node_type, config=config or {}),
    )


def _submodel_definition(
    definition_id: str,
    *nodes: GraphNode,
) -> SubmodelDefinition:
    return SubmodelDefinition(
        definitionId=definition_id,
        file=f"modules/{definition_id}.py",
        graph=PipelineGraph(
            nodes=list(nodes),
            edges=[],
            pipeline_name=definition_id,
        ),
        inputPorts=[],
        outputPorts=[],
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
            _make_node("o", "Output", "output", make_output_config([])),
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

    def test_api_input_in_root_and_submodel_raises_400(self, tmp_path: Path) -> None:
        """Singletons are unique across the flattened executable pipeline."""
        graph = _make_submodel_graph(
            _make_node("child_api", "Child API", "apiInput", {"path": "child.parquet"}),
            root_nodes=(_make_node("root_api", "Root API", "apiInput", {"path": "root.parquet"}),),
        )
        with pytest.raises(HTTPException) as exc_info:
            SavePipelineService(tmp_path).validate_graph(graph, source_file="main.py")
        assert exc_info.value.status_code == 400
        assert "API Input" in exc_info.value.detail
        assert "found 2" in exc_info.value.detail

    def test_api_input_in_repeated_submodel_raises_400(self, tmp_path: Path) -> None:
        """Each occurrence contributes its singleton nodes to execution."""
        graph = _make_submodel_graph(
            _make_node("child_api", "Child API", "apiInput", {"path": "child.parquet"}),
        )
        owner_id = "submodel_instance__pricing"
        graph.nodes.append(
            _make_node(
                "submodel_instance__pricing_copy",
                "pricing copy",
                "submodel",
                {
                    "definitionId": "pricing",
                    "alias": "pricing_copy",
                    "instanceOf": owner_id,
                },
            )
        )
        with pytest.raises(HTTPException) as exc_info:
            SavePipelineService(tmp_path).validate_graph(graph, source_file="main.py")
        assert exc_info.value.status_code == 400
        assert "API Input" in exc_info.value.detail
        assert "found 2" in exc_info.value.detail

    def test_duplicate_output_raises_400(self) -> None:
        """Two Output nodes should raise 400."""
        graph = _make_graph(
            _make_node("o1", "Out 1", "output", make_output_config([])),
            _make_node("o2", "Out 2", "output", make_output_config([])),
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
            _make_node("b", "transform", "dataInput"),
        )
        with pytest.raises(HTTPException) as exc_info:
            SavePipelineService._validate_unique_sanitized_names(graph)
        assert exc_info.value.status_code == 400

    def test_empty_graph_passes(self) -> None:
        """An empty graph has no collisions."""
        graph = _make_graph()
        SavePipelineService._validate_unique_sanitized_names(graph)


# ---------------------------------------------------------------------------
# _validate_unique_sanitized_names — recursive (submodel-aware) scope
# ---------------------------------------------------------------------------


def _make_submodel_graph(
    *nodes: GraphNode,
    sm_name: str = "pricing",
    root_nodes: tuple[GraphNode, ...] = (),
    include_placeholder: bool = True,
) -> PipelineGraph:
    """Build a hierarchical graph with one embedded submodel.

    Mirrors the canonical shape ``merge_submodels`` produces: one explicit
    occurrence in the root graph plus a typed definition containing the full
    child graph.
    """
    all_root: list[GraphNode] = list(root_nodes)
    if include_placeholder:
        all_root.append(
            _make_node(
                f"submodel_instance__{sm_name}",
                sm_name,
                "submodel",
                {"definitionId": sm_name, "alias": sm_name},
            )
        )
    return PipelineGraph(
        nodes=all_root,
        edges=[],
        submodels={sm_name: _submodel_definition(sm_name, *nodes)},
    )


class TestValidateOptimiserInputSelectors:
    def test_validate_graph_rejects_stale_selector_before_writing(self, tmp_path: Path) -> None:
        graph = _make_graph(
            _make_node("quotes", "quotes", "dataInput", {"path": "quotes.parquet"}),
            _make_node(
                "apply",
                "apply",
                "optimiserApply",
                {"optimiser_mode": "ratebook", "ratebook_input": "stale"},
            ),
            edges=[_make_edge("quotes", "apply")],
        )
        service = SavePipelineService(tmp_path)

        with pytest.raises(HTTPException) as exc_info:
            service.validate_graph(graph, source_file="pipeline.py")

        assert exc_info.value.status_code == 400
        assert "ratebook_input" in exc_info.value.detail
        assert not (tmp_path / "pipeline.py").exists()


class TestValidateUniqueSanitizedNamesRecursiveScope:
    """Global (root + submodel) scope for the save-side name guard.

    Runtime is the load-bearing reason: preview/trace/run call
    ``flatten_graph`` which inlines every submodel child into ONE graph
    keyed by ``node.id`` — and ``node.id`` round-trips to the sanitised
    function name for root and submodel nodes alike
    (``_graph_builders._build_rf_nodes``).  ``PipelineGraph.node_map`` is a
    plain ``{n.id: n}`` dict, so a cross-module duplicate silently shadows
    its twin at execution time.  The save guard must therefore agree with
    codegen's ``_error_on_name_collisions`` global scope, and reject with a
    clean 400 instead of leaking codegen's ParseError as a 500.
    """

    def test_cross_module_distinct_labels_raise_400(self) -> None:
        """Root 'Foo Bar' + submodel child 'Foo-Bar' sanitize identically."""
        graph = _make_submodel_graph(
            _make_node("child", "Foo-Bar", "polars", {"code": "df"}),
            root_nodes=(_make_node("root", "Foo Bar", "polars", {"code": "df"}),),
        )
        with pytest.raises(HTTPException) as exc_info:
            SavePipelineService._validate_unique_sanitized_names(graph)
        assert exc_info.value.status_code == 400
        assert "Foo_Bar" in exc_info.value.detail

    def test_cross_module_identical_labels_raise_400(self) -> None:
        """Root 'Foo' + submodel child 'Foo': identical ids after round-trip,
        so the flattened execution graph silently drops one of them."""
        graph = _make_submodel_graph(
            _make_node("child", "Foo", "polars", {"code": "df"}),
            root_nodes=(_make_node("root", "Foo", "polars", {"code": "df"}),),
        )
        with pytest.raises(HTTPException) as exc_info:
            SavePipelineService._validate_unique_sanitized_names(graph)
        assert exc_info.value.status_code == 400
        assert "Foo" in exc_info.value.detail

    def test_collision_inside_one_submodel_raises_400(self) -> None:
        """Two colliding children INSIDE one submodel must be caught at save
        time (previously escaped the root-only guard and hit codegen's
        ParseError as a 500)."""
        graph = _make_submodel_graph(
            _make_node("c1", "my-node", "polars", {"code": "df"}),
            _make_node("c2", "my_node", "polars", {"code": "df"}),
        )
        with pytest.raises(HTTPException) as exc_info:
            SavePipelineService._validate_unique_sanitized_names(graph)
        assert exc_info.value.status_code == 400
        assert "my_node" in exc_info.value.detail

    def test_submodel_placeholder_matching_its_child_passes(self) -> None:
        """A submodel named after one of its own children is legal: the
        placeholder's runtime id is ``submodel__<name>`` (never collides)
        and no ``def`` is emitted for it.  Guard must NOT over-reject."""
        graph = _make_submodel_graph(
            _make_node("pricing", "pricing", "polars", {"code": "df"}),
            sm_name="pricing",
        )
        SavePipelineService._validate_unique_sanitized_names(graph)

    def test_root_node_vs_placeholder_same_label_still_raises_400(self) -> None:
        """Pin current root-graph semantics: a root node whose label matches
        a submodel placeholder's label collides within the root bucket."""
        graph = _make_submodel_graph(
            _make_node("child", "unrelated", "polars", {"code": "df"}),
            sm_name="pricing",
            root_nodes=(_make_node("root", "pricing", "polars", {"code": "df"}),),
        )
        with pytest.raises(HTTPException) as exc_info:
            SavePipelineService._validate_unique_sanitized_names(graph)
        assert exc_info.value.status_code == 400

    def test_child_duplicated_in_root_nodes_raises_400(self) -> None:
        """A definition-owned child duplicated in the parent would collide
        in the canonical flattened namespace, so the guard rejects it."""
        child = _make_node("a", "a", "polars", {"code": "df"})
        graph = _make_submodel_graph(
            child,
            sm_name="sm1",
            root_nodes=(child,),
            include_placeholder=False,
        )
        with pytest.raises(HTTPException) as exc_info:
            SavePipelineService._validate_unique_sanitized_names(graph)
        assert exc_info.value.status_code == 400

    def test_distinct_names_across_modules_pass(self) -> None:
        """No collision: distinct sanitized names everywhere."""
        graph = _make_submodel_graph(
            _make_node("c1", "Base Rate", "polars", {"code": "df"}),
            _make_node("c2", "Adjust", "polars", {"code": "df"}),
            root_nodes=(_make_node("root", "Load Data", "polars", {"code": "df"}),),
        )
        SavePipelineService._validate_unique_sanitized_names(graph)

    def test_save_rejects_cross_module_collision_with_400(self, tmp_path: Path) -> None:
        """End-to-end through ``save()``: the guard fires before codegen, so
        the caller sees a clean 400 (not codegen's ParseError as a 500)."""
        graph = _make_submodel_graph(
            _make_node("child", "Foo-Bar", "polars", {"code": "df"}),
            root_nodes=(_make_node("root", "Foo Bar", "polars", {"code": "df"}),),
        )
        svc = SavePipelineService(tmp_path)
        req = SavePipelineRequest(
            name="main",
            description="",
            graph=graph,
            source_file="main.py",
        )
        with pytest.raises(HTTPException) as exc_info:
            svc.save(req)
        assert exc_info.value.status_code == 400
        assert "Foo_Bar" in exc_info.value.detail


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
            _make_node("src", "Source", "dataInput", {"path": "data.parquet"}),
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

    def test_save_drops_single_parent_stale_inputs_by_parent_key(self, tmp_path: Path) -> None:
        """Browser saves must not guess ownership across a rewire (F003).

        The consumer carries stale ``inputs_by_parent`` ownership for a parent
        (``join_policy_data``) that is no longer connected — the only current
        edge comes from ``join_premiums``. Reassigning the stale columns to the
        lone current parent would fabricate ownership the graph has no evidence
        for, so the stale entry is DROPPED (not repaired) and a
        ``contract_inputs_by_parent_omitted_stale`` warning is emitted.
        """
        import structlog

        svc = SavePipelineService(tmp_path)
        graph = _make_graph(
            _make_node(
                "edgeJoin_10",
                "join_premiums",
                "dataInput",
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

        with (
            patch.object(svc, "_validate_api_inputs_have_schemas"),
            structlog.testing.capture_logs() as logs,
        ):
            result = svc.save(body)

        content = (tmp_path / result.file).read_text()
        # The stale ownership is omitted entirely rather than reassigned to
        # join_premiums — inputs_by_parent drops out of the emitted contract.
        assert "inputs_by_parent" not in content
        assert "join_policy_data" not in content
        # The declared inputs themselves are preserved.
        assert "'inputs': ['premium', 'quote_id']" in content
        # The drop is surfaced, not silent.
        omitted = [log for log in logs if log["event"] == "contract_inputs_by_parent_omitted_stale"]
        assert len(omitted) == 1
        assert omitted[0]["stale_parent_ids"] == ["join_policy_data"]

    def test_save_returns_relative_file_path(self, tmp_path: Path) -> None:
        """The returned file path should be relative to project root."""
        svc = SavePipelineService(tmp_path)
        graph = _make_graph(
            _make_node("src", "Source", "dataInput", {"path": "data.parquet"}),
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
            _make_node("src", "Source", "dataInput", {"path": "data.parquet"}),
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

        main_node = _make_node(
            "sub",
            "scoring",
            "submodel",
            {"definitionId": "scoring", "alias": "scoring"},
        )
        graph = _make_graph(main_node)
        # Set up submodels dict so the multi-file path is taken
        graph.submodels = {
            "scoring": _submodel_definition(
                "scoring",
                _make_node("s1", "S1", "polars"),
            )
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
            _make_node("src", "Source", "dataInput", {"path": "data.parquet"}),
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
        graph.submodels = {"evil": _submodel_definition("evil")}

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
            _make_node("src", "source", "dataInput", {"path": "data.parquet"}),
        )
        child = _make_node("banding", "child_banding", "banding", {"bands": []})
        graph.submodels = {"pricing": _submodel_definition("pricing", child)}

        svc._write_config_files(graph)

        assert (tmp_path / "config" / "data_input" / "source.json").exists()
        assert (tmp_path / "config" / "banding" / "child_banding.json").exists()

    def test_writes_config_files_from_submodel_graph(self, tmp_path: Path) -> None:
        """Config collection includes canonical definition graphs."""
        svc = SavePipelineService(tmp_path)
        deep_child = _make_node("deep", "deep_banding", "banding", {"bands": []})
        graph = _make_graph()
        graph.submodels = {"inner": _submodel_definition("inner", deep_child)}

        svc._write_config_files(graph)

        assert (tmp_path / "config" / "banding" / "deep_banding.json").exists()

    def test_duplicate_submodel_config_path_fails_before_write(self, tmp_path: Path) -> None:
        """Parent and embedded submodels must not race for one sidecar path."""
        svc = SavePipelineService(tmp_path)
        graph = _make_graph(
            _make_node("parent", "Shared", "dataInput", {"path": "parent.csv"}),
        )
        child = _make_node("child", "Shared", "dataInput", {"path": "child.csv"})
        graph.submodels = {"pricing": _submodel_definition("pricing", child)}

        with pytest.raises(HTTPException) as exc_info:
            svc._write_config_files(graph)

        assert exc_info.value.status_code == 400
        assert "Duplicate config sidecar path" in exc_info.value.detail
        assert not (tmp_path / "config" / "data_input" / "Shared.json").exists()

    def test_duplicate_config_path_inside_one_submodel_fails_before_write(
        self,
        tmp_path: Path,
    ) -> None:
        svc = SavePipelineService(tmp_path)
        graph = _make_graph()
        child_a = _make_node("child_a", "Shared", "dataInput", {"path": "a.csv"})
        child_b = _make_node("child_b", "Shared", "dataInput", {"path": "b.csv"})
        graph.submodels = {"pricing": _submodel_definition("pricing", child_a, child_b)}

        with pytest.raises(HTTPException) as exc_info:
            svc._write_config_files(graph)

        assert exc_info.value.status_code == 400
        assert "Duplicate config sidecar path" in exc_info.value.detail
        assert not (tmp_path / "config" / "data_input" / "Shared.json").exists()

    def test_writable_config_conflicting_with_load_error_fails_before_write(
        self,
        tmp_path: Path,
    ) -> None:
        svc = SavePipelineService(tmp_path)
        config_path = tmp_path / "config" / "data_input" / "Shared.json"
        config_path.parent.mkdir(parents=True)
        config_path.write_text("{ broken json")
        graph = _make_graph(
            _make_node("parent", "Shared", "dataInput", {"path": "parent.csv"}),
        )
        child = _make_node("child", "Shared", "dataInput", {"_load_error": "duplicate key"})
        graph.submodels = {"pricing": _submodel_definition("pricing", child)}

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
        graph_with_child.submodels = {"pricing": _submodel_definition("pricing", child)}
        graph_without_child = _make_graph()
        graph_without_child.submodels = {"pricing": _submodel_definition("pricing")}

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
        disk_graph.submodels = {"pricing": _submodel_definition("pricing", child)}

        with patch("haute.routes._helpers.parse_pipeline_to_graph", return_value=disk_graph):
            prev = svc._compute_disk_prev_config_files(py_path)

        assert "config/banding/child_banding.json" in prev

    def test_duplicate_disk_baseline_disables_stale_cleanup(self, tmp_path: Path) -> None:
        """Old duplicate on-disk graphs should not block saving a corrected graph."""
        svc = SavePipelineService(tmp_path)
        py_path = tmp_path / "pipeline.py"
        py_path.write_text("# parsed by patched helper\n")
        parent = _make_node("parent", "Shared", "dataInput", {"path": "parent.csv"})
        child = _make_node("child", "Shared", "dataInput", {"path": "child.csv"})
        disk_graph = _make_graph(parent)
        disk_graph.submodels = {"pricing": _submodel_definition("pricing", child)}

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
        graph.submodels = {"pricing": _submodel_definition("pricing", child)}

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
                        "nodeType": "dataInput",
                        "config": _file_input_config("data.parquet"),
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
                        "config": _file_input_config("d.parquet"),
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

    def test_save_parent_binding_to_unrouted_input_returns_actionable_400(
        self,
        client: TestClient,
    ) -> None:
        """A structural ParseError must reach the client, not become an opaque 500.

        Dropping onto an owner's generic input socket declares the public port
        before it is routed, so this is a state the editor reaches by an
        ordinary gesture; the message has to name the edge, occurrence and port.
        """
        graph = {
            "nodes": [
                {
                    "id": "src",
                    "type": "pipelineNode",
                    "position": {"x": 0, "y": 0},
                    "data": {
                        "label": "Source",
                        "nodeType": "dataInput",
                        "config": _file_input_config("data.parquet"),
                    },
                },
                {
                    "id": "instance_a",
                    "type": "pipelineNode",
                    "position": {"x": 200, "y": 0},
                    "data": {
                        "label": "Scoring",
                        "nodeType": "submodel",
                        "config": {
                            "definitionId": "definition_scoring",
                            "alias": "scoring",
                        },
                    },
                },
            ],
            "edges": [
                {
                    "id": "bind",
                    "source": "src",
                    "target": "instance_a",
                    "targetHandle": "in__policy",
                }
            ],
            "submodels": {
                "definition_scoring": {
                    "definitionId": "definition_scoring",
                    "file": "modules/scoring.py",
                    "graph": {
                        "nodes": [
                            {
                                "id": "child",
                                "data": {
                                    "label": "Child",
                                    "nodeType": "polars",
                                    "config": {"code": "df = df"},
                                },
                            }
                        ],
                        "edges": [],
                    },
                    "inputPorts": [{"portId": "policy", "label": "policy", "targets": []}],
                    "outputPorts": [],
                },
            },
        }
        resp = client.post(
            "/api/pipeline/save",
            json={
                "name": "unrouted_pipe",
                "description": "",
                "graph": graph,
                "source_file": "unrouted_pipe.py",
            },
        )
        assert resp.status_code == 400
        detail = resp.json()["detail"]
        assert "no internal targets" in detail
        assert "edge_id=bind" in detail
        assert "instance_id=instance_a" in detail
        assert "port_id=policy" in detail

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
                        "nodeType": "dataInput",
                        "config": _file_input_config("d.parquet"),
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

    def test_save_then_get_preserves_node_positions(
        self,
        client: TestClient,
        tmp_path: Path,
    ) -> None:
        """Round-trip: non-zero positions sent on save survive a reload via GET.

        Regression for "canvas node positions are not preserved across save →
        page reload".  The frontend sends real per-node positions (its node
        ``id`` is a UI counter id, distinct from the human label); codegen
        names the function from the label, so the reloaded node ``id`` equals
        ``sanitize(label)``.  The sidecar keys positions by ``sanitize(label)``
        too, so the load lookup (``positions.get(node.id)``) must hit.
        """
        graph = {
            "nodes": [
                {
                    "id": "node_1",
                    "type": "pipelineNode",
                    "position": {"x": 111.0, "y": 222.0},
                    "data": {
                        "label": "My Source",
                        "nodeType": "dataInput",
                        "config": _file_input_config("data.parquet"),
                    },
                },
                {
                    "id": "node_2",
                    "type": "pipelineNode",
                    "position": {"x": 333.0, "y": 444.0},
                    "data": {
                        "label": "My Transform",
                        "nodeType": "polars",
                        "config": {"code": "return my_source"},
                    },
                },
            ],
            "edges": [{"id": "e1", "source": "node_1", "target": "node_2"}],
        }
        save = client.post(
            "/api/pipeline/save",
            json={
                "name": "main",
                "description": "",
                "graph": graph,
                "source_file": "main.py",
                "sources": ["live"],
                "active_source": "live",
            },
        )
        assert save.status_code == 200, save.text

        # The sidecar must carry the real positions, keyed by sanitize(label).
        sidecar = json.loads((tmp_path / "main.haute.json").read_text())
        assert sidecar["positions"] == {
            "My_Source": {"x": 111.0, "y": 222.0},
            "My_Transform": {"x": 333.0, "y": 444.0},
        }

        # GET the active pipeline back; positions must come back non-zero,
        # mapped onto the reloaded node ids (== sanitize(label)).
        get = client.get("/api/pipeline")
        assert get.status_code == 200, get.text
        positions = {n["recovery_id"]: n["display_position"] for n in get.json()["nodes"]}
        assert positions == {
            "My_Source": {"x": 111.0, "y": 222.0},
            "My_Transform": {"x": 333.0, "y": 444.0},
        }


# ---------------------------------------------------------------------------
# _validate_api_inputs_have_schemas
# ---------------------------------------------------------------------------


class TestValidateApiInputsHaveSchemas:
    """Saving reports JSON API Inputs whose canonical ``tables`` are empty."""

    def test_warns_when_json_api_input_has_no_tables(self, tmp_path: Path) -> None:
        svc = SavePipelineService(tmp_path)
        json_file = tmp_path / "input.json"
        json_file.write_text('[{"a": 1, "b": {"c": 2}}]')
        graph = _make_graph(
            _make_node("api", "api_input", "apiInput", {"path": "input.json"}),
        )
        warnings: list[str] = []
        svc._validate_api_inputs_have_schemas(graph, warnings)
        # Validation is read-only: it does not infer or write tables.
        cfg = graph.nodes[0].data.config
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
                            "path": "$[:]",
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

    @pytest.mark.parametrize("path", ["input.jsonl", "input.ndjson", "INPUT.NDJSON"])
    def test_warns_when_ndjson_api_input_has_no_tables(
        self,
        tmp_path: Path,
        path: str,
    ) -> None:
        svc = SavePipelineService(tmp_path)
        graph = _make_graph(
            _make_node("api", "api_input", "apiInput", {"path": path}),
        )
        warnings: list[str] = []
        svc._validate_api_inputs_have_schemas(graph, warnings)
        assert any("Infer Tables" in w for w in warnings)

    def test_mirrors_ndjson_api_input_cache(self, tmp_path: Path) -> None:
        svc = SavePipelineService(tmp_path)
        graph = _make_graph(
            _make_node("api", "api_input", "apiInput", {"path": "input.ndjson"}),
        )

        with patch("haute._json_flatten.mirror_cache_to_committed") as mirror:
            svc._mirror_api_input_caches(graph)

        mirror.assert_called_once_with(
            str((tmp_path / "input.ndjson").resolve()), graph.nodes[0].data.config
        )

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
            _make_node("src", "Source", "dataInput", {"path": "data.parquet"}),
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
            _make_node("src", "Source", "dataInput", {"path": "data.parquet"}),
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
            _make_node("src", "Source", "dataInput", {"path": "data.parquet"}),
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
            _make_node("src", "Source", "dataInput", {"path": "data.parquet"}),
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

    def test_submodel_modules_write_under_pipeline_root(self, tmp_path: Path) -> None:
        """A nested active pipeline keeps generated modules beside that pipeline."""
        rating_root = tmp_path / "rating"
        rating_root.mkdir()
        svc = SavePipelineService(tmp_path, pipeline_root=rating_root)
        graph = _make_graph()
        graph.submodels = {"pricing": _submodel_definition("pricing")}
        body = SavePipelineRequest(
            name="main",
            description="",
            graph=graph,
            source_file="rating/main.py",
        )

        with patch(
            "haute.codegen.graph_to_code_multi",
            return_value={
                "rating/main.py": "# main\n",
                "modules/pricing.py": "# submodel\n",
            },
        ):
            svc._write_code(body, graph, rating_root / "main.py")

        assert (rating_root / "modules" / "pricing.py").read_text() == "# submodel\n"
        assert not (tmp_path / "modules" / "pricing.py").exists()

    def test_module_delete_uses_pipeline_root(self, tmp_path: Path) -> None:
        """Dissolving a submodel deletes the active pipeline's module file."""
        rating_root = tmp_path / "rating"
        rating_module = rating_root / "modules" / "pricing.py"
        root_module = tmp_path / "modules" / "pricing.py"
        rating_module.parent.mkdir(parents=True)
        root_module.parent.mkdir(parents=True)
        rating_module.write_text("# rating module\n")
        root_module.write_text("# root module\n")

        svc = SavePipelineService(tmp_path, pipeline_root=rating_root)
        target = svc._resolve_module_delete_file("modules/pricing.py")
        touched: list = []
        svc._stage_delete(target, touched)

        assert not rating_module.exists()
        assert root_module.read_text() == "# root module\n"

    def test_nested_pipeline_root_rejects_source_file_outside_pipeline_root(
        self,
        tmp_path: Path,
    ) -> None:
        """Nested module/config writes must belong to the same source pipeline."""
        rating_root = tmp_path / "rating"
        other_root = tmp_path / "other"
        rating_root.mkdir()
        other_root.mkdir()
        svc = SavePipelineService(tmp_path, pipeline_root=rating_root)
        graph = _make_graph()
        graph.submodels = {"pricing": _submodel_definition("pricing")}
        body = SavePipelineRequest(
            name="other",
            description="",
            graph=graph,
            source_file="other/main.py",
        )

        with patch(
            "haute.codegen.graph_to_code_multi",
            return_value={
                "other/main.py": "# main\n",
                "modules/pricing.py": "# submodel\n",
            },
        ):
            with pytest.raises(HTTPException) as exc_info:
                svc._write_code(body, graph, other_root / "main.py")

        assert exc_info.value.status_code == 400
        assert "source_file" in exc_info.value.detail
