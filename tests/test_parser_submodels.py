"""Canonical submodel parser and merge contract tests."""

from __future__ import annotations

import ast

import pytest

from haute._parser_submodels import (
    SubmodelRegistration,
    extract_submodel_registrations,
    merge_submodels,
    parse_submodel_source,
)
from haute._types import GraphNode, NodeData, NodeType, PipelineGraph
from haute.errors import ParseError


def _registration(
    instance_id: str = "instance_primary",
    alias: str = "pricing",
    *,
    definition_id: str = "definition_pricing",
) -> SubmodelRegistration:
    return SubmodelRegistration(
        path="modules/pricing.py",
        definition_id=definition_id,
        instance_id=instance_id,
        alias=alias,
    )


_VALID_SUBMODEL = """\
import polars as pl
import haute

HELPER = 1
# haute:preserve-start
KEPT = "yes"
# haute:preserve-end

submodel = haute.Submodel(
    "pricing",
    description="Pricing submodel",
    definition_id="definition_pricing",
    input_ports=[
        {
            "name": "records",
            "targets": [{"nodeId": "base_rate", "handleId": None}],
        }
    ],
    output_ports=[
        {
            "name": "priced",
            "source": {"nodeId": "adjust", "handleId": None},
        }
    ],
)

@submodel.polars
def base_rate(df: pl.LazyFrame) -> pl.LazyFrame:
    return df.with_columns(pl.lit(100.0).alias("base"))

@submodel.polars
def adjust(base_rate: pl.LazyFrame) -> pl.LazyFrame:
    return base_rate.with_columns((pl.col("base") * 1.1).alias("adjusted"))

submodel.connect("base_rate", "adjust")
"""


class TestExtractSubmodelRegistrations:
    def test_extracts_explicit_identity(self) -> None:
        tree = ast.parse(
            'pipeline.submodel("modules/pricing.py", '
            'definition_id="definition_pricing", instance_id="instance_primary", '
            'alias="pricing")'
        )

        assert extract_submodel_registrations(tree) == [
            SubmodelRegistration(
                path="modules/pricing.py",
                definition_id="definition_pricing",
                instance_id="instance_primary",
                alias="pricing",
                line=1,
            )
        ]

    def test_preserves_chained_registration_order(self) -> None:
        tree = ast.parse(
            'pipeline.submodel(file="modules/pricing.py", '
            'definition_id="definition_pricing", instance_id="instance_primary", '
            'alias="pricing").submodel("modules/pricing.py", '
            'definition_id="definition_pricing", instance_id="instance_secondary", '
            'alias="pricing_2")'
        )

        registrations = extract_submodel_registrations(tree)

        assert [item.instance_id for item in registrations] == [
            "instance_primary",
            "instance_secondary",
        ]

    def test_ignores_non_pipeline_receivers_and_non_expression_calls(self) -> None:
        tree = ast.parse(
            'other.submodel("ignored.py", definition_id="ignored", '
            'instance_id="ignored", alias="ignored")\n'
            'value = pipeline.submodel("also_ignored.py", definition_id="ignored", '
            'instance_id="ignored", alias="ignored")\n'
        )

        assert extract_submodel_registrations(tree) == []

    @pytest.mark.parametrize("missing", ["definition_id", "instance_id", "alias"])
    def test_rejects_missing_identity_fields(self, missing: str) -> None:
        fields = {
            "definition_id": '"definition_pricing"',
            "instance_id": '"instance_primary"',
            "alias": '"pricing"',
        }
        fields.pop(missing)
        arguments = ", ".join(f"{name}={value}" for name, value in fields.items())
        tree = ast.parse(f'pipeline.submodel("modules/pricing.py", {arguments})')

        with pytest.raises(ParseError, match="explicit stable identity fields") as exc_info:
            extract_submodel_registrations(tree)

        assert missing in exc_info.value.context["missing_fields"]

    def test_rejects_dynamic_path(self) -> None:
        tree = ast.parse(
            'pipeline.submodel(path_value, definition_id="definition_pricing", '
            'instance_id="instance_primary", alias="pricing")'
        )

        with pytest.raises(ParseError, match="string literal"):
            extract_submodel_registrations(tree)

    @pytest.mark.parametrize(
        ("source", "message"),
        [
            (
                'pipeline.submodel("modules/pricing.py", '
                'definition_id="definition_pricing", definition_id="duplicate", '
                'instance_id="instance_primary", alias="pricing")',
                "duplicate keyword",
            ),
            (
                'pipeline.submodel("modules/pricing.py", '
                'definition_id="definition_pricing", instance_id=instance_id, '
                'alias="pricing")',
                "string literal",
            ),
            (
                'pipeline.submodel("modules/pricing.py", '
                'definition_id="definition_pricing", instance_id="instance_primary", '
                'alias=" pricing")',
                "non-empty and unpadded",
            ),
            (
                'pipeline.submodel(" modules/pricing.py", '
                'definition_id="definition_pricing", instance_id="instance_primary", '
                'alias="pricing")',
                "path must be non-empty and unpadded",
            ),
        ],
    )
    def test_rejects_invalid_registration_literals(self, source: str, message: str) -> None:
        with pytest.raises(ParseError, match=message):
            extract_submodel_registrations(ast.parse(source))

    @pytest.mark.parametrize("field", ["instance_id", "alias"])
    def test_rejects_duplicate_occurrence_identity(self, field: str) -> None:
        first = {
            "definition_id": "definition_pricing",
            "instance_id": "instance_primary",
            "alias": "pricing",
        }
        second = {
            "definition_id": "definition_pricing",
            "instance_id": "instance_secondary",
            "alias": "pricing_2",
        }
        second[field] = first[field]
        source = "\n".join(
            'pipeline.submodel("modules/pricing.py", '
            + ", ".join(f'{name}="{value}"' for name, value in values.items())
            + ")"
            for values in (first, second)
        )

        with pytest.raises(ParseError, match="duplicated"):
            extract_submodel_registrations(ast.parse(source))


