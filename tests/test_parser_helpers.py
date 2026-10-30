"""Comprehensive tests for the parser implementation modules.

Covers public functions not already exercised in test_parser_internals.py:
  - _eval_ast_literal
  - _get_decorator_kwargs
  - _is_pipeline_authored_decorator / _is_submodel_authored_decorator
  - _extract_connect_calls
  - _build_edges
  - _build_rf_nodes
  - _extract_meta / _extract_pipeline_meta / _extract_submodel_meta
  - _extract_preserved_blocks
  - _resolve_node_config  (with external config files)

Edge-case coverage for functions already partly tested in test_parser_internals.py:
  - _build_node_config  (rating_step edge cases, constant, scenario_expander, etc.)
"""

from __future__ import annotations

import ast
import json
from typing import Any
from unittest.mock import patch

import pytest

from haute._ast_helpers import (
    _dedent,
    _eval_ast_literal,
    _extract_connect_calls,
    _extract_meta,
    _extract_pipeline_meta,
    _extract_preamble,
    _extract_preserved_blocks,
    _extract_submodel_meta,
    _function_body_source,
    _get_decorator_kwargs,
    _get_decorator_node_type,
    _is_pipeline_authored_decorator,
    _is_submodel_authored_decorator,
    _strip_docstring,
)
from haute._code_extraction import _unwrap_chain_assignment, extract_user_code
from haute._config_builder import _build_node_config, _copy_config_keys, _resolve_node_config
from haute._config_io import has_config_folder
from haute._graph_builders import _build_edges, _build_rf_nodes, _extract_decorated_nodes
from haute._types import NodeType
from haute.errors import ParseError

# ===========================================================================
# _eval_ast_literal
# ===========================================================================


class TestEvalAstLiteral:
    def test_string_literal(self):
        node = ast.parse('"hello"', mode="eval").body
        assert _eval_ast_literal(node) == "hello"

    def test_int_literal(self):
        node = ast.parse("42", mode="eval").body
        assert _eval_ast_literal(node) == 42

    def test_float_literal(self):
        node = ast.parse("3.14", mode="eval").body
        assert _eval_ast_literal(node) == pytest.approx(3.14)

    def test_bool_literal(self):
        node = ast.parse("True", mode="eval").body
        assert _eval_ast_literal(node) is True

    def test_none_literal(self):
        node = ast.parse("None", mode="eval").body
        assert _eval_ast_literal(node) is None

    def test_list_literal(self):
        node = ast.parse("[1, 2, 3]", mode="eval").body
        assert _eval_ast_literal(node) == [1, 2, 3]

    def test_dict_literal(self):
        node = ast.parse('{"a": 1}', mode="eval").body
        assert _eval_ast_literal(node) == {"a": 1}

    def test_negative_number_literal(self):
        """Unary minus is part of the literal grammar — must keep evaluating."""
        node = ast.parse("-7", mode="eval").body
        assert _eval_ast_literal(node) == -7

    def test_non_literal_call_raises_parse_error(self):
        """A function call node cannot be literal_eval'd.

        PIN REVISION (5.5): the prior assertion pinned ast.dump fallback
        output; this replacement pins the stricter fail-loud contract.
        Remediation 5.5: the old behavior returned ``ast.dump(node)`` — an
        AST repr string like ``Call(func=Name(...))`` — which downstream
        codegen re-emitted into the pipeline file as a corrupt decorator.
        Non-literals must instead be rejected loudly at parse time.
        """
        node = ast.parse("foo()", mode="eval").body
        with pytest.raises(ParseError, match="non-literal"):
            _eval_ast_literal(node)

    def test_non_literal_name_raises_parse_error(self):
        node = ast.parse("SOME_CONSTANT", mode="eval").body
        with pytest.raises(ParseError, match="SOME_CONSTANT"):
            _eval_ast_literal(node)

    def test_non_literal_fstring_raises_parse_error(self):
        node = ast.parse('f"exp-{v}"', mode="eval").body
        with pytest.raises(ParseError, match="non-literal"):
            _eval_ast_literal(node)

    def test_non_literal_attribute_raises_parse_error(self):
        node = ast.parse("pl.Float64", mode="eval").body
        with pytest.raises(ParseError, match="pl.Float64"):
            _eval_ast_literal(node)

    def test_non_literal_arithmetic_raises_parse_error(self):
        node = ast.parse("50 + 5", mode="eval").body
        with pytest.raises(ParseError, match="non-literal"):
            _eval_ast_literal(node)

    def test_nested_non_literal_inside_list_raises_parse_error(self):
        """literal_eval rejects the whole container when any element is non-literal."""
        node = ast.parse('[Path("a") / "b"]', mode="eval").body
        with pytest.raises(ParseError, match="non-literal"):
            _eval_ast_literal(node)

    def test_error_never_contains_ast_dump_garbage(self):
        """The corrupt ``Call(func=Name(...))`` repr must never surface anywhere."""
        node = ast.parse("foo(bar)", mode="eval").body
        with pytest.raises(ParseError) as excinfo:
            _eval_ast_literal(node)
        assert "Call(func=" not in str(excinfo.value)
        assert "Name(id=" not in str(excinfo.value)

    def test_contract_constructor_still_lowered(self):
        """``Contract(...)`` is the one sanctioned non-literal spelling."""
        node = ast.parse('Contract(inputs=["a"], outputs=["b"])', mode="eval").body
        assert _eval_ast_literal(node) == {"inputs": ["a"], "outputs": ["b"]}

    def test_qualified_contract_constructor_still_lowered(self):
        node = ast.parse('haute.Contract(inputs=["a"], outputs=["b"])', mode="eval").body
        assert _eval_ast_literal(node) == {"inputs": ["a"], "outputs": ["b"]}

    def test_two_positional_contract_form_still_lowered(self):
        node = ast.parse('Contract(["a"], ["b"])', mode="eval").body
        assert _eval_ast_literal(node) == (["a"], ["b"])

    def test_contract_with_non_literal_inside_raises(self):
        """Non-literals nested inside Contract(...) must also fail loud."""
        node = ast.parse("Contract(inputs=SOME_VAR, outputs=['b'])", mode="eval").body
        with pytest.raises(ParseError, match="SOME_VAR"):
            _eval_ast_literal(node)

    def test_contract_with_unknown_keyword_raises(self):
        """An unrecognised Contract kwarg is not lowered — the call is non-literal."""
        node = ast.parse("Contract(bogus=['a'])", mode="eval").body
        with pytest.raises(ParseError, match="non-literal"):
            _eval_ast_literal(node)

    def test_contract_with_single_positional_raises(self):
        """Only the two-positional and keyword Contract forms are lowered."""
        node = ast.parse("Contract(X)", mode="eval").body
        with pytest.raises(ParseError, match="non-literal"):
            _eval_ast_literal(node)


# ===========================================================================
# _get_decorator_kwargs
# ===========================================================================


