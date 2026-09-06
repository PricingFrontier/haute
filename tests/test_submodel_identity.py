"""A submodel occurrence has one identity, its name (SUB-L03)."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from haute._flatten import flatten_graph
from haute._parser_submodels import extract_submodel_registrations
from haute._types import (
    GraphEdge,
    GraphNode,
    NodeData,
    NodeType,
    PipelineGraph,
    SubmodelDefinition,
    SubmodelEndpoint,
    SubmodelInputPort,
    SubmodelOutputPort,
)
from haute.codegen import graph_to_code_multi
from haute.parser import ParseError, parse_pipeline_file
from haute.routes._helpers import _sidecar_position_key
from haute.routes._submodel_ops import create_submodel_graph

_CHILD_SUBMODEL_SOURCE = """\
import polars as pl
import haute

submodel = haute.Submodel(
    "pricing_def",
    definition_id="def_pricing_declared",
    input_ports=[
        {"name": "in_data", "targets": [{"nodeId": "child_node"}]},
    ],
    output_ports=[
        {"name": "out_data", "source": {"nodeId": "child_node"}},
    ],
)


@submodel.polars
def child_node(in_data: pl.LazyFrame) -> pl.LazyFrame:
    return in_data
"""


def _setup_project(tmp_path: Path) -> Path:
    modules_dir = tmp_path / "modules"
    modules_dir.mkdir(parents=True, exist_ok=True)
    (modules_dir / "pricing.py").write_text(_CHILD_SUBMODEL_SOURCE, encoding="utf-8")
    return tmp_path


def test_parsing_owner_and_copy_sets_name_as_id_label_and_alias(tmp_path: Path) -> None:
    _setup_project(tmp_path)
    main_py = tmp_path / "main.py"
    main_py.write_text(
        """\
import polars as pl
import haute

pipeline = haute.Pipeline("main")


@pipeline.polars
def source() -> pl.LazyFrame:
    return pl.LazyFrame({"x": [1, 2, 3]})


pipeline.submodel("modules/pricing.py", "pricing")
pipeline.submodel("modules/pricing.py", "pricing_2", instance_of="pricing")

pipeline.connect("source", "pricing", target_port="in_data")
pipeline.connect("source", "pricing_2", target_port="in_data")
""",
        encoding="utf-8",
    )

    graph = parse_pipeline_file(main_py)

    owner = graph.node_map["pricing"]
    assert owner.id == "pricing"
    assert owner.data.label == "pricing"
    assert owner.data.config["alias"] == "pricing"
    assert owner.data.config["definitionId"] == "def_pricing_declared"
    assert "instanceOf" not in owner.data.config

    copy = graph.node_map["pricing_2"]
    assert copy.id == "pricing_2"
    assert copy.data.label == "pricing_2"
    assert copy.data.config["alias"] == "pricing_2"
    assert copy.data.config["definitionId"] == "def_pricing_declared"
    assert copy.data.config["instanceOf"] == "pricing"


@pytest.mark.parametrize("kw", ["definition_id", "instance_id", "alias"])
def test_rejected_keywords_raise_parse_error(kw: str) -> None:
    source = f'pipeline.submodel("modules/pricing.py", "pricing", {kw}="foo")'
    tree = ast.parse(source)

    with pytest.raises(ParseError) as exc_info:
        extract_submodel_registrations(tree)

    assert exc_info.value.message == (
        f"pipeline.submodel() takes the file and the occurrence name; {kw}= is not accepted."
    )
    assert exc_info.value.context["keyword"] == kw
    assert exc_info.value.context["path"] == "modules/pricing.py"
    assert exc_info.value.context["remediation"] == (
        "Write pipeline.submodel(<path>, <name>) and let the child file declare its definition id."
    )


def test_missing_name_raises_parse_error() -> None:
    source = 'pipeline.submodel("modules/pricing.py")'
    tree = ast.parse(source)

    with pytest.raises(ParseError) as exc_info:
        extract_submodel_registrations(tree)

    assert exc_info.value.message == (
        "pipeline.submodel() requires the occurrence name as its second argument."
    )
    assert exc_info.value.context["path"] == "modules/pricing.py"


def test_non_canonical_name_raises_parse_error() -> None:
    source = 'pipeline.submodel("modules/pricing.py", "my pricing!")'
    tree = ast.parse(source)

    with pytest.raises(ParseError) as exc_info:
        extract_submodel_registrations(tree)

    assert exc_info.value.message == ("Submodel instance name must be a canonical identifier.")
    assert exc_info.value.context["name"] == "my pricing!"
    assert exc_info.value.context["expected"] == "my_pricing"


def test_codegen_and_roundtrip(tmp_path: Path) -> None:
    _setup_project(tmp_path)
    main_py = tmp_path / "main.py"
    original_source = """\
import polars as pl

import haute

pipeline = haute.Pipeline("main")


@pipeline.polars
def source() -> pl.LazyFrame:
    return pl.LazyFrame({"x": [1, 2, 3]})