class TestParseSubmodelSource:
    def test_parses_definition_identity_and_public_ports(self) -> None:
        graph = parse_submodel_source(_VALID_SUBMODEL, "modules/pricing.py")

        assert graph.pipeline_name == "pricing"
        assert graph.pipeline_description == "Pricing submodel"
        assert graph.source_file == "modules/pricing.py"
        assert graph._parser_definition_id == "definition_pricing"
        assert [port.name for port in graph._parser_input_ports or []] == ["records"]
        assert [port.name for port in graph._parser_output_ports or []] == ["priced"]
        assert {node.id for node in graph.nodes} == {"base_rate", "adjust"}
        assert {(edge.source, edge.target) for edge in graph.edges} == {("base_rate", "adjust")}

    def test_preserves_support_code(self) -> None:
        graph = parse_submodel_source(_VALID_SUBMODEL, "modules/pricing.py")

        assert graph.preamble == "HELPER = 1"
        assert graph.preserved_blocks == ['KEPT = "yes"']

    def test_rejects_obsolete_outputs_argument(self) -> None:
        source = _VALID_SUBMODEL.replace(
            "    output_ports=[",
            '    outputs=["adjust"],\n    output_ports=[',
        )

        with pytest.raises(ParseError, match="outputs is not supported"):
            parse_submodel_source(source, "modules/pricing.py")

    def test_rejects_missing_definition_contract(self) -> None:
        source = 'import haute\nsubmodel = haute.Submodel("pricing")\n'

        with pytest.raises(ParseError, match="requires definition_id"):
            parse_submodel_source(source, "modules/pricing.py")

    @pytest.mark.parametrize(
        ("source", "message"),
        [
            (
                'import haute\nsubmodel = haute.Submodel("pricing", '
                'definition_id="one", definition_id="two", '
                "input_ports=[], output_ports=[])\n",
                "duplicate keyword",
            ),
            (
                'import haute\nsubmodel = haute.Submodel("pricing", '
                "definition_id=DEFINITION_ID, input_ports=[], output_ports=[])\n",
                "literal value",
            ),
            (
                'import haute\nsubmodel = haute.Submodel("pricing", '
                'definition_id=" ", input_ports=[], output_ports=[])\n',
                "non-empty unpadded",
            ),
            (
                'import haute\nsubmodel = haute.Submodel("pricing", '
                'definition_id="definition_pricing", input_ports={}, output_ports=[])\n',
                "literal list",
            ),
            (
                'import haute\nsubmodel = haute.Submodel("pricing", '
                'definition_id="definition_pricing", input_ports=[{}], output_ports=[])\n',
                "invalid public port",
            ),
        ],
    )
    def test_rejects_invalid_definition_contract(self, source: str, message: str) -> None:
        with pytest.raises(ParseError, match=message):
            parse_submodel_source(source, "modules/pricing.py")

    def test_rejects_empty_source_instead_of_inventing_a_definition(self) -> None:
        with pytest.raises(ParseError, match="must assign"):
            parse_submodel_source("", "empty.py")

    def test_rejects_nested_submodel_registrations(self) -> None:
        nested = (
            _VALID_SUBMODEL
            + """\
pipeline.submodel(
    "modules/inner.py",
    definition_id="definition_inner",
    instance_id="instance_inner",
    alias="inner",
)
"""
        )

        with pytest.raises(ParseError, match="Nested submodels") as exc_info:
            parse_submodel_source(nested, "modules/pricing.py")

        assert exc_info.value.context["nested_paths"] == ["modules/inner.py"]

    def test_reports_syntax_errors_with_source_identity(self) -> None:
        with pytest.raises(ParseError, match="syntax errors") as exc_info:
            parse_submodel_source("def broken(:\n    pass\n", "broken.py")

        assert exc_info.value.context["source_file"] == "broken.py"


