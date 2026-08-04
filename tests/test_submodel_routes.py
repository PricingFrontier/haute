"""Tests for haute.routes.submodel — create, get, dissolve endpoints."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest

if TYPE_CHECKING:
    from fastapi.testclient import TestClient

from haute._config_io import config_path_for_node
from haute.errors import ConfigError
from haute.graph_utils import NodeType, PipelineGraph, _sanitize_func_name
from haute.routes._helpers import invalidate_pipeline_index, pipeline_dir

_CURRENT_REVISION = "revision-current"
_SAVED_REVISION = "revision-saved"
DEFINITION_ID = "pricing-definition"
INSTANCE_ID = "pricing-instance"
ALIAS = "pricing"


@pytest.fixture(autouse=True)
def _isolated_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Run every test in a temporary directory."""
    monkeypatch.chdir(tmp_path)
    pipeline_dir.cache_clear()
    invalidate_pipeline_index()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _data_input_config(path: str) -> dict[str, object]:
    suffix = Path(path).suffix.casefold()
    format_name = "csv" if suffix == ".csv" else "parquet"
    return {
        "inputType": "file",
        "format": format_name,
        "mode": "scan",
        "path": path,
        "arguments": {},
    }


def _simple_graph() -> dict:
    """A minimal graph dict with two nodes and an edge."""
    return {
        "nodes": [
            {
                "id": "load",
                "data": {
                    "label": "load",
                    "nodeType": "dataInput",
                    "config": _data_input_config("data.csv"),
                },
            },
            {
                "id": "calc",
                "data": {"label": "calc", "nodeType": "polars", "config": {"code": "return df"}},
            },
        ],
        "edges": [{"id": "e1", "source": "load", "target": "calc"}],
    }


def _graph_with_submodel() -> dict:
    """A graph dict that contains a submodel."""
    return {
        "nodes": [
            {
                "id": "load",
                "data": {
                    "label": "load",
                    "nodeType": "dataInput",
                    "config": _data_input_config("d.csv"),
                },
            },
            {
                "id": INSTANCE_ID,
                "type": "submodel",
                "data": {
                    "label": ALIAS,
                    "nodeType": "submodel",
                    "config": {"definitionId": DEFINITION_ID, "alias": ALIAS},
                },
            },
        ],
        "edges": [],
        "submodels": {
            DEFINITION_ID: {
                "definitionId": DEFINITION_ID,
                "file": "modules/pricing.py",
                "inputPorts": [],
                "outputPorts": [],
                "graph": {
                    "nodes": [
                        {
                            "id": "base_rate",
                            "data": {
                                "label": "base_rate",
                                "nodeType": "polars",
                                "config": {"code": "return df"},
                            },
                        },
                    ],
                    "edges": [],
                    "pipeline_name": "pricing",
                },
            },
        },
    }


def _write_nested_project(tmp_path: Path) -> Path:
    """Create a project whose active pipeline lives in rating/main.py."""
    (tmp_path / "haute.toml").write_text(
        '[project]\npipeline = "rating/main.py"\n',
        encoding="utf-8",
    )
    rating_root = tmp_path / "rating"
    rating_root.mkdir()
    (rating_root / "main.py").write_text(
        'import haute\n\npipeline = haute.Pipeline("main")\n',
        encoding="utf-8",
    )
    return rating_root


def _graph_model(
    raw: dict[str, object],
    *,
    revision: str = _CURRENT_REVISION,
) -> PipelineGraph:
    return PipelineGraph.model_validate({**raw, "source_revision": revision})


def _patch_parent_document(
    tmp_path: Path,
    raw_graph: dict[str, object],
    *,
    source_file: str = "pipeline.py",
):
    """Patch authoritative loading while retaining real route/save boundaries."""
    parent = tmp_path / source_file
    parent.parent.mkdir(parents=True, exist_ok=True)
    if not parent.exists():
        parent.write_text("# mocked parent document\n", encoding="utf-8")
    return patch(
        "haute.routes.submodel._load_parent_document",
        return_value=(tmp_path.resolve(), parent.resolve(), _graph_model(raw_graph)),
    )


def _saved_result() -> SimpleNamespace:
    return SimpleNamespace(source_revision=_SAVED_REVISION)


