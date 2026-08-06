"""Focused end-to-end contracts for safe submodel mutations and drill-down."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest

if TYPE_CHECKING:
    from fastapi.testclient import TestClient

from haute.routes._helpers import invalidate_pipeline_index, parse_pipeline_to_graph, pipeline_dir

DEFINITION_ID = "pricing-definition"
INSTANCE_ID = "pricing-instance"
ALIAS = "pricing"


@pytest.fixture(autouse=True)
def _isolated_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    pipeline_dir.cache_clear()
    invalidate_pipeline_index()
    yield
    pipeline_dir.cache_clear()
    invalidate_pipeline_index()


def _write_flat_parent(root: Path, *, relative: str = "main.py") -> Path:
    parent = root / relative
    parent.parent.mkdir(parents=True, exist_ok=True)
    parent.write_text(
        """import polars as pl
import haute

pipeline = haute.Pipeline("main")

@pipeline.polars
def first() -> pl.LazyFrame:
    return pl.DataFrame({"x": [1]}).lazy()

@pipeline.polars
def second(first: pl.LazyFrame) -> pl.LazyFrame:
    return first
""",
        encoding="utf-8",
    )
    return parent


def _write_child(path: Path, *, node_name: str = "base_rate") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"""import polars as pl
import haute

CHILD_HELPER = {node_name!r}

submodel = haute.Submodel(
    "pricing", definition_id="pricing-definition", input_ports=[], output_ports=[]
)

@submodel.polars
def {node_name}() -> pl.LazyFrame:
    return pl.DataFrame({{"rate": [1.0]}}).lazy()
""",
        encoding="utf-8",
    )


def _write_parent_with_child(
    root: Path,
    *,
    parent_relative: str = "main.py",
    child_reference: str = "modules/pricing.py",
    node_name: str = "base_rate",
    managed_parent: str | None = None,
) -> tuple[Path, Path, Path]:
    parent = root / parent_relative
    parent.parent.mkdir(parents=True, exist_ok=True)
    child = parent.parent / child_reference
    _write_child(child, node_name=node_name)
    parent.write_text(
        f"""import haute

