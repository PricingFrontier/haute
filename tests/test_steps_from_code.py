"""Every stepped node's code is its only copy of its steps (READ-44).

Spec: specs/pipeline-config/high-level.md, Stepped transforms. A Data Input, External File,
Rating Step, Model Score or Scenario Expander has its steps rebuilt from its body each
time it loads, as a transform has (READ-22): its sidecar writes none, one that does is
refused, and an unfinished step list waits in the pipeline's ``.haute.json``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from haute._config_io import collect_node_configs, config_path_for_node, load_node_config
from haute._pipeline_recovery import load_pipeline_editor_document
from haute._polars_steps_lift import canonical_steps
from haute._types import NodeType
from haute.codegen import graph_to_code
from haute.errors import ConfigError, ParseError
from haute.graph_utils import GraphNode, NodeData, PipelineGraph
from haute.parser import parse_pipeline_source
from haute.routes._helpers import parse_pipeline_to_graph, save_sidecar
from tests.conftest import make_edge, make_file_input_config

pytestmark = pytest.mark.usefixtures("_widen_sandbox_root")


def _number(value: float) -> dict[str, Any]:
    return {"kind": "literal", "type": "number", "value": value}


FILTER = {
    "id": "f",
    "kind": "filter",
    "match": "all",
    "conditions": [{"column": "premium", "operator": "gt", "value": _number(100)}],
}
GROSS = {
    "id": "w",
    "kind": "with_column",
    "name": "gross",
    "expr": {
        "type": "binary",
        "left": {"kind": "column", "name": "premium"},
        "op": "*",
        "right": _number(1.12),
    },
}
STEPS = [FILTER, GROSS]
UNFINISHED = [FILTER, {"id": "u", "kind": "filter", "match": "all", "conditions": []}, GROSS]

#: Each frame surface's own settings, beside its steps.
SURFACES: dict[NodeType, dict[str, Any]] = {
    NodeType.DATA_INPUT: make_file_input_config("quotes.parquet"),
    NodeType.EXTERNAL_FILE: {"path": "model.pkl", "file_type": "pickle"},
    NodeType.RATING_STEP: {"tables": []},
    NodeType.MODEL_SCORE: {"source_type": "file", "model_path": "model.cbm"},
    NodeType.SCENARIO_EXPANDER: {
        "column_name": "scenario",
        "min_value": 0,
        "max_value": 1,
        "step_count": 2,
    },
}
SURFACE_IDS = [node_type.value for node_type in SURFACES]


def _graph(node_type: NodeType, steps: list[dict[str, Any]]) -> PipelineGraph:
    """A graph whose node ``n`` is *node_type* with *steps*, fed by a Data Input unless it is
    one."""
    node = GraphNode(
        id="n",
        data=NodeData(
            label="n", nodeType=node_type, config={**SURFACES[node_type], "steps": steps}
        ),
    )
    if node_type == NodeType.DATA_INPUT:
        return PipelineGraph(nodes=[node], edges=[])
    quotes = GraphNode(
        id="quotes",
        data=NodeData(
            label="quotes",
            nodeType=NodeType.DATA_INPUT,
            config=make_file_input_config("quotes.parquet"),
        ),
    )
    return PipelineGraph(nodes=[quotes, node], edges=[make_edge("quotes", "n")])


def _saved(tmp_path: Path, graph: PipelineGraph) -> str:
    """*graph* saved under *tmp_path*: its module, each node's sidecar and ``.haute.json``."""
    for rel, content in collect_node_configs(graph).items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_bytes(content.encode("utf-8"))
    code = graph_to_code(graph, pipeline_name="main")
    (tmp_path / "main.py").write_bytes(code.encode("utf-8"))
    save_sidecar(tmp_path / "main.py", graph)
    return code


def _loaded(tmp_path: Path, code: str | None = None) -> GraphNode:
    source = code if code is not None else (tmp_path / "main.py").read_text(encoding="utf-8")
    graph = parse_pipeline_source(source, _base_dir=tmp_path)
    return next(node for node in graph.nodes if node.id == "n")


def _sidecar(tmp_path: Path, node_type: NodeType) -> dict[str, Any]:
    path = tmp_path / config_path_for_node(node_type, "n")
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.mark.parametrize("node_type", SURFACES, ids=SURFACE_IDS)
def test_the_sidecar_holds_no_steps_and_the_code_gives_them_back(
    tmp_path: Path, node_type: NodeType
) -> None:
    code = _saved(tmp_path, _graph(node_type, STEPS))

    assert "steps" not in _sidecar(tmp_path, node_type)
    assert 'df = df.filter(pl.col("premium") > 100)' in code
    node = _loaded(tmp_path)
    assert node.data.config["steps"] == canonical_steps(node_type, STEPS)
    assert node.data.editor.steps_discarded is None


