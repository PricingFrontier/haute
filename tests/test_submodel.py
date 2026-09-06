"""Tests for submodel features — parser, codegen, flatten_graph, schemas."""

from __future__ import annotations

import textwrap
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from haute.codegen import graph_to_code, graph_to_code_multi
from haute.graph_utils import flatten_graph
from haute.parser import parse_pipeline_file
from tests.conftest import make_graph as _g
from tests.conftest import make_output_config, write_data_input_config

if TYPE_CHECKING:
    from haute.graph_utils import PipelineGraph

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write(tmp_path: Path, name: str, code: str) -> Path:
    p = tmp_path / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(textwrap.dedent(code))
    return p


# ---------------------------------------------------------------------------
# Fixtures — minimal graphs
# ---------------------------------------------------------------------------


@pytest.fixture()
def flat_graph() -> PipelineGraph:
    """A simple 3-node flat graph (no submodels)."""
    return _g(
        {
            "nodes": [
                {
                    "id": "src",
                    "type": "dataInput",
                    "position": {"x": 0, "y": 0},
                    "data": {
                        "label": "Source",
                        "nodeType": "dataInput",
                        "config": {"path": "data/in.parquet"},
                    },
                },
                {
                    "id": "tx",
                    "type": "polars",
                    "position": {"x": 200, "y": 0},
                    "data": {
                        "label": "Transform",
                        "nodeType": "polars",
                        "config": {"code": "df = df.select('x')"},
                    },
                },
                {
                    "id": "out",
                    "type": "output",
                    "position": {"x": 400, "y": 0},
                    "data": {
                        "label": "Output",
                        "nodeType": "output",
                        "config": make_output_config(["x"]),
                    },
                },
            ],
            "edges": [
                {"id": "e1", "source": "src", "target": "tx"},
                {"id": "e2", "source": "tx", "target": "out"},
            ],
        }
    )


@pytest.fixture()
def submodel_graph() -> PipelineGraph:
    """A graph with a submodel node wrapping tx+out."""
    return _g(
        {
            "nodes": [
                {
                    "id": "src",
                    "type": "dataInput",
                    "position": {"x": 0, "y": 0},
                    "data": {
                        "label": "Source",
                        "nodeType": "dataInput",
                        "config": {"path": "data/in.parquet"},
                    },
                },
                {
                    "id": "instance_scoring",
                    "type": "submodel",
                    "position": {"x": 200, "y": 0},
                    "data": {
                        "label": "scoring",
                        "nodeType": "submodel",
                        "config": {"definitionId": "definition_scoring", "alias": "scoring"},
                    },
                },
            ],
            "edges": [
                {
                    "id": "e_src_submodel__scoring__tx",
                    "source": "src",
                    "target": "instance_scoring",
                    "targetHandle": "in__source",
                },
            ],
            "submodels": {
                "definition_scoring": {
                    "definitionId": "definition_scoring",
                    "file": "modules/scoring.py",
                    "inputPorts": [{"name": "source", "targets": [{"nodeId": "tx"}]}],
                    "outputPorts": [],
                    "graph": {
                        "nodes": [
                            {
                                "id": "tx",
                                "type": "polars",
                                "position": {"x": 0, "y": 0},
                                "data": {
                                    "label": "Transform",
                                    "nodeType": "polars",
                                    "config": {"code": "df = df.select('x')"},
                                },
                            },
                            {
                                "id": "out",
                                "type": "output",
                                "position": {"x": 200, "y": 0},
                                "data": {
                                    "label": "Output",
                                    "nodeType": "output",
                                    "config": make_output_config(["x"]),
                                },
                            },
                        ],
                        "edges": [
                            {"id": "e_tx_out", "source": "tx", "target": "out"},
                        ],
                    },
                },
            },
        }
    )


# ---------------------------------------------------------------------------
# flatten_graph tests
# ---------------------------------------------------------------------------


class TestFlattenGraph:
    def test_flat_graph_unchanged(self, flat_graph):
        """A graph with no submodels should pass through unchanged."""
        result = flatten_graph(flat_graph)
        assert len(result.nodes) == 3
        assert len(result.edges) == 2
        node_ids = {n.id for n in result.nodes}
        assert node_ids == {"src", "tx", "out"}

    def test_submodel_dissolved(self, submodel_graph):
        """Flattening should inline the submodel's children and remove the placeholder."""
        result = flatten_graph(submodel_graph)
        node_ids = {n.id for n in result.nodes}
        # Submodel placeholder should be gone
        assert "instance_scoring" not in node_ids
        # Child nodes should be present
        assert "submodel_runtime/instance_scoring/tx" in node_ids
        assert "submodel_runtime/instance_scoring/out" in node_ids
        # Source should still be there
        assert "src" in node_ids

    def test_submodel_edges_rewired(self, submodel_graph):
        """Cross-boundary edges should be rewired to point to child nodes."""
        result = flatten_graph(submodel_graph)
        edge_pairs = [(e.source, e.target) for e in result.edges]
        # Should have src→tx edge (rewired from src→submodel__scoring)
        assert ("src", "submodel_runtime/instance_scoring/tx") in edge_pairs
        # Internal edge tx→out should be present
        assert (
            "submodel_runtime/instance_scoring/tx",
            "submodel_runtime/instance_scoring/out",
        ) in edge_pairs

    def test_no_submodel_key_in_result(self, submodel_graph):
        """The flattened graph should not have a submodels dict."""
        result = flatten_graph(submodel_graph)
        assert not result.submodels


