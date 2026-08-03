"""Explicit-Save filesystem contracts for submodel definition lifecycle."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import HTTPException

from haute.routes._save_pipeline import SavePipelineService
from haute.schemas import SavePipelineRequest
from tests.conftest import make_graph


def _flat_graph():
    return make_graph(
        {
            "pipeline_name": "main",
            "nodes": [
                {
                    "id": "root",
                    "position": {"x": 10.0, "y": 20.0},
                    "data": {
                        "label": "root",
                        "nodeType": "polars",
                        "config": {"code": "return df"},
                    },
                }
            ],
            "edges": [],
        }
    )


def _managed_graph(
    definition_id: str = "child",
    module_file: str = "modules/child.py",
):
    child = {
        "id": "child_node",
        "position": {"x": 17.0, "y": 29.0},
        "data": {
            "label": "child_node",
            "nodeType": "polars",
            "config": {"code": "return df"},
        },
    }
    return make_graph(
        {
            "pipeline_name": "main",
            "nodes": [
                {
                    "id": "child_instance",
                    "type": "submodel",
                    "position": {"x": 50.0, "y": 60.0},
                    "data": {
                        "label": "child",
                        "nodeType": "submodel",
                        "config": {
                            "definitionId": definition_id,
                            "alias": "child",
                        },
                    },
                }
            ],
            "edges": [],
            "submodels": {
                definition_id: {
                    "definitionId": definition_id,
                    "file": module_file,
                    "inputPorts": [],
                    "outputPorts": [],
                    "graph": {
                        "nodes": [child],
                        "edges": [],
                        "pipeline_name": "child",
                        "preamble": "HELPER = 1",
                        "preserved_blocks": ["KEPT = 2"],
                        "source_file": module_file,
                    },
                }
            },
        }
    )


def _service(tmp_path: Path) -> SavePipelineService:
    return SavePipelineService(project_root=tmp_path, pipeline_root=tmp_path)


def _save_request(graph) -> SavePipelineRequest:
    return SavePipelineRequest(
        name="main",
        description="",
        graph=graph,
        preamble="",
        source_file="main.py",
        sources=["live"],
        active_source="live",
        preserved_blocks=[],
    )


def test_create_no_clobber_is_case_insensitive_and_precedes_writes(tmp_path: Path) -> None:
    service = _service(tmp_path)
    service.save_graph_transactionally(
        graph=_flat_graph(),
        name="main",
        description="",
        preamble="",
        source_file="main.py",
    )
    parent = tmp_path / "main.py"
    parent_sidecar = tmp_path / "main.haute.json"
    parent_original = parent.read_bytes()
    parent_sidecar_original = parent_sidecar.read_bytes()
    modules = tmp_path / "modules"
    modules.mkdir(exist_ok=True)
    existing = modules / "Pricing.py"
    existing.write_bytes(b"# hand-authored child\n")

    with pytest.raises(HTTPException) as exc_info:
        service.save_graph_transactionally(
            graph=_managed_graph(
                definition_id="pricing",
                module_file="modules/pricing.py",
            ),
            name="main",
            description="",
            preamble="",
            source_file="main.py",
        )

    assert exc_info.value.status_code == 409
    assert parent.read_bytes() == parent_original
    assert parent_sidecar.read_bytes() == parent_sidecar_original
    assert existing.read_bytes() == b"# hand-authored child\n"
    assert not (modules / "pricing.haute.json").exists()


def test_create_no_clobber_rejects_orphan_child_sidecar(tmp_path: Path) -> None:
    service = _service(tmp_path)
    service.save_graph_transactionally(
        graph=_flat_graph(),
        name="main",
        description="",
        preamble="",
        source_file="main.py",
    )
    parent = tmp_path / "main.py"
    parent_sidecar = tmp_path / "main.haute.json"
    parent_original = parent.read_bytes()
    parent_sidecar_original = parent_sidecar.read_bytes()
    modules = tmp_path / "modules"
    modules.mkdir(exist_ok=True)
    orphan_sidecar = modules / "Child.haute.json"
    sidecar_original = b'{"managed_parent":"other.py"}\n'
    orphan_sidecar.write_bytes(sidecar_original)

    with pytest.raises(HTTPException) as exc_info:
        service.save_graph_transactionally(
            graph=_managed_graph(),
            name="main",
            description="",
            preamble="",
            source_file="main.py",
        )

    assert exc_info.value.status_code == 409
    assert parent.read_bytes() == parent_original
    assert parent_sidecar.read_bytes() == parent_sidecar_original
    assert orphan_sidecar.read_bytes() == sidecar_original
    assert not (modules / "child.py").exists()


def test_managed_child_gets_owner_and_position_sidecar(tmp_path: Path) -> None:
    response = _service(tmp_path).save_graph_transactionally(
        graph=_managed_graph(),
        name="main",
        description="",
        preamble="",
        source_file="main.py",
    )

    sidecar_path = tmp_path / "modules" / "child.haute.json"
    payload = json.loads(sidecar_path.read_text(encoding="utf-8"))
    assert payload["managed_parent"] == "main.py"
    assert payload["positions"]["child_node"] == {"x": 17.0, "y": 29.0}
    assert response.source_revision


def test_new_definition_cannot_claim_hand_authored_child(tmp_path: Path) -> None:
    service = _service(tmp_path)
    service.save_graph_transactionally(
        graph=_flat_graph(),
        name="main",
        description="",
        preamble="",
        source_file="main.py",
    )
    parent = tmp_path / "main.py"
    parent_sidecar = tmp_path / "main.haute.json"
    parent_original = parent.read_bytes()
    parent_sidecar_original = parent_sidecar.read_bytes()
    modules = tmp_path / "modules"
    modules.mkdir(exist_ok=True)
    child = modules / "child.py"
    child_original = b"# hand-authored child\n"
    child.write_bytes(child_original)

    with pytest.raises(HTTPException) as exc_info:
        service.save_graph_transactionally(
            graph=_managed_graph(),
            name="main",
            description="",
            preamble="",
            source_file="main.py",
        )

    assert exc_info.value.status_code == 409
    assert parent.read_bytes() == parent_original
    assert parent_sidecar.read_bytes() == parent_sidecar_original
    assert child.read_bytes() == child_original
    assert not child.with_suffix(".haute.json").exists()


def test_authorised_module_delete_removes_source_and_sidecar(tmp_path: Path) -> None:
    service = _service(tmp_path)
    service.save_graph_transactionally(
        graph=_flat_graph(),
        name="main",
        description="",
        preamble="",
        source_file="main.py",
    )
    service.save_graph_transactionally(
        graph=_managed_graph(),
        name="main",
        description="",
        preamble="",
        source_file="main.py",
    )
    child = tmp_path / "modules" / "child.py"
    child_sidecar = child.with_suffix(".haute.json")
    service.save_graph_transactionally(
        graph=_flat_graph(),
        name="main",
        description="",
        preamble="",
        source_file="main.py",
    )

    assert not child.exists()
    assert not child_sidecar.exists()


def test_post_commit_parse_failure_restores_deleted_source_and_sidecar(tmp_path: Path) -> None:
    from haute.routes import _helpers

    service = _service(tmp_path)
    service.save_graph_transactionally(
        graph=_flat_graph(),
        name="main",
        description="",
        preamble="",
        source_file="main.py",
    )
    service.save_graph_transactionally(
        graph=_managed_graph(),
        name="main",
        description="",
        preamble="",
        source_file="main.py",
    )
    parent = tmp_path / "main.py"
    parent_sidecar = tmp_path / "main.haute.json"
    child = tmp_path / "modules" / "child.py"
    child_sidecar = child.with_suffix(".haute.json")
    tracked = [parent, parent_sidecar, child, child_sidecar]
    originals = {path: path.read_bytes() for path in tracked}
    original_parse = _helpers.parse_pipeline_to_graph

    def fail_post_commit_parse(path: Path, **kwargs):
        if path.resolve() == parent.resolve() and not child.exists():
            raise RuntimeError("committed document cannot be reparsed")
        return original_parse(path, **kwargs)

    with (
        patch(
            "haute.routes._helpers.parse_pipeline_to_graph",
            side_effect=fail_post_commit_parse,
        ),
        pytest.raises(RuntimeError, match="cannot be reparsed"),
    ):
        service.save_graph_transactionally(
            graph=_flat_graph(),
            name="main",
            description="",
            preamble="",
            source_file="main.py",
        )

    assert {path: path.read_bytes() for path in tracked} == originals


def test_definition_id_cannot_be_replaced_for_the_same_module_path(tmp_path: Path) -> None:
    service = _service(tmp_path)
    service.save_graph_transactionally(
        graph=_flat_graph(),
        name="main",
        description="",
        preamble="",
        source_file="main.py",
    )
    service.save_graph_transactionally(
        graph=_managed_graph(),
        name="main",
        description="",
        preamble="",
        source_file="main.py",
    )
    parent = tmp_path / "main.py"
    tracked = [
        parent,
        tmp_path / "main.haute.json",
        tmp_path / "modules" / "child.py",
        tmp_path / "modules" / "child.haute.json",
    ]
    originals = {path: path.read_bytes() for path in tracked}

    with pytest.raises(HTTPException) as exc_info:
        service.save_graph_transactionally(
            graph=_managed_graph(definition_id="replacement"),
            name="main",
            description="",
            preamble="",
            source_file="main.py",
        )

    assert exc_info.value.status_code == 409
    detail = str(exc_info.value.detail)
    assert "modules/child.py" in detail
    assert "'child'" in detail
    assert "'replacement'" in detail
    assert {path: path.read_bytes() for path in tracked} == originals


def test_submitted_definitions_cannot_share_a_canonical_module_path(tmp_path: Path) -> None:
    service = _service(tmp_path)
    service.save_graph_transactionally(
        graph=_flat_graph(),
        name="main",
        description="",
        preamble="",
        source_file="main.py",
    )
    parent = tmp_path / "main.py"
    parent_sidecar = tmp_path / "main.haute.json"
    originals = {
        parent: parent.read_bytes(),
        parent_sidecar: parent_sidecar.read_bytes(),
    }
    graph = _managed_graph()
    child_definition = graph.submodels["child"]
    duplicate_definition = child_definition.model_copy(
        update={
            "definition_id": "replacement",
            "file": "modules/Child.py",
            "graph": child_definition.graph.model_copy(update={"nodes": []}),
        }
    )
    graph = graph.model_copy(
        update={
            "submodels": {
                "child": child_definition,
                "replacement": duplicate_definition,
            }
        }
    )

    with pytest.raises(HTTPException) as exc_info:
        service.save_graph_transactionally(
            graph=graph,
            name="main",
            description="",
            preamble="",
            source_file="main.py",
        )

    assert exc_info.value.status_code == 409
    assert {path: path.read_bytes() for path in originals} == originals
    assert not (tmp_path / "modules").exists()


def test_explicit_save_derives_new_definition_ownership(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    service = _service(tmp_path)
    service.save(_save_request(_flat_graph()))

    response = service.save(_save_request(_managed_graph()))

    child = tmp_path / "modules" / "child.py"
    sidecar = child.with_suffix(".haute.json")
    assert child.is_file()
    payload = json.loads(sidecar.read_text(encoding="utf-8"))
    assert payload["managed_parent"] == "main.py"
    assert payload["positions"]["child_node"] == {"x": 17.0, "y": 29.0}
    assert response.source_revision


def test_explicit_save_deletes_a_removed_uniquely_owned_definition(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    service = _service(tmp_path)
    service.save(_save_request(_flat_graph()))
    service.save(
        _save_request(_managed_graph()),
    )
    child = tmp_path / "modules" / "child.py"
    sidecar = child.with_suffix(".haute.json")
    assert child.is_file()
    assert sidecar.is_file()

    service.save(_save_request(_flat_graph()))

    assert not child.exists()
    assert not sidecar.exists()


def test_explicit_save_retains_removed_definition_referenced_by_another_pipeline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    service = _service(tmp_path)
    service.save(_save_request(_flat_graph()))
    service.save(_save_request(_managed_graph()))
    child = tmp_path / "modules" / "child.py"
    sidecar = child.with_suffix(".haute.json")
    (tmp_path / "other.py").write_text(
        """import haute

pipeline = haute.Pipeline("other")
pipeline.submodel(
    "modules/child.py",
    definition_id="child",
    instance_id="other-child-instance",
    alias="other-child",
)
""",
        encoding="utf-8",
    )

    service.save(_save_request(_flat_graph()))

    assert child.is_file()
    assert sidecar.is_file()


def test_explicit_save_retains_removed_definition_when_reference_audit_is_incomplete(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    service = _service(tmp_path)
    service.save(_save_request(_flat_graph()))
    service.save(_save_request(_managed_graph()))
    child = tmp_path / "modules" / "child.py"
    sidecar = child.with_suffix(".haute.json")
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

    service.save(_save_request(_flat_graph()))

    assert child.is_file()
    assert sidecar.is_file()
