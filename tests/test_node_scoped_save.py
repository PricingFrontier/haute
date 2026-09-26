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
        '@pipeline.data_input(config="a.json")\ndef source_a(): ...\n'
        '@pipeline.data_input(config="b.json")\ndef source_b(): ...\n'
    )
    (root / "a.json").write_text(json.dumps(BROKEN_INPUT))
    (root / "b.json").write_text(json.dumps(BROKEN_INPUT))
    return main


def _recover(root: Path, authored_id: str) -> None:
    from haute._pipeline_repair import (
        apply_recover_unavailable_node_plan,
    )
    from haute.schemas import PipelineRepairRecoverRequest

    document = load_pipeline_editor_document(root / "main.py", project_root=root)
    target = next(node for node in document.nodes if node.authored_id == authored_id)
    request = PipelineRepairRecoverRequest(
        source_file=document.source_file,
        source_revision=document.source_revision,
        target_source_file=target.source_file,
        target_recovery_id=target.recovery_id,
        action="recover",
    )
    apply_recover_unavailable_node_plan(project_root=root, request=request)


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
        "import haute\n"
        'pipeline = haute.Pipeline("demo")\n'
        '@pipeline.data_input(config="x.json")\ndef source_x(): ...\n'
        '@pipeline.constant(config="custom.json")\ndef first(): ...\n'
        '@pipeline.constant(config="custom.json")\ndef second(): ...\n'
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
        '@pipeline.data_input(config="a.json")\ndef source_a(): ...\n'
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


def test_scoped_save_regenerates_the_contract_annotation_from_the_saved_settings(tmp_path):
    """Renaming a banding output changes the columns the node creates, so the
    annotation carried back from the last parse is stale; the save regenerates
    it from the saved settings rather than writing an annotation the reload's
    parse check rejects. Those settings imply the whole contract, so the
    regenerated decorator carries no annotation at all."""
    from haute._config_builder import resolve_parse_time_contract
    from haute._contracts import Contract
    from haute._pipeline_repair_actions import apply_scoped_node_save
    from haute._types import NodeType

    (tmp_path / "haute.toml").write_text('[project]\nname="demo"\n')
    main = tmp_path / "main.py"
    main.write_text(
        "import haute\n\n"
        'pipeline = haute.Pipeline("demo")\n\n\n'
        '@pipeline.data_input(config="a.json")\ndef source_a(): ...\n\n\n'
        "@pipeline.banding(\n"
        '    config="band.json",\n'
        '    contract={"inputs": ["age"], "outputs": ["age_band"]},\n'
        ")\n"
        "def band(source_a): ...\n\n\n"
        'pipeline.connect("source_a", "band")\n',
        encoding="utf-8",
        newline="\n",
    )
    (tmp_path / "a.json").write_text(json.dumps(BROKEN_INPUT))
    factor = {
        "banding": "breakpoints",
        "column": "age",
        "outputColumn": "age_band",
        "rules": [{"boundary": "25", "label": "young"}],
        "default": None,
    }
    (tmp_path / "band.json").write_text(json.dumps({"factors": [factor]}))
    document = load_pipeline_editor_document(main, project_root=tmp_path)
    band = next(node for node in document.nodes if node.authored_id == "band")
    assert band.scoped_editable is True
    assert (band.config or {})["contract"] == {"inputs": ["age"], "outputs": ["age_band"]}

    renamed = {**(band.config or {}), "factors": [{**factor, "outputColumn": "age_group"}]}
    saved = apply_scoped_node_save(
        project_root=tmp_path, request=_save_request(tmp_path, "band", renamed)
    )

    node = next(item for item in saved.nodes if item.authored_id == "band")
    assert node.availability == "blocked"
    saved_source = main.read_text(encoding="utf-8")
    assert '@pipeline.banding(config="band.json")\ndef band(source_a): ...\n' in saved_source
    assert "age_band" not in saved_source
    assert "contract" not in (node.config or {})
    assert resolve_parse_time_contract(NodeType.BANDING, node.config or {}) == Contract(
        inputs=frozenset({"age"}), outputs=frozenset({"age_group"})
    )
    assert "contract" not in json.loads((tmp_path / "band.json").read_text())


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
        {**target.config, "inputType": ["file"]},
        {**target.config, "inputType": {"kind": "file"}},
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


def test_scoped_save_refuses_a_scenario_expander_without_a_grid_size(client, monkeypatch, tmp_path):
    """The grid size has no incomplete form, so the scoped save refuses its absence
    with its own not-loadable refusal and writes nothing."""
    from haute._config_io import collect_node_configs
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
    # A broken sibling makes the document degraded, where the scoped save applies.
    (tmp_path / "main.py").write_text(
        graph_to_code(graph, pipeline_name="demo")
        + '\n@pipeline.data_input(config="a.json")\ndef source_a(): ...\n'
    )
    (tmp_path / "a.json").write_text(json.dumps(BROKEN_INPUT))
    for rel_path, content in collect_node_configs(graph).items():
        config_file = tmp_path / rel_path
        config_file.parent.mkdir(parents=True, exist_ok=True)
        config_file.write_text(content)
    monkeypatch.chdir(tmp_path)
    document = load_pipeline_editor_document(tmp_path / "main.py", project_root=tmp_path)
    target = next(node for node in document.nodes if node.authored_id == "grid")
    assert target.scoped_editable is True
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    config = {key: value for key, value in (target.config or {}).items() if key != "stepCount"}

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
    detail = response.json()["detail"]
    assert detail["code"] == "repair_action_unsupported"
    assert "not loadable" in detail["message"]
    assert "requires stepCount" in detail["message"]
    assert {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()} == before


def _inputs_and_transform(root: Path) -> None:
    _two_inputs(root)
    main = root / "main.py"
    main.write_text(
        main.read_text(encoding="utf-8")
        + "@pipeline.polars\ndef priced(source_b):\n    return source_b\n",
        encoding="utf-8",
    )


@pytest.mark.parametrize(
    "authored_id",
    [
        pytest.param("priced", id="code-only-node"),
        pytest.param("source_b", id="sidecar-node"),
    ],
)
def test_scoped_save_refuses_an_undeclared_key_without_writing(
    client, monkeypatch, tmp_path, authored_id
):
    """Code generation keeps only declared keys, so a scoped save of an
    undeclared one is refused, naming it, rather than silently dropping it."""
    _inputs_and_transform(tmp_path)
    _recover(tmp_path, "source_b")
    monkeypatch.chdir(tmp_path)
    document = load_pipeline_editor_document(tmp_path / "main.py", project_root=tmp_path)
    target = next(node for node in document.nodes if node.authored_id == authored_id)
    assert target.scoped_editable is True
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}

    response = client.post(
        "/api/pipeline/node/save",
        json={
            "source_file": document.source_file,
            "source_revision": document.source_revision,
            "target_source_file": target.source_file,
            "target_recovery_id": target.recovery_id,
            "config": {**target.config, "legacyFlag": True},
        },
    )

    assert response.status_code == 400
    detail = response.json()["detail"]
    assert detail["code"] == "node_config_undeclared_keys"
    assert detail["unrecognized_config_keys"] == ["legacyFlag"]
    assert authored_id in detail["message"]
    assert {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()} == before