class TestGetDecoratorKwargs:
    def _parse_decorator(self, source: str) -> ast.expr:
        """Parse a single decorated function and return its decorator."""
        tree = ast.parse(source)
        func = tree.body[0]
        assert isinstance(func, ast.FunctionDef)
        return func.decorator_list[0]

    def test_no_call_returns_empty(self):
        dec = self._parse_decorator("@pipeline.node\ndef f(): pass")
        assert _get_decorator_kwargs(dec) == {}

    def test_call_with_kwargs(self):
        dec = self._parse_decorator(
            '@pipeline.node(path="data.parquet", output=True)\ndef f(): pass'
        )
        kwargs = _get_decorator_kwargs(dec)
        assert kwargs["path"] == "data.parquet"
        assert kwargs["output"] is True

    def test_call_no_kwargs(self):
        dec = self._parse_decorator("@pipeline.node()\ndef f(): pass")
        assert _get_decorator_kwargs(dec) == {}

    def test_ignores_positional_args(self):
        dec = self._parse_decorator("@pipeline.node(42, x=1)\ndef f(): pass")
        kwargs = _get_decorator_kwargs(dec)
        assert kwargs == {"x": 1}

    def test_star_kwargs_raise(self):
        """``**cfg`` cannot be resolved at parse time and would be silently
        dropped on the next save — reject loudly instead.

        PIN REVISION (5.5): the prior assertion pinned silent ``**kwargs``
        skipping; this replacement pins the stricter fail-loud contract.
        """
        dec = self._parse_decorator("@pipeline.node(**cfg, x=1)\ndef f(): pass")
        with pytest.raises(ParseError, match=r"\*\*"):
            _get_decorator_kwargs(dec)

    # --- Remediation 5.5: non-literal kwarg values are rejected loudly ----

    def test_name_reference_kwarg_raises_naming_the_kwarg(self):
        dec = self._parse_decorator("@pipeline.polars(selected_columns=COLS)\ndef f(): pass")
        with pytest.raises(ParseError, match="selected_columns"):
            _get_decorator_kwargs(dec)

    def test_call_kwarg_raises_naming_the_kwarg(self):
        dec = self._parse_decorator('@pipeline.data_input(path=Path("x"))\ndef f(): pass')
        with pytest.raises(ParseError, match="path"):
            _get_decorator_kwargs(dec)

    def test_fstring_kwarg_raises(self):
        dec = self._parse_decorator(
            '@pipeline.model_score(experiment_name=f"e-{v}")\ndef f(): pass'
        )
        with pytest.raises(ParseError, match="experiment_name"):
            _get_decorator_kwargs(dec)

    def test_nested_non_literal_in_list_kwarg_raises(self):
        """The exact shape pinned by the old corruption test: a list whose
        element is a computed value (``[Path("data") / "input.parquet"]``)."""
        dec = self._parse_decorator(
            '@pipeline.polars(selected_columns=[Path("data") / "input.parquet"])\ndef f(): pass'
        )
        with pytest.raises(ParseError, match="selected_columns"):
            _get_decorator_kwargs(dec)

    def test_nested_non_literal_in_dict_kwarg_raises(self):
        dec = self._parse_decorator('@pipeline.rating_step(tables={"k": VAR})\ndef f(): pass')
        with pytest.raises(ParseError, match="tables"):
            _get_decorator_kwargs(dec)

    def test_error_includes_line_number(self):
        dec = self._parse_decorator("@pipeline.polars(\n    ok=1,\n    bad=COLS,\n)\ndef f(): pass")
        with pytest.raises(ParseError, match="line=3"):
            _get_decorator_kwargs(dec)

    def test_error_message_says_literals_required(self):
        """Charter wording: the rejection must say decorator kwargs must be literals."""
        dec = self._parse_decorator("@pipeline.polars(cols=COLS)\ndef f(): pass")
        with pytest.raises(ParseError, match="must be literal"):
            _get_decorator_kwargs(dec)

    def test_contract_kwarg_still_accepted(self):
        dec = self._parse_decorator(
            '@pipeline.polars(contract=Contract(inputs=["a"], outputs=["b"]))\ndef f(): pass'
        )
        kwargs = _get_decorator_kwargs(dec)
        assert kwargs == {"contract": {"inputs": ["a"], "outputs": ["b"]}}


# ===========================================================================
# _is_pipeline_authored_decorator / _is_submodel_authored_decorator
# ===========================================================================


# ===========================================================================
# _extract_connect_calls
# ===========================================================================


class TestExtractConnectCalls:
    def test_basic_connect(self):
        source = 'pipeline.connect("a", "b")\npipeline.connect("b", "c")'
        tree = ast.parse(source)
        pairs = _extract_connect_calls(tree)
        # Each entry carries optional source and target ports. Bare connect
        # calls report both ports as None.
        assert pairs == [("a", "b", None, None), ("b", "c", None, None)]

    def test_no_connect_calls(self):
        source = "x = 1"
        tree = ast.parse(source)
        assert _extract_connect_calls(tree) == []

    def test_custom_receiver(self):
        source = 'submodel.connect("x", "y")'
        tree = ast.parse(source)
        assert _extract_connect_calls(tree, receiver="submodel") == [("x", "y", None, None)]
        assert _extract_connect_calls(tree, receiver="pipeline") == []

    def test_non_literal_args_raise_parse_error(self):
        """Non-literal connect args used to become ``ast.dump`` garbage strings
        that silently failed the node-name lookup, dropping the edge on the
        next save.

        PIN REVISION (5.5): the prior assertion pinned ast.dump string
        extraction; this replacement pins the stricter fail-loud contract.
        """
        source = "pipeline.connect(a, b)"
        tree = ast.parse(source)
        with pytest.raises(ParseError, match="connect"):
            _extract_connect_calls(tree)

    def test_non_literal_port_raises_parse_error(self):
        source = 'pipeline.connect("a", "b", source_port=PORT)'
        tree = ast.parse(source)
        with pytest.raises(ParseError, match="source_port"):
            _extract_connect_calls(tree)

    @pytest.mark.parametrize(
        ("call", "argument", "fragment"),
        [
            ('pipeline.connect(1, "b")', "source", "must be a non-empty string"),
            ('pipeline.connect("a", 2)', "target", "must be a non-empty string"),
            ('pipeline.connect("", "b")', "source", "must be a non-empty string"),
            ('pipeline.connect("a")', "target", "is missing"),
            ('pipeline.connect(target="b")', "source", "is missing"),
            ('pipeline.connect("a", "b", "c")', "*", "takes at most two positional"),
            ('pipeline.connect("a", "b", source="c")', "source", "is given twice"),
            ('pipeline.connect("a", "b", weird=1)', "weird", "has no argument"),
            ('pipeline.connect("a", "b", source_prt="table")', "source_prt", "has no argument"),
            ('pipeline.connect("a", "b", source_port=123)', "source_port", "None or a non-empty"),
            ('pipeline.connect("a", "b", target_port="")', "target_port", "None or a non-empty"),
            ("pipeline.connect(*pair)", "*", "unpacked"),
            ('pipeline.connect("a", "b", **ports)', "**", "unpacked"),
        ],
    )
    def test_a_call_the_live_signature_refuses_fails_the_parse(
        self, call: str, argument: str, fragment: str
    ):
        """The parser reads connect() by the live signature's rules: a call Python or
        ``Pipeline.connect`` would refuse is a located ParseError, never skipped or
        repaired into an edge with a ``None`` port."""
        tree = ast.parse(f"x = 1\n{call}")
        with pytest.raises(ParseError, match=fragment) as raised:
            _extract_connect_calls(tree)
        assert raised.value.context["line"] == 2
        assert raised.value.context["argument"] == argument

    def test_port_none_treated_as_absent(self):
        source = 'pipeline.connect("a", "b", source_port=None, target_port=None)'
        tree = ast.parse(source)
        assert _extract_connect_calls(tree) == [("a", "b", None, None)]

    def test_chained_connect_calls_unrolled_in_source_order(self):
        """``connect()`` returns ``Self`` and is documented as chainable —
        the parser must not silently drop chained edges."""
        source = 'pipeline.connect("a", "b").connect("b", "c")'
        tree = ast.parse(source)
        assert _extract_connect_calls(tree) == [
            ("a", "b", None, None),
            ("b", "c", None, None),
        ]

    def test_chained_connect_with_ports(self):
        source = 'pipeline.connect("a", "b", source_port="p").connect("b", "c", target_port="q")'
        tree = ast.parse(source)
        assert _extract_connect_calls(tree) == [
            ("a", "b", "p", None),
            ("b", "c", None, "q"),
        ]

    def test_chain_through_non_connect_link_still_extracts_connects(self):
        source = 'pipeline.connect("a", "b").describe().connect("b", "c")'
        tree = ast.parse(source)
        assert _extract_connect_calls(tree) == [
            ("a", "b", None, None),
            ("b", "c", None, None),
        ]

    def test_keyword_source_target_form(self):
        """``source`` / ``target`` are positional-or-keyword in the runtime
        signature, so the all-keyword spelling is valid running code."""
        source = 'pipeline.connect(source="a", target="b")'
        tree = ast.parse(source)
        assert _extract_connect_calls(tree) == [("a", "b", None, None)]

    def test_mixed_positional_and_keyword_target(self):
        source = 'pipeline.connect("a", target="b", target_port="base")'
        tree = ast.parse(source)
        assert _extract_connect_calls(tree) == [("a", "b", None, "base")]

    def test_ignores_wrong_method(self):
        source = 'pipeline.add("a", "b")'
        tree = ast.parse(source)
        assert _extract_connect_calls(tree) == []

    def test_ignores_nested_connect(self):
        """Only module-level calls are captured."""
        source = 'def f():\n    pipeline.connect("x", "y")'
        tree = ast.parse(source)
        assert _extract_connect_calls(tree) == []

    def test_rejects_chained_attribute_receiver(self):
        """module.pipeline.connect() should be rejected (receiver is not ast.Name)."""
        source = 'module.pipeline.connect("a", "b")'
        tree = ast.parse(source)
        assert _extract_connect_calls(tree) == []

    def test_rejects_deeply_chained_receiver(self):
        """a.b.c.connect() should be rejected."""
        source = 'a.b.c.connect("x", "y")'
        tree = ast.parse(source)
        assert _extract_connect_calls(tree) == []

    def test_correct_receiver_still_works_after_fix(self):
        """pipeline.connect() with correct receiver should still work."""
        source = 'pipeline.connect("a", "b")\npipeline.connect("c", "d")'
        tree = ast.parse(source)
        pairs = _extract_connect_calls(tree, receiver="pipeline")
        assert pairs == [("a", "b", None, None), ("c", "d", None, None)]

    def test_extracts_source_and_target_ports(self):
        source = 'pipeline.connect("quotes", "join", source_port="policies", target_port="base")'
        tree = ast.parse(source)
        assert _extract_connect_calls(tree) == [("quotes", "join", "policies", "base")]

    def test_chained_receiver_with_custom_receiver(self):
        """module.submodel.connect() should be rejected for receiver='submodel'."""
        source = 'module.submodel.connect("a", "b")'
        tree = ast.parse(source)
        assert _extract_connect_calls(tree, receiver="submodel") == []

    def test_rejects_subscript_receiver(self):
        """receivers[0].connect() should be rejected (subscript, not ast.Name)."""
        source = 'receivers[0].connect("a", "b")'
        tree = ast.parse(source)
        assert _extract_connect_calls(tree) == []

    def test_rejects_call_receiver(self):
        """get_pipeline().connect() should be rejected (call, not ast.Name)."""
        source = 'get_pipeline().connect("a", "b")'
        tree = ast.parse(source)
        assert _extract_connect_calls(tree) == []


