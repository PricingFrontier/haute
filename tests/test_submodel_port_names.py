"""Public submodel ports have one name (SUB-L01)."""

from __future__ import annotations

import tokenize
from pathlib import Path

import pytest
from pydantic import ValidationError

from haute import Submodel
from haute._parser_submodels import parse_submodel_source
from haute._types import (
    GraphNode,
    PipelineGraph,
    SubmodelDefinition,
    SubmodelEndpoint,
    SubmodelInputPort,
    SubmodelOutputPort,
)
from haute.codegen import graph_to_code_multi
from haute.errors import ParseError
from haute.schemas import EditorIdentityRequestNode, RecoverySubmodelDefinition

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def test_port_model_fields():
    """Assert field models match the unified name contract."""
    assert set(SubmodelInputPort.model_fields.keys()) == {"name", "targets"}
    assert set(SubmodelOutputPort.model_fields.keys()) == {"name", "source"}


def test_canonical_identifier_validation():
    """Assert port names must be canonical Python identifiers."""
    # Valid canonical name
    in_port = SubmodelInputPort(name="quotes", targets=[])
    assert in_port.name == "quotes"
    out_port = SubmodelOutputPort(
        name="quotes",
        source=SubmodelEndpoint(node_id="n1", handle_id=None),
    )
    assert out_port.name == "quotes"

    # Non-canonical: "Quotes Frame" -> expected 'Quotes_Frame'
    exp_quotes = (
        "Submodel port names must be canonical identifiers "
        "(got 'Quotes Frame'; expected 'Quotes_Frame')."
    )
    with pytest.raises(ValidationError) as exc_info:
        SubmodelInputPort(name="Quotes Frame", targets=[])
    assert exp_quotes in str(exc_info.value)

    with pytest.raises(ValidationError) as exc_info:
        SubmodelOutputPort(
            name="Quotes Frame",
            source=SubmodelEndpoint(node_id="n1", handle_id=None),
        )
    assert exp_quotes in str(exc_info.value)

    # Keyword: "class" -> expected 'node_class'
    exp_class = (
        "Submodel port names must be canonical identifiers (got 'class'; expected 'node_class')."
    )
    with pytest.raises(ValidationError) as exc_info:
        SubmodelInputPort(name="class", targets=[])
    assert exp_class in str(exc_info.value)

    with pytest.raises(ValidationError) as exc_info:
        SubmodelOutputPort(
            name="class",
            source=SubmodelEndpoint(node_id="n1", handle_id=None),
        )
    assert exp_class in str(exc_info.value)


def test_cross_direction_duplicate_port_names():
    """Assert duplicate public port names across directions are rejected."""
    with pytest.raises(ValidationError) as exc_info:
        SubmodelDefinition(
            definition_id="def_1",
            file="modules/sub_test.py",
            input_ports=[SubmodelInputPort(name="dup", targets=[])],
            output_ports=[
                SubmodelOutputPort(
                    name="dup",
                    source=SubmodelEndpoint(node_id="n1", handle_id=None),
                )
            ],
            graph=PipelineGraph(nodes=[], edges=[]),
        )
    assert "Submodel definition has duplicate public port names: ['dup']" in str(exc_info.value)


def test_parser_rejects_legacy_keys():
    """Assert parser rejects legacy portId and label with exact remediation."""
    # input_ports declares label
    src_label_in = (
        "import haute\n\n"
        "submodel = haute.Submodel(\n"
        '    "test_sub",\n'
        '    definition_id="def_1",\n'
        '    input_ports=[{"label": "quotes", "name": "quotes", "targets": []}],\n'
        "    output_ports=[],\n"
        ")\n"
    )
    exp_in_label = (
        "Submodel input_ports port declares 'label'; a public port has one name: "
        "replace 'portId' and 'label' with 'name'."
    )
    exp_in_portid = (
        "Submodel input_ports port declares 'portId'; a public port has one name: "
        "replace 'portId' and 'label' with 'name'."
    )
    exp_out_label = (
        "Submodel output_ports port declares 'label'; a public port has one name: "
        "replace 'portId' and 'label' with 'name'."
    )
    exp_out_portid = (
        "Submodel output_ports port declares 'portId'; a public port has one name: "
        "replace 'portId' and 'label' with 'name'."
    )

    with pytest.raises(ParseError) as exc_info:
        parse_submodel_source(src_label_in, "modules/test_sub.py")
    assert exp_in_label in str(exc_info.value)

    # input_ports declares portId
    src_portid_in = (
        "import haute\n\n"
        "submodel = haute.Submodel(\n"
        '    "test_sub",\n'
        '    definition_id="def_1",\n'
        '    input_ports=[{"portId": "quotes", "targets": []}],\n'
        "    output_ports=[],\n"
        ")\n"
    )
    with pytest.raises(ParseError) as exc_info:
        parse_submodel_source(src_portid_in, "modules/test_sub.py")
    assert exp_in_portid in str(exc_info.value)

    # output_ports declares label
    src_label_out = (
        "import haute\n\n"
        "submodel = haute.Submodel(\n"
        '    "test_sub",\n'
        '    definition_id="def_1",\n'
        "    input_ports=[],\n"
        '    output_ports=[{"label": "out", "source": {"nodeId": "n1"}}],\n'
        ")\n"
    )
    with pytest.raises(ParseError) as exc_info:
        parse_submodel_source(src_label_out, "modules/test_sub.py")
    assert exp_out_label in str(exc_info.value)

    # output_ports declares portId
    src_portid_out = (
        "import haute\n\n"
        "submodel = haute.Submodel(\n"
        '    "test_sub",\n'
        '    definition_id="def_1",\n'
        "    input_ports=[],\n"
        '    output_ports=[{"portId": "out", "source": {"nodeId": "n1"}}],\n'
        ")\n"
    )
    with pytest.raises(ParseError) as exc_info:
        parse_submodel_source(src_portid_out, "modules/test_sub.py")
    assert exp_out_portid in str(exc_info.value)


