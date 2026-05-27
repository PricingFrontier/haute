"""Comprehensive tests for haute._parser_helpers.

Covers public functions not already exercised in test_parser_internals.py:
  - _eval_ast_literal
  - _get_decorator_kwargs
  - _is_pipeline_node_decorator / _is_submodel_node_decorator
  - _get_docstring
  - _extract_function_bodies
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

from haute._parser_helpers import (
    _build_edges,
    _build_node_config,
    _build_rf_nodes,
    _copy_config_keys,
    _dedent,
    _eval_ast_literal,
    _extract_connect_calls,
    _extract_decorated_nodes,
    _extract_external_user_code,
    _extract_function_bodies,
    _extract_meta,
    _extract_model_score_user_code,
    _extract_pipeline_meta,
    _extract_preamble,
    _extract_preserved_blocks,
    _extract_rating_step_user_code,
    _extract_scenario_expander_user_code,
    _extract_source_user_code,
    _extract_submodel_meta,
    _extract_user_code,
    _get_decorator_kwargs,
    _get_decorator_node_type,
    _get_docstring,
    _is_pipeline_node_decorator,
    _is_submodel_node_decorator,
    _resolve_node_config,
    _strip_docstring,
    _unwrap_chain_assignment,
)
from haute._types import NodeType

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

    def test_non_literal_returns_ast_dump(self):
        """A function call node cannot be literal_eval'd -- falls back to ast.dump."""
        node = ast.parse("foo()", mode="eval").body
        result = _eval_ast_literal(node)
        assert isinstance(result, str)
        assert len(result) > 0


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

    def test_star_kwargs_ignored(self):
        """**kwargs (arg=None) are skipped."""
        dec = self._parse_decorator("@pipeline.node(**cfg, x=1)\ndef f(): pass")
        kwargs = _get_decorator_kwargs(dec)
        assert "x" in kwargs
        # **cfg has kw.arg == None, so it's skipped
        assert len(kwargs) == 1


# ===========================================================================
# _is_pipeline_node_decorator / _is_submodel_node_decorator
# ===========================================================================


class TestIsPipelineNodeDecorator:
    def _dec(self, source: str) -> ast.expr:
        tree = ast.parse(source)
        return tree.body[0].decorator_list[0]

    def test_bare_attribute(self):
        assert _is_pipeline_node_decorator(self._dec("@pipeline.polars\ndef f(): pass"))

    def test_call(self):
        assert _is_pipeline_node_decorator(self._dec("@pipeline.polars()\ndef f(): pass"))

    def test_call_with_kwargs(self):
        assert _is_pipeline_node_decorator(
            self._dec('@pipeline.data_source(path="x")\ndef f(): pass')
        )

    def test_all_decorator_types(self):
        """All type-specific decorators should be recognised."""
        from haute._types import DECORATOR_TO_NODE_TYPE

        for method in DECORATOR_TO_NODE_TYPE:
            assert _is_pipeline_node_decorator(self._dec(f"@pipeline.{method}\ndef f(): pass")), (
                f"@pipeline.{method} was not recognised"
            )

    def test_wrong_attr(self):
        assert not _is_pipeline_node_decorator(self._dec("@pipeline.connect\ndef f(): pass"))

    def test_old_node_not_matched(self):
        """The old @pipeline.node style is no longer recognised."""
        assert not _is_pipeline_node_decorator(self._dec("@pipeline.node\ndef f(): pass"))

    def test_other_object_does_not_match(self):
        """The function checks both .attr in DECORATOR_TO_NODE_TYPE AND receiver == 'pipeline'."""
        assert not _is_pipeline_node_decorator(self._dec("@other.transform\ndef f(): pass"))

    def test_submodel_does_not_match_pipeline(self):
        """@submodel.polars should NOT match the pipeline checker."""
        assert not _is_pipeline_node_decorator(self._dec("@submodel.polars\ndef f(): pass"))

    def test_submodel_call_does_not_match_pipeline(self):
        """@submodel.data_source(...) should NOT match the pipeline checker."""
        assert not _is_pipeline_node_decorator(
            self._dec("@submodel.data_source(path='x')\ndef f(): pass")
        )

    def test_plain_name_decorator(self):
        assert not _is_pipeline_node_decorator(self._dec("@some_decorator\ndef f(): pass"))


class TestIsSubmodelNodeDecorator:
    def _dec(self, source: str) -> ast.expr:
        tree = ast.parse(source)
        return tree.body[0].decorator_list[0]

    def test_bare_submodel_transform(self):
        assert _is_submodel_node_decorator(self._dec("@submodel.polars\ndef f(): pass"))

    def test_submodel_call(self):
        assert _is_submodel_node_decorator(self._dec("@submodel.polars()\ndef f(): pass"))

    def test_submodel_data_source(self):
        assert _is_submodel_node_decorator(
            self._dec("@submodel.data_source(path='x')\ndef f(): pass")
        )

    def test_pipeline_transform_is_not_submodel(self):
        assert not _is_submodel_node_decorator(self._dec("@pipeline.polars\ndef f(): pass"))

    def test_other_object_is_not_submodel(self):
        assert not _is_submodel_node_decorator(self._dec("@other.transform\ndef f(): pass"))

    def test_submodel_connect_is_not_node(self):
        assert not _is_submodel_node_decorator(self._dec("@submodel.connect\ndef f(): pass"))


# ===========================================================================
# _get_docstring
# ===========================================================================


