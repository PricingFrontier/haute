"""Reset and settings recovery of unavailable nodes; legacy submodel forms are rejected."""

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


def _broken_constant(root: Path, *, prefix: str = "") -> Path:
    """A constant whose config sidecar is malformed: reset rewrites main.py and custom.json."""
    (root / "haute.toml").write_text('[project]\nname = "demo"\n')
    (root / "custom.json").write_text("{broken json")
    parent = root / "main.py"
    parent.write_text(
        prefix + "import haute\nimport polars as pl\nfrom pathlib import Path\n"
        "_HAUTE_CONFIG_BASE = Path(__file__).resolve().parent\n"
        'pipeline = haute.Pipeline("demo")\n'
        '@pipeline.constant(config="custom.json")\ndef source():\n    return None\n',
        encoding="utf-8",
        newline="\n",
    )
    return parent


def _canonical_demo(root: Path, *, legacy_port: bool = False) -> Path:
    """A current-form submodel whose consumer names its input after the old port id."""
    (root / "haute.toml").write_text('[project]\nname = "demo"\n')
    child = root / "modules" / "Inputs.py"
    child.parent.mkdir()
    port = (
        '{"portId": "output_1", "label": "live_switch", '
        if legacy_port
        else '{"name": "output_1", '
    )
    child.write_text(
        "import haute\nimport polars as pl\n\n"
        'submodel = haute.Submodel("Inputs", definition_id="Inputs", input_ports=[], '
        f'output_ports=[{port}"source": {{"nodeId": "live_switch", "handleId": None}}}}])\n\n'
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
        'pipeline.submodel("modules/Inputs.py", "Inputs")\n'
        'pipeline.connect("Inputs", "Polars_3", source_port="output_1")\n',
        encoding="utf-8",
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


def _apply(root: Path, request):
    from haute._pipeline_repair import apply_recover_unavailable_node_plan

    return apply_recover_unavailable_node_plan(project_root=root, request=request)


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
    diagnostic = next(
        diagnostic for diagnostic in document.diagnostics if diagnostic.element_id == "Inputs"
    )
    assert diagnostic.code == "submodel_registration_invalid"
    assert diagnostic.message == (
        "pipeline.submodel() no longer accepts label=; an occurrence's name is the second argument."
    )
    assert diagnostic.remediation == (
        "Write pipeline.submodel(<path>, <name>) and let the child file declare its definition id."
    )
    child = tmp_path / "modules/Inputs.py"
    child.write_text(child.read_text() + "\n# concurrent edit\n")
    changed = load_pipeline_editor_document(parent, project_root=tmp_path)
    assert changed.source_revision != document.source_revision
    assert parent.read_bytes() == original


def test_a_legacy_child_port_reports_the_parsers_remediation(tmp_path):
    parent = _canonical_demo(tmp_path, legacy_port=True)
    document = load_pipeline_editor_document(parent, project_root=tmp_path)

    diagnostic = next(
        diagnostic
        for diagnostic in document.diagnostics
        if diagnostic.code == "submodel_definition_invalid"
    )
    assert "replace 'portId' and 'label' with 'name'" in diagnostic.message
    assert diagnostic.remediation == (
        "Declare each public port as "
        "{'name': ..., 'targets': [...]} or {'name': ..., 'source': {...}}."
    )


def test_reset_rebinds_a_consumer_of_a_submodel_port(tmp_path):
    from haute._pipeline_repair import build_recover_unavailable_node_plan

    parent = _canonical_demo(tmp_path)
    child_before = (tmp_path / "modules/Inputs.py").read_bytes()
    document = load_pipeline_editor_document(parent, project_root=tmp_path)
    nodes = {node.authored_id: node for node in document.nodes}
    assert nodes["Inputs"].availability == "ready"
    assert nodes["Polars_3"].availability == "unavailable"

    reset = _request(tmp_path, "Polars_3", "reset")
    plan = build_recover_unavailable_node_plan(project_root=tmp_path, request=reset)
    assert plan.response.repair_kind == "reset_node"
    result = _apply(tmp_path, reset)

    assert result.document.load_status == "ready"
    source = parent.read_text()
    assert "def Polars_3(output_1: pl.LazyFrame)" in source
    assert "df = live_switch" not in source
    assert 'pipeline.connect("Inputs", "Polars_3", source_port="output_1")' in source
    assert "raise " in source  # the incomplete Polars template, never a silent passthrough
    assert (tmp_path / "modules/Inputs.py").read_bytes() == child_before


def test_a_legacy_submodel_registration_has_no_migration_action(tmp_path, client, monkeypatch):
    from pydantic import ValidationError

    from haute.schemas import PipelineRepairRecoverRequest

    _legacy_demo(tmp_path)
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    document = load_pipeline_editor_document(tmp_path / "main.py", project_root=tmp_path)
    target = next(node for node in document.nodes if node.authored_id == "Inputs")
    request = {
        "source_file": document.source_file,
        "source_revision": document.source_revision,
        "target_source_file": target.source_file,
        "target_recovery_id": target.recovery_id,
        "action": "update",
    }

    with pytest.raises(ValidationError, match="action"):
        PipelineRepairRecoverRequest(**request)
    monkeypatch.chdir(tmp_path)
    response = client.post("/api/pipeline/repair/recover/apply", json=request)

    assert response.status_code == 422
    assert {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()} == before


def test_reset_broken_config_uses_palette_defaults_and_retains_reference(tmp_path):

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
    result = _apply(tmp_path, request)
    assert result.document.load_status == "ready"
    assert "custom.json" in parent.read_text()
    assert "source" in parent.read_text()
    assert json.loads(config.read_text()) == {"values": [{"name": "constant_1", "value": "1.0"}]}


def test_reset_rolls_back_all_artifacts_when_verification_fails(tmp_path, monkeypatch):
    from haute import _pipeline_repair as repair

    _broken_constant(tmp_path)
    request = _request(tmp_path, "source", "reset")
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    real_load = repair.load_pipeline_editor_document

    def fail_after_write(path, *, project_root):
        if (
            Path(path).resolve() == (tmp_path / "main.py").resolve()
            and (tmp_path / "custom.json").read_text() != "{broken json"
        ):
            raise RuntimeError("verification failure")
        return real_load(path, project_root=project_root)

    monkeypatch.setattr(repair, "load_pipeline_editor_document", fail_after_write)
    with pytest.raises(RuntimeError, match="verification failure"):
        _apply(tmp_path, request)
    assert {p: p.read_bytes() for p in before} == before


def test_recovery_actions_round_trip_through_api(tmp_path, client, monkeypatch):
    _broken_constant(tmp_path)
    monkeypatch.chdir(tmp_path)
    request = _request(tmp_path, "source", "reset").model_dump()
    result = client.post("/api/pipeline/repair/recover/apply", json=request)
    assert result.status_code == 200, result.text
    assert result.json()["repair_kind"] == "reset_node"
    assert result.json()["changes"]
    assert result.json()["document"]["load_status"] == "ready"
    rejected = client.post(
        "/api/pipeline/repair/recover/apply",
        json={
            **request,
            "replacement_source": "arbitrary replacement",
        },
    )
    assert rejected.status_code == 422


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
        _apply(tmp_path, request)
        assert (tmp_path / "custom.json").is_file()
    elif case == "required_setting":
        # Palette defaults with an empty required path persist as a loadable
        # incomplete configuration; reset is never blocked by completeness.
        result = _apply(tmp_path, request)
        written = json.loads((tmp_path / "custom.json").read_text())
        assert written["inputType"] == "file"
        assert written["path"] == ""
        assert result.document.load_status == "ready"
    else:
        with pytest.raises(PipelineRepairError, match="shared"):
            build_recover_unavailable_node_plan(project_root=tmp_path, request=request)
        assert {p: p.read_bytes() for p in before} == before


def test_reset_preserves_bom_crlf_and_comments(tmp_path):
    parent = _broken_constant(tmp_path, prefix="# café: preserve comment\n")
    (tmp_path / "main.py").write_bytes(
        b"\xef\xbb\xbf" + parent.read_bytes().replace(b"\n", b"\r\n")
    )
    request = _request(tmp_path, "source", "reset")
    _apply(tmp_path, request)
    assert parent.read_bytes().startswith(b"\xef\xbb\xbf")
    assert "# café: preserve comment\r\n".encode() in parent.read_bytes()
    assert b"\n" not in parent.read_bytes().replace(b"\r\n", b"")


def test_stale_revision_conflict_preserves_authored_bytes(
    tmp_path: Path, client, monkeypatch: pytest.MonkeyPatch
) -> None:
    _broken_constant(tmp_path)
    monkeypatch.chdir(tmp_path)
    before_files = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    request = _request(tmp_path, "source", "reset").model_dump()
    request["source_revision"] = "0" * 64

    response = client.post("/api/pipeline/repair/recover/apply", json=request)
    assert response.status_code == 409
    assert response.json()["detail"] == {
        "code": "repair_revision_conflict",
        "message": (
            "The pipeline changed after this recovery document loaded; reload before repairing."
        ),
    }
    assert {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()} == before_files


def test_apply_recover_rollback_on_second_staged_write_restores_artifacts(
    tmp_path: Path, client, monkeypatch: pytest.MonkeyPatch
) -> None:
    import haute.routes._save_pipeline as save_pipeline

    parent = _broken_constant(tmp_path)
    monkeypatch.chdir(tmp_path)
    config = tmp_path / "custom.json"
    before_parent = parent.read_bytes()
    before_config = config.read_bytes()

    request = _request(tmp_path, "source", "reset").model_dump()

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
        json=request,
    )
    assert response.status_code == 409
    payload = response.json()
    assert payload["detail"]["code"] == "repair_artifact_unavailable"
    assert payload["detail"]["message"] == (
        "A repair artifact could not be written; original artifacts were restored."
    )
    assert staged_writes_successful == 1
    assert (tmp_path / "main.py").read_bytes() == before_parent
    assert (tmp_path / "custom.json").read_bytes() == before_config