# ---------------------------------------------------------------------------
# Codegen multi-file tests
# ---------------------------------------------------------------------------


class TestCodegenMultiFile:
    def test_graph_to_code_multi_returns_files(self, submodel_graph):
        """graph_to_code_multi should return a dict with main + submodel files."""
        files = graph_to_code_multi(submodel_graph, pipeline_name="main")
        assert len(files) >= 1
        main_files = [k for k in files if not k.startswith("modules/")]
        assert len(main_files) >= 1
        for name, code in files.items():
            compile(code, f"<{name}>", "exec")

    def test_submodel_file_generated(self, submodel_graph):
        """The submodel .py file should be generated."""
        files = graph_to_code_multi(submodel_graph, pipeline_name="main")
        sm_files = [k for k in files if k.startswith("modules/")]
        assert len(sm_files) >= 1
        sm_code = files[sm_files[0]]
        compile(sm_code, "<submodel>", "exec")
        assert "haute" in sm_code

    def test_submodel_preamble_and_preserved_block_are_emitted_once(self, submodel_graph):
        subgraph = submodel_graph.submodels["definition_scoring"].graph
        submodel_graph.submodels["definition_scoring"] = submodel_graph.submodels[
            "definition_scoring"
        ].model_copy(
            update={
                "graph": subgraph.model_copy(
                    update={
                        "pipeline_description": "Score a policy",
                        "preamble": "HELPER = 1",
                        "preserved_blocks": ["KEPT = 2"],
                    }
                )
            }
        )
        files = graph_to_code_multi(submodel_graph, pipeline_name="main")
        code = files["modules/scoring.py"]
        assert "description='Score a policy'" in code
        assert code.count("HELPER = 1") == 1
        assert code.count("KEPT = 2") == 1

    def test_main_file_compiles(self, submodel_graph):
        """The main pipeline code should compile without errors."""
        files = graph_to_code_multi(submodel_graph, pipeline_name="main")
        main_files = [k for k in files if not k.startswith("modules/")]
        for fname in main_files:
            compile(files[fname], f"<{fname}>", "exec")

    def test_flat_graph_single_file(self, flat_graph):
        """A flat graph should produce a single main file."""
        code = graph_to_code(flat_graph, pipeline_name="test")
        assert "pipeline" in code
        assert "Pipeline" in code
        compile(code, "<main>", "exec")


# ---------------------------------------------------------------------------
# Parser tests — submodel detection
# ---------------------------------------------------------------------------


class TestParserSubmodel:
    def test_parse_main_with_submodel(self, tmp_path):
        """Parser should detect pipeline.submodel() calls."""
        source_config = write_data_input_config(tmp_path, "Source", "data/in.parquet")
        _write(
            tmp_path,
            "modules/scoring.py",
            """\
            import polars as pl
            import haute

            submodel = haute.Submodel(
                "scoring",
                definition_id="definition_scoring",
                input_ports=[
                    {
                        "name": "source",
                        "targets": [{"nodeId": "Transform"}],
                    }
                ],
                output_ports=[],
            )

            @submodel.polars
            def Transform(source: pl.LazyFrame) -> pl.LazyFrame:
                return source.select("x")
        """,
        )

        _write(
            tmp_path,
            "main.py",
            f"""\
            import polars as pl
            import haute

            pipeline = haute.Pipeline("test")

            @pipeline.data_input(config="{source_config}")
            def Source() -> pl.LazyFrame:
                return pl.scan_parquet("data/in.parquet")

            pipeline.submodel(
                "modules/scoring.py",
                definition_id="definition_scoring",
                instance_id="instance_scoring",
                alias="scoring",
            )

            pipeline.connect("Source", "scoring", target_port="source")
        """,
        )

        graph = parse_pipeline_file(tmp_path / "main.py")
        assert graph.nodes is not None
        node_ids = {n.id for n in graph.nodes}
        assert "Source" in node_ids or "source" in node_ids.union(
            {n.id.lower() for n in graph.nodes}
        )

    def test_parse_flat_pipeline(self, tmp_path):
        """A pipeline without submodels should parse normally."""
        source_config = write_data_input_config(tmp_path, "Source", "data/in.parquet")
        _write(
            tmp_path,
            "main.py",
            f"""\
            import polars as pl
            import haute

            pipeline = haute.Pipeline("basic")

            @pipeline.data_input(config="{source_config}")
            def Source() -> pl.LazyFrame:
                return pl.scan_parquet("data/in.parquet")

            @pipeline.polars
            def Transform(Source: pl.LazyFrame) -> pl.LazyFrame:
                return Source.select("x")

            pipeline.connect("Source", "Transform")
        """,
        )

        graph = parse_pipeline_file(tmp_path / "main.py")
        assert len(graph.nodes) == 2
        assert len(graph.edges) >= 1