class TestGetDocstring:
    def test_with_docstring(self):
        tree = ast.parse('def f():\n    """Hello."""\n    pass')
        func = tree.body[0]
        assert _get_docstring(func) == "Hello."

    def test_without_docstring(self):
        tree = ast.parse("def f():\n    pass")
        func = tree.body[0]
        assert _get_docstring(func) == ""


# ===========================================================================
# _extract_function_bodies
# ===========================================================================


class TestExtractFunctionBodies:
    def test_single_function(self):
        source = "def foo():\n    x = 1\n    return x"
        bodies = _extract_function_bodies(source, tree=ast.parse(source))
        assert "foo" in bodies
        assert "x = 1" in bodies["foo"]
        assert "return x" in bodies["foo"]

    def test_multiple_functions(self):
        source = "def a():\n    return 1\n\ndef b():\n    return 2"
        bodies = _extract_function_bodies(source, tree=ast.parse(source))
        assert set(bodies.keys()) == {"a", "b"}

    def test_nested_function(self):
        source = "def outer():\n    def inner():\n        return 1\n    return inner"
        bodies = _extract_function_bodies(source, tree=ast.parse(source))
        assert "outer" in bodies
        assert "inner" not in bodies  # ast.iter_child_nodes extracts top-level only

    def test_empty_source(self):
        assert _extract_function_bodies("", tree=ast.parse("")) == {}

    def test_pre_parsed_tree(self):
        source = "def f():\n    return 42"
        tree = ast.parse(source)
        bodies = _extract_function_bodies(source, tree=tree)
        assert "f" in bodies

    def test_no_functions(self):
        source = "x = 1\ny = 2"
        assert _extract_function_bodies(source, tree=ast.parse(source)) == {}


# ===========================================================================
# _extract_connect_calls
# ===========================================================================