def test_recover_retains_valid_settings_and_reports_outcomes(tmp_path):
    from haute._pipeline_repair import build_recover_unavailable_node_plan

    (tmp_path / "haute.toml").write_text('[project]\nname="demo"\n')
    (tmp_path / "main.py").write_text(
        'import haute\nimport polars as pl\npipeline=haute.Pipeline("demo")\n'
        '@pipeline.data_input(config="in.json")\ndef source():\n'
        '    df = df.filter(pl.col("premium") > 1234)\n    return df\n'
    )
    (tmp_path / "in.json").write_text(
        json.dumps(
            {
                "inputType": "file",
                "format": "json",
                "path": "quotes.json",
                "arguments": {},
                "code": "",
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
    result = _apply(tmp_path, request)
    assert result.repair_kind == "recover_node"
    assert result.previous_config is not None
    assert result.document.load_status == "ready"
    written = json.loads((tmp_path / "in.json").read_text())
    assert written["path"] == "quotes.json"
    assert written["format"] == "json"
    assert "mode" not in written
    assert "cacheMode" not in written
    # Authored code survives byte-for-byte into the emitted source and the
    # reloaded configuration; an implementation dropping user code fails here.
    assert 'pl.col("premium") > 1234' in (tmp_path / "main.py").read_text()
    node = next(item for item in result.document.nodes if item.authored_id == "source")
    assert 'pl.col("premium") > 1234' in str((node.config or {}).get("code", ""))
    # The sidecar never stores the code slot: the .py body is authoritative.
    assert "code" not in written


def test_recover_reports_what_it_could_not_fix_as_completeness(tmp_path):
    """The worked example: a Scenario Expander sidecar from before the stepCount rename
    (`steps: 11`, no `stepCount`). The recover applies, and the engine's own issue
    reaches the plan as completeness instead of being dropped."""
    from haute._config_io import collect_node_configs
    from haute._pipeline_repair import build_recover_unavailable_node_plan
    from haute._types import GraphEdge, GraphNode, NodeData, NodeType, PipelineGraph
    from haute.codegen import graph_to_code

    graph = PipelineGraph(
        nodes=[
            GraphNode(
                id="quotes",
                data=NodeData(
                    label="quotes",
                    nodeType=NodeType.CONSTANT,
                    config={"values": [{"name": "quote_id", "value": "1"}]},
                ),
            ),
            GraphNode(
                id="grid",
                data=NodeData(
                    label="grid",
                    nodeType=NodeType.SCENARIO_EXPANDER,
                    config={
                        "quote_id": "quote_id",
                        "column_name": "price",
                        "step_column": "scenario_index",
                        "min_value": 0.1,
                        "max_value": 0.3,
                        "stepCount": 3,
                        "steps": [],
                    },
                ),
            ),
        ],
        edges=[GraphEdge(id="e_quotes_grid", source="quotes", target="grid")],
    )
    (tmp_path / "haute.toml").write_text('[project]\nname="demo"\n')
    (tmp_path / "main.py").write_text(graph_to_code(graph, pipeline_name="demo"))
    for rel_path, content in collect_node_configs(graph).items():
        config_file = tmp_path / rel_path
        config_file.parent.mkdir(parents=True, exist_ok=True)
        config_file.write_text(content)
    sidecar = tmp_path / "config/expander/grid.json"
    stale = json.loads(sidecar.read_text())
    del stale["stepCount"]
    stale["steps"] = 11
    sidecar.write_text(json.dumps(stale))
    document = load_pipeline_editor_document(tmp_path / "main.py", project_root=tmp_path)
    target = next(node for node in document.nodes if node.authored_id == "grid")
    assert target.availability == "unavailable"

    request = _request(tmp_path, "grid", "recover")
    plan = build_recover_unavailable_node_plan(project_root=tmp_path, request=request)

    assert [(entry.path, entry.code, entry.message) for entry in plan.response.completeness] == [
        ("/", "incomplete_range", "Scenario range and stepCount are required.")
    ]
    assert all(entry.element_id == target.recovery_id for entry in plan.response.completeness)
    result = _apply(tmp_path, request)
    assert [entry.code for entry in result.completeness] == ["incomplete_range"]
    applied = next(node for node in result.document.nodes if node.authored_id == "grid")
    assert applied.availability == "ready"
    assert "stepCount" not in json.loads(sidecar.read_text())


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
    result = _apply(tmp_path, request)
    assert result.document.load_status == "ready"
    node = next(item for item in result.document.nodes if item.authored_id == "source")
    assert node.availability == "ready"
    assert [(entry.element_id, entry.path) for entry in result.document.completeness] == [
        (node.recovery_id, "path")
    ]


def test_recover_refuses_unreadable_config_sidecars(tmp_path):
    # There is no draft archive: replacing an unreadable sidecar would discard
    # its contents, so recover fails loudly and leaves every byte unchanged.
    from haute._pipeline_repair import PipelineRepairError, build_recover_unavailable_node_plan

    (tmp_path / "haute.toml").write_text('[project]\nname="demo"\n')
    (tmp_path / "main.py").write_text(
        'import haute\nimport polars as pl\npipeline=haute.Pipeline("demo")\n'
        '@pipeline.constant(config="custom.json")\ndef source():\n    return None\n'
    )
    (tmp_path / "custom.json").write_text("{bad json")
    request = _request(tmp_path, "source", "recover")
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    with pytest.raises(PipelineRepairError, match="unreadable"):
        build_recover_unavailable_node_plan(project_root=tmp_path, request=request)
    assert {p: p.read_bytes() for p in before} == before


def test_recover_rejects_custom_body_for_non_code_types(tmp_path):
    from haute._pipeline_repair import PipelineRepairError, build_recover_unavailable_node_plan

    (tmp_path / "haute.toml").write_text('[project]\nname="demo"\n')
    (tmp_path / "main.py").write_text(
        'import haute\nimport polars as pl\npipeline=haute.Pipeline("demo")\n'
        '@pipeline.data_input(config="a.json")\ndef source_a():\n    return None\n'
        '@pipeline.data_output(config="out.json")\ndef sink(source_a):\n'
        "    surprise = 1\n    return surprise\n"
    )
    (tmp_path / "a.json").write_text(
        json.dumps(
            {
                "inputType": "file",
                "format": "parquet",
                "mode": "scan",
                "path": "quotes.parquet",
                "arguments": {},
                "code": "",
            }
        )
    )
    (tmp_path / "out.json").write_text(
        json.dumps(
            {
                "outputType": "file",
                "format": "parquet",
                "mode": "sink",
                "path": "out.parquet",
                "arguments": {},
                "cacheMode": "snapshot",
            }
        )
    )
    request = _request(tmp_path, "sink", "recover")
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    with pytest.raises(PipelineRepairError, match="scaffold|manual"):
        build_recover_unavailable_node_plan(project_root=tmp_path, request=request)
    assert {p: p.read_bytes() for p in before} == before


def test_recover_works_down_a_broken_chain_leaving_the_target_blocked(tmp_path):
    # Recovering a damaged downstream node must not require healthy upstreams:
    # its authored bindings stay trustworthy, and the applied node may remain
    # blocked solely by the still-broken upstream.
    from haute._config_io import config_path_for_node
    from haute._types import GraphNode, NodeData, NodeType
    from haute.codegen import _node_to_code

    (tmp_path / "haute.toml").write_text('[project]\nname="demo"\n')
    sink_config = {
        "outputType": "file",
        "format": "parquet",
        "mode": "sink",
        "path": "out.parquet",
        "arguments": {},
    }
    generated_sink = _node_to_code(
        GraphNode(
            id="sink",
            data=NodeData(label="sink", nodeType=NodeType.DATA_OUTPUT, config=sink_config),
        ),
        source_names=["source_a"],
        derive_contract=False,
    )
    (tmp_path / "main.py").write_text(
        'import haute\nimport polars as pl\npipeline=haute.Pipeline("demo")\n'
        '@pipeline.data_input(config="a.json")\ndef source_a():\n    return None\n' + generated_sink
    )
    broken_input = {
        "inputType": "file",
        "format": "parquet",
        "mode": "scan",
        "path": "quotes.parquet",
        "arguments": {},
        "code": "",
        "cacheMode": "snapshot",
    }
    (tmp_path / "a.json").write_text(json.dumps(broken_input))
    sink_reference = tmp_path / config_path_for_node(NodeType.DATA_OUTPUT, "sink")
    sink_reference.parent.mkdir(parents=True, exist_ok=True)
    sink_reference.write_text(json.dumps({**sink_config, "cacheMode": "snapshot"}))
    document = load_pipeline_editor_document(tmp_path / "main.py", project_root=tmp_path)
    by_id = {node.authored_id: node for node in document.nodes}
    assert by_id["source_a"].availability == "unavailable"
    assert by_id["sink"].availability == "unavailable"
    upstream_bytes = (tmp_path / "a.json").read_bytes()

    request = _request(tmp_path, "sink", "recover")
    result = _apply(tmp_path, request)
    node = next(item for item in result.document.nodes if item.authored_id == "sink")
    assert node.availability == "blocked"
    written = json.loads(sink_reference.read_text())
    assert written["path"] == "out.parquet"
    assert "cacheMode" not in written
    assert (tmp_path / "a.json").read_bytes() == upstream_bytes
    sibling = next(item for item in result.document.nodes if item.authored_id == "source_a")
    assert sibling.availability == "unavailable"
