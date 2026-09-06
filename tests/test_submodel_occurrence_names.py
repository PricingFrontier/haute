"""Submodel occurrences have one name, the alias (SUB-L02).

Contract tests ensuring an occurrence is identified exclusively by its alias:
- Registration syntax requires alias and rejects label=
- Aliases must be canonical Python identifiers
- Parsed occurrence GraphNode uses alias as label
- Invariant: node.data.label == config.alias enforced on graph validation
- Codegen emits alias= without label= and checks alias collisions
- Parse -> codegen -> parse round trip is byte-identical
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from haute._parser_submodels import extract_submodel_registrations
from haute._submodel_instances import validate_submodel_instances
from haute._types import (
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
from haute.errors import ParseError
from haute.parser import parse_pipeline_source
from haute.pipeline import Pipeline

_CHILD_SUBMODEL_SOURCE = """\
import polars as pl
import haute

submodel = haute.Submodel(
    "pricing",
    definition_id="def_pricing",
    input_ports=[
        {"name": "policy", "targets": [{"nodeId": "step_a", "handleId": None}]},
    ],
    output_ports=[
        {"name": "premium", "source": {"nodeId": "step_a", "handleId": None}},
    ],
)

@submodel.polars
def step_a(policy: pl.LazyFrame) -> pl.LazyFrame:
    return policy.with_columns(pl.lit(100.0).alias("premium"))
"""


def _make_definition() -> SubmodelDefinition:
    return SubmodelDefinition(
        definitionId="def_pricing",
        file="modules/pricing.py",
        graph=PipelineGraph(
            nodes=[
                GraphNode(
                    id="step_a",
                    data=NodeData(
                        label="step_a",
                        nodeType=NodeType.POLARS,
                        config={},
                    ),
                ),
            ],
            edges=[],
        ),
        inputPorts=[
            SubmodelInputPort(
                name="policy",
                targets=[SubmodelEndpoint(nodeId="step_a")],
            ),
        ],
        outputPorts=[
            SubmodelOutputPort(
                name="premium",
                source=SubmodelEndpoint(nodeId="step_a"),
            ),
        ],
    )


def test_parsing_parent_with_label_raises_parse_error() -> None:
    tree = ast.parse(
        'pipeline.submodel("modules/pricing.py", '
        'definition_id="def_pricing", instance_id="inst_pricing", '
        'alias="pricing", label="My Pricing")'
    )

    with pytest.raises(ParseError) as exc_info:
        extract_submodel_registrations(tree)

    assert exc_info.value.message == (
        "pipeline.submodel() no longer accepts label=; an occurrence's name is its alias."
    )
    assert exc_info.value.context["remediation"] == (
        "Remove label= and rename the occurrence by changing alias=."
    )
    assert exc_info.value.context["path"] == "modules/pricing.py"
    assert exc_info.value.context["line"] == 1


@pytest.mark.parametrize(
    ("raw_alias", "expected_sanitised"),
    [
        ("My Pricing", "My_Pricing"),
        ("class", "node_class"),
    ],
)
def test_parsing_non_canonical_alias_raises_parse_error(
    raw_alias: str,
    expected_sanitised: str,
) -> None:
    tree = ast.parse(
        f'pipeline.submodel("modules/pricing.py", '
        f'definition_id="def_pricing", instance_id="inst_pricing", '
        f'alias="{raw_alias}")'
    )

    with pytest.raises(ParseError) as exc_info:
        extract_submodel_registrations(tree)

    assert exc_info.value.message == ("Submodel instance alias must be a canonical identifier.")
    assert exc_info.value.context["alias"] == raw_alias
    assert exc_info.value.context["expected"] == expected_sanitised
    assert exc_info.value.context["line"] == 1


def test_after_parsing_occurrence_label_equals_alias(tmp_path: Path) -> None:
    modules_dir = tmp_path / "modules"
    modules_dir.mkdir(parents=True, exist_ok=True)
    (modules_dir / "pricing.py").write_text(_CHILD_SUBMODEL_SOURCE, encoding="utf-8")

    parent_source = """\
import polars as pl
import haute

pipeline = haute.Pipeline("main")

pipeline.submodel(
    "modules/pricing.py",
    definition_id="def_pricing",
    instance_id="inst_primary",
    alias="primary_pricing",
).submodel(
    "modules/pricing.py",
    definition_id="def_pricing",
    instance_id="inst_secondary",
    alias="secondary_pricing",
    instance_of="inst_primary",
)

@pipeline.polars
def source() -> pl.LazyFrame:
    return pl.LazyFrame({"policy": [1, 2, 3]})

@pipeline.polars
def sink(secondary_pricing: pl.LazyFrame) -> pl.LazyFrame:
    return secondary_pricing

