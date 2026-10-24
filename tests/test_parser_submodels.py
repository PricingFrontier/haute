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
def base_rate(records: pl.LazyFrame) -> pl.LazyFrame:
    return records.with_columns(pl.lit(100.0).alias("base"))

@submodel.polars
def adjust(base_rate: pl.LazyFrame) -> pl.LazyFrame:
    return base_rate.with_columns((pl.col("base") * 1.1).alias("adjusted"))

submodel.connect("base_rate", "adjust")
"""


#: ``from modules import pricing``, as a pipeline file imports ``modules/pricing.py``.
_IMPORT = "from modules import pricing\n"


class TestExtractSubmodelRegistrations:
    def test_extracts_explicit_identity(self) -> None:
        tree = ast.parse(_IMPORT + 'pipeline.submodel(pricing.submodel, "pricing")')

        assert extract_submodel_registrations(tree) == [
            SubmodelRegistration(
                path="modules/pricing.py",
                name="pricing",
                line=2,
                binding="pricing",
            )
        ]

    def test_accepts_the_name_keyword(self) -> None:
        tree = ast.parse(_IMPORT + 'pipeline.submodel(pricing.submodel, name="pricing")')

        assert extract_submodel_registrations(tree) == [
            SubmodelRegistration(
                path="modules/pricing.py", name="pricing", line=2, binding="pricing"
            )
        ]

    @pytest.mark.parametrize(
        ("source", "message"),
        [
            ("pipeline.submodel()", "requires the occurrence name"),
            ('pipeline.submodel(name="pricing")', r"definition first, .* it was given nothing"),
        ],
        ids=["no-arguments", "name-only"],
    )
    def test_rejects_a_registration_without_a_definition(self, source: str, message: str) -> None:
        """With no arguments at all, the missing name is reported first."""
        with pytest.raises(ParseError, match=message):
            extract_submodel_registrations(ast.parse(source))

    def test_preserves_chained_registration_order(self) -> None:
        tree = ast.parse(
            _IMPORT + 'pipeline.submodel(pricing.submodel, "pricing")'
            '.submodel(pricing.submodel, "pricing_2", instance_of="pricing")'
        )

        registrations = extract_submodel_registrations(tree)

        assert [item.name for item in registrations] == [
            "pricing",
            "pricing_2",
        ]

    def test_ignores_non_pipeline_receivers_and_non_expression_calls(self) -> None:
        tree = ast.parse(
            'other.submodel(ignored.submodel, "ignored")\n'
            'value = pipeline.submodel(also_ignored.submodel, "ignored")\n'
        )

        assert extract_submodel_registrations(tree) == []

    @pytest.mark.parametrize("keyword", ["definition_id", "instance_id", "alias"])
    def test_rejects_obsolete_keywords(self, keyword: str) -> None:
        tree = ast.parse(
            _IMPORT + f'pipeline.submodel(pricing.submodel, "pricing", {keyword}="foo")'
        )

        with pytest.raises(ParseError, match="not accepted") as exc_info:
            extract_submodel_registrations(tree)

        assert exc_info.value.context["keyword"] == keyword

    def test_rejects_missing_name(self) -> None:
        tree = ast.parse(_IMPORT + "pipeline.submodel(pricing.submodel)")

        with pytest.raises(ParseError, match="requires the occurrence name") as exc_info:
            extract_submodel_registrations(tree)

        assert exc_info.value.context["line"] == 2

    @pytest.mark.parametrize(
        "definition",
        [
            pytest.param("definition_value", id="a-variable"),
            pytest.param("pricing", id="the-module-itself"),
            pytest.param("pricing.other", id="another-attribute"),
            pytest.param("package.pricing.submodel", id="a-dotted-module"),
        ],
    )
    def test_rejects_dynamic_definition(self, definition: str) -> None:
        """The first argument is ``<module>.submodel``, read from the source, never computed."""
        tree = ast.parse(_IMPORT + f'pipeline.submodel({definition}, "pricing")')

        with pytest.raises(ParseError, match=r"definition first, `<module>\.submodel`") as exc_info:
            extract_submodel_registrations(tree)

        assert "`from modules import <module>`" in exc_info.value.context["remediation"]

    @pytest.mark.parametrize(
        ("source", "message"),
        [
            (
                'pipeline.submodel(pricing.submodel, "pricing", name="duplicate")',
                "duplicate keyword",
            ),
            (
                "pipeline.submodel(pricing.submodel, pricing_var)",
                "string literal",
            ),
            (
                'pipeline.submodel(pricing.submodel, " pricing")',
                "non-empty and unpadded",
            ),
        ],
    )
    def test_rejects_invalid_registration_literals(self, source: str, message: str) -> None:
        with pytest.raises(ParseError, match=message):
            extract_submodel_registrations(ast.parse(_IMPORT + source))

    def test_rejects_duplicate_occurrence_identity(self) -> None:
        source = (
            _IMPORT + 'pipeline.submodel(pricing.submodel, "pricing")\n'
            'pipeline.submodel(pricing.submodel, "pricing")\n'
        )

        with pytest.raises(ParseError, match="duplicated"):
            extract_submodel_registrations(ast.parse(source))

    def test_refuses_the_definition_as_a_keyword(self) -> None:
        """One spelling: the definition is the first argument, as codegen writes it and
        as the live API's positional-only parameter takes it."""
        source = (
            "from modules import pricing\n"
            'pipeline.submodel(definition=pricing.submodel, name="pricing")\n'
        )

        with pytest.raises(ParseError, match="as its first argument.*not as definition="):
            extract_submodel_registrations(ast.parse(source))

    @pytest.mark.parametrize(
        ("registration", "path"),
        [
            pytest.param(
                'pipeline.submodel("modules/pricing.py", "pricing")',
                "modules/pricing.py",
                id="a-path",
            ),
            pytest.param(
                'pipeline.submodel(file="modules/pricing.py", name="pricing")',
                "modules/pricing.py",
                id="the-file-keyword",
            ),
        ],
    )
    def test_refuses_the_removed_path_form_naming_the_import_form(
        self, registration: str, path: str
    ) -> None:
        """A file path in ``modules/`` is refused, with the import that replaces it."""
        with pytest.raises(
            ParseError, match="registers 'pricing' by its file path, which was removed"
        ) as exc_info:
            extract_submodel_registrations(ast.parse(registration))

        assert exc_info.value.context["path"] == path
        assert exc_info.value.context["remediation"] == (
            "Write `from modules import pricing`, then "
            "`pipeline.submodel(pricing.submodel, 'pricing')`."
        )

    @pytest.mark.parametrize(
        ("registration", "path"),
        [
            pytest.param(
                'pipeline.submodel("pricing.py", "pricing")', "pricing.py", id="beside-the-pipeline"
            ),
            pytest.param(
                'pipeline.submodel("lib/pricing.py", "pricing")', "lib/pricing.py", id="elsewhere"
            ),
            pytest.param(
                'pipeline.submodel("modules/rating/pricing.py", "pricing")',
                "modules/rating/pricing.py",
                id="nested-in-modules",
            ),
            pytest.param(
                'pipeline.submodel("../pricing.py", "pricing")', "../pricing.py", id="climbing"
            ),
            pytest.param(
                'pipeline.submodel(" modules/pricing.py", "pricing")',
                " modules/pricing.py",
                id="padded",
            ),
            pytest.param(
                'pipeline.submodel(file=path_value, name="pricing")', None, id="computed-file"
            ),
        ],
    )
    def test_refuses_a_path_no_import_can_name_saying_where_the_file_belongs(
        self, registration: str, path: str | None
    ) -> None:
        """``from modules import x`` always means ``modules/x.py``: nothing else is expressible."""
        with pytest.raises(
            ParseError, match="registers 'pricing' by its file path, which was removed"
        ) as exc_info:
            extract_submodel_registrations(ast.parse(registration))

        assert exc_info.value.context["path"] == path
        assert exc_info.value.context["remediation"].startswith(
            "Move the definition into modules/<name>.py beside the pipeline, "
            "then write `from modules import <name>`"
        )

    @pytest.mark.parametrize(
        ("imports", "message"),
        [
            pytest.param("from modules import pricing as rates", r"without `as` or `\*`", id="as"),
            pytest.param("from modules import *", r"without `as` or `\*`", id="star"),
            pytest.param("from .modules import pricing", "is not that form", id="relative"),
            pytest.param("from modules.pricing import submodel", "is not that form", id="dotted"),
            pytest.param("import modules.pricing", "is not that form", id="import-dotted"),
            pytest.param("import modules", "is not that form", id="import-package"),
            pytest.param("from modules import pricing, pricing", "imported twice", id="twice"),
        ],
    )
    def test_refuses_an_import_that_is_not_from_modules_import_name(
        self, imports: str, message: str
    ) -> None:
        source = f'{imports}\npipeline.submodel(pricing.submodel, "pricing")\n'

        with pytest.raises(ParseError, match=message) as exc_info:
            extract_submodel_registrations(ast.parse(source))

        assert exc_info.value.context["line"] == 1

    @pytest.mark.parametrize(
        ("source", "unused"),
        [
            pytest.param(
                "from modules import pricing, rates\n"
                'pipeline.submodel(pricing.submodel, "pricing")',
                ["rates"],
                id="one-of-two",
            ),
            pytest.param(_IMPORT, ["pricing"], id="no-registration-at-all"),
        ],
    )
    def test_refuses_an_import_no_registration_uses(self, source: str, unused: list[str]) -> None:
        with pytest.raises(ParseError, match="from modules without registering it") as exc_info:
            extract_submodel_registrations(ast.parse(source))

        assert exc_info.value.context["names"] == unused

    def test_imports_the_caller_gives_are_not_checked_for_use(self) -> None:
        """Recovery reads each registration alone, passing in the whole file's imports:
        one another statement registers is not unused."""
        tree = ast.parse('pipeline.submodel(pricing.submodel, "pricing")')
        imports = {"pricing": "modules/pricing.py", "rates": "modules/rates.py"}

        registrations = extract_submodel_registrations(tree, imports=imports)

        assert [(item.path, item.binding) for item in registrations] == [
            ("modules/pricing.py", "pricing")
        ]

    def test_refuses_a_registration_naming_an_unimported_module(self) -> None:
        tree = ast.parse('pipeline.submodel(pricing.submodel, "pricing")')

        with pytest.raises(
            ParseError, match="registers pricing.submodel, but 'pricing' is not imported"
        ) as exc_info:
            extract_submodel_registrations(tree)

        assert exc_info.value.context["remediation"] == (
            "Add `from modules import pricing` with the imports."
        )

    @pytest.mark.parametrize(
        ("module", "message"),
        [
            pytest.param("__init__", "not starting with '__'", id="dunder"),
            pytest.param("input", "hides Python's built-in", id="built-in"),
            # Named as a node is, as Create Submodel names it: ASCII only.
            pytest.param("straße", "ASCII letters, digits and underscores", id="non-ASCII"),
        ],
    )
    def test_refuses_a_module_name_python_reserves(self, module: str, message: str) -> None:
        """A keyword cannot be imported at all: ``from modules import class`` does not parse."""
        source = f'from modules import {module}\npipeline.submodel({module}.submodel, "pricing")\n'

        with pytest.raises(ParseError, match=message) as exc_info:
            extract_submodel_registrations(ast.parse(source))

        assert exc_info.value.context["name"] == module
        assert exc_info.value.context["remediation"].startswith(f"Rename modules/{module}.py")


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

    @pytest.mark.parametrize(
        "registration",
        [
            pytest.param(
                'from modules import inner\n\npipeline.submodel(inner.submodel, "inner")\n',
                id="imported",
            ),
            pytest.param('pipeline.submodel("modules/inner.py", "inner")\n', id="by-file-path"),
        ],
    )
    def test_rejects_nested_submodel_registrations(self, registration: str) -> None:
        """A registration in a definition file is nesting, whatever it registers."""
        nested = _VALID_SUBMODEL + registration

        with pytest.raises(ParseError, match="Nested submodels") as exc_info:
            parse_submodel_source(nested, "modules/pricing.py")

        assert exc_info.value.context["nested_lines"] == [len(nested.splitlines())]

    def test_reports_syntax_errors_with_source_identity(self) -> None:
        with pytest.raises(ParseError, match="syntax errors") as exc_info:
            parse_submodel_source("def broken(:\n    pass\n", "broken.py")

        assert exc_info.value.context["source_file"] == "broken.py"

    def test_refuses_a_node_whose_parameters_do_not_name_its_inputs(self) -> None:
        """The file's own parse checks its bindings, so the failure names the file."""
        source = _VALID_SUBMODEL.replace("def base_rate(records:", "def base_rate(df:")

        with pytest.raises(ParseError, match="parameters do not match") as exc_info:
            parse_submodel_source(source, "modules/pricing.py")

        assert exc_info.value.context["node_id"] == "base_rate"
        assert exc_info.value.context["unbound_parameters"] == ["df"]
        assert exc_info.value.context["source_file"] == "modules/pricing.py"

    @pytest.mark.parametrize(
        ("old", "new", "message"),
        [
            pytest.param(
                '"nodeId": "adjust"', '"nodeId": "ghost"', "missing child nodes", id="missing-node"
            ),
            pytest.param(
                '"name": "priced"',
                '"name": "records"',
                "duplicate public port names",
                id="duplicate-name",
            ),
        ],
    )
    def test_refuses_ports_that_break_the_interface(self, old: str, new: str, message: str) -> None:
        """Refused by the file's own parse, not when the definition is built."""
        with pytest.raises(ParseError, match=message) as exc_info:
            parse_submodel_source(_VALID_SUBMODEL.replace(old, new), "modules/pricing.py")

        assert exc_info.value.context["source_file"] == "modules/pricing.py"


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
        registration_definitions={"modules/pricing.py": "definition_pricing"},
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
            registration_definitions={},
        )

        assert set(result.node_map) == {"load", "output"}
        assert result.submodels == {}