def test_dsl_constructor_rejects_legacy_keys():
    """Assert Submodel constructor rejects legacy keys with exact message."""
    exp_in_portid = (
        "Submodel input_ports port declares 'portId'; a public port has one name: "
        "replace 'portId' and 'label' with 'name'."
    )
    exp_in_label = (
        "Submodel input_ports port declares 'label'; a public port has one name: "
        "replace 'portId' and 'label' with 'name'."
    )
    exp_out_portid = (
        "Submodel output_ports port declares 'portId'; a public port has one name: "
        "replace 'portId' and 'label' with 'name'."
    )
    exp_out_label = (
        "Submodel output_ports port declares 'label'; a public port has one name: "
        "replace 'portId' and 'label' with 'name'."
    )

    with pytest.raises(ValueError) as exc_info:
        Submodel(
            "test_sub",
            definition_id="def_1",
            input_ports=[{"portId": "quotes", "targets": []}],
            output_ports=[],
        )
    assert exp_in_portid in str(exc_info.value)

    with pytest.raises(ValueError) as exc_info:
        Submodel(
            "test_sub",
            definition_id="def_1",
            input_ports=[{"label": "Quotes", "targets": []}],
            output_ports=[],
        )
    assert exp_in_label in str(exc_info.value)

    with pytest.raises(ValueError) as exc_info:
        Submodel(
            "test_sub",
            definition_id="def_1",
            input_ports=[],
            output_ports=[{"portId": "out", "source": {"nodeId": "n1"}}],
        )
    assert exp_out_portid in str(exc_info.value)

    with pytest.raises(ValueError) as exc_info:
        Submodel(
            "test_sub",
            definition_id="def_1",
            input_ports=[],
            output_ports=[{"label": "Out", "source": {"nodeId": "n1"}}],
        )
    assert exp_out_label in str(exc_info.value)


def test_codegen_emits_name_and_never_label_or_portid():
    """Assert codegen emits name and never label or portId for public ports."""
    definition = SubmodelDefinition(
        definition_id="sub_test",
        file="modules/sub_test.py",
        input_ports=[
            SubmodelInputPort(
                name="in_data",
                targets=[SubmodelEndpoint(node_id="proc", handle_id=None)],
            )
        ],
        output_ports=[
            SubmodelOutputPort(
                name="out_data",
                source=SubmodelEndpoint(node_id="proc", handle_id=None),
            )
        ],
        graph=PipelineGraph(
            nodes=[
                GraphNode(
                    id="proc",
                    type="polars",
                    name="proc",
                    data={
                        "nodeType": "polars",
                        "label": "proc",
                        "code": (
                            "def proc(in_data: pl.LazyFrame) -> pl.LazyFrame:\n    return in_data\n"
                        ),
                    },
                )
            ],
            edges=[],
        ),
    )
    parent_graph = PipelineGraph(
        nodes=[
            GraphNode(
                id="sub_node_1",
                type="submodel",
                name="inst1",
                data={
                    "nodeType": "submodel",
                    "label": "inst1",
                    "config": {
                        "definitionId": "sub_test",
                        "alias": "inst1",
                    },
                },
            )
        ],
        edges=[],
        submodels={"sub_test": definition},
    )
    files = graph_to_code_multi(parent_graph, pipeline_name="main")
    sub_code = files["modules/sub_test.py"]
    assert "'name': 'in_data'" in sub_code
    assert "'name': 'out_data'" in sub_code
    assert "label" not in sub_code
    assert "portId" not in sub_code


def test_deleted_schema_fields():
    """Assert deleted fields do not exist on request/recovery models."""
    assert "source_handle_labels" not in EditorIdentityRequestNode.model_fields
    assert "input_port_input_names" not in RecoverySubmodelDefinition.model_fields


def test_no_forbidden_tokens_in_src():
    """Assert no forbidden names or identifiers appear anywhere in src/haute/**/*.py."""
    forbidden = {
        "source_handle_label",
        "duplicate_public_label",
        "_public_frame_label",
        "submodel_output_label",
        "edge_input_label",
        "_port_labels",
        "input_port_input_names",
        "port_id",
        "portId",
    }
    offending: list[tuple[str, int, str]] = []
    src_dir = PROJECT_ROOT / "src" / "haute"
    for py_file in src_dir.rglob("*.py"):
        with open(py_file, "rb") as f:
            for tok in tokenize.tokenize(f.readline):
                if tok.type == tokenize.NAME and tok.string in forbidden:
                    offending.append((py_file.as_posix(), tok.start[0], tok.string))

    assert offending == []
