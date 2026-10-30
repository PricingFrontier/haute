"""Removed node reuse forms fail explicitly; only submodel occurrences reuse logic."""

from pathlib import Path

import pytest

import haute
from haute._config_validation import reject_unrecognized_config_keys, validate_node_config
from haute._pipeline_recovery import load_pipeline_editor_document
from haute._pipeline_repair import (
    PipelineRepairError,
    apply_remove_unavailable_node_plan,
    build_recover_unavailable_node_plan,
)
from haute._types import NODE_TYPE_TO_DECORATOR, NodeType
from haute.errors import InvalidRequestError, ParseError
from haute.parser import parse_pipeline_source
from haute.routes._save_pipeline import SavePipelineService
from haute.schemas import (
    PipelineRepairRecoverRequest,
    PipelineRepairRemoveRequest,
    SavePipelineRequest,
)
from tests._git_helpers import init_repo


def _assert_replacement(text: str) -> None:
    for part in (
        "modules/<name>.py",
        'pipeline.submodel(<name>.submodel, "<original>")',
        'pipeline.submodel(<name>.submodel, "<instance>", instance_of="<original>")',
        'pipeline.connect(..., target_port="<input>")',
    ):
        assert part in text


@pytest.mark.parametrize(
    "decorator",
    [
        "instance",
        "instance(of='original')",
        "polars(of=original)",
        *[f"{name}(of='original')" for name in NODE_TYPE_TO_DECORATOR.values()],
    ],
)
def test_parser_refuses_removed_node_decorators_with_replacement(decorator: str) -> None:
    source = (
        'import haute\npipeline = haute.Pipeline("demo")\n'
        f"@pipeline.{decorator}\ndef copy():\n    ...\n"
    )
    with pytest.raises(ParseError) as refused:
        parse_pipeline_source(source)
    assert ("instance" if decorator.startswith("instance") else "of=") in refused.value.message
    _assert_replacement(refused.value.context["remediation"])


@pytest.mark.parametrize("node_type", [kind for kind in NodeType if kind != NodeType.SUBMODEL])
@pytest.mark.parametrize("value", ["original", "", None])
def test_config_refuses_instanceof_on_every_non_occurrence(
    node_type: NodeType, value: object
) -> None:
    for validate in (
        lambda: reject_unrecognized_config_keys(
            node_type, {"instanceOf": value}, node_label="copy"
        ),
        lambda: validate_node_config(node_type, {"instanceOf": value}, require_complete=False),
    ):
        with pytest.raises(ParseError, match="instanceOf") as refused:
            validate()
        _assert_replacement(refused.value.context["remediation"])


@pytest.mark.parametrize("form", ["instance", "of", "config", "unknown_of"])
def test_removed_node_recovery_is_unavailable_and_cannot_rebuild(tmp_path: Path, form: str) -> None:
    init_repo(tmp_path)
    decorator = {
        "instance": "instance(of='original')",
        "of": "polars(of='original')",
        "unknown_of": "unknown(of='original')",
        "config": "polars(config='copy.json')",
    }[form]
    if form == "config":
        (tmp_path / "copy.json").write_text(
            '{"instanceOf":"original"}', encoding="utf-8", newline="\n"
        )
    source = (
        'import haute\npipeline = haute.Pipeline("demo")\n'
        f"@pipeline.{decorator}\ndef copy():\n    ...\n"
    )
    path = tmp_path / "main.py"
    path.write_text(source, encoding="utf-8", newline="\n")
    document = load_pipeline_editor_document(path, project_root=tmp_path)
    node = next(node for node in document.nodes if node.authored_id == "copy")
    assert node.availability == "unavailable"
    diagnostic = next(d for d in document.diagnostics if d.diagnostic_id in node.diagnostic_ids)
    assert diagnostic.code == "node_instance_removed"
    _assert_replacement(diagnostic.remediation or "")
    assert not node.scoped_editable
    for action in ("reset", "recover"):
        request = PipelineRepairRecoverRequest(
            source_file=document.source_file,
            source_revision=document.source_revision,
            target_source_file=node.source_file,
            target_recovery_id=node.recovery_id,
            action=action,
        )
        with pytest.raises(PipelineRepairError, match="removed"):
            build_recover_unavailable_node_plan(project_root=tmp_path, request=request)
    assert path.read_text(encoding="utf-8") == source
    apply_remove_unavailable_node_plan(
        project_root=tmp_path,
        request=PipelineRepairRemoveRequest(
            source_file=document.source_file,
            source_revision=document.source_revision,
            target_source_file=node.source_file,
            target_recovery_id=node.recovery_id,
        ),
    )
    recovered = load_pipeline_editor_document(path, project_root=tmp_path)
    assert not recovered.nodes
    assert "def copy" not in path.read_text(encoding="utf-8")


@pytest.mark.parametrize("kind", [haute.Pipeline, haute.Submodel])
def test_live_dsl_has_no_instance_decorator(kind: type) -> None:
    assert not hasattr(kind, "instance")


def test_save_refuses_ordinary_instanceof_before_writing(tmp_path: Path) -> None:
    init_repo(tmp_path)
    source = (
        'import haute\nimport polars as pl\npipeline = haute.Pipeline("demo")\n'
        '@pipeline.polars\ndef original():\n    return pl.LazyFrame({"x": [1]})\n'
    )
    path = tmp_path / "main.py"
    path.write_text(source, encoding="utf-8", newline="\n")
    graph = parse_pipeline_source(source)
    graph.nodes[0].data.config["instanceOf"] = "other"
    before = {
        p.relative_to(tmp_path): p.read_bytes()
        for p in tmp_path.rglob("*")
        if p.is_file() and ".git" not in p.parts
    }
    with pytest.raises(InvalidRequestError) as refused:
        SavePipelineService(project_root=tmp_path).save(
            SavePipelineRequest(graph=graph, name="demo", source_file="main.py", base_revision=None)
        )
    assert refused.value.status_code == 400
    assert "instanceOf" in refused.value.detail
    _assert_replacement(refused.value.detail)
    after = {
        p.relative_to(tmp_path): p.read_bytes()
        for p in tmp_path.rglob("*")
        if p.is_file() and ".git" not in p.parts
    }
    assert after == before


@pytest.mark.parametrize("kind", [haute.Pipeline, haute.Submodel])
@pytest.mark.parametrize("key", ["of", "instanceOf"])
def test_live_dsl_refuses_removed_reuse_keywords(kind: type, key: str) -> None:
    registry = (
        kind("demo", definition_id="demo", input_ports=[], output_ports=[])
        if kind is haute.Submodel
        else kind("demo")
    )
    with pytest.raises(ParseError) as refused:
        registry.polars(**{key: "original"})
    _assert_replacement(refused.value.context["remediation"])
    assert not registry.nodes