pipeline = haute.Pipeline({parent.stem!r})
pipeline.submodel(
    {child_reference!r}, definition_id="pricing-definition",
    instance_id="pricing-instance", alias="pricing",
)
""",
        encoding="utf-8",
    )
    sidecar = child.with_suffix(".haute.json")
    sidecar_payload: dict[str, object] = {
        "positions": {node_name: {"x": 40.0, "y": 80.0}},
    }
    if managed_parent is not None:
        sidecar_payload["managed_parent"] = managed_parent
    sidecar.write_text(json.dumps(sidecar_payload), encoding="utf-8")
    return parent, child, sidecar


def _current_graph(parent: Path, root: Path):
    graph = parse_pipeline_to_graph(parent, project_root=root)
    assert isinstance(graph.source_revision, str) and graph.source_revision
    return graph


def _dissolve_body(graph, *, source_file: str = "main.py") -> dict[str, object]:
    return {
        "instance_id": INSTANCE_ID,
        "graph": graph.model_dump(mode="json"),
        "preamble": graph.preamble or "",
        "preserved_blocks": graph.preserved_blocks,
        "source_file": source_file,
        "base_revision": graph.source_revision,
        "pipeline_name": graph.pipeline_name or "main",
    }


def test_create_rejects_stale_revision_before_transform_or_save(
    client: TestClient,
    haute_scratch: Path,
) -> None:
    parent = _write_flat_parent(haute_scratch)
    graph = _current_graph(parent, haute_scratch)
    (haute_scratch / "main.py").write_text(
        parent.read_text(encoding="utf-8") + "\n# external edit\n", encoding="utf-8"
    )
    body = {
        "name": "pricing",
        "node_ids": ["first", "second"],
        "graph": graph.model_dump(mode="json"),
        "source_file": "main.py",
        "base_revision": graph.source_revision,
    }

    with (
        patch("haute.routes._submodel_ops.create_submodel_graph") as transform,
        patch("haute.routes._save_pipeline.SavePipelineService.save_graph_transactionally") as save,
    ):
        response = client.post("/api/submodel/create", json=body)

    assert response.status_code == 409
    transform.assert_not_called()
    save.assert_not_called()


def test_create_refuses_existing_module_under_different_casing(
    client: TestClient,
    tmp_path: Path,
) -> None:
    parent = _write_flat_parent(tmp_path)
    graph = _current_graph(parent, tmp_path)
    parent_before = parent.read_bytes()
    existing = tmp_path / "modules" / "Pricing.py"
    existing.parent.mkdir()
    existing_before = b"# hand-authored pricing module\n"
    existing.write_bytes(existing_before)

    response = client.post(
        "/api/submodel/create",
        json={
            "name": "pricing",
            "node_ids": ["first", "second"],
            "graph": graph.model_dump(mode="json"),
            "preamble": graph.preamble or "",
            "preserved_blocks": graph.preserved_blocks,
            "source_file": "main.py",
            "base_revision": graph.source_revision,
        },
    )

    assert response.status_code == 409
    assert parent.read_bytes() == parent_before
    assert existing.read_bytes() == existing_before
    assert not (tmp_path / "main.haute.json").exists()


def test_drill_down_requires_parent_source_file(client: TestClient) -> None:
    response = client.get(f"/api/submodel/{DEFINITION_ID}")
    assert response.status_code == 400
    assert "source_file" in response.json()["detail"]


def test_drill_down_is_scoped_when_two_parents_reuse_name(
    client: TestClient,
    tmp_path: Path,
) -> None:
    _write_parent_with_child(
        tmp_path,
        parent_relative="one/main.py",
        child_reference="lib/pricing.py",
        node_name="one_rate",
    )
    _write_parent_with_child(
        tmp_path,
        parent_relative="two/main.py",
        child_reference="lib/pricing.py",
        node_name="two_rate",
    )

    response = client.get(
        f"/api/submodel/{DEFINITION_ID}",
        params={"source_file": "two/main.py"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["submodel_file"] == "lib/pricing.py"
    assert {node["id"] for node in payload["graph"]["nodes"]} == {"two_rate"}


def test_dissolve_retains_uniquely_owned_child_and_sidecar_until_save(
    client: TestClient,
    tmp_path: Path,
) -> None:
    parent, child, sidecar = _write_parent_with_child(
        tmp_path,
        managed_parent="main.py",
    )
    graph = _current_graph(parent, tmp_path)

    response = client.post("/api/submodel/dissolve", json=_dissolve_body(graph))

    assert response.status_code == 200
    payload = response.json()
    assert "submodel_file_deleted" not in payload
    assert "retained_submodel_file" not in payload
    assert payload["source_revision"]
    assert child.exists()
    assert sidecar.exists()
    assert parent.exists()


def test_dissolve_retains_unowned_child_without_exposing_lifecycle_state(
    client: TestClient,
    tmp_path: Path,
) -> None:
    parent, child, sidecar = _write_parent_with_child(tmp_path)
    graph = _current_graph(parent, tmp_path)

    response = client.post("/api/submodel/dissolve", json=_dissolve_body(graph))

    assert response.status_code == 200
    payload = response.json()
    assert "submodel_file_deleted" not in payload
    assert "retained_submodel_file" not in payload
    assert child.exists()
    assert sidecar.exists()


def test_dissolve_rejects_non_object_submitted_metadata(
    client: TestClient,
    tmp_path: Path,
) -> None:
    parent, child, sidecar = _write_parent_with_child(tmp_path)
    graph = _current_graph(parent, tmp_path)
    body = _dissolve_body(graph)
    submitted_graph = dict(body["graph"])
    submitted_graph["submodels"] = {"pricing": "not-an-object"}
    body["graph"] = submitted_graph
    parent_before = parent.read_bytes()

    response = client.post("/api/submodel/dissolve", json=body)

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert isinstance(detail, list)
    assert any("submodel" in str(error).lower() for error in detail)
    assert parent.read_bytes() == parent_before
    assert child.exists()
    assert sidecar.exists()


def test_dissolve_retains_child_referenced_by_another_pipeline(
    client: TestClient,
    tmp_path: Path,
) -> None:
    parent, child, sidecar = _write_parent_with_child(
        tmp_path,
        managed_parent="main.py",
    )
    (tmp_path / "other.py").write_text(
        """import haute

pipeline = haute.Pipeline("other")
pipeline.submodel(
    "modules/pricing.py",
    definition_id="pricing-definition",
    instance_id="pricing-instance",
    alias="pricing",
)
""",
        encoding="utf-8",
    )
    graph = _current_graph(parent, tmp_path)

    response = client.post("/api/submodel/dissolve", json=_dissolve_body(graph))

    assert response.status_code == 200
    assert "submodel_file_deleted" not in response.json()
    assert child.exists()
    assert sidecar.exists()


def test_dissolve_retains_child_when_sibling_audit_is_incomplete(
    client: TestClient,
    tmp_path: Path,
) -> None:
    parent, child, sidecar = _write_parent_with_child(
        tmp_path,
        managed_parent="main.py",
    )
    (tmp_path / "broken.py").write_text(
        """import haute

pipeline = haute.Pipeline("broken")
pipeline.submodel(
    "modules/missing.py",
    definition_id="missing-definition",
    instance_id="missing-instance",
    alias="missing",
)
""",
        encoding="utf-8",
    )
    graph = _current_graph(parent, tmp_path)

    response = client.post("/api/submodel/dissolve", json=_dissolve_body(graph))

    assert response.status_code == 200
    assert "submodel_file_deleted" not in response.json()
    assert child.exists()
    assert sidecar.exists()
