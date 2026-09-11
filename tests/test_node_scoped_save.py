"""Node-scoped saves in degraded documents (REC-R01).

Editing one loadable node must not depend on the whole-document save fence:
`scoped_editable` marks eligible nodes, and the scoped save rewrites exactly
that node's settings and code while every other artifact byte is conserved.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from haute._pipeline_recovery import load_pipeline_editor_document
from haute._pipeline_repair import PipelineRepairError

BROKEN_INPUT = {
    "inputType": "file",
    "format": "parquet",
    "mode": "scan",
    "path": "",
    "arguments": {},
    "code": "",
    "cacheMode": "snapshot",
}


def _two_inputs(root: Path) -> Path:
    (root / "haute.toml").write_text('[project]\nname="demo"\n')
    main = root / "main.py"
    main.write_text(
        "import haute\nimport polars as pl\n"
        'pipeline = haute.Pipeline("demo")\n'
        '@pipeline.data_input(config="a.json")\ndef source_a():\n    return None\n'
        '@pipeline.data_input(config="b.json")\ndef source_b():\n    return None\n'
    )
    (root / "a.json").write_text(json.dumps(BROKEN_INPUT))
    (root / "b.json").write_text(json.dumps(BROKEN_INPUT))
    return main


def _recover(root: Path, authored_id: str) -> None:
    from haute._pipeline_repair import (
        apply_recover_unavailable_node_plan,
        build_recover_unavailable_node_plan,
    )
    from haute.schemas import PipelineRepairRecoverApplyRequest, PipelineRepairRecoverRequest

    document = load_pipeline_editor_document(root / "main.py", project_root=root)
    target = next(node for node in document.nodes if node.authored_id == authored_id)
    request = PipelineRepairRecoverRequest(
        source_file=document.source_file,
        source_revision=document.source_revision,
        target_source_file=target.source_file,
        target_recovery_id=target.recovery_id,
        action="recover",
    )
    plan = build_recover_unavailable_node_plan(project_root=root, request=request)
    apply_recover_unavailable_node_plan(
        project_root=root,
        request=PipelineRepairRecoverApplyRequest(
            **request.model_dump(), plan_hash=plan.response.plan_hash
        ),
    )


def _save_request(root: Path, authored_id: str, config: dict):
    from haute.schemas import PipelineNodeSaveRequest

    document = load_pipeline_editor_document(root / "main.py", project_root=root)
    target = next(node for node in document.nodes if node.authored_id == authored_id)
    return PipelineNodeSaveRequest(
        source_file=document.source_file,
        source_revision=document.source_revision,
        target_source_file=target.source_file,
        target_recovery_id=target.recovery_id,
        config=config,
    )


def test_scoped_save_completes_a_recovered_node_while_sibling_stays_broken(tmp_path):
    from haute._pipeline_repair_actions import apply_scoped_node_save

    _two_inputs(tmp_path)
    _recover(tmp_path, "source_b")
    document = load_pipeline_editor_document(tmp_path / "main.py", project_root=tmp_path)
    by_id = {node.authored_id: node for node in document.nodes}
    assert by_id["source_a"].availability == "unavailable"
    assert by_id["source_a"].scoped_editable is False
    assert by_id["source_b"].availability == "ready"
    assert by_id["source_b"].scoped_editable is True
    assert document.capabilities.can_save is False
    sibling_config = (tmp_path / "a.json").read_bytes()

    completed = {**by_id["source_b"].config, "path": "data/quotes.parquet"}
    request = _save_request(tmp_path, "source_b", completed)
    saved = apply_scoped_node_save(project_root=tmp_path, request=request)
    node = next(item for item in saved.nodes if item.authored_id == "source_b")
    assert node.availability == "ready"
    assert node.config["path"] == "data/quotes.parquet"
    assert [entry for entry in saved.completeness if entry.element_id == node.recovery_id] == []
    assert (tmp_path / "a.json").read_bytes() == sibling_config
    assert json.loads((tmp_path / "b.json").read_text())["path"] == "data/quotes.parquet"
    sibling = next(item for item in saved.nodes if item.authored_id == "source_a")
    assert sibling.availability == "unavailable"
    assert saved.capabilities.can_save is False
    assert saved.load_status == "degraded"


def test_scoped_save_rejects_stale_revision_without_writing(tmp_path):
    from haute._pipeline_repair_actions import apply_scoped_node_save

    _two_inputs(tmp_path)
    _recover(tmp_path, "source_b")
    request = _save_request(tmp_path, "source_b", {**BROKEN_INPUT, "path": "x.parquet"})
    request = request.model_copy(update={"source_revision": "0" * len(request.source_revision)})
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    with pytest.raises(PipelineRepairError, match="changed"):
        apply_scoped_node_save(project_root=tmp_path, request=request)
    assert {p: p.read_bytes() for p in before} == before


def test_scoped_save_rejects_unavailable_target_and_shared_config(tmp_path):
    from haute._pipeline_repair_actions import apply_scoped_node_save

    _two_inputs(tmp_path)
    _recover(tmp_path, "source_b")
    broken = _save_request(tmp_path, "source_a", dict(BROKEN_INPUT))
    with pytest.raises(PipelineRepairError, match="isolation|unavailable|Recover"):
        apply_scoped_node_save(project_root=tmp_path, request=broken)

    shared_root = tmp_path / "shared"
    shared_root.mkdir()
    (shared_root / "haute.toml").write_text('[project]\nname="demo"\n')
    (shared_root / "main.py").write_text(
        "import haute\nimport polars as pl\n"
        'pipeline = haute.Pipeline("demo")\n'
        '@pipeline.data_input(config="x.json")\ndef source_x():\n    return None\n'
        '@pipeline.constant(config="custom.json")\ndef first():\n    return None\n'
        '@pipeline.constant(config="custom.json")\ndef second():\n    return None\n'
    )
    (shared_root / "x.json").write_text(json.dumps(BROKEN_INPUT))
    (shared_root / "custom.json").write_text(
        json.dumps({"values": [{"name": "constant_1", "value": "1.0"}]})
    )
    document = load_pipeline_editor_document(shared_root / "main.py", project_root=shared_root)
    assert document.load_status == "degraded"
    for node in document.nodes:
        if node.authored_id in {"first", "second"}:
            assert node.availability == "ready"
        assert node.scoped_editable is False
    request = _save_request(shared_root, "first", {"values": [{"name": "c", "value": "2.0"}]})
    with pytest.raises(PipelineRepairError, match="isolation|shared"):
        apply_scoped_node_save(project_root=shared_root, request=request)


def test_scoped_save_edits_blocked_node_downstream_of_broken_input(tmp_path):
    from haute._pipeline_repair_actions import apply_scoped_node_save

    (tmp_path / "haute.toml").write_text('[project]\nname="demo"\n')
    main = tmp_path / "main.py"
    main.write_text(
        "import haute\nimport polars as pl\n"
        'pipeline = haute.Pipeline("demo")\n'
        '@pipeline.data_input(config="a.json")\ndef source_a():\n    return None\n'
        "@pipeline.polars\ndef transform(source_a: pl.LazyFrame) -> pl.LazyFrame:\n"
        "    df: pl.LazyFrame\n    df = source_a\n    return df\n"
    )
    (tmp_path / "a.json").write_text(json.dumps(BROKEN_INPUT))
    document = load_pipeline_editor_document(main, project_root=tmp_path)
    transform = next(node for node in document.nodes if node.authored_id == "transform")
    assert transform.availability == "blocked"
    assert transform.scoped_editable is True

    request = _save_request(
        tmp_path,
        "transform",
        {**(transform.config or {}), "code": "df = source_a\ndf = df.head(1)\nreturn df"},
    )
    saved = apply_scoped_node_save(project_root=tmp_path, request=request)
    node = next(item for item in saved.nodes if item.authored_id == "transform")
    assert node.availability == "blocked"
    assert "df.head(1)" in main.read_text()
    assert json.loads((tmp_path / "a.json").read_text()) == BROKEN_INPUT


def test_scoped_save_route_saves_and_rejects_stale_revisions(client, monkeypatch, tmp_path):
    _two_inputs(tmp_path)
    _recover(tmp_path, "source_b")
    monkeypatch.chdir(tmp_path)
    document = load_pipeline_editor_document(tmp_path / "main.py", project_root=tmp_path)
    target = next(node for node in document.nodes if node.authored_id == "source_b")
    body = {
        "source_file": document.source_file,
        "source_revision": document.source_revision,
        "target_source_file": target.source_file,
        "target_recovery_id": target.recovery_id,
        "config": {**target.config, "path": "data/quotes.parquet"},
    }
    response = client.post("/api/pipeline/node/save", json=body)
    assert response.status_code == 200
    payload = response.json()
    assert payload["load_status"] == "degraded"
    node = next(item for item in payload["nodes"] if item["authored_id"] == "source_b")
    assert node["config"]["path"] == "data/quotes.parquet"
    assert node["scoped_editable"] is True
    assert payload["capabilities"]["can_save"] is False

    stale = client.post("/api/pipeline/node/save", json=body)
    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "repair_revision_conflict"


def test_scoped_save_route_maps_io_failures_without_leaking_details(client, monkeypatch, tmp_path):
    from haute.routes import pipeline as pipeline_routes

    _two_inputs(tmp_path)
    monkeypatch.chdir(tmp_path)

    def fail_save(*_args: object, **_kwargs: object) -> None:
        raise PermissionError("private marker")

    monkeypatch.setattr(pipeline_routes, "apply_scoped_node_save", fail_save)
    response = client.post(
        "/api/pipeline/node/save",
        json={
            "source_file": "main.py",
            "source_revision": "0" * 64,
            "target_source_file": "main.py",
            "target_recovery_id": "node@1",
            "config": {},
        },
    )
    assert response.status_code == 409
    payload = response.json()
    assert payload["detail"]["code"] == "repair_artifact_unavailable"
    assert payload["detail"]["message"] == (
        "A save artifact could not be written; original artifacts were restored."
    )
    assert "private marker" not in response.text


def test_scoped_save_route_rejects_malformed_discriminant_shapes(client, monkeypatch, tmp_path):
    # JSON arrays/objects in discriminator positions must produce a structured
    # 4xx, never an unhandled TypeError, and must write nothing.
    _two_inputs(tmp_path)
    _recover(tmp_path, "source_b")
    monkeypatch.chdir(tmp_path)
    document = load_pipeline_editor_document(tmp_path / "main.py", project_root=tmp_path)
    target = next(node for node in document.nodes if node.authored_id == "source_b")
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    for config in (
        {**BROKEN_INPUT, "inputType": ["file"]},
        {**BROKEN_INPUT, "inputType": {"kind": "file"}},
    ):
        response = client.post(
            "/api/pipeline/node/save",
            json={
                "source_file": document.source_file,
                "source_revision": document.source_revision,
                "target_source_file": target.source_file,
                "target_recovery_id": target.recovery_id,
                "config": config,
            },
        )
        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "repair_action_unsupported"
        assert "Unknown inputType" in response.json()["detail"]["message"]
    assert {p: p.read_bytes() for p in before} == before