pipeline.connect("source", "primary_pricing", target_port="policy")
pipeline.connect(
    "primary_pricing",
    "secondary_pricing",
    source_port="premium",
    target_port="policy",
)
pipeline.connect("secondary_pricing", "sink", source_port="premium")
"""
    main_path = tmp_path / "main.py"
    main_path.write_text(parent_source, encoding="utf-8")

    parsed = parse_pipeline_source(
        parent_source,
        source_file=str(main_path),
        _base_dir=tmp_path,
    )

    occurrences = [node for node in parsed.nodes if node.data.nodeType == NodeType.SUBMODEL]
    assert len(occurrences) == 2
    for node in occurrences:
        assert node.data.label == node.data.config["alias"]


def test_pipeline_submodel_dsl_validation() -> None:
    p = Pipeline("main")

    with pytest.raises(TypeError):
        p.submodel(  # type: ignore[call-arg]
            "modules/pricing.py",
            definition_id="def_pricing",
            instance_id="inst_1",
            alias="pricing_1",
            label="Pricing One",
        )

    with pytest.raises(
        ValueError,
        match=(
            r"Submodel alias must be a canonical identifier "
            r"\(got 'My Pricing'; expected 'My_Pricing'\)\."
        ),
    ):
        p.submodel(
            "modules/pricing.py",
            definition_id="def_pricing",
            instance_id="inst_1",
            alias="My Pricing",
        )

    with pytest.raises(
        ValueError,
        match=(
            r"Submodel alias must be a canonical identifier "
            r"\(got 'class'; expected 'node_class'\)\."
        ),
    ):
        p.submodel(
            "modules/pricing.py",
            definition_id="def_pricing",
            instance_id="inst_1",
            alias="class",
        )


def test_codegen_two_occurrences_no_label_roundtrip_byte_identical(tmp_path: Path) -> None:
    modules_dir = tmp_path / "modules"
    modules_dir.mkdir(parents=True, exist_ok=True)
    (modules_dir / "pricing.py").write_text(_CHILD_SUBMODEL_SOURCE, encoding="utf-8")

    parent_source = """\
import polars as pl
import haute

pipeline = haute.Pipeline("main")

pipeline.submodel(
    "modules/pricing.py",
    definition_id="def_pricing",
    instance_id="inst_a",
    alias="pricing_a",
).submodel(
    "modules/pricing.py",
    definition_id="def_pricing",
    instance_id="inst_b",
    alias="pricing_b",
    instance_of="inst_a",
)

@pipeline.polars
def source() -> pl.LazyFrame:
    return pl.LazyFrame({"policy": [1, 2, 3]})

@pipeline.polars
def sink(pricing_b: pl.LazyFrame) -> pl.LazyFrame:
    return pricing_b

pipeline.connect("source", "pricing_a", target_port="policy")
pipeline.connect("pricing_a", "pricing_b", source_port="premium", target_port="policy")
pipeline.connect("pricing_b", "sink", source_port="premium")
"""
    main_path = tmp_path / "main.py"
    main_path.write_text(parent_source, encoding="utf-8")

    parsed_initial = parse_pipeline_source(
        parent_source,
        source_file=str(main_path),
        _base_dir=tmp_path,
    )

    generated_files = graph_to_code_multi(
        parsed_initial,
        pipeline_name="main",
        source_file="main.py",
    )
    main_code = generated_files["main.py"]

    assert "label=" not in main_code
    assert 'alias="pricing_a"' in main_code
    assert 'alias="pricing_b"' in main_code

    reparsed = parse_pipeline_source(
        main_code,
        source_file=str(main_path),
        _base_dir=tmp_path,
    )
    regenerated_files = graph_to_code_multi(
        reparsed,
        pipeline_name="main",
        source_file="main.py",
    )
    regenerated_code = regenerated_files["main.py"]

    assert main_code == regenerated_code


def test_validate_submodel_instances_invariant_label_differs_from_alias() -> None:
    graph = PipelineGraph(
        nodes=[
            GraphNode(
                id="inst_1",
                type="submodel",
                data=NodeData(
                    label="Different Label",
                    nodeType=NodeType.SUBMODEL,
                    config={"definitionId": "def_pricing", "alias": "pricing"},
                ),
            ),
        ],
        edges=[],
        submodels={"def_pricing": _make_definition()},
    )

    with pytest.raises(ParseError) as exc_info:
        validate_submodel_instances(graph)

    assert exc_info.value.message == "Submodel occurrence label must equal its alias."
    assert exc_info.value.context["instance_id"] == "inst_1"
    assert exc_info.value.context["label"] == "Different Label"
    assert exc_info.value.context["alias"] == "pricing"
    assert exc_info.value.context["remediation"] == (
        "An occurrence's display name is its alias; rename it by changing the alias."
    )


def test_alias_colliding_with_root_node_function_name_fails_codegen() -> None:
    graph = PipelineGraph(
        nodes=[
            GraphNode(
                id="root_pricing",
                data=NodeData(
                    label="pricing",
                    nodeType=NodeType.POLARS,
                    config={"code": "return pl.DataFrame()"},
                ),
            ),
            GraphNode(
                id="inst_pricing",
                type="submodel",
                data=NodeData(
                    label="pricing",
                    nodeType=NodeType.SUBMODEL,
                    config={"definitionId": "def_pricing", "alias": "pricing"},
                ),
            ),
        ],
        edges=[],
        submodels={"def_pricing": _make_definition()},
    )

    with pytest.raises(
        ParseError, match="Multiple node labels sanitize to the same Python function name"
    ):
        graph_to_code_multi(
            graph,
            pipeline_name="main",
            source_file="main.py",
        )