# ===========================================================================
# _build_edges
# ===========================================================================


class TestBuildEdges:
    @staticmethod
    def _raw(name: str, params: list[str]) -> dict:
        return {
            "func_name": name,
            "param_names": params,
            "edge_param_names": params,
            "node_type": "polars",
        }

    def test_explicit_edges(self):
        nodes = [self._raw("a", []), self._raw("b", ["a"])]
        edges = _build_edges(nodes, [("a", "b", None, None)])
        assert len(edges) == 1
        assert edges[0].source == "a" and edges[0].target == "b"

    def test_implicit_from_param_names(self):
        nodes = [self._raw("source", []), self._raw("transform", ["source"])]
        edges = _build_edges(nodes, [])
        assert len(edges) == 1
        assert edges[0].source == "source" and edges[0].target == "transform"

    def test_explicit_and_implicit_edges_coexist(self):
        """Implicit edges supplement explicit ones for the same target."""
        nodes = [
            self._raw("a", []),
            self._raw("b", []),
            self._raw("c", ["a", "b"]),  # param names match a and b
        ]
        edges = _build_edges(nodes, [("a", "c", None, None)])
        targets_of_c = sorted([(e.source, e.target) for e in edges if e.target == "c"])
        # Explicit (a,c) + implicit (b,c) -- both present
        assert targets_of_c == [("a", "c"), ("b", "c")]

    def test_no_edges_are_invented_without_params_or_connects(self):
        """Zero declared edges parse as zero edges — the parser never invents a chain.

        Regression for the definition-order fallback that fabricated a linear
        chain for any multi-node file with no explicit or implicit edges: a
        deliberately disconnected graph was unrepresentable (deleting the last
        edge resurrected it on reparse, and a GUI save then materialised the
        invented edge into source).
        """
        nodes = [
            self._raw("x", []),
            self._raw("y", ["unrelated"]),
            self._raw("z", ["other"]),
        ]
        assert _build_edges(nodes, []) == []

    def test_single_node_no_edges(self):
        nodes = [self._raw("only", [])]
        assert _build_edges(nodes, []) == []

    def test_ignores_connect_to_unknown_node(self):
        nodes = [self._raw("a", [])]
        edges = _build_edges(nodes, [("a", "missing", None, None)])
        assert edges == []

    def test_self_reference_not_added(self):
        """A node whose param name matches itself should not create a self-edge."""
        nodes = [self._raw("a", []), self._raw("b", ["b"])]
        edges = _build_edges(nodes, [])
        for e in edges:
            assert not (e.source == "b" and e.target == "b")


# ===========================================================================
# _build_rf_nodes
# ===========================================================================


class TestBuildRfNodes:
    def test_positions_and_labels(self):
        raw = [
            {"func_name": "a", "node_type": "dataInput", "description": "desc A", "config": {}},
            {
                "func_name": "b",
                "node_type": "polars",
                "description": "",
                "config": {"code": "x"},
            },
        ]
        nodes = _build_rf_nodes(raw)
        assert len(nodes) == 2
        assert nodes[0].id == "a"
        assert nodes[0].data.label == "a"
        assert nodes[0].data.description == "desc A"
        assert nodes[0].data.nodeType == "dataInput"
        assert nodes[1].data.nodeType == "polars"
        assert nodes[0].position == {"x": 0, "y": 0}
        assert nodes[1].position == {"x": 300, "y": 0}

    def test_custom_spacing(self):
        raw = [
            {"func_name": "a", "node_type": "polars", "description": "", "config": {}},
            {"func_name": "b", "node_type": "polars", "description": "", "config": {}},
        ]
        nodes = _build_rf_nodes(raw, x_spacing=500)
        assert nodes[1].position == {"x": 500, "y": 0}

    def test_empty_input(self):
        assert _build_rf_nodes([]) == []


# ===========================================================================
# _extract_meta / _extract_pipeline_meta / _extract_submodel_meta
# ===========================================================================


