"""A request input and a response node are named by their type alone (READ-46).

Spec: specs/pipeline-config/high-level.md, "The request input and the response node are named
by their type", and specs/frontend-graph-canvas/high-level.md, Fixed names. A pipeline holds one
of each, so a Quote Input is ``quote_input``, a Quote Response ``quote_response``, a Workbench
Input ``workbench_input`` and a Workbench Output ``workbench_output``; anything else is refused
where it would be read or written.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

import haute
from haute._graph_shape import validate_singleton_groups
from haute._types import SINGLETON_NODE_NAMES, NodeType, singleton_name_problem
from haute.errors import ConfigError
from haute.graph_utils import GraphNode, NodeData, PipelineGraph
from haute.parser import parse_pipeline_source

NODE_TYPES_TS = Path(__file__).parents[1] / "frontend" / "src" / "utils" / "nodeTypes.ts"


def test_the_editor_names_each_one_as_the_backend_does() -> None:
    text = NODE_TYPES_TS.read_text(encoding="utf-8")
    block = re.search(r"export const SINGLETON_NODE_NAMES[^=]*= \{(.*?)\n\}", text, re.S)
    assert block, "nodeTypes.ts declares no SINGLETON_NODE_NAMES"
    editor = {
        NodeType[member].value: name
        for member, name in re.findall(r'\[NODE_TYPES\.(\w+)\]: "(\w+)"', block.group(1))
    }
    assert editor == {node_type.value: name for node_type, name in SINGLETON_NODE_NAMES.items()}
    assert set(SINGLETON_NODE_NAMES.values()) == {
        "quote_input",
        "quote_response",
        "workbench_input",
        "workbench_output",
    }


@pytest.mark.parametrize("node_type", list(SINGLETON_NODE_NAMES), ids=lambda t: t.value)
def test_its_one_name_passes_and_any_other_is_refused(node_type: NodeType) -> None:
    fixed = SINGLETON_NODE_NAMES[node_type]
    assert singleton_name_problem(node_type, fixed) is None
    problem = singleton_name_problem(node_type, f"{fixed}_1")
    assert problem is not None
    assert f"always named {fixed!r}" in problem
    assert singleton_name_problem(NodeType.POLARS, "anything") is None


_RESPONSE_NAMED = """\
import haute

pipeline = haute.Pipeline("p")


@pipeline.output(config="config/quote_response/response.json")
def {name}(): ...
"""


def test_a_file_that_names_one_otherwise_is_refused(tmp_path: Path) -> None:
    (tmp_path / "config" / "quote_response").mkdir(parents=True)
    (tmp_path / "config" / "quote_response" / "response.json").write_text(
        '{"output_mapping": []}', encoding="utf-8"
    )

    with pytest.raises(ConfigError, match="always named 'quote_response'"):
        parse_pipeline_source(_RESPONSE_NAMED.format(name="response"), _base_dir=tmp_path)
    graph = parse_pipeline_source(_RESPONSE_NAMED.format(name="quote_response"), _base_dir=tmp_path)
    assert [node.id for node in graph.nodes] == ["quote_response"]


def test_a_standalone_run_refuses_it_too() -> None:
    pipeline = haute.Pipeline("p")
    with pytest.raises(ConfigError, match="always named 'quote_response'"):

        @pipeline.output(config="config/quote_response/response.json")
        def response(): ...


@pytest.mark.parametrize("node_type", list(SINGLETON_NODE_NAMES), ids=lambda t: t.value)
def test_a_save_or_deploy_refuses_a_graph_that_names_one_otherwise(node_type: NodeType) -> None:
    fixed = SINGLETON_NODE_NAMES[node_type]
    named = GraphNode(id="n", data=NodeData(label=f"{fixed}_1", nodeType=node_type, config={}))
    with pytest.raises(ValueError, match=f"always named {fixed!r}"):
        validate_singleton_groups(PipelineGraph(nodes=[named], edges=[]))
    right = GraphNode(id="n", data=NodeData(label=fixed, nodeType=node_type, config={}))
    validate_singleton_groups(PipelineGraph(nodes=[right], edges=[]))