def _parent_graph() -> PipelineGraph:
    return PipelineGraph(
        pipeline_name="main",
        nodes=[
            GraphNode(
                id="load",
                data=NodeData(
                    label="load",
                    nodeType=NodeType.DATA_INPUT,
                    config={"path": "data.csv"},
                ),
            ),
            GraphNode(
                id="output",
                data=NodeData(label="output", nodeType=NodeType.OUTPUT, config={}),
            ),
        ],
    )


def _child_graph() -> PipelineGraph:
    return parse_submodel_source(_VALID_SUBMODEL, "modules/pricing.py")


def _merge(
    *,
    registrations: list[SubmodelRegistration] | None = None,
    parent_edges: list[tuple[str, str, str | None, str | None]] | None = None,
    flatten: bool = False,
    definition_key: str = "definition_pricing",
) -> PipelineGraph:
    return merge_submodels(
        _parent_graph(),
        {definition_key: _child_graph()},
        {definition_key: "modules/pricing.py"},
        parent_edges or [],
        registrations=registrations or [_registration()],
        flatten=flatten,
    )


class TestMergeSubmodels:
    def test_builds_one_definition_and_explicit_occurrence(self) -> None:
        result = _merge()

        assert set(result.submodels or {}) == {"definition_pricing"}
        definition = (result.submodels or {})["definition_pricing"]
        assert definition.definition_id == "definition_pricing"
        assert definition.file == "modules/pricing.py"
        occurrence = result.node_map["instance_primary"]
        assert occurrence.data.nodeType == NodeType.SUBMODEL
        assert occurrence.data.config == {
            "definitionId": "definition_pricing",
            "alias": "pricing",
        }

    def test_reuses_one_definition_for_multiple_occurrences(self) -> None:
        result = _merge(
            registrations=[
                _registration(),
                _registration("instance_secondary", "pricing_2"),
            ]
        )

        assert set(result.submodels or {}) == {"definition_pricing"}
        assert result.node_map["instance_primary"].data.label == "pricing"
        assert result.node_map["instance_secondary"].data.label == "pricing_2"
        assert result.node_map["instance_secondary"].data.config["definitionId"] == (
            "definition_pricing"
        )

    def test_rewrites_alias_edges_to_public_port_handles(self) -> None:
        result = _merge(
            parent_edges=[
                ("load", "pricing", None, "records"),
                ("pricing", "output", "priced", None),
            ]
        )

        edges = {
            (edge.source, edge.target, edge.sourceHandle, edge.targetHandle)
            for edge in result.edges
        }
        assert ("load", "instance_primary", None, "in__records") in edges
        assert ("instance_primary", "output", "out__priced", None) in edges

    def test_flatten_qualifies_runtime_children_and_removes_definition(self) -> None:
        result = _merge(
            parent_edges=[
                ("load", "pricing", None, "records"),
                ("pricing", "output", "priced", None),
            ],
            flatten=True,
        )

        assert "instance_primary" not in result.node_map
        assert "submodel_runtime/instance_primary/base_rate" in result.node_map
        assert "submodel_runtime/instance_primary/adjust" in result.node_map
        assert result.submodels is None
        edges = {(edge.source, edge.target) for edge in result.edges}
        assert (
            "load",
            "submodel_runtime/instance_primary/base_rate",
        ) in edges
        assert (
            "submodel_runtime/instance_primary/adjust",
            "output",
        ) in edges

    @pytest.mark.parametrize(
        ("edge", "message"),
        [
            (("load", "pricing", None, "missing"), "declared input port"),
            (("pricing", "output", "missing", None), "declared output port"),
        ],
    )
    def test_rejects_unknown_public_ports(
        self,
        edge: tuple[str, str, str | None, str | None],
        message: str,
    ) -> None:
        with pytest.raises(ParseError, match=message):
            _merge(parent_edges=[edge])

    def test_rejects_parent_child_definition_identity_mismatch(self) -> None:
        with pytest.raises(ParseError, match="do not match"):
            _merge(definition_key="definition_other")

    def test_rejects_unresolved_registration_definition(self) -> None:
        with pytest.raises(ParseError, match="unresolved definition"):
            _merge(registrations=[_registration(definition_id="definition_missing")])

    def test_rejects_occurrence_identity_collision_with_parent_node(self) -> None:
        with pytest.raises(ParseError, match="collides with a parent node id"):
            _merge(registrations=[_registration("load", "pricing")])

    def test_empty_registration_set_keeps_parent_without_definitions(self) -> None:
        result = merge_submodels(
            _parent_graph(),
            {},
            {},
            [],
            registrations=[],
        )

        assert set(result.node_map) == {"load", "output"}
        assert result.submodels == {}