class TestExtractMeta:
    def test_basic_pipeline_meta(self):
        source = 'pipeline = haute.Pipeline("my_pipeline", description="A test")'
        tree = ast.parse(source)
        name, desc = _extract_pipeline_meta(tree)
        assert name == "my_pipeline"
        assert desc == "A test"

    def test_pipeline_meta_defaults(self):
        source = "x = 1"
        tree = ast.parse(source)
        name, desc = _extract_pipeline_meta(tree)
        assert name == "main"
        assert desc == ""

    def test_pipeline_meta_no_description(self):
        source = 'pipeline = haute.Pipeline("named")'
        tree = ast.parse(source)
        name, desc = _extract_pipeline_meta(tree)
        assert name == "named"
        assert desc == ""

    def test_submodel_meta(self):
        source = 'submodel = haute.Submodel("freq", description="Frequency model")'
        tree = ast.parse(source)
        name, desc = _extract_submodel_meta(tree)
        assert name == "freq"
        assert desc == "Frequency model"

    def test_submodel_meta_defaults(self):
        source = "x = 1"
        tree = ast.parse(source)
        name, desc = _extract_submodel_meta(tree)
        assert name == "unnamed"
        assert desc == ""

    def test_generic_extract_meta_wrong_var(self):
        source = 'other = haute.Pipeline("test")'
        tree = ast.parse(source)
        name, desc = _extract_meta(tree, "pipeline", "fallback")
        assert name == "fallback"

    def test_multiple_assignments_picks_first(self):
        source = 'pipeline = haute.Pipeline("first")\npipeline = haute.Pipeline("second")\n'
        tree = ast.parse(source)
        name, _ = _extract_pipeline_meta(tree)
        assert name == "first"

    def test_non_call_assignment_skipped(self):
        source = 'pipeline = "not a call"'
        tree = ast.parse(source)
        name, desc = _extract_pipeline_meta(tree)
        assert name == "main"

    def test_multi_target_assignment_skipped(self):
        source = 'pipeline = submodel = haute.Pipeline("test")'
        tree = ast.parse(source)
        name, _ = _extract_pipeline_meta(tree)
        # multi-target: len(targets) != 1, so skipped
        assert name == "main"

    # --- Remediation 5.5: non-literal metadata is rejected loudly ---------

    def test_non_literal_pipeline_name_raises(self):
        """``haute.Pipeline(NAME)`` used to store the ``ast.dump`` repr as the
        pipeline name, which codegen then re-emitted into the file."""
        source = "pipeline = haute.Pipeline(NAME)"
        tree = ast.parse(source)
        with pytest.raises(ParseError, match="name"):
            _extract_pipeline_meta(tree)

    def test_non_literal_pipeline_description_raises(self):
        source = 'pipeline = haute.Pipeline("p", description=make_desc())'
        tree = ast.parse(source)
        with pytest.raises(ParseError, match="description"):
            _extract_pipeline_meta(tree)

    def test_factory_constructed_pipeline_raises(self):
        """A factory call would be silently rewritten to a literal
        ``haute.Pipeline(...)`` line on save — loud rejection protects it."""
        source = "pipeline = build_pipeline(cfg)"
        tree = ast.parse(source)
        with pytest.raises(ParseError):
            _extract_pipeline_meta(tree)

    def test_non_literal_submodel_name_raises(self):
        source = "submodel = haute.Submodel(NAME)"
        tree = ast.parse(source)
        with pytest.raises(ParseError, match="name"):
            _extract_submodel_meta(tree)

    def test_non_string_literal_name_falls_back_to_default(self):
        """A literal-but-not-string name keeps the historical skip semantics."""
        source = "pipeline = haute.Pipeline(123)"
        tree = ast.parse(source)
        name, _ = _extract_pipeline_meta(tree)
        assert name == "main"


# ===========================================================================
# _extract_preamble — additional edge cases
# ===========================================================================


class TestExtractPreambleEdgeCases:
    def test_preamble_before_decorator(self):
        source = (
            "import polars as pl\n"
            "import haute\n"
            "\n"
            "MY_CONST = 10\n"
            "\n"
            "@pipeline.polars\n"
            "def f(): pass\n"
        )
        preamble = _extract_preamble(source)
        assert "MY_CONST = 10" in preamble

    def test_preamble_strips_blank_lines(self):
        source = (
            'import polars as pl\nimport haute\n\n\nX = 1\n\n\npipeline = haute.Pipeline("test")\n'
        )
        preamble = _extract_preamble(source)
        assert preamble == "X = 1"

    def test_no_standard_imports_returns_empty(self):
        source = "import json\npipeline = haute.Pipeline('test')\n"
        assert _extract_preamble(source) == ""


# ===========================================================================
# _extract_preserved_blocks
# ===========================================================================


class TestExtractPreservedBlocks:
    def test_single_block(self):
        source = (
            "# some code\n"
            "# haute:preserve-start\n"
            "LOOKUP = {1: 'a', 2: 'b'}\n"
            "# haute:preserve-end\n"
            "# more code\n"
        )
        blocks = _extract_preserved_blocks(source)
        assert len(blocks) == 1
        assert "LOOKUP" in blocks[0]

    def test_multiple_blocks(self):
        source = (
            "# haute:preserve-start\n"
            "A = 1\n"
            "# haute:preserve-end\n"
            "\n"
            "# haute:preserve-start\n"
            "B = 2\n"
            "# haute:preserve-end\n"
        )
        blocks = _extract_preserved_blocks(source)
        assert len(blocks) == 2
        assert "A = 1" in blocks[0]
        assert "B = 2" in blocks[1]

    def test_unmatched_start_ignored(self):
        source = "# haute:preserve-start\nX = 1\n# no end marker\n"
        blocks = _extract_preserved_blocks(source)
        assert blocks == []

    def test_no_blocks(self):
        assert _extract_preserved_blocks("x = 1\ny = 2") == []

    def test_empty_source(self):
        assert _extract_preserved_blocks("") == []

    def test_block_with_blank_lines_stripped(self):
        source = "# haute:preserve-start\n\nX = 1\n\n# haute:preserve-end\n"
        blocks = _extract_preserved_blocks(source)
        assert blocks[0] == "X = 1"


# ===========================================================================
# _build_node_config — additional edge cases
# ===========================================================================


class TestBuildNodeConfigExtended:
    def test_instance_reference_added_to_config(self):
        config = _build_node_config(
            NodeType.POLARS,
            {"of": "original_node"},
            "",
            [],
        )
        assert config["instanceOf"] == "original_node"


# ===========================================================================
# _resolve_node_config
# ===========================================================================