class TestExtractConnectCalls:
    def test_basic_connect(self):
        source = 'pipeline.connect("a", "b")\npipeline.connect("b", "c")'
        tree = ast.parse(source)
        pairs = _extract_connect_calls(tree)
        # Each entry is now a 3-tuple (src, tgt, source_port) per
        # commit-6 port-aware codegen. Bare connect calls report
        # source_port as None.
        assert pairs == [("a", "b", None), ("b", "c", None)]

    def test_no_connect_calls(self):
        source = "x = 1"
        tree = ast.parse(source)
        assert _extract_connect_calls(tree) == []

    def test_custom_receiver(self):
        source = 'submodel.connect("x", "y")'
        tree = ast.parse(source)
        assert _extract_connect_calls(tree, receiver="submodel") == [("x", "y", None)]
        assert _extract_connect_calls(tree, receiver="pipeline") == []

    def test_non_literal_args_become_ast_dump_strings(self):
        """Non-literal args are eval'd via ast.dump, producing string representations."""
        source = "pipeline.connect(a, b)"
        tree = ast.parse(source)
        # _eval_ast_literal falls back to ast.dump for Name nodes,
        # which produces strings, so they pass the isinstance(str) check.
        pairs = _extract_connect_calls(tree)
        assert len(pairs) == 1
        assert "Name" in pairs[0][0]  # ast.dump output

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
        assert pairs == [("a", "b", None), ("c", "d", None)]

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
        return {"func_name": name, "param_names": params, "node_type": "polars"}

    def test_explicit_edges(self):
        nodes = [self._raw("a", []), self._raw("b", ["a"])]
        edges = _build_edges(nodes, [("a", "b")])
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
        edges = _build_edges(nodes, [("a", "c")])
        targets_of_c = sorted([(e.source, e.target) for e in edges if e.target == "c"])
        # Explicit (a,c) + implicit (b,c) -- both present
        assert targets_of_c == [("a", "c"), ("b", "c")]

    def test_fallback_linear_chain(self):
        """With no edges and no matching params, nodes form a linear chain."""
        nodes = [
            self._raw("x", []),
            self._raw("y", ["unrelated"]),
            self._raw("z", ["other"]),
        ]
        edges = _build_edges(nodes, [])
        assert len(edges) == 2
        assert edges[0].source == "x" and edges[0].target == "y"
        assert edges[1].source == "y" and edges[1].target == "z"

    def test_single_node_no_edges(self):
        nodes = [self._raw("only", [])]
        assert _build_edges(nodes, []) == []

    def test_ignores_connect_to_unknown_node(self):
        nodes = [self._raw("a", [])]
        edges = _build_edges(nodes, [("a", "missing")])
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
            {"func_name": "a", "node_type": "dataSource", "description": "desc A", "config": {}},
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
        assert nodes[0].data.nodeType == "dataSource"
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
    def test_banding_multi_factor(self):
        config = _build_node_config(
            NodeType.BANDING,
            {
                "factors": [
                    {
                        "banding": "discrete",
                        "column": "age",
                        "output_column": "age_band",
                        "rules": [],
                        "default": "0",
                    },
                ]
            },
            "",
            [],
        )
        assert len(config["factors"]) == 1
        assert config["factors"][0]["outputColumn"] == "age_band"
        assert config["factors"][0]["default"] == "0"

    def test_banding_single_factor_format(self):
        config = _build_node_config(
            NodeType.BANDING,
            {
                "banding": "continuous",
                "column": "x",
                "output_column": "x_factor",
                "rules": [{"min": 0, "max": 1, "value": 1.0}],
            },
            "",
            [],
        )
        assert len(config["factors"]) == 1
        assert config["factors"][0]["column"] == "x"

    def test_banding_non_list_factors_wrapped(self):
        """If 'factors' is not a list, empty list is used."""
        config = _build_node_config(
            NodeType.BANDING,
            {"factors": "not_a_list"},
            "",
            [],
        )
        assert config["factors"] == []

    def test_rating_step_non_list_tables(self):
        config = _build_node_config(
            NodeType.RATING_STEP,
            {"tables": "not_a_list"},
            "",
            [],
        )
        assert config["tables"] == []

    def test_rating_step_with_operation_and_combined(self):
        config = _build_node_config(
            NodeType.RATING_STEP,
            {"tables": [], "operation": "multiply", "combinedColumn": "premium"},
            "",
            [],
        )
        assert config["operation"] == "multiply"
        assert config["combinedColumn"] == "premium"

    def test_rating_step_op_shorthand(self):
        config = _build_node_config(
            NodeType.RATING_STEP,
            {"tables": [], "op": "add"},
            "",
            [],
        )
        assert config["operation"] == "add"

    def test_rating_step_entries_default_to_list(self):
        config = _build_node_config(
            NodeType.RATING_STEP,
            {"tables": [{"name": "T", "entries": "not_a_list", "factors": "also_not"}]},
            "",
            [],
        )
        assert config["tables"][0]["entries"] == []
        assert config["tables"][0]["factors"] == []

    def test_constant_values(self):
        config = _build_node_config(
            NodeType.CONSTANT,
            {"values": [{"name": "pi", "value": 3.14}]},
            "",
            [],
        )
        assert config["values"] == [{"name": "pi", "value": "3.14"}]

    def test_constant_non_list_values(self):
        config = _build_node_config(
            NodeType.CONSTANT,
            {"values": "bad"},
            "",
            [],
        )
        assert config["values"] == []

    def test_scenario_expander_config(self):
        config = _build_node_config(
            NodeType.SCENARIO_EXPANDER,
            {
                "scenario_expander": True,
                "quote_id": "qid",
                "min_value": 0.8,
                "max_value": 1.2,
                "steps": 5,
            },
            "",
            [],
        )
        assert config["quote_id"] == "qid"
        assert config["min_value"] == 0.8
        assert config["steps"] == 5

    def test_scenario_expander_config_extracts_user_code_after_boilerplate(self):
        body = (
            '    """Expand."""\n'
            "    df = source\n"
            '    df = df.filter(pl.col("sv") > 0.9)\n'
            "    return df"
        )
        config = _build_node_config(
            NodeType.SCENARIO_EXPANDER,
            {"scenario_expander": True, "steps": 5},
            body,
            ["source"],
        )
        assert "code" in config
        assert '.filter(pl.col("sv") > 0.9)' in config["code"]

    def test_scenario_expander_config_empty_code_without_sentinel(self):
        body = '    """Expand."""\n    return source'
        config = _build_node_config(
            NodeType.SCENARIO_EXPANDER,
            {"scenario_expander": True, "steps": 5},
            body,
            ["source"],
        )
        assert config.get("code", "") == ""

    def test_optimiser_config(self):
        config = _build_node_config(
            NodeType.OPTIMISER,
            {"optimiser": True, "mode": "online", "quote_id": "id", "objective": "premium"},
            "",
            [],
        )
        assert config["mode"] == "online"
        assert config["objective"] == "premium"

    def test_optimiser_apply_config(self):
        config = _build_node_config(
            NodeType.OPTIMISER_APPLY,
            {
                "optimiser_apply": True,
                "source_type": "file",
                "artifact_path": "/path/to/artifact.json",
            },
            "",
            [],
        )
        assert config["sourceType"] == "file"
        assert config["artifact_path"] == "/path/to/artifact.json"

    def test_modelling_config(self):
        config = _build_node_config(
            NodeType.MODELLING,
            {"modelling": True, "target": "loss", "algorithm": "catboost", "task": "regression"},
            "",
            [],
        )
        assert config["target"] == "loss"
        assert config["algorithm"] == "catboost"

    def test_instance_of_added_to_config(self):
        config = _build_node_config(
            NodeType.POLARS,
            {"instance_of": "original_node"},
            "",
            [],
        )
        assert config["instanceOf"] == "original_node"

    def test_data_source_with_query(self):
        config = _build_node_config(
            NodeType.DATA_SOURCE,
            {"table": "catalog.schema.tbl", "http_path": "/sql/x", "query": "SELECT *"},
            "",
            [],
        )
        assert config["http_path"] == "/sql/x"
        assert config["query"] == "SELECT *"

    def test_data_sink_defaults(self):
        config = _build_node_config(NodeType.DATA_SINK, {"sink": ""}, "", [])
        assert config["format"] == "parquet"
        assert config["path"] == ""

    def test_external_file_default_file_type(self):
        config = _build_node_config(NodeType.EXTERNAL_FILE, {"external": "m.pkl"}, "", [])
        assert config["fileType"] == "pickle"

    def test_output_empty_fields(self):
        config = _build_node_config(NodeType.OUTPUT, {}, "", [])
        assert config["fields"] == []

    def test_model_score_source_type_mapped_to_camelcase(self):
        config = _build_node_config(
            NodeType.MODEL_SCORE,
            {"source_type": "run", "run_id": "abc"},
            "",
            [],
        )
        assert config["sourceType"] == "run"

    def test_model_score_code_after_scoring_call(self):
        body = (
            "    result = score_from_config(\n"
            "        df,\n"
            '        config="config/model_scoring/model.json",\n'
            "    )\n"
            "    x = 1\n"
            "    return result"
        )
        config = _build_node_config(NodeType.MODEL_SCORE, {}, body, [])
        assert "x = 1" in config["code"]


# ===========================================================================
# _resolve_node_config
# ===========================================================================