pipeline.submodel("modules/pricing.py", "pricing")
pipeline.submodel("modules/pricing.py", "pricing_2", instance_of="pricing")
pipeline.connect("source", "pricing", target_port="in_data")
pipeline.connect("source", "pricing_2", target_port="in_data")
"""
    main_py.write_text(original_source, encoding="utf-8")

    parsed = parse_pipeline_file(main_py)
    emitted = graph_to_code_multi(parsed, pipeline_name="main", source_file="main.py")

    assert 'pipeline.submodel("modules/pricing.py", "pricing")' in emitted["main.py"]
    expected_copy = 'pipeline.submodel("modules/pricing.py", "pricing_2", instance_of="pricing")'
    assert expected_copy in emitted["main.py"]

    main_py.write_text(emitted["main.py"], encoding="utf-8")
    reparsed = parse_pipeline_file(main_py)
    re_emitted = graph_to_code_multi(reparsed, pipeline_name="main", source_file="main.py")

    assert emitted["main.py"] == re_emitted["main.py"]


def test_codegen_with_stale_editor_node_id_emits_name_everywhere() -> None:
    child_graph = PipelineGraph(
        nodes=[
            GraphNode(
                id="child_node",
                data=NodeData(
                    label="child_node",
                    nodeType=NodeType.POLARS,
                    config={},
                ),
            )
        ],
        edges=[],
        pipeline_name="pricing_def",
    )
    definition = SubmodelDefinition(
        definitionId="def_pricing",
        file="modules/pricing.py",
        graph=child_graph,
        inputPorts=[
            SubmodelInputPort(name="in_data", targets=[SubmodelEndpoint(node_id="child_node")])
        ],
        outputPorts=[
            SubmodelOutputPort(name="out_data", source=SubmodelEndpoint(node_id="child_node"))
        ],
    )

    owner_node = GraphNode(
        id="old_name",
        type="submodel",
        data=NodeData(
            label="new_name",
            nodeType=NodeType.SUBMODEL,
            config={"definitionId": "def_pricing", "alias": "new_name"},
        ),
    )
    copy_node = GraphNode(
        id="copy_id",
        type="submodel",
        data=NodeData(
            label="copy_name",
            nodeType=NodeType.SUBMODEL,
            config={
                "definitionId": "def_pricing",
                "alias": "copy_name",
                "instanceOf": "old_name",
            },
        ),
    )
    consumer_node = GraphNode(
        id="consumer",
        data=NodeData(
            label="consumer",
            nodeType=NodeType.POLARS,
            config={},
        ),
    )
    edge = GraphEdge(
        id="e1",
        source="old_name",
        target="consumer",
        sourceHandle="out__out_data",
        targetHandle=None,
    )

    graph = PipelineGraph(
        nodes=[owner_node, copy_node, consumer_node],
        edges=[edge],
        submodels={"def_pricing": definition},
        pipeline_name="main",
    )

    files = graph_to_code_multi(graph, pipeline_name="main", source_file="main.py")
    main_code = files["main.py"]

    assert "old_name" not in main_code
    assert 'pipeline.submodel("modules/pricing.py", "new_name")' in main_code
    expected_copy = 'pipeline.submodel("modules/pricing.py", "copy_name", instance_of="new_name")'
    assert expected_copy in main_code
    assert 'pipeline.connect("new_name", "consumer"' in main_code


def test_flattening_qualifies_runtime_ids_with_occurrence_name(tmp_path: Path) -> None:
    _setup_project(tmp_path)
    main_py = tmp_path / "main.py"
    main_py.write_text(
        """\
import polars as pl
import haute

pipeline = haute.Pipeline("main")


@pipeline.polars
def source() -> pl.LazyFrame:
    return pl.LazyFrame({"x": [1, 2, 3]})


pipeline.submodel("modules/pricing.py", "pricing")
pipeline.connect("source", "pricing", target_port="in_data")
""",
        encoding="utf-8",
    )

    graph = parse_pipeline_file(main_py)
    flat = flatten_graph(graph)

    assert "pricing" not in flat.node_map
    assert "submodel_runtime/pricing/child_node" in flat.node_map


def test_grouping_through_create_submodel_graph_mints_name_as_id() -> None:
    graph = PipelineGraph(
        nodes=[
            GraphNode(
                id="n1",
                data=NodeData(label="n1", nodeType=NodeType.POLARS, config={}),
            ),
            GraphNode(
                id="n2",
                data=NodeData(label="n2", nodeType=NodeType.POLARS, config={}),
            ),
            GraphNode(
                id="n3",
                data=NodeData(label="n3", nodeType=NodeType.POLARS, config={}),
            ),
        ],
        edges=[
            GraphEdge(id="e1", source="n1", target="n2"),
            GraphEdge(id="e2", source="n2", target="n3"),
        ],
        pipeline_name="main",
    )

    result = create_submodel_graph(graph, ["n1", "n2"], "grp")
    sm_node = next(n for n in result.graph.nodes if n.data.nodeType == NodeType.SUBMODEL)

    assert sm_node.id == "grp"
    assert sm_node.data.label == "grp"
    assert sm_node.data.config["alias"] == "grp"
    assert sm_node.data.config["definitionId"] == "grp"


def test_sidecar_position_key_returns_alias_for_submodel() -> None:
    node = GraphNode(
        id="node_old_id",
        type="submodel",
        data=NodeData(
            label="node_alias",
            nodeType=NodeType.SUBMODEL,
            config={"alias": "node_alias"},
        ),
    )
    assert _sidecar_position_key(node) == "node_alias"

    # Fallback to id if config has no alias
    node_no_alias = GraphNode(
        id="fallback_id",
        type="submodel",
        data=NodeData(
            label="some_label",
            nodeType=NodeType.SUBMODEL,
            config={},
        ),
    )
    assert _sidecar_position_key(node_no_alias) == "fallback_id"