class TestResolveNodeConfig:
    def test_config_node_without_config_reference_raises(self):
        """Config-backed node types must reference their JSON sidecar."""
        from haute.errors import ConfigError

        with pytest.raises(ConfigError):
            _resolve_node_config(
                {
                    "inputType": "file",
                    "format": "parquet",
                    "mode": "scan",
                    "path": "data.parquet",
                    "arguments": {},
                },
                "",
                [],
                None,
                explicit_node_type=NodeType.DATA_INPUT,
            )

    @pytest.mark.parametrize(
        "node_type",
        sorted((kind for kind in NodeType if has_config_folder(kind)), key=lambda kind: kind.value),
        ids=lambda kind: kind.value,
    )
    def test_a_folder_backed_type_takes_its_settings_from_its_sidecar(self, tmp_path, node_type):
        """Every type with a config folder parses from its sidecar alone, as a new node
        writes it, and the inline builder refuses it: no decorator form stands in for
        the file."""
        from haute._config_builder import _build_node_config
        from haute._config_io import (
            NODE_TYPE_TO_FOLDER,
            _normalise_loaded_config,
            palette_default_config,
        )

        folder = NODE_TYPE_TO_FOLDER[node_type]
        settings = palette_default_config(node_type)
        sidecar = tmp_path / "config" / folder / "node.json"
        sidecar.parent.mkdir(parents=True)
        sidecar.write_text(json.dumps(settings), encoding="utf-8")

        resolved_type, config = _resolve_node_config(
            {"config": f"config/{folder}/node.json"},
            "...",
            [],
            tmp_path,
            func_name="node",
            explicit_node_type=node_type,
        )

        assert resolved_type == node_type
        expected = _normalise_loaded_config(dict(settings), node_type)
        assert {key: value for key, value in config.items() if key not in {"code", "inputs"}} == {
            key: value for key, value in expected.items() if key not in {"code", "inputs"}
        }
        with pytest.raises(ValueError, match="keep their settings in a JSON sidecar"):
            _build_node_config(node_type, {}, "...", [])

    def test_sidecar_required_error_names_folder_and_remediation(self):
        """The sidecar-required error must name the concrete folder and how to fix it.

        Regression for F532: the message used a hard-coded ``config/<type>``
        placeholder, never resolving the real folder, and gave no remediation.
        """
        from haute.errors import ConfigError

        with pytest.raises(ConfigError) as excinfo:
            _resolve_node_config(
                {
                    "inputType": "file",
                    "format": "parquet",
                    "mode": "scan",
                    "path": "data.parquet",
                    "arguments": {},
                },
                "",
                [],
                None,
                explicit_node_type=NodeType.DATA_INPUT,
            )
        message = str(excinfo.value)
        # Concrete folder resolved from NODE_TYPE_TO_FOLDER, not a placeholder.
        assert "config/data_input/" in message
        assert "<type>" not in message
        # Says where its settings go + points at a generator.
        assert "not in decorator keywords" in message
        assert "haute init" in message

    def test_a_sidecar_backed_node_refuses_its_other_decorator_keywords(self, tmp_path):
        """Every setting of a sidecar-backed node is in its sidecar: a keyword beside
        ``config=`` fails the parse by name rather than being dropped."""
        from haute.errors import ConfigError

        cfg_dir = tmp_path / "config" / "constant"
        cfg_dir.mkdir(parents=True)
        (cfg_dir / "rates.json").write_text('{"values": [{"name": "base", "value": "1"}]}')

        with pytest.raises(ConfigError) as excinfo:
            _resolve_node_config(
                {
                    "config": "config/constant/rates.json",
                    "values": [{"name": "base", "value": "2"}],
                    "colour": "red",
                },
                "...",
                [],
                tmp_path,
                func_name="rates",
                explicit_node_type=NodeType.CONSTANT,
            )
        assert excinfo.value.message == (
            "Node 'rates' takes its settings from config/constant/rates.json, so its "
            "decorator keywords 'colour', 'values' would be ignored: move them into that "
            "file, or remove them."
        )
        assert excinfo.value.context["keywords"] == ["colour", "values"]

    def test_a_sidecar_backed_node_keeps_its_contract_keyword(self, tmp_path):
        cfg_dir = tmp_path / "config" / "constant"
        cfg_dir.mkdir(parents=True)
        (cfg_dir / "rates.json").write_text('{"values": [{"name": "base", "value": "1"}]}')

        node_type, config = _resolve_node_config(
            {"config": "config/constant/rates.json", "contract": "opaque"},
            "...",
            [],
            tmp_path,
            func_name="rates",
            explicit_node_type=NodeType.CONSTANT,
        )
        assert node_type == NodeType.CONSTANT
        assert config["contract"] == "opaque"
        assert config["values"] == [{"name": "base", "value": "1"}]

    def test_invalid_content_error_leads_with_underlying_cause(self, tmp_path):
        """Valid JSON whose *content* is invalid must surface the real reason.

        Regression for F526: a ``ValueError`` from content validation (the file
        loaded and parsed fine) was bundled with path/parse errors under the
        generic "check that the path exists" headline, masking the precise
        cause. It must now lead with the underlying message and still name the
        config path.
        """
        from haute.errors import ConfigError

        cfg_dir = tmp_path / "config" / "data_input"
        cfg_dir.mkdir(parents=True)
        cfg_file = cfg_dir / "my_source.json"
        # Valid JSON, but a list — not a config object. ``_load_json_object``
        # raises ``ValueError("Node config JSON must contain an object")``.
        cfg_file.write_text("[1, 2, 3]")

        with pytest.raises(ConfigError) as excinfo:
            _resolve_node_config(
                {"config": "config/data_input/my_source.json"},
                "",
                [],
                tmp_path,
                explicit_node_type=NodeType.DATA_INPUT,
            )
        message = str(excinfo.value)
        # Leads with the precise underlying validation message, not the generic
        # "check that the path exists / valid JSON" headline.
        assert "Node config JSON must contain an object" in message
        assert "check that the path exists" not in message
        # Still names the offending config path.
        assert "config/data_input/my_source.json" in message

    def test_missing_file_keeps_generic_path_headline(self, tmp_path):
        """A missing/unreadable file keeps the path-focused headline (F526 split)."""
        from haute.errors import ConfigError

        with pytest.raises(ConfigError) as excinfo:
            _resolve_node_config(
                {"config": "config/data_input/missing.json"},
                "",
                [],
                tmp_path,
                explicit_node_type=NodeType.DATA_INPUT,
            )
        message = str(excinfo.value)
        assert "check that the path exists" in message
        assert "config/data_input/missing.json" in message

    def test_polars_without_config_reference_builds_from_body(self):
        """Polars nodes keep code in the function body and need no sidecar."""
        node_type, config = _resolve_node_config(
            {},
            "    return df",
            ["df"],
            None,
            explicit_node_type=NodeType.POLARS,
        )
        assert node_type == NodeType.POLARS
        assert isinstance(config["code"], str)

    def test_external_config_file(self, tmp_path):
        """With config= key, loads JSON from file."""
        cfg = {
            "inputType": "file",
            "format": "csv",
            "mode": "scan",
            "path": "data.csv",
            "arguments": {},
        }
        cfg_dir = tmp_path / "config" / "data_input"
        cfg_dir.mkdir(parents=True)
        cfg_file = cfg_dir / "my_source.json"
        cfg_file.write_text(json.dumps(cfg))

        node_type, loaded = _resolve_node_config(
            {"config": "config/data_input/my_source.json"},
            "",
            [],
            tmp_path,
            explicit_node_type=NodeType.DATA_INPUT,
        )
        assert node_type == NodeType.DATA_INPUT
        assert loaded["path"] == "data.csv"

    def test_data_input_hook_extracts_its_code(self, tmp_path):
        """A Data Input ``df`` hook's post-load code becomes the node's code."""
        cfg = {
            "inputType": "file",
            "format": "parquet",
            "mode": "scan",
            "path": "data.parquet",
            "arguments": {},
        }
        cfg_dir = tmp_path / "config" / "data_input"
        cfg_dir.mkdir(parents=True)
        cfg_file = cfg_dir / "my_source.json"
        cfg_file.write_text(json.dumps(cfg))

        body = '    """Load data."""\n    df = df.filter(pl.col(\'x\') > 0)\n    return df'
        node_type, loaded = _resolve_node_config(
            {"config": "config/data_input/my_source.json"},
            body,
            ["df"],
            tmp_path,
            explicit_node_type=NodeType.DATA_INPUT,
        )
        assert node_type == NodeType.DATA_INPUT
        assert loaded["code"] == "df = df.filter(pl.col('x') > 0)"

    def test_data_input_without_post_code_gives_empty_code(self, tmp_path):
        """A Data Input declaration (its docstring alone) has empty code."""
        cfg = {
            "inputType": "file",
            "format": "parquet",
            "mode": "scan",
            "path": "data.parquet",
            "arguments": {},
        }
        cfg_dir = tmp_path / "config" / "data_input"
        cfg_dir.mkdir(parents=True)
        cfg_file = cfg_dir / "my_source.json"
        cfg_file.write_text(json.dumps(cfg))

        body = '    """Load data."""'
        node_type, loaded = _resolve_node_config(
            {"config": "config/data_input/my_source.json"},
            body,
            [],
            tmp_path,
            explicit_node_type=NodeType.DATA_INPUT,
        )
        assert node_type == NodeType.DATA_INPUT
        assert loaded.get("code", "") == ""

    def test_external_config_file_not_found(self, tmp_path):
        """Missing config file must fail loudly with ``ConfigError``.

        The previous silent-recovery behaviour masked genuine path
        mistakes; post Item #18 fix a missing config raises instead.
        """
        from haute.errors import ConfigError

        with pytest.raises(ConfigError):
            _resolve_node_config(
                {"config": "config/data_input/missing.json"},
                "",
                [],
                tmp_path,
                explicit_node_type=NodeType.DATA_INPUT,
            )

    def test_banding_type_from_explicit_decorator(self, tmp_path):
        """Explicit decorator type is used directly for config resolution."""
        cfg_dir = tmp_path / "config" / "banding"
        cfg_dir.mkdir(parents=True)
        cfg_file = cfg_dir / "my_transform.json"
        cfg_file.write_text("{}")

        body = '    """doc"""\n    ...'

        node_type, config = _resolve_node_config(
            {"config": "config/banding/my_transform.json"},
            body,
            ["source"],
            tmp_path,
            explicit_node_type=NodeType.BANDING,
        )
        assert node_type == NodeType.BANDING
        assert "code" not in config

    def test_does_not_mutate_decorator_kwargs(self):
        """_resolve_node_config must not modify the caller's dict (B21)."""
        kwargs: dict[str, Any] = {"config": "config/data_input/x.json", "contract": "opaque"}
        original = dict(kwargs)
        with patch("haute._config_builder.load_node_config", return_value={}):
            _resolve_node_config(kwargs, "", [], None)
        # The original dict must be untouched — "config" and "contract" keys stay.
        assert kwargs == original

    def test_no_mutation_polars_path(self):
        """The no-sidecar path must not mutate the input dict (B21)."""
        kwargs: dict[str, Any] = {
            "inputType": "file",
            "format": "parquet",
            "mode": "scan",
            "path": "data.parquet",
            "arguments": {},
        }
        original = dict(kwargs)
        _resolve_node_config(kwargs, "", [], None)
        assert kwargs == original

    def test_no_mutation_with_multiple_keys(self):
        """Input dict with many keys must not lose or gain any entries (B21), even when
        the keywords beside ``config=`` are refused."""
        from haute.errors import ConfigError

        kwargs: dict[str, Any] = {
            "config": "config/banding/x.json",
            "sink": "out.parquet",
            "format": "parquet",
        }
        original = dict(kwargs)
        with (
            patch("haute._config_builder.load_node_config", return_value={}),
            pytest.raises(ConfigError, match="'format', 'sink' would be ignored"),
        ):
            _resolve_node_config(kwargs, "", [], None)
        assert kwargs == original

    def test_mangled_config_path_raises_config_error(self, tmp_path):
        """A Windows-mangled config path must fail loudly.

        Prior behaviour silently recovered via a func-name scan, which
        could load the wrong file if another folder happened to hold a
        matching name.  Post Item #18 the default is fail-loudly.
        """
        from haute.errors import ConfigError

        cfg = {"factors": [{"column": "age", "banding": "breakpoints"}]}
        cfg_dir = tmp_path / "config" / "banding"
        cfg_dir.mkdir(parents=True)
        cfg_file = cfg_dir / "age_band.json"
        cfg_file.write_text(json.dumps(cfg))

        mangled_path = "config/\x08anding/age_band.json"
        with pytest.raises(ConfigError):
            _resolve_node_config(
                {"config": mangled_path},
                "",
                ["df"],
                tmp_path,
                func_name="age_band",
                explicit_node_type=NodeType.BANDING,
            )