@pytest.mark.parametrize("node_type", SURFACES, ids=SURFACE_IDS)
def test_a_hand_edit_to_one_statement_keeps_the_other_steps(
    tmp_path: Path, node_type: NodeType
) -> None:
    code = _saved(tmp_path, _graph(node_type, STEPS)).replace(
        'df = df.filter(pl.col("premium") > 100)', "df = df.pipe(lambda frame: frame)"
    )

    node = _loaded(tmp_path, code)

    steps = node.data.config["steps"]
    assert [step["kind"] for step in steps] == ["free_code", "with_column"]
    assert steps[0]["code"] == "df = df.pipe(lambda frame: frame)"
    assert steps[1] == canonical_steps(node_type, STEPS)[1]


# A Model Score with no code is generated from its model's columns, which needs a model.
_DECLARED = [node_type for node_type in SURFACES if node_type != NodeType.MODEL_SCORE]


@pytest.mark.parametrize("node_type", _DECLARED, ids=[t.value for t in _DECLARED])
def test_a_node_with_no_steps_loads_with_none_yet(tmp_path: Path, node_type: NodeType) -> None:
    """Its body is a declaration, so the node keeps its steps, none, rather than reading as
    a node with no code."""
    _saved(tmp_path, _graph(node_type, []))

    assert _loaded(tmp_path).data.config["steps"] == []


@pytest.mark.parametrize("node_type", SURFACES, ids=SURFACE_IDS)
def test_a_sidecar_that_still_holds_steps_is_refused_naming_it(
    tmp_path: Path, node_type: NodeType
) -> None:
    code = _saved(tmp_path, _graph(node_type, STEPS))
    path = tmp_path / config_path_for_node(node_type, "n")
    path.write_text(json.dumps({**_sidecar(tmp_path, node_type), "steps": STEPS}), "utf-8")

    with pytest.raises(ConfigError, match=r"holds the node's steps") as refused:
        load_node_config(path, node_type=node_type)
    assert str(path) in str(refused.value)
    assert refused.value.context["removed_config_keys"] == ["steps"]
    with pytest.raises(ConfigError, match=r"rebuilt from the function's body"):
        _loaded(tmp_path, code)


@pytest.mark.parametrize("node_type", SURFACES, ids=SURFACE_IDS)
def test_an_unfinished_step_list_waits_in_the_pipeline_sidecar(
    tmp_path: Path, node_type: NodeType
) -> None:
    graph = _graph(node_type, UNFINISHED)
    assert next(n for n in graph.nodes if n.id == "n").data.editor.steps_error is not None

    code = _saved(tmp_path, graph)

    assert "raise NotImplementedError" in code
    assert "steps" not in _sidecar(tmp_path, node_type)
    held = json.loads((tmp_path / "main.haute.json").read_text(encoding="utf-8"))
    assert held["unfinished_steps"] == {"n": UNFINISHED}
    # The graph a run loads and the editor's document both give the node its steps back.
    loaded = next(n for n in parse_pipeline_to_graph(tmp_path / "main.py").nodes if n.id == "n")
    assert loaded.data.config["steps"] == UNFINISHED
    document = load_pipeline_editor_document(tmp_path / "main.py", project_root=tmp_path)
    opened = next(node for node in document.nodes if node.recovery_id == "n")
    assert opened.config is not None
    assert opened.config["steps"] == UNFINISHED

    # Completed, the steps move into the code and leave the pipeline's sidecar.
    _saved(tmp_path, _graph(node_type, STEPS))
    assert "unfinished_steps" not in json.loads(
        (tmp_path / "main.haute.json").read_text(encoding="utf-8")
    )
    assert _loaded(tmp_path).data.config["steps"] == canonical_steps(node_type, STEPS)


def _join(input_name: str) -> dict[str, Any]:
    return {
        "id": "j",
        "kind": "join",
        "input": input_name,
        "how": "left",
        "maintain_order": "left_right",
        "left_on": ["region"],
        "right_on": ["region"],
        "suffix": "_r",
    }


@pytest.mark.parametrize("node_type", [NodeType.POLARS, NodeType.EXTERNAL_FILE], ids=str)
def test_a_step_naming_an_input_not_connected_waits_in_the_pipeline_sidecar(
    tmp_path: Path, node_type: NodeType
) -> None:
    """The steps render on their own, so the node carries no ``steps_error``; against the
    inputs connected to it they do not, so the save writes the placeholder and keeps the
    list in ``.haute.json`` rather than losing it."""
    steps = [_join("rates")]
    if node_type == NodeType.POLARS:
        steps = [{"id": "s", "kind": "source", "input": "quotes"}, *steps]
        config: dict[str, Any] = {"steps": steps}
    else:
        config = {**SURFACES[node_type], "steps": steps}
    quotes = GraphNode(
        id="quotes",
        data=NodeData(
            label="quotes",
            nodeType=NodeType.DATA_INPUT,
            config=make_file_input_config("quotes.parquet"),
        ),
    )
    node = GraphNode(id="n", data=NodeData(label="n", nodeType=node_type, config=config))
    assert node.data.editor.steps_error is None
    graph = PipelineGraph(nodes=[quotes, node], edges=[make_edge("quotes", "n")])

    code = _saved(tmp_path, graph)

    assert "raise NotImplementedError" in code
    held = json.loads((tmp_path / "main.haute.json").read_text(encoding="utf-8"))
    assert held["unfinished_steps"] == {"n": steps}
    loaded = next(n for n in parse_pipeline_to_graph(tmp_path / "main.py").nodes if n.id == "n")
    assert loaded.data.config["steps"] == steps