def _create_body(
    *,
    graph: dict[str, object] | None = None,
    source_file: str = "pipeline.py",
) -> dict[str, object]:
    return {
        "name": "pricing",
        "node_ids": ["load", "calc"],
        "graph": graph or _simple_graph(),
        "preamble": "",
        "preserved_blocks": [],
        "source_file": source_file,
        "base_revision": _CURRENT_REVISION,
        "pipeline_name": "main",
    }


def _dissolve_body(
    *,
    graph: dict[str, object] | None = None,
    source_file: str = "pipeline.py",
) -> dict[str, object]:
    return {
        "instance_id": INSTANCE_ID,
        "graph": graph or _graph_with_submodel(),
        "preamble": "",
        "preserved_blocks": [],
        "source_file": source_file,
        "base_revision": _CURRENT_REVISION,
        "pipeline_name": "main",
    }


def _write_submodel(path: Path, *, node_name: str = "base_rate") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"""import polars as pl
import haute

submodel = haute.Submodel(
    "pricing",
    definition_id="pricing-definition",
    input_ports=[],
    output_ports=[],
)

@submodel.polars
def {node_name}(df: pl.LazyFrame) -> pl.LazyFrame:
    return df
""",
        encoding="utf-8",
    )


def _write_parent_reference(path: Path, child_reference: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"""import haute

pipeline = haute.Pipeline({path.stem!r})
pipeline.submodel(
    {child_reference!r}, definition_id="pricing-definition",
    instance_id="pricing-instance", alias="pricing",
)
""",
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# POST /api/submodel/create
# ---------------------------------------------------------------------------


class TestCreateSubmodel:
    def test_invalid_node_ids(self, client: TestClient, tmp_path: Path) -> None:
        """An unknown selected id is a stale-selection conflict."""
        body = _create_body()
        body["node_ids"] = ["nonexistent"]
        with _patch_parent_document(tmp_path, _simple_graph()):
            response = client.post("/api/submodel/create", json=body)
        assert response.status_code == 409

    def test_too_few_nodes(self, client: TestClient, tmp_path: Path) -> None:
        """A submodel must contain at least 2 nodes."""
        body = _create_body()
        body["node_ids"] = ["calc"]
        with _patch_parent_document(tmp_path, _simple_graph()):
            response = client.post("/api/submodel/create", json=body)
        assert response.status_code == 400

    def test_successful_create(self, client: TestClient, tmp_path: Path) -> None:
        """Happy path returns the transformed graph without saving it."""
        result = SimpleNamespace(
            sm_file="modules/pricing.py",
            graph=_graph_model(_graph_with_submodel()),
        )

        with (
            _patch_parent_document(tmp_path, _simple_graph()),
            patch("haute.routes._submodel_ops.create_submodel_graph", return_value=result),
            patch(
                "haute.routes._save_pipeline.SavePipelineService.save_graph_transactionally",
                return_value=_saved_result(),
            ) as save,
        ):
            response = client.post("/api/submodel/create", json=_create_body())

        assert response.status_code == 200
        payload = response.json()
        assert payload["status"] == "ok"
        assert payload["submodel_file"] == "modules/pricing.py"
        assert payload["source_revision"] == _CURRENT_REVISION
        assert payload["graph"]["source_revision"] == _CURRENT_REVISION
        save.assert_not_called()

    def test_no_clobber_preflight_config_error_is_a_bad_request(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        """A read-only preflight configuration failure remains actionable."""
        result = SimpleNamespace(
            sm_file="modules/pricing.py",
            graph=_graph_model(_graph_with_submodel()),
        )
        detail = (
            "polars transform has no user code and no upstream sources; "
            "either connect an input or provide code. (node_id=polars_1, label=Polars 1)"
        )

        with (
            _patch_parent_document(tmp_path, _simple_graph()),
            patch("haute.routes._submodel_ops.create_submodel_graph", return_value=result),
            patch(
                "haute.routes._save_pipeline.SavePipelineService.validate_new_module_files",
                side_effect=ConfigError(detail),
            ),
        ):
            response = client.post("/api/submodel/create", json=_create_body())

        assert response.status_code == 400
        assert response.json() == {"detail": detail}

    def test_create_does_not_forward_pipeline_description_to_save(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        """Transform metadata cannot trigger the persistence service."""
        result = SimpleNamespace(
            sm_file="modules/pricing.py",
            graph=_graph_model(_graph_with_submodel()),
        )
        body = _create_body()
        body["pipeline_description"] = "My pricing pipeline"

        with (
            _patch_parent_document(tmp_path, _simple_graph()),
            patch("haute.routes._submodel_ops.create_submodel_graph", return_value=result),
            patch(
                "haute.routes._save_pipeline.SavePipelineService.save_graph_transactionally",
                return_value=_saved_result(),
            ) as save,
        ):
            response = client.post("/api/submodel/create", json=body)

        assert response.status_code == 200
        save.assert_not_called()

    def test_create_does_not_run_codegen_or_touch_the_parent(
        self,
        client: TestClient,
        tmp_path: Path,
    ) -> None:
        """Code generation is reserved for explicit Save."""
        result = SimpleNamespace(
            sm_file="modules/pricing.py",
            graph=_graph_model(_graph_with_submodel()),
        )

        with (
            _patch_parent_document(tmp_path, _simple_graph()),
            patch("haute.routes._submodel_ops.create_submodel_graph", return_value=result),
            patch(
                "haute.codegen.graph_to_code_multi",
                return_value={
                    "pipeline.py": "# generated main\n",
                    "config/escaped.py": "# not an allowed codegen output\n",
                },
            ) as codegen,
        ):
            response = client.post("/api/submodel/create", json=_create_body())

        assert response.status_code == 200
        codegen.assert_not_called()
        assert (tmp_path / "pipeline.py").read_text(encoding="utf-8") == (
            "# mocked parent document\n"
        )
        assert not (tmp_path / "config" / "escaped.py").exists()

    def test_create_leaves_configured_pipeline_root_unchanged(
        self,
        client: TestClient,
        tmp_path: Path,
    ) -> None:
        """Transform-only create writes neither configured nor root artifacts."""
        rating_root = _write_nested_project(tmp_path)
        child = {
            "id": "child_source",
            "data": {
                "label": "Child Source",
                "nodeType": "dataInput",
                "config": _data_input_config("data.parquet"),
            },
        }
        result_graph = _graph_model(
            {
                "pipeline_name": "main",
                "nodes": [],
                "submodels": {
                    DEFINITION_ID: {
                        "definitionId": DEFINITION_ID,
                        "file": "modules/pricing.py",
                        "inputPorts": [],
                        "outputPorts": [],
                        "graph": {"nodes": [child], "edges": []},
                    },
                },
            }
        )
        result = SimpleNamespace(sm_file="modules/pricing.py", graph=result_graph)
        committed = PipelineGraph(
            pipeline_name="main",
            source_revision=_SAVED_REVISION,
        )

        with (
            _patch_parent_document(
                tmp_path,
                _simple_graph(),
                source_file="rating/main.py",
            ),
            patch("haute.routes._submodel_ops.create_submodel_graph", return_value=result),
            patch(
                "haute.codegen.graph_to_code_multi",
                return_value={
                    "rating/main.py": "# main\n",
                    "modules/pricing.py": "# submodel\n",
                },
            ),
            patch(
                "haute.routes._helpers.parse_pipeline_to_graph",
                return_value=committed,
            ),
        ):
            response = client.post(
                "/api/submodel/create",
                json=_create_body(source_file="rating/main.py"),
            )

        assert response.status_code == 200
        child_config_path = config_path_for_node(
            NodeType.DATA_INPUT,
            _sanitize_func_name(child["data"]["label"]),
        )
        assert not (rating_root / "modules" / "pricing.py").exists()
        assert not (rating_root / child_config_path).exists()
        assert not (tmp_path / "modules" / "pricing.py").exists()
        assert not (tmp_path / "config").exists()


# ---------------------------------------------------------------------------
# GET /api/submodel/{name}
# ---------------------------------------------------------------------------


class TestGetSubmodel:
    def test_submodel_not_found(self, client: TestClient, tmp_path: Path) -> None:
        (tmp_path / "pipeline.py").write_text(
            'import haute\npipeline = haute.Pipeline("main")\n',
            encoding="utf-8",
        )
        response = client.get(
            "/api/submodel/nonexistent",
            params={"source_file": "pipeline.py"},
        )
        assert response.status_code == 404
        assert "not found" in response.json()["detail"]

    def test_successful_get(self, client: TestClient, tmp_path: Path) -> None:
        _write_submodel(tmp_path / "modules" / "pricing.py")
        _write_parent_reference(tmp_path / "pipeline.py", "modules/pricing.py")
        response = client.get(
            f"/api/submodel/{DEFINITION_ID}",
            params={"source_file": "pipeline.py"},
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["status"] == "ok"
        assert payload["definition_id"] == DEFINITION_ID
        assert payload["submodel_file"] == "modules/pricing.py"
        assert len(payload["graph"]["nodes"]) >= 1

    def test_name_with_dots_cannot_escape_parent_metadata(
        self,
        client: TestClient,
        tmp_path: Path,
    ) -> None:
        (tmp_path / "pipeline.py").write_text(
            'import haute\npipeline = haute.Pipeline("main")\n',
            encoding="utf-8",
        )
        response = client.get(
            "/api/submodel/..something",
            params={"source_file": "pipeline.py"},
        )
        assert response.status_code == 404

    def test_get_uses_configured_pipeline_root(
        self,
        client: TestClient,
        tmp_path: Path,
    ) -> None:
        """Submodel drill-down reads rating/modules and rating/config."""
        rating_root = _write_nested_project(tmp_path)
        config_dir = rating_root / "config" / "data_input"
        config_dir.mkdir(parents=True)
        (config_dir / "source.json").write_text(
            '{"inputType":"file","format":"parquet","mode":"scan",'
            '"path":"rating-data.parquet","arguments":{}}'
        )
        modules_dir = rating_root / "modules"
        modules_dir.mkdir()
        (modules_dir / "pricing.py").write_text(
            """\
import polars as pl
import haute

submodel = haute.Submodel(
    "pricing",
    definition_id="pricing-definition",
    input_ports=[],
    output_ports=[],
)


@submodel.data_input(config="config/data_input/source.json")
def source() -> pl.LazyFrame:
    return pl.scan_parquet("rating-data.parquet")
""",
            encoding="utf-8",
        )
        _write_parent_reference(rating_root / "main.py", "modules/pricing.py")

        resp = client.get(
            f"/api/submodel/{DEFINITION_ID}",
            params={"source_file": "rating/main.py"},
        )

        assert resp.status_code == 200
        data = resp.json()
        node = data["graph"]["nodes"][0]
        assert node["data"]["config"]["path"] == "rating-data.parquet"

    def test_get_uses_path_recorded_by_active_pipeline(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        rating_root = tmp_path / "rating"
        _write_nested_project(tmp_path)
        library = rating_root / "lib"
        library.mkdir()
        (library / "pricing.py").write_text(
            "import polars as pl\n"
            "import haute\n"
            "submodel = haute.Submodel(\n"
            '    "pricing",\n'
            '    definition_id="pricing-definition",\n'
            "    input_ports=[],\n"
            "    output_ports=[],\n"
            ")\n"
            "@submodel.polars\ndef rate(df: pl.LazyFrame) -> pl.LazyFrame:\n    return df\n",
            encoding="utf-8",
        )
        (rating_root / "main.py").write_text(
            'import haute\npipeline = haute.Pipeline("main")\n'
            "pipeline.submodel(\n"
            '    "lib/pricing.py",\n'
            '    definition_id="pricing-definition",\n'
            '    instance_id="pricing-instance",\n'
            '    alias="pricing",\n'
            ")\n",
            encoding="utf-8",
        )
        response = client.get(
            f"/api/submodel/{DEFINITION_ID}",
            params={"source_file": "rating/main.py"},
        )
        assert response.status_code == 200
        assert response.json()["submodel_file"] == "lib/pricing.py"
        source_file = response.json()["graph"]["source_file"].replace("\\", "/")
        assert source_file.endswith("lib/pricing.py")

    def test_get_does_not_scan_broken_sibling_pipeline(
        self,
        client: TestClient,
        tmp_path: Path,
    ) -> None:
        modules_dir = tmp_path / "modules"
        modules_dir.mkdir()
        (modules_dir / "pricing.py").write_text(
            "import polars as pl\n"
            "import haute\n"
            "submodel = haute.Submodel(\n"
            '    "pricing",\n'
            '    definition_id="pricing-definition",\n'
            "    input_ports=[],\n"
            "    output_ports=[],\n"
            ")\n"
            "@submodel.polars\ndef rate(df: pl.LazyFrame) -> pl.LazyFrame:\n    return df\n",
            encoding="utf-8",
        )
        (tmp_path / "a_broken.py").write_text(
            'import haute\npipeline = haute.Pipeline("broken")\n'
            "pipeline.submodel(\n"
            '    "modules/missing.py",\n'
            '    definition_id="missing-definition",\n'
            '    instance_id="missing-instance",\n'
            '    alias="missing",\n'
            ")\n",
            encoding="utf-8",
        )
        (tmp_path / "z_owner.py").write_text(
            'import haute\npipeline = haute.Pipeline("owner")\n'
            "pipeline.submodel(\n"
            '    "modules/pricing.py",\n'
            '    definition_id="pricing-definition",\n'
            '    instance_id="pricing-instance",\n'
            '    alias="pricing",\n'
            ")\n",
            encoding="utf-8",
        )

        response = client.get(
            f"/api/submodel/{DEFINITION_ID}",
            params={"source_file": "z_owner.py"},
        )

        assert response.status_code == 200
        source_file = response.json()["graph"]["source_file"].replace("\\", "/")
        assert source_file.endswith("modules/pricing.py")

    def test_encoded_backslash_traversal_is_bad_request(self, client: TestClient) -> None:
        response = client.get(
            "/api/submodel/%5C..%5Coutside",
            params={"source_file": "pipeline.py"},
        )
        assert response.status_code == 400


# ---------------------------------------------------------------------------
# POST /api/submodel/dissolve
# ---------------------------------------------------------------------------


class TestDissolveSubmodel:
    @pytest.fixture(autouse=True)
    def _authoritative_disk_state(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Keep route tests focused while modelling the required disk authority."""

        def load_parent(source_file: str):
            parent = tmp_path / source_file if source_file else tmp_path
            if source_file:
                parent.parent.mkdir(parents=True, exist_ok=True)
                if not parent.exists():
                    parent.write_text("# mocked parent document\n", encoding="utf-8")
            return (
                tmp_path.resolve(),
                parent.resolve(),
                _graph_model(_graph_with_submodel()),
            )

        monkeypatch.setattr(
            "haute.routes.submodel._load_parent_document",
            load_parent,
        )
        monkeypatch.setattr(
            "haute.parser.parse_submodel_file",
            lambda *_args, **_kwargs: PipelineGraph(pipeline_name="pricing"),
        )
        monkeypatch.setattr(
            "haute.routes._helpers.parse_pipeline_to_graph",
            lambda *_args, **_kwargs: PipelineGraph(
                pipeline_name="main",
                source_revision=_SAVED_REVISION,
            ),
        )

    def test_submodel_not_in_graph(self, client: TestClient) -> None:
        body = _dissolve_body(graph=_simple_graph())
        body["instance_id"] = "nonexistent-instance"
        resp = client.post("/api/submodel/dissolve", json=body)
        assert resp.status_code == 404
        assert "not found" in resp.json()["detail"]

    def test_missing_source_file(self, client: TestClient) -> None:
        body = _dissolve_body(source_file="")
        resp = client.post("/api/submodel/dissolve", json=body)
        assert resp.status_code == 400
        assert "source_file" in resp.json()["detail"]

    def test_successful_dissolve(self, client: TestClient, tmp_path: Path) -> None:
        """Happy path dissolves in memory and returns the unchanged revision."""
        modules_dir = tmp_path / "modules"
        modules_dir.mkdir()
        sm_file = modules_dir / "pricing.py"
        sm_file.write_text("# submodel code\n")

        # Create the main pipeline file path
        pipeline_file = tmp_path / "pipeline.py"
        pipeline_file.write_text("# main pipeline\n")

        flat_graph = PipelineGraph(pipeline_name="main")

        with patch("haute.graph_utils.flatten_graph", return_value=flat_graph):
            with patch("haute.codegen.graph_to_code", return_value="# code\n"):
                resp = client.post(
                    "/api/submodel/dissolve",
                    json=_dissolve_body(),
                )

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["source_revision"] == _CURRENT_REVISION
        assert "submodel_file_deleted" not in data
        assert "retained_submodel_file" not in data

    def test_dissolve_uses_the_submitted_definition_without_reparsing_disk(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        """The in-memory canonical definition is authoritative until Save."""
        modules_dir = tmp_path / "modules"
        modules_dir.mkdir()
        (modules_dir / "pricing.py").write_text("# valid disk module\n", encoding="utf-8")
        (tmp_path / "pipeline.py").write_text("# parent\n", encoding="utf-8")
        disk_graph = PipelineGraph(
            pipeline_name="pricing",
            preamble="DISK_HELPER = 1",
            preserved_blocks=["DISK_KEPT = 2"],
            nodes=[
                {
                    "id": "disk_child",
                    "data": {
                        "label": "disk_child",
                        "nodeType": "polars",
                        "config": {"code": "return df"},
                    },
                },
                {
                    "id": "disk_internal",
                    "data": {
                        "label": "disk_internal",
                        "nodeType": "polars",
                        "config": {"code": "return df"},
                    },
                },
            ],
            edges=[{"id": "disk_edge", "source": "disk_child", "target": "disk_internal"}],
        )
        flat_graph = PipelineGraph(
            pipeline_name="main", nodes=disk_graph.nodes, edges=disk_graph.edges
        )
        with (
            patch("haute.parser.parse_submodel_file", return_value=disk_graph) as parse_disk,
            patch("haute.graph_utils.flatten_graph", return_value=flat_graph) as flatten,
            patch("haute.codegen.graph_to_code", return_value="# regenerated\n"),
        ):
            response = client.post(
                "/api/submodel/dissolve",
                json=_dissolve_body(),
            )
        assert response.status_code == 200
        parse_disk.assert_not_called()
        flattened_graph = flatten.call_args_list[0].args[0]
        submitted = flattened_graph.submodels[DEFINITION_ID].graph
        assert [node.id for node in submitted.nodes] == ["base_rate"]

    def test_dissolve_does_not_read_sidecar_positions(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        modules_dir = tmp_path / "modules"
        modules_dir.mkdir()
        (modules_dir / "pricing.py").write_text("# valid disk module\n", encoding="utf-8")
        (modules_dir / "pricing.haute.json").write_text(
            '{"positions":{"base_rate":{"x":125.0,"y":260.0}}}',
            encoding="utf-8",
        )
        (tmp_path / "pipeline.py").write_text("# parent\n", encoding="utf-8")
        disk_graph = PipelineGraph(
            pipeline_name="pricing",
            nodes=[
                {
                    "id": "base_rate",
                    "data": {
                        "label": "base_rate",
                        "nodeType": "polars",
                        "config": {"code": "return df"},
                    },
                }
            ],
        )

        def flatten_authoritative_graph(graph: PipelineGraph, **_kwargs: object) -> PipelineGraph:
            if graph.submodels is None:
                return graph
            return PipelineGraph(
                pipeline_name="main",
                nodes=graph.submodels[DEFINITION_ID].graph.nodes,
            )

        with (
            patch("haute.parser.parse_submodel_file", return_value=disk_graph),
            patch("haute.graph_utils.flatten_graph", side_effect=flatten_authoritative_graph),
            patch("haute.codegen.graph_to_code", return_value="# regenerated\n"),
        ):
            response = client.post(
                "/api/submodel/dissolve",
                json=_dissolve_body(),
            )

        assert response.status_code == 200
        nodes = {node["id"]: node for node in response.json()["graph"]["nodes"]}
        assert nodes["base_rate"]["position"] == {"x": 0.0, "y": 0.0}

    def test_dissolve_does_not_run_codegen(self, client: TestClient, tmp_path: Path) -> None:
        """Pipeline metadata is persisted only by explicit Save."""
        modules_dir = tmp_path / "modules"
        modules_dir.mkdir()
        sm_file = modules_dir / "pricing.py"
        sm_file.write_text("# submodel code\n")

        pipeline_file = tmp_path / "pipeline.py"
        pipeline_file.write_text("# main pipeline\n")

        flat_graph = PipelineGraph(pipeline_name="main")

        with patch("haute.graph_utils.flatten_graph", return_value=flat_graph):
            with patch("haute.codegen.graph_to_code", return_value="# code\n") as mock_codegen:
                body = _dissolve_body()
                body["pipeline_description"] = "Risk scoring pipeline"
                resp = client.post("/api/submodel/dissolve", json=body)

        assert resp.status_code == 200
        mock_codegen.assert_not_called()

    def test_dissolve_retains_submodel_file_until_save(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        """Dissolve never deletes the child before explicit Save."""
        modules_dir = tmp_path / "modules"
        modules_dir.mkdir()
        sm_file = modules_dir / "pricing.py"
        sm_file.write_text("# code\n")

        pipeline_file = tmp_path / "pipeline.py"
        pipeline_file.write_text("# main\n")

        flat_graph = PipelineGraph(pipeline_name="main")

        with (
            patch("haute.graph_utils.flatten_graph", return_value=flat_graph),
            patch("haute.codegen.graph_to_code", return_value="# code\n"),
        ):
            response = client.post(
                "/api/submodel/dissolve",
                json=_dissolve_body(),
            )

        assert response.status_code == 200
        assert sm_file.exists()

    def test_dissolve_retains_configured_pipeline_module_until_save(
        self,
        client: TestClient,
        tmp_path: Path,
    ) -> None:
        """Dissolve leaves every filesystem location unchanged."""
        rating_root = _write_nested_project(tmp_path)
        rating_module = rating_root / "modules" / "pricing.py"
        root_module = tmp_path / "modules" / "pricing.py"
        rating_module.parent.mkdir(parents=True)
        root_module.parent.mkdir(parents=True)
        rating_module.write_text("# rating module\n")
        root_module.write_text("# root module\n")

        flat_graph = PipelineGraph(pipeline_name="main")

        with (
            patch("haute.graph_utils.flatten_graph", return_value=flat_graph),
            patch("haute.codegen.graph_to_code", return_value="# code\n"),
        ):
            resp = client.post(
                "/api/submodel/dissolve",
                json=_dissolve_body(source_file="rating/main.py"),
            )

        assert resp.status_code == 200
        assert rating_module.read_text() == "# rating module\n"
        assert root_module.read_text() == "# root module\n"

    def test_dissolve_does_not_touch_sidecars_or_main_file(
        self,
        client: TestClient,
        tmp_path: Path,
    ) -> None:
        """A patched sidecar writer is irrelevant because dissolve performs no I/O."""
        modules_dir = tmp_path / "modules"
        modules_dir.mkdir()
        sm_file = modules_dir / "pricing.py"
        sm_file.write_text("# submodel code\n")

        pipeline_file = tmp_path / "pipeline.py"
        original = "# original main\n"
        pipeline_file.write_text(original)

        flat_graph = PipelineGraph(pipeline_name="main")

        with (
            patch("haute.graph_utils.flatten_graph", return_value=flat_graph),
            patch("haute.codegen.graph_to_code", return_value="# regenerated main\n"),
            patch("haute.routes._save_pipeline.save_sidecar", side_effect=OSError("disk full")),
        ):
            resp = client.post(
                "/api/submodel/dissolve",
                json=_dissolve_body(),
            )

        assert resp.status_code == 200
        assert pipeline_file.read_text() == original
        assert sm_file.exists()

    def test_dissolve_never_attempts_to_unlink_the_child(
        self,
        client: TestClient,
        tmp_path: Path,
    ) -> None:
        """A locked child does not affect an in-memory dissolve."""
        modules_dir = tmp_path / "modules"
        modules_dir.mkdir()
        sm_file = modules_dir / "pricing.py"
        sm_file.write_text("# submodel code\n")

        pipeline_file = tmp_path / "pipeline.py"
        original = "# original main\n"
        pipeline_file.write_text(original)

        flat_graph = PipelineGraph(pipeline_name="main")
        path_type = type(sm_file)
        original_unlink = path_type.unlink

        def unlink_maybe_locked(self: Path, *args: object, **kwargs: object) -> None:
            if self == sm_file:
                raise PermissionError("submodel file is locked")
            original_unlink(self, *args, **kwargs)

        with (
            patch("haute.graph_utils.flatten_graph", return_value=flat_graph),
            patch("haute.codegen.graph_to_code", return_value="# regenerated main\n"),
            patch.object(path_type, "unlink", unlink_maybe_locked),
        ):
            resp = client.post(
                "/api/submodel/dissolve",
                json=_dissolve_body(),
            )

        assert resp.status_code == 200
        assert pipeline_file.read_text() == original
        assert sm_file.exists()


class TestTransformOnlySubmodelRoutes:
    def test_create_returns_an_unsaved_transform_without_calling_save(
        self,
        client: TestClient,
        tmp_path: Path,
    ) -> None:
        result = SimpleNamespace(
            sm_file="modules/pricing.py",
            graph=_graph_model(_graph_with_submodel()),
        )
        parent = tmp_path / "pipeline.py"

        with (
            _patch_parent_document(tmp_path, _simple_graph()),
            patch("haute.routes._submodel_ops.create_submodel_graph", return_value=result),
            patch(
                "haute.routes._save_pipeline.SavePipelineService.save_graph_transactionally",
                return_value=_saved_result(),
            ) as save,
        ):
            response = client.post("/api/submodel/create", json=_create_body())

        assert response.status_code == 200
        payload = response.json()
        assert payload["source_revision"] == _CURRENT_REVISION
        assert payload["graph"]["source_revision"] == _CURRENT_REVISION
        save.assert_not_called()
        assert parent.read_text(encoding="utf-8") == "# mocked parent document\n"
        assert not (tmp_path / "modules" / "pricing.py").exists()

    def test_dissolve_returns_an_unsaved_transform_without_calling_save(
        self,
        client: TestClient,
        tmp_path: Path,
    ) -> None:
        parent = tmp_path / "pipeline.py"
        child = tmp_path / "modules" / "pricing.py"
        child.parent.mkdir()
        child.write_text("# existing child\n", encoding="utf-8")
        flat_graph = _graph_model(_simple_graph())

        with (
            _patch_parent_document(tmp_path, _graph_with_submodel()),
            patch(
                "haute.parser.parse_submodel_file",
                return_value=PipelineGraph(pipeline_name="pricing"),
            ),
            patch("haute.graph_utils.flatten_graph", return_value=flat_graph),
            patch(
                "haute.routes._save_pipeline.SavePipelineService.save_graph_transactionally",
                return_value=_saved_result(),
            ) as save,
        ):
            response = client.post("/api/submodel/dissolve", json=_dissolve_body())

        assert response.status_code == 200
        payload = response.json()
        assert payload["source_revision"] == _CURRENT_REVISION
        assert payload["graph"]["source_revision"] == _CURRENT_REVISION
        assert "submodel_file_deleted" not in payload
        assert "retained_submodel_file" not in payload
        save.assert_not_called()
        assert parent.read_text(encoding="utf-8") == "# mocked parent document\n"
        assert child.read_text(encoding="utf-8") == "# existing child\n"

    def test_dissolve_accepts_a_definition_created_only_in_memory(
        self,
        client: TestClient,
        tmp_path: Path,
    ) -> None:
        flat_graph = _graph_model(_simple_graph())

        with (
            _patch_parent_document(tmp_path, _simple_graph()),
            patch("haute.graph_utils.flatten_graph", return_value=flat_graph) as flatten,
            patch(
                "haute.routes._save_pipeline.SavePipelineService.save_graph_transactionally"
            ) as save,
        ):
            response = client.post("/api/submodel/dissolve", json=_dissolve_body())

        assert response.status_code == 200
        payload = response.json()
        assert payload["definition_id"] == DEFINITION_ID
        assert payload["source_revision"] == _CURRENT_REVISION
        assert "retained_submodel_file" not in payload
        submitted = flatten.call_args.args[0]
        assert submitted.submodels[DEFINITION_ID].graph.nodes[0].id == "base_rate"
        save.assert_not_called()