# ===========================================================================
# _strip_docstring — additional edge cases
# ===========================================================================


class TestStripDocstringEdgeCases:
    def test_single_quote_docstring(self):
        lines = ["    '''single quotes.'''", "    return df"]
        result = _strip_docstring(lines)
        assert result == ["    return df"]

    def test_multi_line_single_quotes(self):
        lines = [
            "    '''First line.",
            "    Second line.'''",
            "    return df",
        ]
        result = _strip_docstring(lines)
        assert result == ["    return df"]


# ===========================================================================
# _dedent — additional edge cases
# ===========================================================================


class TestDedentEdgeCases:
    def test_all_blank_lines(self):
        code = "   \n   \n"
        result = _dedent(code)
        # No non-blank lines, so no indent to remove, returns as-is
        assert result == "   \n   \n"

    def test_mixed_indent_shorter_line(self):
        """Lines shorter than minimum indent should not crash."""
        code = "    x = 1\n  y"
        result = _dedent(code)
        assert "x = 1" in result


# ===========================================================================
# extract_user_code (transforms) — additional edge cases
# ===========================================================================


class TestExtractUserCodeEdgeCases:
    def test_whitespace_only_body(self):
        assert extract_user_code("   \n   \n", kind="polars", param_names=[]) == ""

    def test_multiline_return(self):
        body = (
            '    """doc"""\n    return (\n        source\n        .filter(pl.col("x") > 0)\n    )'
        )
        result = extract_user_code(body, kind="polars", param_names=["source"])
        assert "source" in result
        assert "filter" in result

    def test_multi_statement_no_bare_df_leak(self):
        """Regression: codegen 'return df' must not leak as bare 'df'."""
        body = (
            '    """desc"""\n'
            "    df = df.rename({'a': 'b'})\n"
            "    df = df.select('b')\n"
            "    return df"
        )
        result = extract_user_code(body, kind="polars", param_names=["quotes"])
        assert result == "df = df.rename({'a': 'b'})\ndf = df.select('b')"
        assert "return" not in result

    def test_multi_statement_roundtrip_stable(self):
        """Regression: repeated wrap→extract must not accumulate bare 'df'."""
        from haute._codegen_builders import _transform_body

        code = "df = quotes.rename({'a': 'b'})\ndf = df.select('b')"
        for _ in range(5):
            wrapped = "".join(f"    {line}\n" for line in _transform_body(code).splitlines())
            body = '    """desc"""\n' + wrapped
            code = extract_user_code(body, kind="polars", param_names=["quotes"])
        assert code == "df = quotes.rename({'a': 'b'})\ndf = df.select('b')"

    def test_output_declaration_roundtrip_stable(self):
        """Code that never binds ``df`` gets the output declaration, which is
        generated and never accumulates across repeated wrap→extract cycles."""
        from haute._codegen_builders import _transform_body

        code = "quotes.select('b')"
        for _ in range(3):
            wrapped = "".join(f"    {line}\n" for line in _transform_body(code).splitlines())
            assert wrapped.startswith("    df: pl.LazyFrame\n")
            code = extract_user_code(wrapped, kind="polars", param_names=["quotes"])
        assert code == "quotes.select('b')"


# ===========================================================================
# _copy_config_keys
# ===========================================================================


class TestCopyConfigKeys:
    def test_copies_present_keys(self):
        config: dict[str, Any] = {}
        kwargs = {"a": 1, "b": 2, "c": 3}
        _copy_config_keys(config, kwargs, ["a", "c"])
        assert config == {"a": 1, "c": 3}

    def test_skips_missing_keys(self):
        config: dict[str, Any] = {}
        kwargs = {"a": 1}
        _copy_config_keys(config, kwargs, ["a", "missing", "also_missing"])
        assert config == {"a": 1}

    def test_empty_keys_does_nothing(self):
        config: dict[str, Any] = {}
        kwargs = {"a": 1}
        _copy_config_keys(config, kwargs, [])
        assert config == {}

    def test_empty_kwargs_does_nothing(self):
        config: dict[str, Any] = {}
        _copy_config_keys(config, {}, ["a", "b"])
        assert config == {}

    def test_preserves_existing_config(self):
        config: dict[str, Any] = {"existing": "value"}
        _copy_config_keys(config, {"a": 1}, ["a"])
        assert config == {"existing": "value", "a": 1}

    def test_accepts_tuple_keys(self):
        config: dict[str, Any] = {}
        _copy_config_keys(config, {"x": 10, "y": 20}, ("x", "y"))
        assert config == {"x": 10, "y": 20}


# ===========================================================================
# _extract_decorated_nodes
# ===========================================================================


