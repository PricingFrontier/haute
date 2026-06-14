"""Tests for haute.routes.submodel — create, get, dissolve endpoints."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import pytest

if TYPE_CHECKING:
    from fastapi.testclient import TestClient

from haute.graph_utils import PipelineGraph


@pytest.fixture(autouse=True)
def _isolated_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Run every test in a temporary directory."""
    monkeypatch.chdir(tmp_path)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _simple_graph() -> dict:
    """A minimal graph dict with two nodes and an edge."""
    return {
        "nodes": [
            {
                "id": "load",
                "data": {"label": "load", "nodeType": "dataSource", "config": {"path": "data.csv"}},
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
                "data": {"label": "load", "nodeType": "dataSource", "config": {"path": "d.csv"}},
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
    (rating_root / "main.py").write_text("# main pipeline\n", encoding="utf-8")
    return rating_root


# ---------------------------------------------------------------------------
# POST /api/submodel/create
# ---------------------------------------------------------------------------


class TestCreateSubmodel:
    def test_invalid_node_ids(self, client: TestClient) -> None:
        """Requesting node IDs that don't exist should return 400."""
        body = {
            "name": "pricing",
            "node_ids": ["nonexistent"],
            "graph": _simple_graph(),
            "source_file": "pipeline.py",
        }
        resp = client.post("/api/submodel/create", json=body)
        assert resp.status_code == 400

    def test_too_few_nodes(self, client: TestClient) -> None:
        """A submodel must contain at least 2 nodes."""
        body = {
            "name": "pricing",
            "node_ids": ["calc"],
            "graph": _simple_graph(),
            "source_file": "",
        }
        resp = client.post("/api/submodel/create", json=body)
        assert resp.status_code == 400

    def test_successful_create(self, client: TestClient, tmp_path: Path) -> None:
        """Happy path: creates submodel file and returns updated graph."""
        mock_result = MagicMock()
        mock_result.sm_file = "modules/pricing.py"
        mock_result.graph = PipelineGraph(
            pipeline_name="main",
            submodels={"pricing": {"file": "modules/pricing.py", "graph": {"nodes": []}}},
        )

        with patch("haute.routes._submodel_ops.create_submodel_graph", return_value=mock_result):
            with patch("haute.codegen.graph_to_code_multi", return_value={}):
                body = {
                    "name": "pricing",
                    "node_ids": ["calc"],
                    "graph": _simple_graph(),
                    "source_file": "pipeline.py",
                    "pipeline_name": "main",
                }
                resp = client.post("/api/submodel/create", json=body)

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["submodel_file"] == "modules/pricing.py"

    def test_create_passes_pipeline_description(self, client: TestClient, tmp_path: Path) -> None:
        """pipeline_description should be forwarded to graph_to_code_multi."""
        mock_result = MagicMock()
        mock_result.sm_file = "modules/pricing.py"
        mock_result.graph = PipelineGraph(
            pipeline_name="main",
            submodels={"pricing": {"file": "modules/pricing.py", "graph": {"nodes": []}}},
        )

        with patch("haute.routes._submodel_ops.create_submodel_graph", return_value=mock_result):
            with patch("haute.codegen.graph_to_code_multi", return_value={}) as mock_codegen:
                body = {
                    "name": "pricing",
                    "node_ids": ["calc"],
                    "graph": _simple_graph(),
                    "source_file": "pipeline.py",
                    "pipeline_name": "main",
                    "pipeline_description": "My pricing pipeline",
                }
                resp = client.post("/api/submodel/create", json=body)

        assert resp.status_code == 200
        mock_codegen.assert_called_once()
        call_kwargs = mock_codegen.call_args
        assert call_kwargs.kwargs.get("description") == "My pricing pipeline"

    def test_create_rejects_unallowlisted_codegen_path_and_rolls_back(
        self,
        client: TestClient,
        tmp_path: Path,
    ) -> None:
        """Submodel create must use the same output allowlist + rollback as save."""
        mock_result = MagicMock()
        mock_result.sm_file = "modules/pricing.py"
        mock_result.graph = PipelineGraph(pipeline_name="main", submodels={"pricing": {}})

        body = {
            "name": "pricing",
            "node_ids": ["load", "calc"],
            "graph": _simple_graph(),
            "source_file": "pipeline.py",
            "pipeline_name": "main",
        }

        with patch("haute.routes._submodel_ops.create_submodel_graph", return_value=mock_result):
            with patch(
                "haute.codegen.graph_to_code_multi",
                return_value={
                    "pipeline.py": "# generated main\n",
                    "config/escaped.py": "# not an allowed codegen output\n",
                },
            ):
                resp = client.post("/api/submodel/create", json=body)

        assert resp.status_code == 400
        assert not (tmp_path / "pipeline.py").exists()
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
                "nodeType": "dataSource",
                "config": {"path": "data.parquet"},
            },
        }
        mock_result = MagicMock()
        mock_result.sm_file = "modules/pricing.py"
        mock_result.graph = PipelineGraph(
            pipeline_name="main",
            nodes=[],
            submodels={
                "pricing": {
                    "file": "modules/pricing.py",
                    "graph": {"nodes": [child], "edges": []},
                },
            },
        )

        with patch("haute.routes._submodel_ops.create_submodel_graph", return_value=mock_result):
            with patch(
                "haute.codegen.graph_to_code_multi",
                return_value={
                    "rating/main.py": "# main\n",
                    "modules/pricing.py": "# submodel\n",
                },
            ):
                body = {
                    "name": "pricing",
                    "node_ids": ["load", "calc"],
                    "graph": _simple_graph(),
                    "source_file": "rating/main.py",
                    "pipeline_name": "main",
                }
                resp = client.post("/api/submodel/create", json=body)

        assert resp.status_code == 200
        assert (rating_root / "modules" / "pricing.py").exists()
        assert (rating_root / "config" / "data_source" / "child_source.json").exists()
        assert not (tmp_path / "modules" / "pricing.py").exists()
        assert not (tmp_path / "config").exists()


# ---------------------------------------------------------------------------
# GET /api/submodel/{name}
# ---------------------------------------------------------------------------


class TestGetSubmodel:
    def test_submodel_not_found(self, client: TestClient) -> None:
        resp = client.get("/api/submodel/nonexistent")
        assert resp.status_code == 404
        assert "not found" in resp.json()["detail"]

    def test_successful_get(self, client: TestClient, tmp_path: Path) -> None:
        """Create a submodel file and fetch it."""
        modules_dir = tmp_path / "modules"
        modules_dir.mkdir()
        sm_file = modules_dir / "pricing.py"
        sm_file.write_text("""\
import polars as pl
import haute

submodel = haute.Submodel("pricing", description="Test submodel")

@submodel.polars
def base_rate(df: pl.LazyFrame) -> pl.LazyFrame:
    return df
""")
        resp = client.get("/api/submodel/pricing")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["submodel_name"] == "pricing"
        assert len(data["graph"]["nodes"]) >= 1

    def test_name_with_dots_returns_404(self, client: TestClient, tmp_path: Path) -> None:
        """A name like '..something' still resolves to modules/ and 404s if not found."""
        resp = client.get("/api/submodel/..something")
        assert resp.status_code == 404

    def test_get_uses_configured_pipeline_root(
        self,
        client: TestClient,
        tmp_path: Path,
    ) -> None:
        """Submodel drill-down reads rating/modules and rating/config."""
        rating_root = _write_nested_project(tmp_path)
        config_dir = rating_root / "config" / "data_source"
        config_dir.mkdir(parents=True)
        (config_dir / "source.json").write_text('{"path": "rating-data.parquet"}')
        modules_dir = rating_root / "modules"
        modules_dir.mkdir()
        (modules_dir / "pricing.py").write_text(
            """\
import polars as pl
import haute

submodel = haute.Submodel("pricing")


@submodel.data_source(config="config/data_source/source.json")
def source() -> pl.LazyFrame:
    return pl.scan_parquet("rating-data.parquet")
""",
            encoding="utf-8",
        )

        resp = client.get("/api/submodel/pricing")

        assert resp.status_code == 200
        data = resp.json()
        node = data["graph"]["nodes"][0]
        assert node["data"]["config"]["path"] == "rating-data.parquet"

    def test_get_falls_back_to_legacy_project_root_module(
        self,
        client: TestClient,
        tmp_path: Path,
    ) -> None:
        """Drill-down matches parser compatibility for root modules."""
        _write_nested_project(tmp_path)
        config_dir = tmp_path / "config" / "data_source"
        config_dir.mkdir(parents=True)
        (config_dir / "source.json").write_text('{"path": "root-data.parquet"}')
        modules_dir = tmp_path / "modules"
        modules_dir.mkdir()
        (modules_dir / "pricing.py").write_text(
            """\
import polars as pl
import haute

submodel = haute.Submodel("pricing")


@submodel.data_source(config="config/data_source/source.json")
def source() -> pl.LazyFrame:
    return pl.scan_parquet("root-data.parquet")
""",
            encoding="utf-8",
        )

        resp = client.get("/api/submodel/pricing")

        assert resp.status_code == 200
        data = resp.json()
        node = data["graph"]["nodes"][0]
        assert node["data"]["config"]["path"] == "root-data.parquet"


# ---------------------------------------------------------------------------
# POST /api/submodel/dissolve
# ---------------------------------------------------------------------------


class TestDissolveSubmodel:
    def test_submodel_not_in_graph(self, client: TestClient) -> None:
        body = {
            "submodel_name": "nonexistent",
            "graph": _simple_graph(),
            "source_file": "pipeline.py",
        }
        resp = client.post("/api/submodel/dissolve", json=body)
        assert resp.status_code == 404
        assert "not found" in resp.json()["detail"]

    def test_missing_source_file(self, client: TestClient) -> None:
        body = {
            "submodel_name": "pricing",
            "graph": _graph_with_submodel(),
            "source_file": "",
        }
        resp = client.post("/api/submodel/dissolve", json=body)
        assert resp.status_code == 400
        assert "source_file" in resp.json()["detail"]

    def test_successful_dissolve(self, client: TestClient, tmp_path: Path) -> None:
        """Happy path: dissolves submodel, writes code, deletes file."""
        # Create the submodel file so it can be deleted
        modules_dir = tmp_path / "modules"
        modules_dir.mkdir()
        sm_file = modules_dir / "pricing.py"
        sm_file.write_text("# submodel code\n")

        # Create the main pipeline file path
        pipeline_file = tmp_path / "pipeline.py"
        pipeline_file.write_text("# main pipeline\n")

        flat_graph = PipelineGraph(pipeline_name="main")

        with patch("haute._flatten.flatten_graph", return_value=flat_graph):
            with patch("haute.codegen.graph_to_code", return_value="# code\n"):
                body = {
                    "submodel_name": "pricing",
                    "graph": _graph_with_submodel(),
                    "source_file": "pipeline.py",
                    "pipeline_name": "main",
                }
                resp = client.post("/api/submodel/dissolve", json=body)

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"

    def test_dissolve_passes_pipeline_description(self, client: TestClient, tmp_path: Path) -> None:
        """pipeline_description should be forwarded to graph_to_code."""
        modules_dir = tmp_path / "modules"
        modules_dir.mkdir()
        sm_file = modules_dir / "pricing.py"
        sm_file.write_text("# submodel code\n")

        pipeline_file = tmp_path / "pipeline.py"
        pipeline_file.write_text("# main pipeline\n")

        flat_graph = PipelineGraph(pipeline_name="main")

        with patch("haute._flatten.flatten_graph", return_value=flat_graph):
            with patch("haute.codegen.graph_to_code", return_value="# code\n") as mock_codegen:
                body = {
                    "submodel_name": "pricing",
                    "graph": _graph_with_submodel(),
                    "source_file": "pipeline.py",
                    "pipeline_name": "main",
                    "pipeline_description": "Risk scoring pipeline",
                }
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

        with patch("haute._flatten.flatten_graph", return_value=flat_graph):
            with patch("haute.codegen.graph_to_code", return_value="# code\n"):
                body = {
                    "submodel_name": "pricing",
                    "graph": _graph_with_submodel(),
                    "source_file": "pipeline.py",
                    "pipeline_name": "main",
                }
                client.post("/api/submodel/dissolve", json=body)

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

        with patch("haute._flatten.flatten_graph", return_value=flat_graph):
            with patch("haute.codegen.graph_to_code", return_value="# code\n"):
                body = {
                    "submodel_name": "pricing",
                    "graph": _graph_with_submodel(),
                    "source_file": "rating/main.py",
                    "pipeline_name": "main",
                }
                resp = client.post("/api/submodel/dissolve", json=body)

        assert resp.status_code == 200
        assert not rating_module.exists()
        assert root_module.read_text() == "# root module\n"

    def test_dissolve_deletes_legacy_project_root_module_when_no_local_module(
        self,
        client: TestClient,
        tmp_path: Path,
    ) -> None:
        """Dissolve deletes the same legacy root module the parser loaded."""
        _write_nested_project(tmp_path)
        root_module = tmp_path / "modules" / "pricing.py"
        root_module.parent.mkdir(parents=True)
        root_module.write_text("# root module\n")

        flat_graph = PipelineGraph(pipeline_name="main")

        with patch("haute._flatten.flatten_graph", return_value=flat_graph):
            with patch("haute.codegen.graph_to_code", return_value="# code\n"):
                body = {
                    "submodel_name": "pricing",
                    "graph": _graph_with_submodel(),
                    "source_file": "rating/main.py",
                    "pipeline_name": "main",
                }
                resp = client.post("/api/submodel/dissolve", json=body)

        assert resp.status_code == 200
        assert not root_module.exists()

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
            patch("haute._flatten.flatten_graph", return_value=flat_graph),
            patch("haute.codegen.graph_to_code", return_value="# regenerated main\n"),
            patch("haute.routes._save_pipeline.save_sidecar", side_effect=OSError("disk full")),
        ):
            resp = client.post(
                "/api/submodel/dissolve",
                json={
                    "submodel_name": "pricing",
                    "graph": _graph_with_submodel(),
                    "source_file": "pipeline.py",
                    "pipeline_name": "main",
                },
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
            patch("haute._flatten.flatten_graph", return_value=flat_graph),
            patch("haute.codegen.graph_to_code", return_value="# regenerated main\n"),
            patch.object(path_type, "unlink", unlink_maybe_locked),
        ):
            resp = client.post(
                "/api/submodel/dissolve",
                json={
                    "submodel_name": "pricing",
                    "graph": _graph_with_submodel(),
                    "source_file": "pipeline.py",
                    "pipeline_name": "main",
                },
            )

        assert resp.status_code == 500
        assert pipeline_file.read_text() == original
        assert sm_file.exists()
