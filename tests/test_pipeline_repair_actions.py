"""Explicit recovery of legacy submodels and reset of unavailable nodes."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from haute._pipeline_recovery import load_pipeline_editor_document


def _legacy_demo(root: Path) -> Path:
    (root / "haute.toml").write_text('[project]\nname = "demo"\n')
    child = root / "modules" / "Inputs.py"
    child.parent.mkdir()
    child.write_text(
        "import haute\nimport polars as pl\n\n"
        'submodel = haute.Submodel("Inputs", definition_id="Inputs", input_ports=[], '
        'output_ports=[{"portId": "output_1", "label": "live_switch", '
        '"source": {"nodeId": "live_switch", "handleId": None}}])\n\n'
        "@submodel.polars\ndef live_switch():\n"
        '    df = pl.LazyFrame({"premium": [1]})\n    return df\n',
        encoding="utf-8",
    )
    parent = root / "main.py"
    parent.write_text(
        'import haute\nimport polars as pl\n\npipeline = haute.Pipeline("demo")\n\n'
        '@pipeline.polars(contract="opaque")\n'
        "def Polars_3(Inputs__output_1: pl.LazyFrame) -> pl.LazyFrame:\n"
        "    df: pl.LazyFrame\n    df = live_switch\n    return df\n\n"
        'pipeline.submodel("modules/Inputs.py", definition_id="Inputs", '
        'instance_id="old_instance", alias="Inputs", label="Inputs")\n'
        'pipeline.connect("Inputs", "Polars_3", source_port="output_1")\n',
        encoding="utf-8",
    )
    parent.with_suffix(".haute.json").write_text(
        '{"positions":{"old_instance":{"x":12,"y":34},'
        '"Polars_3":{"x":56,"y":78}},"sources":["live"],"active_source":"live"}\n'
    )
    return parent


def _request(root: Path, target_id: str, action: str):
    from haute.schemas import PipelineRepairRecoverRequest

    document = load_pipeline_editor_document(root / "main.py", project_root=root)
    target = next(node for node in document.nodes if node.authored_id == target_id)
    return PipelineRepairRecoverRequest(
        source_file=document.source_file,
        source_revision=document.source_revision,
        target_source_file=target.source_file,
        target_recovery_id=target.recovery_id,
        action=action,
    )


def _apply(root: Path, request, plan):
    from haute._pipeline_repair import apply_recover_unavailable_node_plan
    from haute.schemas import PipelineRepairRecoverApplyRequest

    return apply_recover_unavailable_node_plan(
        project_root=root,
        request=PipelineRepairRecoverApplyRequest(
            **request.model_dump(), plan_hash=plan.response.plan_hash
        ),
    )


def test_legacy_registration_retains_submodel_position_connection_and_revision(tmp_path):
    parent = _legacy_demo(tmp_path)
    original = parent.read_bytes()
    document = load_pipeline_editor_document(parent, project_root=tmp_path)
    nodes = {node.authored_id: node for node in document.nodes}
    assert set(nodes) == {"Inputs", "Polars_3"}
    assert nodes["Inputs"].node_type == "submodel"
    assert nodes["Inputs"].availability == "unavailable"
    assert nodes["Inputs"].display_position == {"x": 12, "y": 34}
    assert nodes["Polars_3"].availability == "blocked"
    assert [(edge.source_authored_id, edge.target_authored_id) for edge in document.edges] == [
        ("Inputs", "Polars_3")
    ]
    child = tmp_path / "modules/Inputs.py"
    child.write_text(child.read_text() + "\n# concurrent edit\n")
    changed = load_pipeline_editor_document(parent, project_root=tmp_path)
    assert changed.source_revision != document.source_revision
    assert parent.read_bytes() == original


def test_update_then_reset_demo_preserves_child_and_exposes_consumer(tmp_path):
    from haute._pipeline_repair import build_recover_unavailable_node_plan

    parent = _legacy_demo(tmp_path)
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    request = _request(tmp_path, "Inputs", "update")
    plan = build_recover_unavailable_node_plan(project_root=tmp_path, request=request)
    assert plan.response.repair_kind == "update_node"
    assert {p: p.read_bytes() for p in before} == before
    assert {change.path for change in plan.response.changes} == {
        "main.py",
        "modules/Inputs.py",
        "main.haute.json",
    }
    result = _apply(tmp_path, request, plan)
    nodes = {node.authored_id: node for node in result.document.nodes}
    assert nodes["Inputs"].availability == "ready"
    assert nodes["Polars_3"].availability == "unavailable"
    assert "df = live_switch" in parent.read_text()
    assert 'df = pl.LazyFrame({"premium": [1]})' in (tmp_path / "modules/Inputs.py").read_text()
    positions = json.loads(parent.with_suffix(".haute.json").read_text())["positions"]
    assert positions == {"Inputs": {"x": 12, "y": 34}, "Polars_3": {"x": 56, "y": 78}}
    child_after_update = (tmp_path / "modules/Inputs.py").read_bytes()
    reset = _request(tmp_path, "Polars_3", "reset")
    reset_plan = build_recover_unavailable_node_plan(project_root=tmp_path, request=reset)
    assert reset_plan.response.repair_kind == "reset_node"
    assert reset_plan.response.warnings
    reset_result = _apply(tmp_path, reset, reset_plan)
    assert reset_result.document.load_status == "ready"
    assert "def Polars_3(Inputs: pl.LazyFrame)" in parent.read_text()
    assert "df = live_switch" not in parent.read_text()
    assert 'pipeline.connect("Inputs", "Polars_3", source_port="output_1")' in parent.read_text()
    assert (tmp_path / "modules/Inputs.py").read_bytes() == child_after_update
    assert (
        "raise " in parent.read_text()
    )  # normal incomplete Polars template, no silent passthrough


@pytest.mark.parametrize("drift", ["child", "plan"])
def test_update_rejects_stale_child_or_plan_without_writing(tmp_path, drift):
    from haute._pipeline_repair import PipelineRepairError, build_recover_unavailable_node_plan

    _legacy_demo(tmp_path)
    request = _request(tmp_path, "Inputs", "update")
    plan = build_recover_unavailable_node_plan(project_root=tmp_path, request=request)
    if drift == "child":
        child = tmp_path / "modules/Inputs.py"
        child.write_text(child.read_text() + "\n# new user edit\n")
    else:
        plan.response.plan_hash = "0" * 64
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    with pytest.raises(PipelineRepairError, match="changed"):
        _apply(tmp_path, request, plan)
    assert {p: p.read_bytes() for p in before} == before


def test_reset_broken_config_uses_palette_defaults_and_retains_reference(tmp_path):
    from haute._pipeline_repair import build_recover_unavailable_node_plan

    (tmp_path / "haute.toml").write_text('[project]\nname = "demo"\n')
    config = tmp_path / "custom.json"
    config.write_text("{broken json")
    parent = tmp_path / "main.py"
    parent.write_text(
        "import haute\nimport polars as pl\nfrom pathlib import Path\n"
        "_HAUTE_CONFIG_BASE = Path(__file__).resolve().parent\n"
        'pipeline = haute.Pipeline("demo")\n'
        '@pipeline.constant(config="custom.json")\ndef source():\n    return None\n'
    )
    request = _request(tmp_path, "source", "reset")
    plan = build_recover_unavailable_node_plan(project_root=tmp_path, request=request)
    result = _apply(tmp_path, request, plan)
    assert result.document.load_status == "ready"
    assert "custom.json" in parent.read_text()
    assert "source" in parent.read_text()
    assert json.loads(config.read_text()) == {"values": [{"name": "constant_1", "value": "1.0"}]}


def test_update_rolls_back_all_artifacts_when_verification_fails(tmp_path, monkeypatch):
    from haute import _pipeline_repair as repair

    _legacy_demo(tmp_path)
    request = _request(tmp_path, "Inputs", "update")
    plan = repair.build_recover_unavailable_node_plan(project_root=tmp_path, request=request)
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    real_load = repair.load_pipeline_editor_document

    def fail_after_write(path, *, project_root):
        if (
            Path(path).resolve() == (tmp_path / "main.py").resolve()
            and b"instance_id=" not in Path(path).read_bytes()
        ):
            raise RuntimeError("verification failure")
        return real_load(path, project_root=project_root)

    monkeypatch.setattr(repair, "load_pipeline_editor_document", fail_after_write)
    with pytest.raises(RuntimeError, match="verification failure"):
        _apply(tmp_path, request, plan)
    assert {p: p.read_bytes() for p in before} == before


def test_recovery_actions_round_trip_through_api(tmp_path, client, monkeypatch):
    _legacy_demo(tmp_path)
    monkeypatch.chdir(tmp_path)
    for node_id, action in (("Inputs", "update"), ("Polars_3", "reset")):
        request = _request(tmp_path, node_id, action).model_dump()
        before = (tmp_path / "main.py").read_bytes()
        response = client.post("/api/pipeline/repair/recover/dry-run", json=request)
        assert response.status_code == 200, response.text
        plan = response.json()
        assert (tmp_path / "main.py").read_bytes() == before
        assert plan["repair_kind"] == f"{action}_node"
        assert plan["delete_config"] is False
        forged = client.post(
            "/api/pipeline/repair/recover/apply",
            json={
                **request,
                "plan_hash": "0" * 64,
            },
        )
        assert forged.status_code == 409
        assert (tmp_path / "main.py").read_bytes() == before
        result = client.post(
            "/api/pipeline/repair/recover/apply",
            json={
                **request,
                "plan_hash": plan["plan_hash"],
            },
        )
        assert result.status_code == 200, result.text
        assert result.json()["repair_kind"] == f"{action}_node"
    assert result.json()["document"]["load_status"] == "ready"
    rejected = client.post(
        "/api/pipeline/repair/recover/dry-run",
        json={
            **request,
            "replacement_source": "arbitrary replacement",
        },
    )
    assert rejected.status_code == 422


@pytest.mark.parametrize("case", ["duplicate", "port_conflict", "path_escape"])
def test_submodel_update_rejects_ambiguous_or_escaping_identity(tmp_path, case):
    from haute._pipeline_repair import PipelineRepairError, build_recover_unavailable_node_plan
    from haute.schemas import PipelineRepairRecoverRequest

    _legacy_demo(tmp_path)
    parent = tmp_path / "main.py"
    if case == "duplicate":
        parent.write_text(
            parent.read_text()
            + 'pipeline.submodel("modules/Inputs.py", alias="Inputs", instance_id="other")\n'
        )
    elif case == "port_conflict":
        child = tmp_path / "modules/Inputs.py"
        child.write_text(
            child.read_text().replace(
                '"portId": "output_1"', '"name": "other", "portId": "output_1"'
            )
        )
    else:
        parent.write_text(parent.read_text().replace('"modules/Inputs.py"', '"../outside.py"'))
    document = load_pipeline_editor_document(parent, project_root=tmp_path)
    target = next(node for node in document.nodes if node.authored_id == "Inputs")
    if case == "duplicate":
        assert len({node.recovery_id for node in document.nodes}) == len(document.nodes)
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    request = PipelineRepairRecoverRequest(
        source_file=document.source_file,
        source_revision=document.source_revision,
        target_source_file=target.source_file,
        target_recovery_id=target.recovery_id,
        action="update",
    )
    with pytest.raises(PipelineRepairError):
        build_recover_unavailable_node_plan(project_root=tmp_path, request=request)
    assert {p: p.read_bytes() for p in before} == before


@pytest.mark.parametrize("case", ["shared", "required_setting", "missing"])
def test_config_reset_ownership_and_required_settings(tmp_path, case):
    from haute._pipeline_repair import PipelineRepairError, build_recover_unavailable_node_plan

    (tmp_path / "haute.toml").write_text('[project]\nname="demo"\n')
    path = tmp_path / "main.py"
    kind = "data_input" if case == "required_setting" else "constant"
    path.write_text(
        'import haute\nimport polars as pl\npipeline=haute.Pipeline("demo")\n'
        f'@pipeline.{kind}(config="custom.json")\ndef source():\n    return None\n'
    )
    if case != "missing":
        (tmp_path / "custom.json").write_text("{bad json")
    if case == "shared":
        path.write_text(
            path.read_text()
            + '@pipeline.constant(config="custom.json")\ndef other():\n    return None\n'
        )
    request = _request(tmp_path, "source", "reset")
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    if case == "missing":
        plan = build_recover_unavailable_node_plan(project_root=tmp_path, request=request)
        _apply(tmp_path, request, plan)
        assert (tmp_path / "custom.json").is_file()
    elif case == "required_setting":
        # Palette defaults with an empty required path persist as a loadable
        # incomplete configuration; reset is never blocked by completeness.
        plan = build_recover_unavailable_node_plan(project_root=tmp_path, request=request)
        result = _apply(tmp_path, request, plan)
        written = json.loads((tmp_path / "custom.json").read_text())
        assert written["inputType"] == "file"
        assert written["path"] == ""
        assert result.document.load_status == "ready"
    else:
        with pytest.raises(PipelineRepairError, match="shared"):
            build_recover_unavailable_node_plan(project_root=tmp_path, request=request)
        assert {p: p.read_bytes() for p in before} == before


def test_update_preserves_bom_crlf_comments_and_unrelated_sidecar_bytes(tmp_path):
    from haute._pipeline_repair import build_recover_unavailable_node_plan

    _legacy_demo(tmp_path)
    parent = tmp_path / "main.py"
    source = parent.read_text().replace(
        "pipeline.submodel(", "# café: preserve comment\npipeline.submodel("
    )
    parent.write_bytes(b"\xef\xbb\xbf" + source.replace("\n", "\r\n").encode("utf-8"))
    sidecar = parent.with_suffix(".haute.json")
    original_sidecar = sidecar.read_bytes()
    request = _request(tmp_path, "Inputs", "update")
    plan = build_recover_unavailable_node_plan(project_root=tmp_path, request=request)
    _apply(tmp_path, request, plan)
    assert parent.read_bytes().startswith(b"\xef\xbb\xbf")
    assert "# café: preserve comment\r\n".encode() in parent.read_bytes()
    assert b"\n" not in parent.read_bytes().replace(b"\r\n", b"")
    assert sidecar.read_bytes() == original_sidecar.replace(b'"old_instance"', b'"Inputs"')


def test_dry_run_stale_revision_conflict_preserves_authored_bytes(
    tmp_path: Path, client, monkeypatch: pytest.MonkeyPatch
) -> None:
    _legacy_demo(tmp_path)
    monkeypatch.chdir(tmp_path)
    before_files = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    request = _request(tmp_path, "Inputs", "update").model_dump()
    request["source_revision"] = "0" * 64

    response = client.post("/api/pipeline/repair/recover/dry-run", json=request)
    assert response.status_code == 409
    assert response.json()["detail"] == {
        "code": "repair_revision_conflict",
        "message": (
            "The pipeline changed after this recovery document loaded; reload before repairing."
        ),
    }
    assert {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()} == before_files


def test_dry_run_planner_io_error_sanitizes_message_and_preserves_artifacts(
    tmp_path: Path, client, monkeypatch: pytest.MonkeyPatch
) -> None:
    import haute.routes.pipeline as pipeline_routes

    parent = _legacy_demo(tmp_path)
    monkeypatch.chdir(tmp_path)
    sidecar = tmp_path / "main.haute.json"
    child = tmp_path / "modules" / "Inputs.py"
    before_parent = parent.read_bytes()
    before_sidecar = sidecar.read_bytes()
    before_child = child.read_bytes()

    def fail_planner(*_args: object, **_kwargs: object) -> None:
        raise PermissionError("private marker")

    monkeypatch.setattr(pipeline_routes, "build_recover_unavailable_node_plan", fail_planner)
    request = _request(tmp_path, "Inputs", "update").model_dump()
    response = client.post("/api/pipeline/repair/recover/dry-run", json=request)

    assert response.status_code == 409
    payload = response.json()
    assert payload["detail"]["code"] == "repair_artifact_unavailable"
    assert payload["detail"]["message"] == (
        "A repair artifact could not be read; reload and try again."
    )
    assert "private marker" not in response.text
    assert parent.read_bytes() == before_parent
    assert sidecar.read_bytes() == before_sidecar
    assert child.read_bytes() == before_child


def test_apply_recover_rollback_on_second_staged_write_restores_artifacts(
    tmp_path: Path, client, monkeypatch: pytest.MonkeyPatch
) -> None:
    import haute.routes._save_pipeline as save_pipeline

    parent = _legacy_demo(tmp_path)
    monkeypatch.chdir(tmp_path)
    sidecar = tmp_path / "main.haute.json"
    child = tmp_path / "modules" / "Inputs.py"
    before_parent = parent.read_bytes()
    before_sidecar = sidecar.read_bytes()
    before_child = child.read_bytes()

    request = _request(tmp_path, "Inputs", "update").model_dump()
    dry_run_response = client.post("/api/pipeline/repair/recover/dry-run", json=request)
    assert dry_run_response.status_code == 200, dry_run_response.text
    plan_hash = dry_run_response.json()["plan_hash"]

    real_stage_write = save_pipeline._stage_artifact_write_bytes
    staged_writes_successful = 0

    def fail_second_stage_write(path: Path, payload: bytes, touched: list) -> None:
        nonlocal staged_writes_successful
        if staged_writes_successful == 1:
            raise PermissionError("permission denied on second artifact")
        real_stage_write(path, payload, touched)
        staged_writes_successful += 1

    monkeypatch.setattr(save_pipeline, "_stage_artifact_write_bytes", fail_second_stage_write)

    response = client.post(
        "/api/pipeline/repair/recover/apply",
        json={**request, "plan_hash": plan_hash},
    )
    assert response.status_code == 409
    payload = response.json()
    assert payload["detail"]["code"] == "repair_artifact_unavailable"
    assert payload["detail"]["message"] == (
        "A repair artifact could not be written; original artifacts were restored."
    )
    assert staged_writes_successful == 1
    assert (tmp_path / "main.py").read_bytes() == before_parent
    assert (tmp_path / "main.haute.json").read_bytes() == before_sidecar
    assert (tmp_path / "modules" / "Inputs.py").read_bytes() == before_child


def test_recover_retains_valid_settings_and_reports_outcomes(tmp_path):
    from haute._pipeline_repair import build_recover_unavailable_node_plan

    (tmp_path / "haute.toml").write_text('[project]\nname="demo"\n')
    (tmp_path / "main.py").write_text(
        'import haute\nimport polars as pl\npipeline=haute.Pipeline("demo")\n'
        '@pipeline.data_input(config="in.json")\ndef source():\n    return None\n'
    )
    (tmp_path / "in.json").write_text(
        json.dumps(
            {
                "inputType": "file",
                "format": "json",
                "path": "quotes.json",
                "arguments": {},
                "code": "df = df.head(5)",
                "cacheMode": "snapshot",
            }
        )
    )
    document = load_pipeline_editor_document(tmp_path / "main.py", project_root=tmp_path)
    target = next(node for node in document.nodes if node.authored_id == "source")
    assert target.availability == "unavailable"
    request = _request(tmp_path, "source", "recover")
    plan = build_recover_unavailable_node_plan(project_root=tmp_path, request=request)
    assert plan.response.repair_kind == "recover_node"
    assert plan.response.previous_config is not None
    assert plan.response.previous_config["cacheMode"] == "snapshot"
    outcomes = {(change.path, change.outcome) for change in plan.response.field_changes}
    assert ("/cacheMode", "removed") in outcomes
    assert ("/path", "retained") in outcomes
    assert plan.response.completeness == []
    result = _apply(tmp_path, request, plan)
    assert result.repair_kind == "recover_node"
    assert result.previous_config is not None
    assert result.document.load_status == "ready"
    written = json.loads((tmp_path / "in.json").read_text())
    assert written["path"] == "quotes.json"
    assert written["format"] == "json"
    assert "mode" not in written
    assert "cacheMode" not in written


def test_recover_empty_locator_applies_as_incomplete(tmp_path):
    from haute._pipeline_repair import build_recover_unavailable_node_plan

    (tmp_path / "haute.toml").write_text('[project]\nname="demo"\n')
    (tmp_path / "main.py").write_text(
        'import haute\nimport polars as pl\npipeline=haute.Pipeline("demo")\n'
        '@pipeline.data_input(config="in.json")\ndef source():\n    return None\n'
    )
    (tmp_path / "in.json").write_text(
        json.dumps(
            {
                "inputType": "file",
                "format": "parquet",
                "mode": "scan",
                "path": "",
                "arguments": {},
                "code": "",
                "cacheMode": "snapshot",
            }
        )
    )
    request = _request(tmp_path, "source", "recover")
    plan = build_recover_unavailable_node_plan(project_root=tmp_path, request=request)
    assert [(entry.path, entry.code) for entry in plan.response.completeness] == [
        ("path", "required")
    ]
    result = _apply(tmp_path, request, plan)
    assert result.document.load_status == "ready"
    node = next(item for item in result.document.nodes if item.authored_id == "source")
    assert node.availability == "ready"
    assert [(entry.element_id, entry.path) for entry in result.document.completeness] == [
        (node.recovery_id, "path")
    ]


def test_recover_rejects_custom_body_for_non_code_types(tmp_path):
    from haute._pipeline_repair import PipelineRepairError, build_recover_unavailable_node_plan

    (tmp_path / "haute.toml").write_text('[project]\nname="demo"\n')
    (tmp_path / "main.py").write_text(
        'import haute\nimport polars as pl\npipeline=haute.Pipeline("demo")\n'
        '@pipeline.constant(config="custom.json")\ndef source():\n'
        "    surprise = 1\n    return surprise\n"
    )
    (tmp_path / "custom.json").write_text("{bad json")
    request = _request(tmp_path, "source", "recover")
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    with pytest.raises(PipelineRepairError, match="scaffold|manual"):
        build_recover_unavailable_node_plan(project_root=tmp_path, request=request)
    assert {p: p.read_bytes() for p in before} == before