class TestExtractDecoratedNodes:
    def _parse_source(self, source: str):
        return ast.parse(source), source

    def test_extracts_pipeline_nodes(self, tmp_path):
        cfg_dir = tmp_path / "config" / "data_input"
        cfg_dir.mkdir(parents=True)
        (cfg_dir / "source.json").write_text(
            json.dumps(
                {
                    "inputType": "file",
                    "format": "parquet",
                    "mode": "scan",
                    "path": "data.parquet",
                    "arguments": {},
                }
            )
        )
        source = (
            "import polars as pl\n"
            "import haute\n"
            'pipeline = haute.Pipeline("test")\n'
            "\n"
            '@pipeline.data_input(config="config/data_input/source.json")\n'
            "def source():\n"
            '    """Load data."""\n'
            "\n"
            "@pipeline.polars\n"
            "def transform(source):\n"
            "    return source\n"
        )
        tree, text = self._parse_source(source)
        nodes = _extract_decorated_nodes(
            tree,
            _is_pipeline_authored_decorator,
            tmp_path,
            source=text,
        )
        assert len(nodes) == 2
        assert nodes[0]["func_name"] == "source"
        assert nodes[0]["node_type"] == NodeType.DATA_INPUT
        assert nodes[0]["description"] == "Load data."
        assert nodes[0]["config"]["code"] == ""
        assert nodes[1]["func_name"] == "transform"

    def test_extracts_submodel_nodes(self):
        source = (
            "import polars as pl\n"
            "import haute\n"
            'submodel = haute.Submodel("freq")\n'
            "\n"
            "@submodel.polars\n"
            "def calc(data):\n"
            "    return data\n"
        )
        tree, text = self._parse_source(source)
        nodes = _extract_decorated_nodes(
            tree,
            _is_submodel_authored_decorator,
            None,
            source=text,
        )
        assert len(nodes) == 1
        assert nodes[0]["func_name"] == "calc"

    def test_ignores_non_matching_decorators(self):
        source = (
            "@other.decorator\n"
            "def ignored():\n"
            "    pass\n"
            "\n"
            "@pipeline.polars\n"
            "def matched():\n"
            "    return 1\n"
        )
        tree, text = self._parse_source(source)
        nodes = _extract_decorated_nodes(
            tree,
            _is_pipeline_authored_decorator,
            None,
            source=text,
        )
        assert len(nodes) == 1
        assert nodes[0]["func_name"] == "matched"

    def test_ignores_non_function_stmts(self):
        source = "x = 1\ny = 2\n@pipeline.polars\ndef only_func():\n    return 1\n"
        tree, text = self._parse_source(source)
        nodes = _extract_decorated_nodes(
            tree,
            _is_pipeline_authored_decorator,
            None,
            source=text,
        )
        assert len(nodes) == 1

    def test_empty_tree_returns_empty(self):
        tree, text = self._parse_source("x = 1\n")
        nodes = _extract_decorated_nodes(
            tree,
            _is_pipeline_authored_decorator,
            None,
            source=text,
        )
        assert nodes == []

    def test_extracts_param_names(self):
        source = "@pipeline.polars\ndef transform(a, b, c):\n    return a\n"
        tree, text = self._parse_source(source)
        nodes = _extract_decorated_nodes(
            tree,
            _is_pipeline_authored_decorator,
            None,
            source=text,
        )
        assert nodes[0]["param_names"] == ["a", "b", "c"]

    def test_extracts_docstring(self):
        source = '@pipeline.polars\ndef transform(a):\n    """My transform doc."""\n    return a\n'
        tree, text = self._parse_source(source)
        nodes = _extract_decorated_nodes(
            tree,
            _is_pipeline_authored_decorator,
            None,
            source=text,
        )
        assert nodes[0]["description"] == "My transform doc."

    def test_pipeline_checker_does_not_match_submodel(self):
        source = "@submodel.polars\ndef calc(x):\n    return x\n"
        tree, text = self._parse_source(source)
        # submodel checker matches @submodel.polars
        nodes = _extract_decorated_nodes(
            tree,
            _is_submodel_authored_decorator,
            None,
            source=text,
        )
        assert len(nodes) == 1
        # pipeline checker must NOT match @submodel.polars —
        # it checks decorator.value.id == "pipeline"
        nodes2 = _extract_decorated_nodes(
            tree,
            _is_pipeline_authored_decorator,
            None,
            source=text,
        )
        assert len(nodes2) == 0


# ===========================================================================
# _unwrap_chain_assignment
# ===========================================================================


class TestUnwrapChainAssignment:
    """Remediation 5.1 (CODE_REVIEW C5): the unwrap proves redundancy.

    The historical contract (rewrite ``df = (\\n <chain>\\n)`` into a bare
    expression chain) corrupted statements whose parens were not one
    wrapping pair, and expression-form output broke the save path
    (codegen re-emits code boxes verbatim as statement bodies).  The new
    contract only strips paren pairs that provably wrap the entire RHS,
    keeps statement form, and leaves everything unprovable verbatim.
    The full production-path coverage lives in
    ``tests/test_code_extraction_roundtrip.py``.
    """

    def test_multiline_chain_stays_verbatim(self):
        # The wrapper parens carry line continuation — stripping them
        # would emit invalid Python on the next save.
        code = "df = (\n    source\n    .filter(pl.col('x') > 0)\n)"
        assert _unwrap_chain_assignment(code) is None

    def test_non_matching_pattern_returns_none(self):
        assert _unwrap_chain_assignment("x = 1") is None
        assert _unwrap_chain_assignment("result = foo()") is None
        assert _unwrap_chain_assignment("") is None

    def test_review_corruption_cases_stay_verbatim(self):
        # Previously: "df = (a + b) * c" -> "a + b) * c" (invalid) and
        # the chained-call case lost its balanced parens.
        assert _unwrap_chain_assignment("df = (a + b) * c") is None
        assert _unwrap_chain_assignment("df = (up.filter(x)).join(y)") is None

    def test_redundant_single_line_wrapper_reduces_to_statement(self):
        assert _unwrap_chain_assignment("df = (source.filter(x > 0))") == (
            "df = source.filter(x > 0)"
        )

    def test_no_space_variant(self):
        assert _unwrap_chain_assignment("df=(source.select('a'))") == "df=source.select('a')"

    def test_nested_redundant_wrappers_reduce_fully(self):
        assert _unwrap_chain_assignment("df = ((source.select('a')))") == (
            "df = source.select('a')"
        )


# ===========================================================================
# extract_user_code — a Data Input's ``df`` hook (the ``hook`` kind)
# ===========================================================================


class TestExtractDataInputHookCode:
    """A hook generates only its closing ``return df``; the decorator loads the data."""

    def test_post_load_code_is_user_code(self):
        body = "    df = df.filter(pl.col('x') > 0)\n    return df"
        assert extract_user_code(body, kind="hook") == "df = df.filter(pl.col('x') > 0)"

    def test_declaration_returns_empty(self):
        assert extract_user_code('    """Load the quotes."""\n    ...', kind="hook") == ""

    def test_bare_closing_return_is_not_user_code(self):
        assert extract_user_code("    return df", kind="hook") == ""

    def test_retired_loader_scaffold_is_user_code(self):
        """There is no compatibility path: the retired loader chain is not
        recognised as generated, so it reloads as the user's own code."""
        body = (
            "    from haute._project import get_project_root\n"
            "    from haute.graph_utils import resolve_data_input_from_config\n"
            "    project_root = get_project_root(_HAUTE_CONFIG_BASE)\n"
            "    df = resolve_data_input_from_config(\n"
            '        "config/data_input/input.json",\n'
            "        base_dir=_HAUTE_CONFIG_BASE, project_root=project_root,\n"
            "    )\n"
            "    return df"
        )

        assert extract_user_code(body, kind="hook") == (
            "from haute._project import get_project_root\n"
            "from haute.graph_utils import resolve_data_input_from_config\n"
            "project_root = get_project_root(_HAUTE_CONFIG_BASE)\n"
            "df = resolve_data_input_from_config(\n"
            '    "config/data_input/input.json",\n'
            "    base_dir=_HAUTE_CONFIG_BASE, project_root=project_root,\n"
            ")"
        )


