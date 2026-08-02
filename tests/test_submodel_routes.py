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
from haute.graph_utils import NodeType, PipelineGraph, _sanitize_func_name
from haute.routes._helpers import invalidate_pipeline_index, pipeline_dir

_CURRENT_REVISION = "revision-current"
_SAVED_REVISION = "revision-saved"


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
                "id": "submodel__pricing",
                "type": "submodel",
                "data": {
                    "label": "pricing",
                    "nodeType": "submodel",
                    "config": {
                        "file": "modules/pricing.py",
                        "childNodeIds": ["base_rate"],
                        "inputPorts": [],
                        "outputPorts": [],
                    },
                },
            },
        ],
        "edges": [],
        "submodels": {
            "pricing": {
                "file": "modules/pricing.py",
                "childNodeIds": ["base_rate"],
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
        "submodel_name": "pricing",
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

submodel = haute.Submodel("pricing")

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
pipeline.submodel({child_reference!r})
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
        """Happy path: creates submodel file and returns updated graph."""
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
        assert payload["source_revision"] == _SAVED_REVISION
        assert payload["graph"]["source_revision"] == _SAVED_REVISION
        assert save.call_args.kwargs["require_absent_module_files"] == ["modules/pricing.py"]
        assert save.call_args.kwargs["claim_managed_module_files"] == ["modules/pricing.py"]

    def test_create_passes_pipeline_description(self, client: TestClient, tmp_path: Path) -> None:
        """pipeline_description is forwarded through the shared save service."""
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
        assert save.call_args.kwargs["description"] == "My pricing pipeline"

    def test_create_rejects_unallowlisted_codegen_path_and_rolls_back(
        self,
        client: TestClient,
        tmp_path: Path,
    ) -> None:
        """Submodel create must use the same output allowlist + rollback as save."""
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
            ),
        ):
            response = client.post("/api/submodel/create", json=_create_body())

        assert response.status_code == 400
        assert (tmp_path / "pipeline.py").read_text(encoding="utf-8") == (
            "# mocked parent document\n"
        )
        assert not (tmp_path / "config" / "escaped.py").exists()

    def test_create_uses_configured_pipeline_root(
        self,
        client: TestClient,
        tmp_path: Path,
    ) -> None:
        """Submodel create writes modules and configs beside rating/main.py."""
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
                    "pricing": {
                        "file": "modules/pricing.py",
                        "childNodeIds": ["child_source"],
                        "inputPorts": [],
                        "outputPorts": [],
                        "managed": True,
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
        assert (rating_root / "modules" / "pricing.py").exists()
        assert (rating_root / child_config_path).exists()
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
            "/api/submodel/pricing",
            params={"source_file": "pipeline.py"},
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["status"] == "ok"
        assert payload["submodel_name"] == "pricing"
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

submodel = haute.Submodel("pricing")


@submodel.data_input(config="config/data_input/source.json")
def source() -> pl.LazyFrame:
    return pl.scan_parquet("rating-data.parquet")
""",
            encoding="utf-8",
        )
        _write_parent_reference(rating_root / "main.py", "modules/pricing.py")

        resp = client.get(
            "/api/submodel/pricing",
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
            'import polars as pl\nimport haute\nsubmodel = haute.Submodel("pricing")\n'
            "@submodel.polars\ndef rate(df: pl.LazyFrame) -> pl.LazyFrame:\n    return df\n",
            encoding="utf-8",
        )
        (rating_root / "main.py").write_text(
            'import haute\npipeline = haute.Pipeline("main")\n'
            'pipeline.submodel("lib/pricing.py")\n',
            encoding="utf-8",
        )
        response = client.get(
            "/api/submodel/pricing",
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
            'import polars as pl\nimport haute\nsubmodel = haute.Submodel("pricing")\n'
            "@submodel.polars\ndef rate(df: pl.LazyFrame) -> pl.LazyFrame:\n    return df\n",
            encoding="utf-8",
        )
        (tmp_path / "a_broken.py").write_text(
            'import haute\npipeline = haute.Pipeline("broken")\n'
            'pipeline.submodel("modules/missing.py")\n',
            encoding="utf-8",
        )
        (tmp_path / "z_owner.py").write_text(
            'import haute\npipeline = haute.Pipeline("owner")\n'
            'pipeline.submodel("modules/pricing.py")\n',
            encoding="utf-8",
        )

        response = client.get(
            "/api/submodel/pricing",
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
        body["submodel_name"] = "nonexistent"
        resp = client.post("/api/submodel/dissolve", json=body)
        assert resp.status_code == 404
        assert "not found" in resp.json()["detail"]

    def test_missing_source_file(self, client: TestClient) -> None:
        body = _dissolve_body(source_file="")
        resp = client.post("/api/submodel/dissolve", json=body)
        assert resp.status_code == 400
        assert "source_file" in resp.json()["detail"]

    def test_successful_dissolve(self, client: TestClient, tmp_path: Path) -> None:
        """Happy path: dissolves the graph and returns the new revision."""
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
        assert data["source_revision"] == _SAVED_REVISION
        assert data["submodel_file_deleted"] is False
        assert data["retained_submodel_file"] == "modules/pricing.py"

    def test_dissolve_reparses_disk_submodel_before_flattening(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        """The request graph is stale; the recorded module on disk is authoritative."""
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
        parse_disk.assert_called_once()
        flattened_graph = flatten.call_args_list[0].args[0]
        disk_meta = flattened_graph.submodels["pricing"]["graph"]
        assert {node["id"] for node in disk_meta["nodes"]} == {"disk_child", "disk_internal"}
        assert [
            {key: edge[key] for key in ("id", "source", "target")} for edge in disk_meta["edges"]
        ] == [{"id": "disk_edge", "source": "disk_child", "target": "disk_internal"}]
        assert disk_meta["preamble"] == "DISK_HELPER = 1"
        assert disk_meta["preserved_blocks"] == ["DISK_KEPT = 2"]

    def test_dissolve_applies_authoritative_sidecar_positions(
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

        with (
            patch("haute.parser.parse_submodel_file", return_value=disk_graph),
            patch("haute.codegen.graph_to_code", return_value="# regenerated\n"),
        ):
            response = client.post(
                "/api/submodel/dissolve",
                json=_dissolve_body(),
            )

        assert response.status_code == 200
        nodes = {node["id"]: node for node in response.json()["graph"]["nodes"]}
        assert nodes["base_rate"]["position"] == {"x": 125.0, "y": 260.0}

    def test_dissolve_passes_pipeline_description(self, client: TestClient, tmp_path: Path) -> None:
        """pipeline_description should be forwarded to graph_to_code."""
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
        mock_codegen.assert_called_once()
        call_kwargs = mock_codegen.call_args
        assert call_kwargs.kwargs.get("description") == "Risk scoring pipeline"

    def test_dissolve_deletes_submodel_file(self, client: TestClient, tmp_path: Path) -> None:
        """After dissolve, the submodel .py file should be deleted."""
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
            patch("haute.routes.submodel._can_delete_managed_child", return_value=True),
        ):
            client.post(
                "/api/submodel/dissolve",
                json=_dissolve_body(),
            )

        assert not sm_file.exists()

    def test_dissolve_deletes_configured_pipeline_module(
        self,
        client: TestClient,
        tmp_path: Path,
    ) -> None:
        """Dissolve removes rating/modules file and leaves root modules alone."""
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
            patch("haute.routes.submodel._can_delete_managed_child", return_value=True),
        ):
            resp = client.post(
                "/api/submodel/dissolve",
                json=_dissolve_body(source_file="rating/main.py"),
            )

        assert resp.status_code == 200
        assert not rating_module.exists()
        assert root_module.read_text() == "# root module\n"

    def test_dissolve_sidecar_failure_rolls_back_main_file(
        self,
        client: TestClient,
        tmp_path: Path,
    ) -> None:
        """Submodel dissolve must restore already-written code when a later write fails."""
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

        assert resp.status_code == 500
        assert pipeline_file.read_text() == original
        assert sm_file.exists()

    def test_dissolve_delete_failure_rolls_back_main_file(
        self,
        client: TestClient,
        tmp_path: Path,
    ) -> None:
        """Submodel file delete failure must roll back the parent graph save."""
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
            patch("haute.routes.submodel._can_delete_managed_child", return_value=True),
            patch.object(path_type, "unlink", unlink_maybe_locked),
        ):
            resp = client.post(
                "/api/submodel/dissolve",
                json=_dissolve_body(),
            )

        assert resp.status_code == 500
        assert pipeline_file.read_text() == original
        assert sm_file.exists()