# ---------------------------------------------------------------------------
# Schema tests
# ---------------------------------------------------------------------------


class TestSchemas:
    def test_create_submodel_request(self):
        from haute.schemas import CreateSubmodelRequest

        req = CreateSubmodelRequest(
            name="scoring",
            node_ids=["tx", "out"],
            graph={"nodes": [], "edges": []},
            preserved_blocks=["KEEP = 1"],
            base_revision="revision-1",
        )
        assert req.name == "scoring"
        assert req.node_ids == ["tx", "out"]
        assert req.preserved_blocks == ["KEEP = 1"]
        assert req.base_revision == "revision-1"

    def test_create_submodel_response(self):
        from haute.schemas import CreateSubmodelResponse

        resp = CreateSubmodelResponse(
            status="ok",
            submodel_file="modules/scoring.py",
            parent_file="main.py",
            source_revision="revision-2",
            graph={"nodes": [], "edges": []},
        )
        assert resp.status == "ok"
        assert resp.submodel_file == "modules/scoring.py"
        assert resp.source_revision == "revision-2"

    def test_dissolve_submodel_request(self):
        from haute.schemas import DissolveSubmodelRequest

        req = DissolveSubmodelRequest(
            instance_id="instance_scoring",
            graph={"nodes": [], "edges": []},
            preserved_blocks=["KEEP = 1"],
            base_revision="revision-1",
        )
        assert req.instance_id == "instance_scoring"
        assert req.preserved_blocks == ["KEEP = 1"]
        assert req.base_revision == "revision-1"

    @pytest.mark.parametrize("instance_id", ["", " instance_scoring", "instance_scoring "])
    def test_dissolve_submodel_request_rejects_invalid_identity(self, instance_id: str):
        from pydantic import ValidationError

        from haute.schemas import DissolveSubmodelRequest

        with pytest.raises(ValidationError, match="non-empty and unpadded"):
            DissolveSubmodelRequest(
                instance_id=instance_id,
                graph={"nodes": [], "edges": []},
                base_revision="revision-1",
            )

    def test_dissolve_submodel_request_rejects_name_target(self):
        from pydantic import ValidationError

        from haute.schemas import DissolveSubmodelRequest

        with pytest.raises(ValidationError, match="instance_id"):
            DissolveSubmodelRequest(
                submodel_name="scoring",
                graph={"nodes": [], "edges": []},
                base_revision="revision-1",
            )

    @pytest.mark.parametrize("operation", ["create", "dissolve"])
    def test_submodel_mutation_revision_must_not_be_whitespace(self, operation: str):
        from pydantic import ValidationError

        from haute.schemas import CreateSubmodelRequest, DissolveSubmodelRequest

        common = {
            "graph": {"nodes": [], "edges": []},
            "base_revision": "   ",
        }
        with pytest.raises(ValidationError, match="base_revision"):
            if operation == "create":
                CreateSubmodelRequest(name="scoring", node_ids=["tx", "out"], **common)
            else:
                DissolveSubmodelRequest(instance_id="instance_scoring", **common)

    def test_dissolve_submodel_response(self):
        from haute.schemas import DissolveSubmodelResponse

        resp = DissolveSubmodelResponse(
            status="ok",
            graph={"nodes": [], "edges": []},
            source_revision="revision-2",
            instance_id="instance_scoring",
            definition_id="definition_scoring",
        )
        assert resp.instance_id == "instance_scoring"
        assert resp.definition_id == "definition_scoring"
        assert resp.source_revision == "revision-2"

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("submodel_file_deleted", False),
            ("retained_submodel_file", "modules/scoring.py"),
        ],
    )
    def test_dissolve_submodel_response_rejects_removed_lifecycle_fields(
        self,
        field,
        value,
    ):
        from pydantic import ValidationError

        from haute.schemas import DissolveSubmodelResponse

        with pytest.raises(ValidationError, match=field):
            DissolveSubmodelResponse(
                graph={"nodes": [], "edges": []},
                source_revision="revision-2",
                instance_id="instance_scoring",
                definition_id="definition_scoring",
                **{field: value},
            )

    def test_submodel_graph_response(self):
        from haute.schemas import SubmodelGraphResponse

        resp = SubmodelGraphResponse(
            status="ok",
            submodel_name="scoring",
            definition_id="definition_scoring",
            submodel_file="lib/scoring.py",
            graph={"nodes": [{"id": "tx"}], "edges": []},
        )
        assert resp.submodel_name == "scoring"
        assert resp.definition_id == "definition_scoring"
        assert resp.submodel_file == "lib/scoring.py"
        assert len(resp.graph.nodes) == 1

    def test_save_pipeline_response_carries_committed_revision(self):
        from haute.schemas import SavePipelineResponse

        resp = SavePipelineResponse(
            file="main.py",
            pipeline_name="main",
            source_revision="revision-2",
        )
        assert resp.source_revision == "revision-2"