# ===========================================================================
# extract_user_code — a Scenario Expander's ``df`` hook
# ===========================================================================


class TestExtractScenarioExpanderUserCode:
    def test_handwritten_first_statement_is_not_mistaken_for_scaffold(self):
        body = "    df = df.filter(pl.col('scenario_value') > 1)\n    return df"
        result = extract_user_code(body, kind="hook", param_names=["df", "quotes"])
        assert result == "df = df.filter(pl.col('scenario_value') > 1)"


# ===========================================================================
# extract_user_code — a Model Score's ``df`` hook
# ===========================================================================


class TestExtractModelScoreUserCode:
    def test_every_statement_before_the_closing_return_is_user_code(self):
        """Nothing in a hook is anchored on a scoring call: all of it is code."""
        body = "    x = 1\n    return x"
        result = extract_user_code(body, kind="hook")
        assert result == "x = 1\ndf = x"

    def test_declaration_is_not_user_code(self):
        body = '    """Score the quotes."""'
        result = extract_user_code(body, kind="hook")
        assert result == ""

    def test_hook_preserves_only_its_code(self):
        body = "    df = df.with_columns(double_score=pl.col('prediction') * 2)\n    return df"
        result = extract_user_code(body, kind="hook")
        assert result == "df = df.with_columns(double_score=pl.col('prediction') * 2)"


# ===========================================================================
# extract_user_code — a Rating Step's ``df`` hook
# ===========================================================================


class TestExtractRatingStepUserCode:
    def test_rating_declaration_is_not_user_code(self):
        assert extract_user_code("    ...", kind="hook", param_names=["quotes"]) == ""

    def test_rating_hook_code_can_reference_another_input(self):
        """``df`` stands in for the first input; the others keep their names."""
        body = (
            "    audit = regions.select('quote_id')\n"
            "    df = df.join(audit, on='quote_id')\n"
            "    return df"
        )
        result = extract_user_code(body, kind="hook", param_names=["df", "regions"])
        assert result == "audit = regions.select('quote_id')\ndf = df.join(audit, on='quote_id')"


# ===========================================================================
# extract_user_code — an External File hook (the ``external`` kind)
# ===========================================================================


class TestExtractExternalUserCode:
    def test_empty_body_returns_empty(self):
        result = extract_user_code("", kind="external", param_names=["model_input"])
        assert result == ""

    def test_generated_binding_is_not_user_code(self):
        body = "df = model_input\ndf = df.limit(10)\nreturn df"
        result = extract_user_code(body, kind="external", param_names=["model_input"])
        assert result == "df = df.limit(10)"


# ===========================================================================
# _function_body_source — a body that starts on the ``def`` line
# ===========================================================================


class TestFunctionBodySource:
    """A body may share the header's last line: ``def f(): ...`` is a declaration."""

    @staticmethod
    def _body(source: str) -> str:
        function = ast.parse(source).body[0]
        assert isinstance(function, ast.FunctionDef)
        return _function_body_source(source.splitlines(), function)

    def test_one_line_declaration_body_is_the_ellipsis(self):
        assert self._body("def sink(quotes): ...\n") == "..."

    def test_multibyte_signature_before_the_body_is_sliced_by_bytes(self):
        # ``ast`` reports the body's column in UTF-8 bytes, and "é" is two bytes:
        # slicing by characters would cut into the ``...``.
        assert self._body("def tarif_été(quotes): ...\n") == "..."
        assert self._body('def rate(quotes: "données") -> None: ...\n') == "..."

    def test_body_after_a_multi_line_signature(self):
        assert self._body("def join(\n    base,\n    other,\n): ...\n") == "..."

    def test_one_line_docstring_body(self):
        assert self._body('def rates(): """Données."""\n') == '"""Données."""'

    def test_statements_sharing_the_def_line(self):
        assert self._body("def f(): x = 1; return x\n") == "x = 1; return x"

    def test_indented_body_keeps_whole_lines(self):
        assert self._body("def f(a):\n    x = 1\n    return x\n") == "    x = 1\n    return x"

    def test_comments_before_the_first_statement_belong_to_the_body(self):
        source = "def f(df):\n\n    # keep two rows\n\n    # then stop\n    df = df.head(2)\n"
        assert self._body(source) == "    # keep two rows\n\n    # then stop\n    df = df.head(2)"

    def test_comments_inside_a_multi_line_signature_stay_in_the_header(self):
        source = (
            "@decorate(\n    # a decorator note\n)\ndef f(\n    df,\n    # a parameter note\n"
            "):\n    # the body's note\n    return df\n"
        )
        assert self._body(source) == "    # the body's note\n    return df"

    def test_a_comment_indented_less_than_the_body_is_not_recovered(self):
        assert self._body("def f(df):\n# outdented\n    return df\n") == "    return df"

    def test_one_line_declarations_parse_as_declarations(self, tmp_path):
        """A one-line declaration, even after a multi-byte name, is not a body."""
        from haute.parser import parse_pipeline_source
        from tests.conftest import write_data_input_config, write_node_config

        input_ref = write_data_input_config(tmp_path, "quotes", "quotes.parquet")
        output_ref = write_node_config(
            tmp_path,
            NodeType.DATA_OUTPUT,
            "sortie",
            {
                "outputType": "file",
                "format": "parquet",
                "mode": "sink",
                "path": "out.parquet",
                "arguments": {},
            },
        )
        source = (
            "import haute\n\n"
            'pipeline = haute.Pipeline("one_liners")\n\n\n'
            f'@pipeline.data_input(config="{input_ref}")\n'
            "def données(): ...\n\n\n"
            f'@pipeline.data_output(config="{output_ref}")\n'
            "def sortie(données): ...\n"
        )

        graph = parse_pipeline_source(source, source_file="main.py", _base_dir=tmp_path)

        assert graph.node_map["données"].data.nodeType == NodeType.DATA_INPUT
        assert graph.node_map["données"].data.config["code"] == ""
        assert graph.node_map["sortie"].data.nodeType == NodeType.DATA_OUTPUT
        assert [(edge.source, edge.target) for edge in graph.edges] == [("données", "sortie")]


# ===========================================================================
# _get_decorator_node_type
# ===========================================================================


class TestGetDecoratorNodeType:
    def _dec(self, source: str) -> ast.expr:
        tree = ast.parse(source)
        return tree.body[0].decorator_list[0]

    def test_pipeline_data_input(self):
        result = _get_decorator_node_type(self._dec("@pipeline.data_input\ndef f(): pass"))
        assert result == NodeType.DATA_INPUT

    def test_pipeline_polars(self):
        result = _get_decorator_node_type(self._dec("@pipeline.polars\ndef f(): pass"))
        assert result == NodeType.POLARS

    def test_submodel_polars(self):
        result = _get_decorator_node_type(self._dec("@submodel.polars\ndef f(): pass"))
        assert result == NodeType.POLARS

    def test_unrecognized_decorator_returns_none(self):
        result = _get_decorator_node_type(self._dec("@pipeline.connect\ndef f(): pass"))
        assert result is None

    def test_call_style_decorator(self):
        result = _get_decorator_node_type(
            self._dec("@pipeline.data_input(path='x')\ndef f(): pass")
        )
        assert result == NodeType.DATA_INPUT

    def test_plain_name_returns_none(self):
        result = _get_decorator_node_type(self._dec("@some_decorator\ndef f(): pass"))
        assert result is None

    def test_other_object_returns_none(self):
        result = _get_decorator_node_type(self._dec("@other.polars\ndef f(): pass"))
        assert result is None
