"""Persistent recovery proposals conserve source and reject stale mutations."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from haute._pipeline_recovery import load_pipeline_editor_document
from haute._pipeline_repair import PipelineRepairError
from haute._recovery_schemas import (
    RecoveryDraftApply,
    RecoveryDraftCreate,
    RecoveryDraftPatch,
    RecoveryTarget,
)


def _project(root: Path, config: str = '{"values":[{"name":"kept","value":"2"}],"retired":true}'):
    (root / "haute.toml").write_text('[project]\nname="draft-test"\n')
    (root / "custom.json").write_text(config)
    source = root / "main.py"
    source.write_text(
        'import haute\nimport polars as pl\npipeline=haute.Pipeline("draft-test")\n\n'
        '# keep this comment\n@pipeline.constant(config="custom.json")\n'
        'def value():\n    return pl.LazyFrame({"kept": [2.0]})\n'
    )
    return source


def _create(root: Path, node: str = "value", mode: str = "recover"):
    from haute._pipeline_recovery_drafts import create_draft

    doc = load_pipeline_editor_document(root / "main.py", project_root=root)
    return create_draft(
        root,
        RecoveryDraftCreate(
            source_file=doc.source_file,
            source_revision=doc.source_revision,
            targets=[RecoveryTarget(source_file="main.py", recovery_id=node)],
            mode=mode,
        ),
    )


def test_draft_retains_settings_and_resumes_without_writing_source(tmp_path):
    from haute._pipeline_recovery_drafts import edit_draft, get_draft

    source = _project(tmp_path)
    before = {p: p.read_bytes() for p in (source, tmp_path / "custom.json")}
    draft = _create(tmp_path)
    assert draft.nodes[0].config["values"] == [{"name": "kept", "value": "2"}]
    assert "retired" not in draft.nodes[0].config
    assert any(c.path == "/retired" and c.outcome == "removed" for c in draft.nodes[0].changes)
    updated = edit_draft(
        tmp_path,
        draft.draft_id,
        RecoveryDraftPatch(
            draft_revision=draft.draft_revision,
            configs={draft.nodes[0].key: {"values": [{"name": "kept", "value": "3"}]}},
        ),
    )
    assert get_draft(tmp_path, draft.draft_id).model_dump() == updated.model_dump()
    assert updated.draft_revision != draft.draft_revision
    assert {p: p.read_bytes() for p in before} == before
    with pytest.raises(PipelineRepairError, match="draft changed"):
        edit_draft(
            tmp_path, draft.draft_id, RecoveryDraftPatch(draft_revision=draft.draft_revision)
        )


def test_draft_apply_and_restore_are_exact_and_idempotent(tmp_path):
    from haute._pipeline_recovery_drafts import apply_draft, edit_draft, preview_draft

    source = _project(tmp_path)
    before = {p: p.read_bytes() for p in (source, tmp_path / "custom.json")}
    draft = _create(tmp_path)
    draft = edit_draft(
        tmp_path,
        draft.draft_id,
        RecoveryDraftPatch(
            draft_revision=draft.draft_revision,
            reviewed=True,
        ),
    )
    preview = preview_draft(tmp_path, draft.draft_id, draft.draft_revision)
    assert preview.plan_hash, preview.issues
    assert {p: p.read_bytes() for p in before} == before
    request = RecoveryDraftApply(
        draft_revision=draft.draft_revision,
        source_revision=draft.source_revision,
        plan_hash=preview.plan_hash,
        operation_id="apply-test-1",
    )
    applied = apply_draft(tmp_path, draft.draft_id, request)
    assert applied.draft.state == "applied"
    assert json.loads((tmp_path / "custom.json").read_text()) == {
        "values": [{"name": "kept", "value": "2"}]
    }
    assert "# keep this comment" in source.read_text()
    assert apply_draft(tmp_path, draft.draft_id, request).model_dump() == applied.model_dump()
    preview = preview_draft(tmp_path, draft.draft_id, applied.draft.draft_revision, restore=True)
    restored = apply_draft(
        tmp_path,
        draft.draft_id,
        RecoveryDraftApply(
            draft_revision=applied.draft.draft_revision,
            source_revision=applied.document.source_revision,
            plan_hash=preview.plan_hash,
            operation_id="restore-test-1",
        ),
        restore=True,
    )
    assert restored.draft.state == "restored"
    assert {p: p.read_bytes() for p in before} == before


def test_required_blank_is_editable_but_cannot_apply(tmp_path):
    from haute._pipeline_recovery_drafts import edit_draft, preview_draft

    _project(tmp_path, '{"inputType":"file","format":"parquet","mode":"scan","path":""}')
    source = tmp_path / "main.py"
    source.write_text(source.read_text().replace("pipeline.constant", "pipeline.data_input"))
    before = source.read_bytes()
    draft = _create(tmp_path)
    assert draft.state == "needs_configuration"
    assert draft.nodes[0].editable
    preview = preview_draft(tmp_path, draft.draft_id, draft.draft_revision)
    assert preview.plan_hash is None
    edited = edit_draft(
        tmp_path,
        draft.draft_id,
        RecoveryDraftPatch(
            draft_revision=draft.draft_revision,
            configs={draft.nodes[0].key: {**draft.nodes[0].config, "path": "data/example.parquet"}},
            reviewed=True,
        ),
    )
    assert edited.nodes[0].config["path"] == "data/example.parquet"
    assert source.read_bytes() == before


def test_external_changes_make_draft_stale_without_discarding_values(tmp_path):
    from haute._pipeline_recovery_drafts import get_draft, preview_draft

    _project(tmp_path)
    source = tmp_path / "main.py"
    draft = _create(tmp_path)
    source.write_text(source.read_text() + "\n# later external edit\n")
    stale = get_draft(tmp_path, draft.draft_id)
    assert stale.state == "stale"
    assert stale.nodes[0].config == draft.nodes[0].config
    with pytest.raises(PipelineRepairError):
        preview_draft(tmp_path, draft.draft_id, draft.draft_revision)


def _review(root, draft, configs=None):
    from haute._pipeline_recovery_drafts import edit_draft, preview_draft

    draft = edit_draft(
        root,
        draft.draft_id,
        RecoveryDraftPatch(
            draft_revision=draft.draft_revision,
            configs=configs or {},
            reviewed=True,
        ),
    )
    preview = preview_draft(root, draft.draft_id, draft.draft_revision)
    return draft, preview


def _apply_review(root, draft, preview, operation_id="apply-reviewed-1"):
    from haute._pipeline_recovery_drafts import apply_draft

    return apply_draft(
        root,
        draft.draft_id,
        RecoveryDraftApply(
            draft_revision=draft.draft_revision,
            source_revision=draft.source_revision,
            plan_hash=preview.plan_hash,
            operation_id=operation_id,
        ),
    )


def test_legacy_submodel_recovery_preserves_children_then_requires_explicit_code_fix(tmp_path):
    from tests.test_pipeline_repair_actions import _legacy_demo

    parent = _legacy_demo(tmp_path)
    child = tmp_path / "modules/Inputs.py"
    draft, preview = _review(tmp_path, _create(tmp_path, "Inputs"))
    assert preview.plan_hash, preview.issues
    applied = _apply_review(tmp_path, draft, preview)
    assert applied.document.load_status == "degraded"
    assert "df = live_switch" in parent.read_text()
    assert 'df = pl.LazyFrame({"premium": [1]})' in child.read_text()
    assert json.loads(parent.with_suffix(".haute.json").read_text())["positions"]["Inputs"] == {
        "x": 12,
        "y": 34,
    }
    child_after = child.read_bytes()
    consumer, preview = _review(tmp_path, _create(tmp_path, "Polars_3"))
    assert "live_switch" in consumer.nodes[0].config["code"]
    assert preview.plan_hash is None
    assert any(issue.code == "unbound_name" for issue in preview.issues)
    consumer, preview = _review(
        tmp_path,
        consumer,
        {
            consumer.nodes[0].key: {**consumer.nodes[0].config, "code": "df = Inputs"},
        },
    )
    assert preview.plan_hash, preview.issues
    applied = _apply_review(tmp_path, consumer, preview)
    assert applied.document.load_status == "ready"
    assert child.read_bytes() == child_after


def test_reset_polars_stays_a_draft_until_configured(tmp_path):
    _project(tmp_path)
    source = tmp_path / "main.py"
    source.write_text(
        'import haute\nimport polars as pl\npipeline=haute.Pipeline("test")\n'
        '@pipeline.polars\ndef value():\n    return pl.LazyFrame({"a":[1]})\n'
    )
    before = source.read_bytes()
    draft, preview = _review(tmp_path, _create(tmp_path, mode="reset"))
    assert draft.state == "needs_configuration"
    assert not draft.nodes[0].config.get("code")
    assert preview.plan_hash is None
    assert source.read_bytes() == before


@pytest.mark.parametrize("failure_at", [1, 2])
def test_apply_write_failure_rolls_back_and_keeps_editable_draft(tmp_path, monkeypatch, failure_at):
    from haute import _pipeline_recovery_drafts as service
    from haute.routes import _save_pipeline as writes

    source = _project(tmp_path)
    before = {p: p.read_bytes() for p in (source, tmp_path / "custom.json")}
    draft, preview = _review(tmp_path, _create(tmp_path))
    original = writes._stage_artifact_write_bytes
    count = 0

    def fail(path, payload, touched):
        nonlocal count
        count += 1
        if count == failure_at:
            raise OSError("injected failure")
        original(path, payload, touched)

    monkeypatch.setattr(writes, "_stage_artifact_write_bytes", fail)
    with pytest.raises(OSError, match="injected"):
        _apply_review(tmp_path, draft, preview)
    assert {p: p.read_bytes() for p in before} == before
    assert service.get_draft(tmp_path, draft.draft_id).state == "ready_to_apply"


def test_verification_failure_rolls_back_exact_bytes(tmp_path, monkeypatch):
    from haute import _pipeline_recovery_drafts as service

    source = _project(tmp_path)
    before = {p: p.read_bytes() for p in (source, tmp_path / "custom.json")}
    draft, preview = _review(tmp_path, _create(tmp_path))
    original = service._finish_operation

    def fail(root, record):
        raise RuntimeError("verification failed")

    monkeypatch.setattr(service, "_finish_operation", fail)
    with pytest.raises(RuntimeError, match="verification failed"):
        _apply_review(tmp_path, draft, preview)
    assert {p: p.read_bytes() for p in before} == before
    monkeypatch.setattr(service, "_finish_operation", original)
    assert service.get_draft(tmp_path, draft.draft_id).state == "ready_to_apply"


def test_restore_refuses_later_edits(tmp_path):
    from haute._pipeline_recovery_drafts import preview_draft

    _project(tmp_path)
    source = tmp_path / "main.py"
    draft, preview = _review(tmp_path, _create(tmp_path))
    applied = _apply_review(tmp_path, draft, preview)
    source.write_text(source.read_text() + "\n# user's later change\n")
    with pytest.raises(PipelineRepairError, match="later work"):
        preview_draft(tmp_path, draft.draft_id, applied.draft.draft_revision, restore=True)
    assert "user's later change" in source.read_text()


def test_custom_decorator_requires_manual_action(tmp_path):
    _project(tmp_path)
    source = tmp_path / "main.py"
    source.write_text(
        source.read_text().replace("@pipeline.constant", "@my_wrapper\n@pipeline.constant")
    )
    draft = _create(tmp_path)
    assert draft.state == "manual_action"
    assert not draft.nodes[0].editable


def test_recover_custom_body_requires_manual_action_but_reset_is_explicit(tmp_path):
    _project(tmp_path)
    source = tmp_path / "main.py"
    source.write_text(source.read_text().replace('{"kept": [2.0]}', '{"custom": [99]}'))
    original = source.read_bytes()
    draft, preview = _review(tmp_path, _create(tmp_path))
    assert draft.state == "manual_action"
    assert not draft.nodes[0].editable
    assert any("body" in issue.message for issue in draft.nodes[0].issues)
    assert preview.plan_hash is None
    assert source.read_bytes() == original

    reset, preview = _review(tmp_path, _create(tmp_path, mode="reset"))
    assert preview.plan_hash, preview.issues
    applied = _apply_review(tmp_path, reset, preview)
    assert applied.document.load_status == "ready"
    assert "custom" not in source.read_text().split("def value", 1)[1]


@pytest.mark.parametrize(
    ("kind", "config", "inputs"),
    [
        ("apiInput", {"path": "quotes.json"}, []),
        ("constant", {"values": [{"name": "kept", "value": "2"}]}, []),
        ("liveSwitch", {"input_scenario_map": {"quotes": "live"}}, ["quotes"]),
        ("output", {"outputMapping": []}, ["quotes"]),
        ("dataOutput", {"outputType": "file", "format": "csv", "path": "out.csv"}, ["quotes"]),
        (
            "banding",
            {"factors": [{"column": "age", "outputColumn": "age_band", "rules": []}]},
            ["quotes"],
        ),
        ("edgeJoin", {"how": "cross"}, ["quotes", "rates"]),
        ("modelling", {"algorithm": "catboost", "target": "y"}, ["quotes"]),
        ("optimiser", {"mode": "online", "data_input": "quotes"}, ["quotes"]),
        (
            "optimiserApply",
            {"sourceType": "file", "artifact_path": "opt.json", "optimiser_mode": "online"},
            ["quotes"],
        ),
    ],
)
def test_non_code_node_templates_remain_recoverable_and_custom_statements_do_not(
    kind, config, inputs
):
    import ast

    from haute._config_io import config_path_for_node, has_config_folder
    from haute._python_syntax import prepend_function_statements
    from haute._recovery_schemas import RecoveryDraftNode
    from haute._recovery_sources import _require_generated_body
    from haute._types import GraphNode, NodeData, NodeType
    from haute.codegen import _node_to_code

    node_type = NodeType(kind)
    code = _node_to_code(
        GraphNode(id="value", data=NodeData(label="value", nodeType=node_type, config=config)),
        source_names=inputs,
        derive_contract=False,
    )
    if has_config_folder(node_type):
        code = code.replace(config_path_for_node(node_type, "value").as_posix(), "custom.json")
    code = code.replace("pipeline", "submodel")
    if kind == "apiInput":
        code = prepend_function_statements(
            code,
            "from pathlib import Path as _HauteResetPath\n"
            "_HAUTE_CONFIG_BASE = _HauteResetPath(__file__).resolve().parents[1]\n",
        )
    function = ast.parse(code).body[0]
    node = RecoveryDraftNode(
        key="value",
        source_file="modules/Child.py",
        recovery_id="value",
        authored_id="value",
        label="value",
        node_type=kind,
        config=config,
        changes=[],
        issues=[],
    )
    _require_generated_body(
        node,
        function,
        params=inputs,
        reference="custom.json",
        receiver="submodel",
        config_base_depth=1,
    )
    function.body.append(ast.parse("audit_custom_result()").body[0])
    with pytest.raises(PipelineRepairError, match="body"):
        _require_generated_body(
            node,
            function,
            params=inputs,
            reference="custom.json",
            receiver="submodel",
            config_base_depth=1,
        )


@pytest.mark.parametrize(
    "signature", ["value(extra=2)", "value(*, extra=2)", "value(*args)", "value(**kwargs)"]
)
def test_non_generated_parameter_declarations_require_manual_body_review(tmp_path, signature):
    _project(tmp_path)
    source = tmp_path / "main.py"
    source.write_text(source.read_text().replace("value()", signature))
    draft = _create(tmp_path)
    assert draft.state == "manual_action"
    assert any("body" in issue.message for issue in draft.nodes[0].issues)


def _relink_project(root, output_ports, input_ports=None):
    from tests.test_pipeline_repair_actions import _legacy_demo

    _legacy_demo(root)
    parent = root / "main.py"
    parent.write_text(
        'import haute\nimport polars as pl\npipeline = haute.Pipeline("demo")\n'
        'pipeline.submodel("modules/Inputs.py", definition_id="Inputs", '
        'instance_id="old_instance", alias="Inputs", label="Inputs")\n'
    )
    original_child = root / "modules/Inputs.py"
    original_child.write_text(
        original_child.read_text().replace(
            "input_ports=[]", 'input_ports=[{"name": "input_1", "targets": []}]'
        )
    )
    replacement = root / "modules/Replacement.py"
    replacement_inputs = (
        input_ports if input_ports is not None else [{"name": "input_1", "targets": []}]
    )
    replacement.write_text(
        "import haute\nimport polars as pl\n"
        'submodel = haute.Submodel("Inputs", definition_id="Inputs", '
        f"input_ports={replacement_inputs!r}, "
        f"output_ports={output_ports!r})\n"
        "@submodel.polars\ndef live_switch():\n"
        '    df = pl.LazyFrame({"premium": [1]})\n    return df\n'
    )
    return parent, original_child, replacement


@pytest.mark.parametrize(
    ("output_name", "input_name"),
    [(None, "input_1"), ("renamed_output", "input_1"), ("output_1", "renamed_input")],
)
def test_relink_rejects_lost_unconnected_public_port(tmp_path, output_name, input_name):
    from haute._pipeline_recovery_drafts import edit_draft, get_draft

    outputs = (
        []
        if output_name is None
        else [{"name": output_name, "source": {"nodeId": "live_switch", "handleId": None}}]
    )
    parent, original_child, replacement = _relink_project(
        tmp_path, outputs, [{"name": input_name, "targets": []}]
    )
    draft = _create(tmp_path, "Inputs")
    before = {path: path.read_bytes() for path in (parent, original_child, replacement)}
    with pytest.raises(PipelineRepairError, match="public port"):
        edit_draft(
            tmp_path,
            draft.draft_id,
            RecoveryDraftPatch(
                draft_revision=draft.draft_revision,
                configs={
                    draft.nodes[0].key: {**draft.nodes[0].config, "file": "modules/Replacement.py"}
                },
            ),
        )
    assert get_draft(tmp_path, draft.draft_id).model_dump() == draft.model_dump()
    assert {path: path.read_bytes() for path in before} == before


def test_relink_preserving_public_ports_can_apply(tmp_path):
    outputs = [{"name": "output_1", "source": {"nodeId": "live_switch", "handleId": None}}]
    parent, original_child, _replacement = _relink_project(tmp_path, outputs)
    original_bytes = original_child.read_bytes()
    draft = _create(tmp_path, "Inputs")
    draft, preview = _review(
        tmp_path,
        draft,
        {draft.nodes[0].key: {**draft.nodes[0].config, "file": "modules/Replacement.py"}},
    )
    assert draft.nodes[0].config["output_ports"] == outputs
    assert preview.plan_hash, preview.issues
    applied = _apply_review(tmp_path, draft, preview)
    assert applied.document.load_status == "ready"
    assert "modules/Replacement.py" in parent.read_text()
    assert original_child.read_bytes() == original_bytes


def test_shared_config_reports_every_owner_and_preserves_both_nodes(tmp_path):
    _project(tmp_path)
    source = tmp_path / "main.py"
    source.write_text(
        source.read_text() + '\n@pipeline.constant(config="custom.json")\ndef second():\n'
        '    return pl.LazyFrame({"kept": [2.0]})\n'
    )
    draft, preview = _review(tmp_path, _create(tmp_path))
    assert any("second" in owner for owner in draft.nodes[0].affected_owners)
    applied = _apply_review(tmp_path, draft, preview)
    assert {node.authored_id for node in applied.document.nodes} == {"value", "second"}


def test_invalid_literal_json_is_archived_and_never_silently_rewritten(tmp_path):
    source = _project(tmp_path, '{"values": [], "values": [{"name":"lost","value":"3"}]}')
    before = (tmp_path / "custom.json").read_bytes()
    draft = _create(tmp_path)
    assert any(
        "Unreadable configuration is archived" in change.reason for change in draft.nodes[0].changes
    )
    assert (tmp_path / "custom.json").read_bytes() == before
    assert source.exists()


def test_coupled_submodel_and_consumer_can_apply_as_one_explicit_group(tmp_path):
    from haute._pipeline_recovery_drafts import create_draft
    from tests.test_pipeline_repair_actions import _legacy_demo

    parent = _legacy_demo(tmp_path)
    document = load_pipeline_editor_document(parent, project_root=tmp_path)
    draft = create_draft(
        tmp_path,
        RecoveryDraftCreate(
            source_file=document.source_file,
            source_revision=document.source_revision,
            targets=[
                RecoveryTarget(source_file="main.py", recovery_id=name)
                for name in ("Polars_3", "Inputs")
            ],
        ),
    )
    consumer = next(node for node in draft.nodes if node.authored_id == "Polars_3")
    draft, preview = _review(
        tmp_path, draft, {consumer.key: {**consumer.config, "code": "df = Inputs"}}
    )
    assert preview.plan_hash, preview.issues
    applied = _apply_review(tmp_path, draft, preview)
    assert applied.document.load_status == "ready"
    assert len(applied.document.nodes) == 2
    assert 'source_port="output_1"' in parent.read_text()


def test_conflicting_shared_config_candidates_cannot_be_applied(tmp_path):
    from haute._pipeline_recovery_drafts import create_draft

    _project(tmp_path)
    source = tmp_path / "main.py"
    source.write_text(
        source.read_text() + '\n@pipeline.constant(config="custom.json")\ndef second():\n'
        '    return pl.LazyFrame({"kept": [2.0]})\n'
    )
    document = load_pipeline_editor_document(source, project_root=tmp_path)
    draft = create_draft(
        tmp_path,
        RecoveryDraftCreate(
            source_file=document.source_file,
            source_revision=document.source_revision,
            targets=[
                RecoveryTarget(source_file="main.py", recovery_id=name)
                for name in ("value", "second")
            ],
        ),
    )
    draft, preview = _review(
        tmp_path, draft, {draft.nodes[1].key: {"values": [{"name": "kept", "value": "9"}]}}
    )
    assert preview.plan_hash is None
    assert any("shared" in issue.message.lower() for issue in preview.issues)


def test_submodel_port_rebinding_keeps_public_name_and_validates_child_reference(tmp_path):
    from haute._pipeline_recovery_drafts import edit_draft
    from tests.test_pipeline_repair_actions import _legacy_demo

    _legacy_demo(tmp_path)
    draft = _create(tmp_path, "Inputs")
    config = {
        **draft.nodes[0].config,
        "output_ports": [
            {"name": "output_1", "source": {"nodeId": "missing_child", "handleId": None}}
        ],
    }
    draft, preview = _review(tmp_path, draft, {draft.nodes[0].key: config})
    assert not preview.plan_hash
    with pytest.raises(PipelineRepairError, match="port"):
        edit_draft(
            tmp_path,
            draft.draft_id,
            RecoveryDraftPatch(
                draft_revision=draft.draft_revision,
                configs={
                    draft.nodes[0].key: {
                        **config,
                        "output_ports": [
                            {
                                "name": "renamed",
                                "source": {"nodeId": "live_switch", "handleId": None},
                            }
                        ],
                    }
                },
            ),
        )