class TestResolveNodeConfig:
    def test_config_node_without_config_reference_raises(self):
        """Config-backed node types must reference their JSON sidecar."""
        from haute.errors import ConfigError

        with patch("haute._config_builder.warn_unrecognized_config_keys"):
            with pytest.raises(ConfigError):
                _resolve_node_config(
                    {"path": "data.parquet"},
                    "",
                    [],
                    0,
                    None,
                    explicit_node_type=NodeType.DATA_SOURCE,
                )

    def test_polars_without_config_reference_builds_from_body(self):
        """Polars nodes keep code in the function body and need no sidecar."""
        with patch("haute._config_builder.warn_unrecognized_config_keys"):
            node_type, config = _resolve_node_config(
                {},
                "    return df",
                ["df"],
                1,
                None,
                explicit_node_type=NodeType.POLARS,
            )
        assert node_type == NodeType.POLARS
        assert isinstance(config["code"], str)

    def test_external_config_file(self, tmp_path):
        """With config= key, loads JSON from file."""
        cfg = {"path": "data.csv", "sourceType": "flat_file"}
        cfg_dir = tmp_path / "config" / "data_source"
        cfg_dir.mkdir(parents=True)
        cfg_file = cfg_dir / "my_source.json"
        cfg_file.write_text(json.dumps(cfg))

        with patch("haute._config_builder.warn_unrecognized_config_keys"):
            node_type, loaded = _resolve_node_config(
                {"config": "config/data_source/my_source.json"},
                "",
                [],
                0,
                tmp_path,
                explicit_node_type=NodeType.DATA_SOURCE,
            )
        assert node_type == NodeType.DATA_SOURCE
        assert loaded["path"] == "data.csv"

    def test_data_source_extracts_code_after_boilerplate(self, tmp_path):
        """DataSource extracts user code from the function body."""
        cfg = {"path": "data.parquet", "sourceType": "flat_file"}
        cfg_dir = tmp_path / "config" / "data_source"
        cfg_dir.mkdir(parents=True)
        cfg_file = cfg_dir / "my_source.json"
        cfg_file.write_text(json.dumps(cfg))

        body = (
            '    """Load data."""\n'
            '    df = pl.scan_parquet("data.parquet")\n'
            "    df = df.filter(pl.col('x') > 0)\n"
            "    return df"
        )
        with patch("haute._config_builder.warn_unrecognized_config_keys"):
            node_type, loaded = _resolve_node_config(
                {"config": "config/data_source/my_source.json"},
                body,
                [],
                0,
                tmp_path,
                explicit_node_type=NodeType.DATA_SOURCE,
            )
        assert node_type == NodeType.DATA_SOURCE
        assert "filter" in loaded.get("code", "")

    def test_data_source_no_sentinel_gives_empty_code(self, tmp_path):
        """DataSource without sentinel has empty code."""
        cfg = {"path": "data.parquet", "sourceType": "flat_file"}
        cfg_dir = tmp_path / "config" / "data_source"
        cfg_dir.mkdir(parents=True)
        cfg_file = cfg_dir / "my_source.json"
        cfg_file.write_text(json.dumps(cfg))

        body = '    """Load data."""\n    return pl.scan_parquet("data.parquet")'
        with patch("haute._config_builder.warn_unrecognized_config_keys"):
            node_type, loaded = _resolve_node_config(
                {"config": "config/data_source/my_source.json"},
                body,
                [],
                0,
                tmp_path,
                explicit_node_type=NodeType.DATA_SOURCE,
            )
        assert node_type == NodeType.DATA_SOURCE
        assert loaded.get("code", "") == ""

    def test_external_config_file_not_found(self, tmp_path):
        """Missing config file must fail loudly with ``ConfigError``.

        The previous silent-recovery behaviour masked genuine path
        mistakes; post Item #18 fix a missing config raises instead.
        """
        from haute.errors import ConfigError

        with patch("haute._config_builder.warn_unrecognized_config_keys"):
            with pytest.raises(ConfigError):
                _resolve_node_config(
                    {"config": "config/data_source/missing.json"},
                    "",
                    [],
                    0,
                    tmp_path,
                    explicit_node_type=NodeType.DATA_SOURCE,
                )

    def test_banding_type_from_explicit_decorator(self, tmp_path):
        """Explicit decorator type is used directly for config resolution."""
        cfg_dir = tmp_path / "config" / "banding"
        cfg_dir.mkdir(parents=True)
        cfg_file = cfg_dir / "my_transform.json"
        cfg_file.write_text("{}")

        body = '    """doc"""\n    df = df.filter(pl.col("x") > 0)\n    return df'

        with patch("haute._config_builder.warn_unrecognized_config_keys"):
            node_type, config = _resolve_node_config(
                {"config": "config/banding/my_transform.json"},
                body,
                ["source"],
                1,
                tmp_path,
                explicit_node_type=NodeType.BANDING,
            )
        assert node_type == NodeType.BANDING

    def test_does_not_mutate_decorator_kwargs(self):
        """_resolve_node_config must not modify the caller's dict (B21)."""
        kwargs: dict[str, Any] = {"config": "config/data_source/x.json", "extra": True}
        original = dict(kwargs)
        with patch("haute._config_builder.warn_unrecognized_config_keys"):
            with patch("haute._config_builder.load_node_config", return_value={}):
                _resolve_node_config(kwargs, "", [], 0, None)
        # The original dict must be untouched — "config" key stays.
        assert kwargs == original

    def test_no_mutation_polars_path(self):
        """The no-sidecar path must not mutate the input dict (B21)."""
        kwargs: dict[str, Any] = {"path": "data.parquet"}
        original = dict(kwargs)
        with patch("haute._config_builder.warn_unrecognized_config_keys"):
            _resolve_node_config(kwargs, "", [], 0, None)
        assert kwargs == original

    def test_no_mutation_with_multiple_keys(self):
        """Input dict with many keys must not lose or gain any entries (B21)."""
        kwargs: dict[str, Any] = {
            "config": "config/banding/x.json",
            "sink": "out.parquet",
            "format": "parquet",
        }
        original = dict(kwargs)
        with patch("haute._config_builder.warn_unrecognized_config_keys"):
            with patch("haute._config_builder.load_node_config", return_value={}):
                _resolve_node_config(kwargs, "", [], 0, None)
        assert kwargs == original

    def test_mangled_config_path_raises_config_error(self, tmp_path):
        """A Windows-mangled config path must fail loudly.

        Prior behaviour silently recovered via a func-name scan, which
        could load the wrong file if another folder happened to hold a
        matching name.  Post Item #18 the default is fail-loudly.
        """
        from haute.errors import ConfigError

        cfg = {"factors": [{"column": "age", "banding": "continuous"}]}
        cfg_dir = tmp_path / "config" / "banding"
        cfg_dir.mkdir(parents=True)
        cfg_file = cfg_dir / "age_band.json"
        cfg_file.write_text(json.dumps(cfg))

        mangled_path = "config/\x08anding/age_band.json"
        with patch("haute._config_builder.warn_unrecognized_config_keys"):
            with pytest.raises(ConfigError):
                _resolve_node_config(
                    {"config": mangled_path},
                    "",
                    ["df"],
                    1,
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
# _extract_user_code — additional edge cases
# ===========================================================================


class TestExtractUserCodeEdgeCases:
    def test_whitespace_only_body(self):
        assert _extract_user_code("   \n   \n", []) == ""

    def test_multiline_return(self):
        body = (
            '    """doc"""\n    return (\n        source\n        .filter(pl.col("x") > 0)\n    )'
        )
        result = _extract_user_code(body, ["source"])
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
        result = _extract_user_code(body, ["quotes"])
        assert result == "df = df.rename({'a': 'b'})\ndf = df.select('b')"
        assert "return" not in result

    def test_multi_statement_roundtrip_stable(self):
        """Regression: repeated wrap→extract must not accumulate bare 'df'."""
        from haute._codegen_builders import _wrap_user_code

        code = "df = df.rename({'a': 'b'})\ndf = df.select('b')"
        for _ in range(5):
            wrapped = _wrap_user_code(code, ["quotes"])
            body = '    """desc"""\n' + wrapped
            code = _extract_user_code(body, ["quotes"])
        assert code == "df = df.rename({'a': 'b'})\ndf = df.select('b')"


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
        tree = ast.parse(source)
        func_bodies = _extract_function_bodies(source, tree=tree)
        return tree, func_bodies

    def test_extracts_pipeline_nodes(self, tmp_path):
        cfg_dir = tmp_path / "config" / "data_source"
        cfg_dir.mkdir(parents=True)
        (cfg_dir / "source.json").write_text(
            json.dumps({"path": "data.parquet", "sourceType": "flat_file"})
        )
        source = (
            "import polars as pl\n"
            "import haute\n"
            'pipeline = haute.Pipeline("test")\n'
            "\n"
            '@pipeline.data_source(config="config/data_source/source.json")\n'
            "def source():\n"
            '    """Load data."""\n'
            "    return pl.scan_parquet('data.parquet')\n"
            "\n"
            "@pipeline.polars\n"
            "def transform(source):\n"
            "    return source\n"
        )
        tree, bodies = self._parse_source(source)
        with patch("haute._config_builder.warn_unrecognized_config_keys"):
            nodes = _extract_decorated_nodes(
                tree,
                _is_pipeline_node_decorator,
                bodies,
                tmp_path,
            )
        assert len(nodes) == 2
        assert nodes[0]["func_name"] == "source"
        assert nodes[0]["node_type"] == NodeType.DATA_SOURCE
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
        tree, bodies = self._parse_source(source)
        with patch("haute._config_builder.warn_unrecognized_config_keys"):
            nodes = _extract_decorated_nodes(
                tree,
                _is_submodel_node_decorator,
                bodies,
                None,
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
        tree, bodies = self._parse_source(source)
        with patch("haute._config_builder.warn_unrecognized_config_keys"):
            nodes = _extract_decorated_nodes(
                tree,
                _is_pipeline_node_decorator,
                bodies,
                None,
            )
        assert len(nodes) == 1
        assert nodes[0]["func_name"] == "matched"

    def test_ignores_non_function_stmts(self):
        source = "x = 1\ny = 2\n@pipeline.polars\ndef only_func():\n    return 1\n"
        tree, bodies = self._parse_source(source)
        with patch("haute._config_builder.warn_unrecognized_config_keys"):
            nodes = _extract_decorated_nodes(
                tree,
                _is_pipeline_node_decorator,
                bodies,
                None,
            )
        assert len(nodes) == 1

    def test_empty_tree_returns_empty(self):
        tree, bodies = self._parse_source("x = 1\n")
        with patch("haute._config_builder.warn_unrecognized_config_keys"):
            nodes = _extract_decorated_nodes(
                tree,
                _is_pipeline_node_decorator,
                bodies,
                None,
            )
        assert nodes == []

    def test_extracts_param_names(self):
        source = "@pipeline.polars\ndef transform(a, b, c):\n    return a\n"
        tree, bodies = self._parse_source(source)
        with patch("haute._config_builder.warn_unrecognized_config_keys"):
            nodes = _extract_decorated_nodes(
                tree,
                _is_pipeline_node_decorator,
                bodies,
                None,
            )
        assert nodes[0]["param_names"] == ["a", "b", "c"]

    def test_extracts_docstring(self):
        source = '@pipeline.polars\ndef transform(a):\n    """My transform doc."""\n    return a\n'
        tree, bodies = self._parse_source(source)
        with patch("haute._config_builder.warn_unrecognized_config_keys"):
            nodes = _extract_decorated_nodes(
                tree,
                _is_pipeline_node_decorator,
                bodies,
                None,
            )
        assert nodes[0]["description"] == "My transform doc."

    def test_pipeline_checker_does_not_match_submodel(self):
        source = "@submodel.polars\ndef calc(x):\n    return x\n"
        tree, bodies = self._parse_source(source)
        with patch("haute._config_builder.warn_unrecognized_config_keys"):
            # submodel checker matches @submodel.polars
            nodes = _extract_decorated_nodes(
                tree,
                _is_submodel_node_decorator,
                bodies,
                None,
            )
            assert len(nodes) == 1
            # pipeline checker must NOT match @submodel.polars —
            # it checks decorator.value.id == "pipeline"
            nodes2 = _extract_decorated_nodes(
                tree,
                _is_pipeline_node_decorator,
                bodies,
                None,
            )
            assert len(nodes2) == 0


# ===========================================================================
# _unwrap_chain_assignment
# ===========================================================================


class TestUnwrapChainAssignment:
    def test_unwraps_chain_syntax(self):
        code = "df = (\n    source\n    .filter(pl.col('x') > 0)\n)"
        result = _unwrap_chain_assignment(code)
        assert result is not None
        assert ".filter(pl.col('x') > 0)" in result

    def test_non_matching_pattern_returns_none(self):
        assert _unwrap_chain_assignment("x = 1") is None
        assert _unwrap_chain_assignment("result = foo()") is None
        assert _unwrap_chain_assignment("") is None

    def test_strips_leading_source_when_not_in_param_names(self):
        code = "df = (\n    injected_var\n    .filter(pl.col('x') > 0)\n)"
        result = _unwrap_chain_assignment(code, param_names=["other"])
        assert result is not None
        assert "injected_var" not in result
        assert ".filter(pl.col('x') > 0)" in result

    def test_keeps_leading_source_when_in_param_names(self):
        code = "df = (\n    source\n    .filter(pl.col('x') > 0)\n)"
        result = _unwrap_chain_assignment(code, param_names=["source"])
        assert result is not None
        assert "source" in result
        assert ".filter(pl.col('x') > 0)" in result

    def test_no_space_variant(self):
        code = "df=(\n    source\n    .select('a')\n)"
        result = _unwrap_chain_assignment(code)
        assert result is not None
        assert ".select('a')" in result

    def test_single_line_inside_parens(self):
        code = "df = (\n    source.filter(x > 0)\n)"
        result = _unwrap_chain_assignment(code)
        assert result is not None
        assert "source.filter(x > 0)" in result


# ===========================================================================
# _extract_source_user_code
# ===========================================================================


class TestExtractSourceUserCode:
    def test_multiline_first_assignment(self):
        body = (
            '    df = pl.scan_parquet(\n        "data.parquet"\n    )\n'
            "    df = df.filter(pl.col('x') > 0)\n"
            "    return df"
        )
        result = _extract_source_user_code(body)
        assert "filter" in result

    def test_first_assignment_with_parenthesized_expression(self):
        body = (
            '    df = pl.scan_parquet("data.parquet")\n'
            "    df = df.select('a', 'b')\n"
            "    return df"
        )
        result = _extract_source_user_code(body)
        assert "select" in result
        assert "scan_parquet" not in result

    def test_code_after_first_assignment(self):
        body = (
            '    df = pl.scan_parquet("data.parquet")\n'
            "    df = df.rename({'a': 'b'})\n"
            "    df = df.select('b')\n"
            "    return df"
        )
        result = _extract_source_user_code(body)
        assert "rename" in result
        assert "select" in result

    def test_repeated_generated_loads_are_not_user_code(self):
        body = (
            '    df = pl.scan_parquet("data.parquet")\n'
            '    df = pl.scan_parquet("data.parquet")\n'
            "    df = df.limit(10)\n"
            "    return df"
        )
        result = _extract_source_user_code(body)
        assert "scan_parquet" not in result
        assert result == "df = df.limit(10)"

    def test_generated_read_data_source_load_is_not_user_code(self):
        body = (
            "    from pathlib import Path\n"
            "    from haute.graph_utils import read_data_source\n"
            '    df = read_data_source({"sourceType": "flat_file", '
            '"path": str(Path(__file__).parent / "data.csv")})\n'
            "    df = df.limit(10)\n"
            "    return df"
        )
        result = _extract_source_user_code(body)
        assert "read_data_source" not in result
        assert result == "df = df.limit(10)"

    def test_repeated_multiline_generated_loads_are_not_user_code(self):
        body = (
            "    from pathlib import Path\n"
            '    df = pl.scan_parquet(Path(__file__).parent / "data.parquet")\n'
            "    df = pl.scan_parquet(\n"
            '        Path(__file__).parent / "data.parquet"\n'
            "    )\n"
            "    return df"
        )
        result = _extract_source_user_code(body)
        assert result == ""

    def test_empty_rest_returns_empty(self):
        body = '    df = pl.scan_parquet("data.parquet")\n    return df'
        result = _extract_source_user_code(body)
        assert result == ""

    def test_blank_before_return_load_is_not_user_code(self):
        body = (
            "    \n"
            "    from pathlib import Path\n"
            '    return pl.scan_parquet(Path(__file__).parent / "data.parquet")'
        )
        result = _extract_source_user_code(body)
        assert result == ""

    def test_duplicate_generated_load_is_not_user_code(self):
        body = (
            "    from pathlib import Path\n"
            '    df = pl.scan_parquet(Path(__file__).parent / "data.parquet")\n'
            '    df = pl.scan_parquet(Path(__file__).parent / "data.parquet")\n'
            "    return df"
        )
        result = _extract_source_user_code(body)
        assert result == ""

    def test_stale_generated_import_and_load_block_is_not_user_code(self):
        body = (
            "    from pathlib import Path\n"
            '    df = pl.scan_parquet(Path(__file__).parent / "data.parquet")\n'
            "    from pathlib import Path\n"
            "    df = pl.scan_parquet(\n"
            '        Path(__file__).parent / "data.parquet"\n'
            "    )\n"
            "    return df"
        )
        result = _extract_source_user_code(body)
        assert result == ""

    def test_transform_after_stale_generated_load_block_is_preserved(self):
        body = (
            "    from pathlib import Path\n"
            '    df = pl.scan_csv(Path(__file__).parent / "data.csv")\n'
            "    from pathlib import Path\n"
            "    df = pl.scan_csv(\n"
            '        Path(__file__).parent / "data.csv"\n'
            "    )\n"
            "    df = df.filter(pl.col('x') > 0)\n"
            "    return df"
        )
        result = _extract_source_user_code(body)
        assert result == "df = df.filter(pl.col('x') > 0)"


# ===========================================================================
# _extract_scenario_expander_user_code
# ===========================================================================


class TestExtractScenarioExpanderUserCode:
    def test_generated_passthrough_is_not_user_code(self):
        body = "    return quotes"
        result = _extract_scenario_expander_user_code(body, ["quotes"])
        assert result == ""

    def test_generated_alias_is_stripped_but_post_code_is_preserved(self):
        body = "    df = quotes\n    df = df.filter(pl.col('scenario_value') > 1)\n    return df"
        result = _extract_scenario_expander_user_code(body, ["quotes"])
        assert result == "df = df.filter(pl.col('scenario_value') > 1)"

    def test_handwritten_first_statement_is_not_mistaken_for_scaffold(self):
        body = "    df = df.filter(pl.col('scenario_value') > 1)\n    return df"
        result = _extract_scenario_expander_user_code(body, ["quotes"])
        assert result == "df = df.filter(pl.col('scenario_value') > 1)"


# ===========================================================================
# _extract_model_score_user_code
# ===========================================================================


class TestExtractModelScoreUserCode:
    def test_finds_score_from_config_call(self):
        body = (
            "    from pathlib import Path\n"
            '    result = score_from_config(df, Path("model.json"))\n'
            "    result = result.with_columns(x=1)\n"
            "    return result"
        )
        result = _extract_model_score_user_code(body)
        assert "with_columns" in result
        assert "result" not in result
        assert "df" in result

    def test_extracts_code_after_scoring_call(self):
        body = (
            '    result = score_from_config(df, Path("model.json"))\n'
            "    result = result.filter(pl.col('x') > 0)\n"
            "    return result"
        )
        result = _extract_model_score_user_code(body)
        assert "filter" in result
        assert result == "df = df.filter(pl.col('x') > 0)"

    def test_no_sentinel_and_no_score_returns_empty(self):
        body = "    x = 1\n    return x"
        result = _extract_model_score_user_code(body)
        assert result == ""

    def test_multiline_score_from_config_call(self):
        body = (
            "    from pathlib import Path\n"
            "    result = score_from_config(\n"
            "        df,\n"
            '        Path("model.json"),\n'
            "    )\n"
            "    result = result.with_columns(flag=True)\n"
            "    return result"
        )
        result = _extract_model_score_user_code(body)
        assert "with_columns" in result
        assert "score_from_config" not in result
        assert "result" not in result

    def test_no_code_after_score_returns_empty(self):
        body = (
            "    from pathlib import Path\n"
            '    result = score_from_config(df, Path("model.json"))\n'
            "    return result"
        )
        result = _extract_model_score_user_code(body)
        assert result == ""

    def test_score_to_df_template_return_is_not_user_code(self):
        body = (
            "    from pathlib import Path\n"
            "    from haute.graph_utils import score_from_config\n"
            "    base = str(Path(__file__).parent)\n"
            "    df = score_from_config(\n"
            '        source, config="config/model_scoring/Score.json",\n'
            "        base_dir=base,\n"
            "    )\n"
            "    return df"
        )
        result = _extract_model_score_user_code(body)
        assert result == ""

    def test_score_to_df_template_preserves_only_post_score_code(self):
        body = (
            "    from pathlib import Path\n"
            "    from haute.graph_utils import score_from_config\n"
            "    base = str(Path(__file__).parent)\n"
            "    df = score_from_config(\n"
            '        source, config="config/model_scoring/Score.json",\n'
            "        base_dir=base,\n"
            "    )\n"
            "    df = df.with_columns(double_score=pl.col('prediction') * 2)\n"
            "    return df"
        )
        result = _extract_model_score_user_code(body)
        assert result == "df = df.with_columns(double_score=pl.col('prediction') * 2)"
        assert "score_from_config" not in result


# ===========================================================================
# _extract_rating_step_user_code
# ===========================================================================


class TestExtractRatingStepUserCode:
    def test_rating_scaffold_is_not_user_code(self):
        body = (
            "    from pathlib import Path\n"
            "    from haute.graph_utils import apply_rating_step_from_config\n"
            "    base = Path(__file__).parent\n"
            "    df = apply_rating_step_from_config(\n"
            '        quotes, "config/rating_step/Rate.json", base_dir=base\n'
            "    )\n"
            "    return df"
        )
        result = _extract_rating_step_user_code(body, ["quotes"])
        assert result == ""

    def test_rating_post_code_can_reference_original_input_name(self):
        body = (
            "    from pathlib import Path\n"
            "    from haute.graph_utils import apply_rating_step_from_config\n"
            "    base = Path(__file__).parent\n"
            "    df = apply_rating_step_from_config(\n"
            '        quotes, "config/rating_step/Rate.json", base_dir=base\n'
            "    )\n"
            "    audit = quotes.select('quote_id')\n"
            "    df = df.join(audit, on='quote_id')\n"
            "    return df"
        )
        result = _extract_rating_step_user_code(body, ["quotes"])
        assert "audit = quotes.select('quote_id')" in result
        assert "apply_rating_step_from_config" not in result


# ===========================================================================
# _extract_external_user_code
# ===========================================================================


class TestExtractExternalUserCode:
    def test_strips_import_and_with_block(self):
        body = (
            "import pickle\n"
            "with open('model.pkl', 'rb') as f:\n"
            "    obj = pickle.load(f)\n"
            "df = df.with_columns(pred=obj.predict(df['x']))\n"
            "return df"
        )
        result = _extract_external_user_code(body, ["df"])
        assert "with_columns" in result
        assert "import pickle" not in result
        assert "with open" not in result

    def test_strips_obj_assignment(self):
        body = (
            "import pickle\n"
            "with open('model.pkl', 'rb') as f:\n"
            "    obj = pickle.load(f)\n"
            "obj = obj.submodel\n"
            "df = df.with_columns(pred=obj.predict(df['x']))\n"
            "return df"
        )
        result = _extract_external_user_code(body, ["df"])
        assert "with_columns" in result
        assert "obj = obj.submodel" not in result

    def test_empty_body_returns_empty(self):
        result = _extract_external_user_code("", ["df"])
        assert result == ""

    def test_only_boilerplate_returns_empty(self):
        body = (
            "import pickle\nwith open('model.pkl', 'rb') as f:\n    obj = pickle.load(f)\nreturn df"
        )
        result = _extract_external_user_code(body, ["df"])
        assert result == ""

    def test_user_code_after_boilerplate(self):
        body = (
            "import pickle\n"
            "with open('model.pkl', 'rb') as f:\n"
            "    obj = pickle.load(f)\n"
            "x = obj.predict(df['a'])\n"
            "df = df.with_columns(pred=x)\n"
            "return df"
        )
        result = _extract_external_user_code(body, ["df"])
        assert "x = obj.predict" in result
        assert "with_columns" in result

    def test_multiline_load_external_object_is_not_user_code(self):
        body = (
            "from pathlib import Path\n"
            "from haute.graph_utils import load_external_object\n"
            "obj = load_external_object(\n"
            '    Path(__file__).parent / "model.pkl", "pickle"\n'
            ")\n"
            "df = df.limit(10)\n"
            "return df"
        )
        result = _extract_external_user_code(body, ["df"])
        assert result == "df = df.limit(10)"
        assert "load_external_object" not in result


# ===========================================================================
# _extract_function_bodies — zero-coverage extras
# ===========================================================================


class TestExtractFunctionBodiesZeroCov:
    def test_single_function_body_content(self):
        source = "def greet():\n    msg = 'hi'\n    return msg"
        bodies = _extract_function_bodies(source, tree=ast.parse(source))
        assert "greet" in bodies
        assert "msg = 'hi'" in bodies["greet"]
        assert "return msg" in bodies["greet"]

    def test_multiple_functions_isolated(self):
        source = (
            "def alpha():\n    return 1\n\ndef beta():\n    return 2\n\ndef gamma():\n    return 3"
        )
        bodies = _extract_function_bodies(source, tree=ast.parse(source))
        assert set(bodies.keys()) == {"alpha", "beta", "gamma"}
        assert "return 1" in bodies["alpha"]
        assert "return 2" in bodies["beta"]
        assert "return 3" in bodies["gamma"]

    def test_nested_not_extracted(self):
        source = "def outer():\n    def inner():\n        pass\n    return inner()"
        bodies = _extract_function_bodies(source, tree=ast.parse(source))
        assert "outer" in bodies
        assert "inner" not in bodies

    def test_empty_source_returns_empty(self):
        assert _extract_function_bodies("", tree=ast.parse("")) == {}


# ===========================================================================
# _get_decorator_node_type
# ===========================================================================


class TestGetDecoratorNodeType:
    def _dec(self, source: str) -> ast.expr:
        tree = ast.parse(source)
        return tree.body[0].decorator_list[0]

    def test_pipeline_data_source(self):
        result = _get_decorator_node_type(self._dec("@pipeline.data_source\ndef f(): pass"))
        assert result == NodeType.DATA_SOURCE

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
            self._dec("@pipeline.data_source(path='x')\ndef f(): pass")
        )
        assert result == NodeType.DATA_SOURCE

    def test_plain_name_returns_none(self):
        result = _get_decorator_node_type(self._dec("@some_decorator\ndef f(): pass"))
        assert result is None

    def test_other_object_returns_none(self):
        result = _get_decorator_node_type(self._dec("@other.polars\ndef f(): pass"))
        assert result is None
