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
    name: str = "pricing",
    *,
    path: str = "modules/pricing.py",
    instance_of: str | None = None,
) -> SubmodelRegistration:
    return SubmodelRegistration(
        path=path,
        name=name,
        instance_of=instance_of,
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
        tree = ast.parse('pipeline.submodel("modules/pricing.py", "pricing")')

        assert extract_submodel_registrations(tree) == [
            SubmodelRegistration(
                path="modules/pricing.py",
                name="pricing",
                line=1,
            )
        ]

    def test_preserves_chained_registration_order(self) -> None:
        tree = ast.parse(
            'pipeline.submodel("modules/pricing.py", "pricing")'
            '.submodel("modules/pricing.py", "pricing_2", instance_of="pricing")'
        )

        registrations = extract_submodel_registrations(tree)

        assert [item.name for item in registrations] == [
            "pricing",
            "pricing_2",
        ]

    def test_ignores_non_pipeline_receivers_and_non_expression_calls(self) -> None:
        tree = ast.parse(
            'other.submodel("ignored.py", "ignored")\n'
            'value = pipeline.submodel("also_ignored.py", "ignored")\n'
        )

        assert extract_submodel_registrations(tree) == []

    @pytest.mark.parametrize("keyword", ["definition_id", "instance_id", "alias"])
    def test_rejects_obsolete_keywords(self, keyword: str) -> None:
        tree = ast.parse(f'pipeline.submodel("modules/pricing.py", "pricing", {keyword}="foo")')

        with pytest.raises(ParseError, match="not accepted") as exc_info:
            extract_submodel_registrations(tree)

        assert exc_info.value.context["keyword"] == keyword

    def test_rejects_missing_name(self) -> None:
        tree = ast.parse('pipeline.submodel("modules/pricing.py")')

        with pytest.raises(ParseError, match="requires the occurrence name") as exc_info:
            extract_submodel_registrations(tree)

        assert exc_info.value.context["path"] == "modules/pricing.py"

    def test_rejects_dynamic_path(self) -> None:
        tree = ast.parse('pipeline.submodel(path_value, "pricing")')

        with pytest.raises(ParseError, match="string literal"):
            extract_submodel_registrations(tree)

    @pytest.mark.parametrize(
        ("source", "message"),
        [
            (
                'pipeline.submodel("modules/pricing.py", "pricing", name="duplicate")',
                "duplicate keyword",
            ),
            (
                'pipeline.submodel("modules/pricing.py", pricing_var)',
                "string literal",
            ),
            (
                'pipeline.submodel("modules/pricing.py", " pricing")',
                "non-empty and unpadded",
            ),
            (
                'pipeline.submodel(" modules/pricing.py", "pricing")',
                "path must be non-empty and unpadded",
            ),
        ],
    )
    def test_rejects_invalid_registration_literals(self, source: str, message: str) -> None:
        with pytest.raises(ParseError, match=message):
            extract_submodel_registrations(ast.parse(source))

    def test_rejects_duplicate_occurrence_identity(self) -> None:
        source = (
            'pipeline.submodel("modules/pricing.py", "pricing")\n'
            'pipeline.submodel("modules/pricing.py", "pricing")\n'
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
pipeline.submodel("modules/inner.py", "inner")
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
        occurrence = result.node_map["pricing"]
        assert occurrence.data.nodeType == NodeType.SUBMODEL
        assert occurrence.data.config == {
            "definitionId": "definition_pricing",
            "alias": "pricing",
        }

    def test_reuses_one_definition_for_multiple_occurrences(self) -> None:
        result = _merge(
            registrations=[
                _registration(),
                _registration("pricing_2", instance_of="pricing"),
            ]
        )

        assert set(result.submodels or {}) == {"definition_pricing"}
        assert result.node_map["pricing"].data.label == "pricing"
        assert result.node_map["pricing_2"].data.label == "pricing_2"
        assert result.node_map["pricing_2"].data.config["definitionId"] == ("definition_pricing")
        assert result.node_map["pricing_2"].data.config["instanceOf"] == "pricing"

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
        assert ("load", "pricing", None, "in__records") in edges
        assert ("pricing", "output", "out__priced", None) in edges

    def test_flatten_qualifies_runtime_children_and_removes_definition(self) -> None:
        result = _merge(
            parent_edges=[
                ("load", "pricing", None, "records"),
                ("pricing", "output", "priced", None),
            ],
            flatten=True,
        )

        assert "pricing" not in result.node_map
        assert "submodel_runtime/pricing/base_rate" in result.node_map
        assert "submodel_runtime/pricing/adjust" in result.node_map
        assert result.submodels is None
        edges = {(edge.source, edge.target) for edge in result.edges}
        assert (
            "load",
            "submodel_runtime/pricing/base_rate",
        ) in edges
        assert (
            "submodel_runtime/pricing/adjust",
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

    def test_uses_child_definition_identity(self) -> None:
        result = _merge(definition_key="definition_other")
        assert "definition_pricing" in (result.submodels or {})

    def test_rejects_unresolved_registration_definition(self) -> None:
        with pytest.raises(ParseError, match="unresolved definition"):
            _merge(registrations=[_registration(path="modules/missing.py")])

    def test_rejects_occurrence_identity_collision_with_parent_node(self) -> None:
        with pytest.raises(ParseError, match="collides with a parent node id"):
            _merge(registrations=[_registration("load")])

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
