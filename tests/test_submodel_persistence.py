"""Filesystem contracts shared by submodel create/dissolve saves."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import HTTPException

from haute.routes._save_pipeline import SavePipelineService
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


def _managed_graph():
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
                    "id": "submodel__child",
                    "type": "submodel",
                    "position": {"x": 50.0, "y": 60.0},
                    "data": {
                        "label": "child",
                        "nodeType": "submodel",
                        "config": {
                            "file": "modules/child.py",
                            "childNodeIds": ["child_node"],
                            "inputPorts": [],
                            "outputPorts": [],
                        },
                    },
                }
            ],
            "edges": [],
            "submodels": {
                "child": {
                    "file": "modules/child.py",
                    "childNodeIds": ["child_node"],
                    "inputPorts": [],
                    "outputPorts": [],
                    "managed": True,
                    "graph": {
                        "nodes": [child],
                        "edges": [],
                        "pipeline_name": "child",
                        "preamble": "HELPER = 1",
                        "preserved_blocks": ["KEPT = 2"],
                        "source_file": "modules/child.py",
                    },
                }
            },
        }
    )


def _service(tmp_path: Path) -> SavePipelineService:
    return SavePipelineService(project_root=tmp_path, pipeline_root=tmp_path)


def test_create_no_clobber_is_case_insensitive_and_precedes_writes(tmp_path: Path) -> None:
    parent = tmp_path / "main.py"
    parent.write_bytes(b"# original parent\n")
    modules = tmp_path / "modules"
    modules.mkdir()
    existing = modules / "Pricing.py"
    existing.write_bytes(b"# hand-authored child\n")

    with pytest.raises(HTTPException) as exc_info:
        _service(tmp_path).save_graph_transactionally(
            graph=_flat_graph(),
            name="main",
            description="",
            preamble="",
            source_file="main.py",
            require_absent_module_files=["modules/pricing.py"],
        )

    assert exc_info.value.status_code == 409
    assert parent.read_bytes() == b"# original parent\n"
    assert existing.read_bytes() == b"# hand-authored child\n"
    assert not (tmp_path / "main.haute.json").exists()


def test_create_no_clobber_rejects_orphan_child_sidecar(tmp_path: Path) -> None:
    parent = tmp_path / "main.py"
    parent_original = b"# original parent\n"
    parent.write_bytes(parent_original)
    modules = tmp_path / "modules"
    modules.mkdir()
    orphan_sidecar = modules / "Child.haute.json"
    sidecar_original = b'{"managed_parent":"other.py"}\n'
    orphan_sidecar.write_bytes(sidecar_original)

    with pytest.raises(HTTPException) as exc_info:
        _service(tmp_path).save_graph_transactionally(
            graph=_flat_graph(),
            name="main",
            description="",
            preamble="",
            source_file="main.py",
            require_absent_module_files=["modules/child.py"],
        )

    assert exc_info.value.status_code == 409
    assert parent.read_bytes() == parent_original
    assert orphan_sidecar.read_bytes() == sidecar_original
    assert not (tmp_path / "main.haute.json").exists()


def test_managed_child_gets_owner_and_position_sidecar(tmp_path: Path) -> None:
    response = _service(tmp_path).save_graph_transactionally(
        graph=_managed_graph(),
        name="main",
        description="",
        preamble="",
        source_file="main.py",
        require_absent_module_files=["modules/child.py"],
        claim_managed_module_files=["modules/child.py"],
    )

    sidecar_path = tmp_path / "modules" / "child.haute.json"
    payload = json.loads(sidecar_path.read_text(encoding="utf-8"))
    assert payload["managed_parent"] == "main.py"
    assert payload["positions"]["child_node"] == {"x": 17.0, "y": 29.0}
    assert response.source_revision


def test_request_managed_flag_cannot_claim_hand_authored_child(tmp_path: Path) -> None:
    parent = tmp_path / "main.py"
    parent_original = b"# original parent\n"
    parent.write_bytes(parent_original)
    modules = tmp_path / "modules"
    modules.mkdir()
    child = modules / "child.py"
    child_original = b"# hand-authored child\n"
    child.write_bytes(child_original)

    with pytest.raises(HTTPException) as exc_info:
        _service(tmp_path).save_graph_transactionally(
            graph=_managed_graph(),
            name="main",
            description="",
            preamble="",
            source_file="main.py",
        )

    assert exc_info.value.status_code == 409
    assert parent.read_bytes() == parent_original
    assert child.read_bytes() == child_original
    assert not child.with_suffix(".haute.json").exists()


def test_authorised_module_delete_removes_source_and_sidecar(tmp_path: Path) -> None:
    parent = tmp_path / "main.py"
    parent.write_text("# old parent\n", encoding="utf-8")
    modules = tmp_path / "modules"
    modules.mkdir()
    child = modules / "child.py"
    child.write_bytes(b"# child\n")
    child_sidecar = modules / "child.haute.json"
    child_sidecar.write_bytes(b'{"managed_parent":"main.py"}\n')

    _service(tmp_path).save_graph_transactionally(
        graph=_flat_graph(),
        name="main",
        description="",
        preamble="",
        source_file="main.py",
        delete_module_files=["modules/child.py"],
    )

    assert not child.exists()
    assert not child_sidecar.exists()


def test_post_commit_parse_failure_restores_deleted_source_and_sidecar(tmp_path: Path) -> None:
    parent = tmp_path / "main.py"
    parent_original = b"# old parent\n"
    parent.write_bytes(parent_original)
    modules = tmp_path / "modules"
    modules.mkdir()
    child = modules / "child.py"
    child_original = b"# child\n"
    child.write_bytes(child_original)
    child_sidecar = modules / "child.haute.json"
    sidecar_original = b'{"managed_parent":"main.py"}\n'
    child_sidecar.write_bytes(sidecar_original)

    with (
        patch(
            "haute.routes._helpers.parse_pipeline_to_graph",
            side_effect=RuntimeError("committed document cannot be reparsed"),
        ),
        pytest.raises(RuntimeError, match="cannot be reparsed"),
    ):
        _service(tmp_path).save_graph_transactionally(
            graph=_flat_graph(),
            name="main",
            description="",
            preamble="",
            source_file="main.py",
            delete_module_files=["modules/child.py"],
        )

    assert parent.read_bytes() == parent_original
    assert child.read_bytes() == child_original
    assert child_sidecar.read_bytes() == sidecar_original


def test_unmatched_managed_claim_fails_400_before_writes(tmp_path: Path) -> None:
    parent = tmp_path / "main.py"
    parent_original = b"# original parent\n"
    parent.write_bytes(parent_original)

    with pytest.raises(HTTPException) as exc_info:
        _service(tmp_path).save_graph_transactionally(
            graph=_flat_graph(),
            name="main",
            description="",
            preamble="",
            source_file="main.py",
            require_absent_module_files=["modules/orphan.py"],
            claim_managed_module_files=["modules/orphan.py"],
        )

    assert exc_info.value.status_code == 400
    assert "claim" in str(exc_info.value.detail).lower()
    assert parent.read_bytes() == parent_original
    assert not (tmp_path / "modules" / "orphan.py").exists()
    assert not (tmp_path / "main.haute.json").exists()