@pytest.mark.parametrize("node_type", SURFACES, ids=SURFACE_IDS)
def test_a_run_parses_the_unfinished_steps_back_and_refuses_a_placeholder_without_them(
    tmp_path: Path, node_type: NodeType
) -> None:
    """``haute run`` and a deploy parse the file themselves: the node takes its steps from
    ``.haute.json`` and fails naming the unfinished one. Without them, a frame surface would
    pass its frame on with its steps skipped, so it is refused; a Transform with no code
    fails when run, so it loads."""
    from haute.parser import parse_pipeline_file

    _saved(tmp_path, _graph(node_type, UNFINISHED))

    ran = next(n for n in parse_pipeline_file(tmp_path / "main.py").nodes if n.id == "n")
    assert ran.data.config["steps"] == UNFINISHED
    assert ran.data.editor.steps_error is not None

    (tmp_path / "main.haute.json").write_text("{}", encoding="utf-8")
    if node_type == NodeType.POLARS:
        bare = next(n for n in parse_pipeline_file(tmp_path / "main.py").nodes if n.id == "n")
        assert "steps" not in bare.data.config
        return
    with pytest.raises(ParseError, match="'n' is saved with unfinished steps.*holds none"):
        parse_pipeline_file(tmp_path / "main.py")


def _external_file_joining_rates() -> PipelineGraph:
    """An External File whose step joins ``rates``, which is not connected to it: the steps
    render, but not against its inputs, so its body is saved as the placeholder."""
    quotes = GraphNode(
        id="quotes",
        data=NodeData(
            label="quotes",
            nodeType=NodeType.DATA_INPUT,
            config=make_file_input_config("quotes.parquet"),
        ),
    )
    node = GraphNode(
        id="n",
        data=NodeData(
            label="n",
            nodeType=NodeType.EXTERNAL_FILE,
            config={**SURFACES[NodeType.EXTERNAL_FILE], "steps": [_join("rates")]},
        ),
    )
    return PipelineGraph(nodes=[quotes, node], edges=[make_edge("quotes", "n")])


def test_a_degraded_document_gives_a_frame_surface_its_unfinished_steps(tmp_path: Path) -> None:
    """Another node's broken setting degrades the document; the stepped node still opens
    with the steps ``.haute.json`` holds, as a Transform's do (READ-44)."""
    _saved(tmp_path, _graph(NodeType.EXTERNAL_FILE, UNFINISHED))
    quotes_sidecar = tmp_path / config_path_for_node(NodeType.DATA_INPUT, "quotes")
    quotes_sidecar.write_text('{"colour": "red"}', encoding="utf-8")

    document = load_pipeline_editor_document(tmp_path / "main.py", project_root=tmp_path)

    assert document.load_status == "degraded"
    opened = next(node for node in document.nodes if node.recovery_id == "n")
    assert opened.config is not None
    assert opened.config["steps"] == UNFINISHED


def test_recover_keeps_the_unfinished_steps_held_for_the_node(tmp_path: Path) -> None:
    """Recovering an unrelated setting regenerates the node from its settings and its steps:
    the steps ``.haute.json`` holds, unfinished against its inputs, stay there behind the
    placeholder rather than being overwritten by a declaration."""
    from haute._pipeline_repair import apply_recover_unavailable_node_plan
    from haute.schemas import PipelineRepairRecoverRequest

    (tmp_path / "haute.toml").write_text('[project]\nname = "demo"\n', encoding="utf-8")
    _saved(tmp_path, _external_file_joining_rates())
    held = json.loads((tmp_path / "main.haute.json").read_text(encoding="utf-8"))
    assert held["unfinished_steps"] == {"n": [_join("rates")]}
    sidecar = tmp_path / config_path_for_node(NodeType.EXTERNAL_FILE, "n")
    sidecar.write_text(
        json.dumps({**_sidecar(tmp_path, NodeType.EXTERNAL_FILE), "colour": "red"}),
        encoding="utf-8",
    )
    document = load_pipeline_editor_document(tmp_path / "main.py", project_root=tmp_path)
    target = next(node for node in document.nodes if node.authored_id == "n")
    assert target.availability == "unavailable"

    result = apply_recover_unavailable_node_plan(
        project_root=tmp_path,
        request=PipelineRepairRecoverRequest(
            source_file=document.source_file,
            source_revision=document.source_revision,
            target_source_file=target.source_file,
            target_recovery_id=target.recovery_id,
            action="recover",
        ),
    )

    assert "raise NotImplementedError" in (tmp_path / "main.py").read_text(encoding="utf-8")
    held = json.loads((tmp_path / "main.haute.json").read_text(encoding="utf-8"))
    assert held["unfinished_steps"] == {"n": [_join("rates")]}
    recovered = next(node for node in result.document.nodes if node.authored_id == "n")
    assert recovered.config is not None
    assert recovered.config["steps"] == [_join("rates")]
