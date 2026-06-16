"""Tests for codegen injection bug B2 (triple-quote in descriptions).

B2: Node descriptions containing triple quotes must not break generated
docstrings.  The fix is in ``_sanitize_description`` which replaces ``\"\"\"``
with ``'''`` so the generated ``\"\"\"{description}\"\"\"`` remains valid Python.

Also includes regression tests confirming that curly braces in user-controlled
values (file paths, table names, etc.) are safe with Python's
``str.format()`` — values substituted via keyword arguments are NOT
re-processed by the format engine.

Every test verifies the generated code is syntactically valid via ``ast.parse``.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from haute._codegen_builders import _sanitize_description
from haute._config_io import collect_node_configs
from haute.codegen import (
    _instance_to_code,
    _node_to_code,
    graph_to_code,
)
from haute.parser import parse_pipeline_source
from tests.conftest import compile_node_code as _compile_node_code
from tests.conftest import make_graph as _g
from tests.conftest import make_node as _n

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ast_parse_node_code(code: str) -> None:
    """Verify generated node code parses as valid Python via ast.parse."""
    wrapper = f"import polars as pl\nimport haute\npipeline = haute.Pipeline('test')\n\n{code}\n"
    ast.parse(wrapper)


def _make_node(
    node_type: str, config: dict, label: str = "TestNode", description: str | None = None
):
    """Build a GraphNode for codegen testing."""
    data: dict = {"label": label, "nodeType": node_type, "config": config}
    if description is not None:
        data["description"] = description
    return _n({"id": "test_id", "data": data})


# ---------------------------------------------------------------------------
# B2: _sanitize_description unit tests
# ---------------------------------------------------------------------------


class TestSanitizeDescription:
    """Unit tests for _sanitize_description.

    Post Wave 9D #122: the sanitiser returns content suitable for
    interpolation between ``\"\"\"`` triple-double-quotes.  It backslash-
    escapes every ``\\`` and every ``"`` so that no triple-quote run can
    form inside the literal and so that escape sequences stay literal.
    A helper asserts the full round-trip invariant — the docstring
    extracted from the generated code must equal the original.
    """

    @staticmethod
    def _assert_roundtrip(description: str) -> None:
        """Helper — verify ``f'\"\"\"{_sanitize_description(x)}\"\"\"'`` parses
        and yields a docstring whose cleandoc value equals *description*.
        """
        result = _sanitize_description(description)
        code = f'def f():\n    """{result}"""\n    pass'
        tree = ast.parse(code)
        assert (ast.get_docstring(tree.body[0]) or "") == description, (
            f"round-trip failed for {description!r}: "
            f"sanitized={result!r}, "
            f"docstring={ast.get_docstring(tree.body[0])!r}"
        )

    def test_triple_quotes_escaped(self):
        """Every ``\"`` (including those inside ``\"\"\"``) is backslash-escaped."""
        result = _sanitize_description('Has """triple""" quotes')
        # No bare """ run can appear in the emitted content.
        assert '"""' not in result
        # Round-trip invariant.
        self._assert_roundtrip('Has """triple""" quotes')

    def test_single_triple_quote(self):
        result = _sanitize_description('Ends with """')
        assert '"""' not in result
        self._assert_roundtrip('Ends with """')

    def test_multiple_triple_quotes(self):
        result = _sanitize_description('A """ B """ C')
        assert result.count('"""') == 0
        self._assert_roundtrip('A """ B """ C')

    def test_no_triple_quotes_unchanged(self):
        original = "Normal description"
        self._assert_roundtrip(original)

    def test_single_double_quote_interior_roundtrips(self):
        self._assert_roundtrip('Has a " quote')

    def test_two_double_quotes_interior_roundtrips(self):
        self._assert_roundtrip('Has "" two quotes')

    def test_four_double_quotes_roundtrips(self):
        """Four consecutive double-quotes must not form a run in output."""
        result = _sanitize_description('Has """" four')
        assert '"""' not in result
        self._assert_roundtrip('Has """" four')

    def test_single_quotes_not_affected(self):
        self._assert_roundtrip("Has '''single triple''' quotes")

    def test_trailing_backslash_roundtrips(self):
        self._assert_roundtrip("ends with backslash\\")

    def test_empty_string(self):
        assert _sanitize_description("") == ""

    def test_mixed_quotes(self):
        desc = '''Has " and "" and """ and '  '''
        result = _sanitize_description(desc)
        assert '"""' not in result
        self._assert_roundtrip(desc)

    def test_trailing_single_double_quote_roundtrips(self):
        self._assert_roundtrip('ends with"')

    def test_trailing_two_double_quotes_roundtrips(self):
        self._assert_roundtrip('ends with""')

    def test_trailing_four_double_quotes_roundtrips(self):
        self._assert_roundtrip("ends with" + '"' * 4)

    def test_trailing_backslash_then_quote(self):
        self._assert_roundtrip('ends with\\"')

    def test_trailing_double_backslash_then_quote(self):
        self._assert_roundtrip('ends with\\\\"')

    def test_just_a_single_double_quote(self):
        self._assert_roundtrip('"')

    def test_just_two_double_quotes(self):
        self._assert_roundtrip('""')

    @pytest.mark.parametrize("n_quotes", range(1, 8))
    def test_trailing_n_quotes_all_safe(self, n_quotes):
        """Any number of trailing double-quotes produces valid Python."""
        desc = "x" + '"' * n_quotes
        result = _sanitize_description(desc)
        code = f'def f():\n    """{result}"""\n    pass'
        ast.parse(code)

    def test_result_safe_in_docstring(self):
        """Sanitized text must produce a valid Python docstring."""
        for desc in [
            '"""',
            '""""""',
            'a """b""" c',
            'end"""',
            '"""start',
            'end"',
            'end""',
            'end""""',
            '"',
            '""',
            '\\"',
            '\\\\"',
        ]:
            sanitized = _sanitize_description(desc)
            code = f'def f():\n    """{sanitized}"""\n    pass'
            ast.parse(code)


# ---------------------------------------------------------------------------
# B2: Triple-quote injection in node descriptions — all node types
# ---------------------------------------------------------------------------


class TestTripleQuoteInjection:
    """Descriptions containing triple quotes must produce valid Python."""

    @pytest.mark.parametrize(
        "node_type,config",
        [
            ("dataSource", {"path": "data.parquet"}),
            ("polars", {"code": "df = df.drop_nulls()"}),
            ("dataSink", {"path": "out.parquet", "format": "parquet"}),
            (
                "banding",
                {
                    "factors": [
                        {"banding": "continuous", "column": "x", "outputColumn": "x_f", "rules": []}
                    ]
                },
            ),
            (
                "ratingStep",
                {"tables": [{"name": "T", "factors": ["x"], "outputColumn": "f", "entries": []}]},
            ),
            ("constant", {"values": [{"name": "v", "value": "1"}]}),
            ("output", {"fields": ["a"]}),
            ("scenarioExpander", {}),
            ("optimiser", {}),
            ("optimiserApply", {}),
            ("modelling", {}),
            ("externalFile", {"path": "model.pkl", "fileType": "pickle", "code": ""}),
            ("liveSwitch", {"input_scenario_map": {"live": "live"}}),
        ],
        ids=lambda x: x if isinstance(x, str) else "",
    )
    def test_triple_quote_in_description_all_types(self, node_type, config):
        """Every node type handles triple-quote in description safely.

        Post Wave 9D #122: the sanitiser backslash-escapes every ``\"`` in
        the description, so no bare triple-quote run can form inside the
        docstring literal.  The docstring extracted via
        ``ast.get_docstring`` must still equal the original description.
        """
        import ast

        description = 'Has """triple""" quotes'
        node = _make_node(
            node_type,
            config,
            label="TestNode",
            description=description,
        )
        code = _node_to_code(node, source_names=["upstream"])
        _compile_node_code(code)
        _ast_parse_node_code(code)
        # Round-trip: the extracted docstring equals the original.
        wrapper = (
            f"import polars as pl\nimport haute\npipeline = haute.Pipeline('test')\n\n{code}\n"
        )
        tree = ast.parse(wrapper)
        fn = next(
            n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "TestNode"
        )
        assert (ast.get_docstring(fn) or "") == description

    def test_triple_quote_in_transform_description(self):
        """Transform node with triple-quote description compiles."""
        node = _make_node(
            "polars",
            {"code": "df = df.with_columns(y=pl.lit(1))"},
            description='Load the """premium""" data',
        )
        code = _node_to_code(node, source_names=["src"])
        _compile_node_code(code)
        assert "premium" in code

    def test_triple_quote_in_data_source_description(self):
        """Data source with triple-quote description compiles."""
        node = _make_node(
            "dataSource",
            {"path": "data.parquet"},
            description='Source for """raw""" data',
        )
        code = _node_to_code(node)
        _compile_node_code(code)

    def test_triple_quote_only_description(self):
        """Description that is nothing but triple quotes."""
        node = _make_node(
            "polars",
            {"code": ""},
            description='"""',
        )
        code = _node_to_code(node, source_names=["upstream"])
        _compile_node_code(code)

    def test_six_quotes_description(self):
        """Description with six consecutive double quotes (two triple-quotes)."""
        node = _make_node(
            "polars",
            {"code": ""},
            description='""""""',
        )
        code = _node_to_code(node, source_names=["upstream"])
        _compile_node_code(code)

    def test_triple_quote_at_start(self):
        """Triple quote at the very start of description."""
        node = _make_node(
            "polars",
            {"code": ""},
            description='"""Starts with quotes',
        )
        code = _node_to_code(node, source_names=["upstream"])
        _compile_node_code(code)

    def test_triple_quote_at_end(self):
        """Triple quote at the very end of description."""
        node = _make_node(
            "polars",
            {"code": ""},
            description='Ends with quotes"""',
        )
        code = _node_to_code(node, source_names=["upstream"])
        _compile_node_code(code)

    def test_single_quotes_in_description_unchanged(self):
        """Single and double quotes (not triple) should pass through unchanged."""
        node = _make_node(
            "polars",
            {"code": ""},
            description="Has 'single' and \"double\" quotes",
        )
        code = _node_to_code(node, source_names=["upstream"])
        _compile_node_code(code)
        assert "single" in code
        assert "double" in code

    def test_instance_node_triple_quote_description(self):
        """Instance nodes also handle triple-quote descriptions."""
        node = _make_node(
            "polars",
            {"code": "", "instanceOf": "original"},
            label="Instance1",
            description='Instance """special"""',
        )
        code = _instance_to_code(node, "original_func", source_names=["src"])
        _compile_node_code(code)
        _ast_parse_node_code(code)

    def test_output_node_triple_quote_description(self):
        """Output node (f-string path) handles triple-quote description."""
        node = _make_node(
            "output",
            {"fields": ["a", "b"]},
            description='Output """result"""',
        )
        code = _node_to_code(node, source_names=["src"])
        _compile_node_code(code)
        _ast_parse_node_code(code)

    def test_model_score_with_user_code_triple_quote_description(self):
        """Model score with user code (f-string path) handles triple quotes."""
        node = _make_node(
            "modelScore",
            {
                "sourceType": "run",
                "run_id": "abc",
                "artifact_path": "model",
                "task": "regression",
                "output_column": "pred",
                "code": "result = result.with_columns(pl.lit(1))",
            },
            description='Score """model"""',
        )
        code = _node_to_code(node, source_names=["df_in"])
        _compile_node_code(code)
        _ast_parse_node_code(code)

    def test_model_score_without_user_code_triple_quote_description(self):
        """Model score without user code (.format() path) handles triple quotes."""
        node = _make_node(
            "modelScore",
            {
                "sourceType": "run",
                "run_id": "abc",
                "artifact_path": "model",
                "task": "regression",
                "output_column": "pred",
            },
            description='Score """model"""',
        )
        code = _node_to_code(node, source_names=["df_in"])
        _compile_node_code(code)
        _ast_parse_node_code(code)

    def test_graph_level_triple_quote_node_description(self):
        """Full graph with a node whose description has triple quotes."""
        graph = _g(
            {
                "nodes": [
                    {
                        "id": "a",
                        "data": {
                            "label": "A",
                            "nodeType": "dataSource",
                            "config": {"path": "d.parquet"},
                            "description": 'Load """raw""" data',
                        },
                    }
                ],
                "edges": [],
            }
        )
        code = graph_to_code(graph)
        compile(code, "<test>", "exec")
        ast.parse(code)

    def test_backslash_before_closing_triple_quote(self):
        """Description ending with backslash would escape the closing triple-quote."""
        node = _make_node(
            "polars",
            {"code": ""},
            description="ends with backslash\\",
        )
        code = _node_to_code(node, source_names=["upstream"])
        _compile_node_code(code)
        _ast_parse_node_code(code)

    def test_mixed_triple_and_single_quotes(self):
        """Description with both triple double-quotes and single quotes."""
        node = _make_node(
            "polars",
            {"code": ""},
            description="""Has ''' and \"\"\", both""",
        )
        code = _node_to_code(node, source_names=["upstream"])
        _compile_node_code(code)
        _ast_parse_node_code(code)

    def test_trailing_double_quote_in_description(self):
        """Description ending with a double-quote must not break docstring."""
        node = _make_node(
            "polars",
            {"code": ""},
            description='ends with a quote"',
        )
        code = _node_to_code(node, source_names=["upstream"])
        _compile_node_code(code)
        _ast_parse_node_code(code)

    def test_trailing_two_double_quotes_in_description(self):
        """Description ending with two double-quotes."""
        node = _make_node(
            "dataSource",
            {"path": "data.parquet"},
            description='double trouble""',
        )
        code = _node_to_code(node)
        _compile_node_code(code)
        _ast_parse_node_code(code)

    def test_trailing_backslash_then_quote_in_description(self):
        r"""Description ending with ``\"`` (backslash then quote)."""
        node = _make_node(
            "polars",
            {"code": ""},
            description='path is C:\\"',
        )
        code = _node_to_code(node, source_names=["upstream"])
        _compile_node_code(code)
        _ast_parse_node_code(code)

    @pytest.mark.parametrize(
        "node_type,config",
        [
            ("dataSource", {"path": "data.parquet"}),
            ("polars", {"code": ""}),
            ("dataSink", {"path": "out.parquet", "format": "parquet"}),
            ("output", {"fields": ["a"]}),
            ("constant", {"values": [{"name": "v", "value": "1"}]}),
        ],
        ids=lambda x: x if isinstance(x, str) else "",
    )
    def test_trailing_quote_all_node_types(self, node_type, config):
        """Trailing double-quote in description across multiple node types."""
        node = _make_node(
            node_type,
            config,
            label="TestNode",
            description='field is "premium"',
        )
        code = _node_to_code(node, source_names=["upstream"])
        _compile_node_code(code)
        _ast_parse_node_code(code)


# ---------------------------------------------------------------------------
# Curly braces in user-controlled values — confirming NOT a bug
# ---------------------------------------------------------------------------


class TestCurlyBracesInValues:
    """Verify that curly braces in user values are safe with .format().

    Python's str.format() processes replacement fields in the TEMPLATE only —
    values substituted via keyword arguments are NOT re-processed.  These
    tests document this behavior as a safety net.
    """

    def test_path_with_braces_data_source_parquet(self):
        """Parquet data source with {braces} in path."""
        node = _make_node(
            "dataSource",
            {"path": "data/{year}/input.parquet"},
        )
        code = _node_to_code(node)
        _compile_node_code(code)
        assert "{year}" in code

    def test_path_with_braces_data_source_csv(self):
        """CSV data source with {braces} in path."""
        node = _make_node(
            "dataSource",
            {"path": "data/{year}/input.csv"},
        )
        code = _node_to_code(node)
        _compile_node_code(code)
        assert "{year}" in code

    def test_path_with_braces_api_input(self):
        """API input with {braces} in path."""
        node = _make_node(
            "apiInput",
            {"path": "data/{region}/api.parquet"},
        )
        code = _node_to_code(node)
        _compile_node_code(code)
        assert "{region}" in code

    def test_path_with_braces_api_input_json(self):
        """API input JSON with {braces} in path."""
        node = _make_node(
            "apiInput",
            {"path": "data/{region}/api.json"},
        )
        code = _node_to_code(node)
        _compile_node_code(code)
        assert "{region}" in code

    def test_path_with_braces_data_sink(self):
        """Data sink with {braces} in path."""
        node = _make_node(
            "dataSink",
            {"path": "output/{date}/results.parquet", "format": "parquet"},
        )
        code = _node_to_code(node, source_names=["src"])
        _compile_node_code(code)
        assert "{date}" in code

    def test_path_with_braces_data_sink_csv(self):
        """CSV sink with {braces} in path."""
        node = _make_node(
            "dataSink",
            {"path": "output/{date}/results.csv", "format": "csv"},
        )
        code = _node_to_code(node, source_names=["src"])
        _compile_node_code(code)
        assert "{date}" in code

    def test_path_with_braces_external_file(self):
        """External file with {braces} in path."""
        node = _make_node(
            "externalFile",
            {"path": "models/{version}/model.pkl", "fileType": "pickle", "code": ""},
        )
        code = _node_to_code(node)
        _compile_node_code(code)
        assert "{version}" in code

    def test_path_with_nested_double_braces(self):
        """Path with already-doubled {{braces}} — pass through as-is."""
        node = _make_node(
            "dataSource",
            {"path": "data/{{year}}/input.parquet"},
        )
        code = _node_to_code(node)
        _compile_node_code(code)
        assert "{{year}}" in code

    def test_databricks_table_with_braces(self):
        """Databricks table name with {braces}."""
        node = _make_node(
            "dataSource",
            {
                "sourceType": "databricks",
                "table": "catalog.{env}.table",
            },
        )
        code = _node_to_code(node)
        _compile_node_code(code)
        assert "{env}" in code

    def test_description_with_braces(self):
        """Description containing {braces} in .format() templates."""
        node = _make_node(
            "dataSource",
            {"path": "data.parquet"},
            description="Loads data for {region} pricing",
        )
        code = _node_to_code(node)
        _compile_node_code(code)
        assert "{region}" in code

    def test_description_with_braces_in_transform(self):
        """Transform description with {braces} (f-string path)."""
        node = _make_node(
            "polars",
            {"code": ""},
            description="Transform {step_1} output",
        )
        code = _node_to_code(node, source_names=["upstream"])
        _compile_node_code(code)
        assert "{step_1}" in code

    def test_empty_braces_in_path(self):
        """Path with empty {} braces."""
        node = _make_node(
            "dataSource",
            {"path": "data/{}/input.parquet"},
        )
        code = _node_to_code(node)
        _compile_node_code(code)
        assert "{}" in code

    def test_multiple_brace_patterns_in_path(self):
        """Path with multiple brace patterns."""
        node = _make_node(
            "dataSource",
            {"path": "data/{year}/{month}/{day}/input.parquet"},
        )
        code = _node_to_code(node)
        _compile_node_code(code)
        assert "{year}" in code
        assert "{month}" in code
        assert "{day}" in code

    def test_external_file_body_with_braces(self):
        """External file user code with braces should not be mangled."""
        node = _make_node(
            "externalFile",
            {
                "path": "model.pkl",
                "fileType": "pickle",
                "code": 'result = {"key": obj.predict(df)}',
            },
        )
        code = _node_to_code(node)
        _compile_node_code(code)
        assert '{"key"' in code

    def test_graph_with_braces_in_path(self):
        """Full graph with braces in source path."""
        graph = _g(
            {
                "nodes": [
                    {
                        "id": "a",
                        "data": {
                            "label": "A",
                            "nodeType": "dataSource",
                            "config": {"path": "data/{env}/input.parquet"},
                        },
                    }
                ],
                "edges": [],
            }
        )
        code = graph_to_code(graph)
        compile(code, "<test>", "exec")
        assert "{env}" in code


# ---------------------------------------------------------------------------
# Combined B2 + braces: both triple-quotes AND braces in same node
# ---------------------------------------------------------------------------


class TestCombinedInjection:
    """Tests where both description and path contain dangerous characters."""

    def test_triple_quote_description_and_brace_path(self):
        """Node with triple-quote description AND brace-containing path."""
        node = _make_node(
            "dataSource",
            {"path": "data/{year}/input.parquet"},
            description='Load """raw""" data for {region}',
        )
        code = _node_to_code(node)
        _compile_node_code(code)
        _ast_parse_node_code(code)
        assert "{year}" in code
        assert "{region}" in code

    def test_triple_quote_and_braces_in_sink(self):
        """Sink with both dangerous chars."""
        node = _make_node(
            "dataSink",
            {"path": "output/{date}/results.parquet", "format": "parquet"},
            description='Write """final""" output',
        )
        code = _node_to_code(node, source_names=["src"])
        _compile_node_code(code)
        _ast_parse_node_code(code)

    def test_triple_quote_and_braces_in_api_input(self):
        """API input with both dangerous chars."""
        node = _make_node(
            "apiInput",
            {"path": "data/{region}/api.parquet"},
            description='API """input""" for {product}',
        )
        code = _node_to_code(node)
        _compile_node_code(code)
        _ast_parse_node_code(code)

    def test_all_injection_vectors_at_once(self):
        """Full pipeline graph with multiple injection vectors."""
        graph = _g(
            {
                "nodes": [
                    {
                        "id": "src",
                        "data": {
                            "label": "Source",
                            "nodeType": "dataSource",
                            "config": {"path": "data/{env}/input.parquet"},
                            "description": 'Load """raw""" {env} data',
                        },
                    },
                    {
                        "id": "t",
                        "data": {
                            "label": "Clean",
                            "nodeType": "polars",
                            "config": {"code": "df = df.drop_nulls()"},
                            "description": 'Clean """dirty""" records',
                        },
                    },
                    {
                        "id": "sink",
                        "data": {
                            "label": "Write",
                            "nodeType": "dataSink",
                            "config": {"path": "output/{date}/out.parquet", "format": "parquet"},
                            "description": 'Write to """storage"""',
                        },
                    },
                ],
                "edges": [
                    {"id": "e1", "source": "src", "target": "t"},
                    {"id": "e2", "source": "t", "target": "sink"},
                ],
            }
        )
        code = graph_to_code(graph)
        compile(code, "<test>", "exec")
        ast.parse(code)


# ---------------------------------------------------------------------------
# Regression tests: ensure normal descriptions still work
# ---------------------------------------------------------------------------


class TestDescriptionRegression:
    """Ensure normal descriptions are not broken by sanitization."""

    def test_normal_description_unchanged(self):
        node = _make_node(
            "polars",
            {"code": ""},
            description="Normal description text",
        )
        code = _node_to_code(node, source_names=["upstream"])
        assert "Normal description text" in code
        _compile_node_code(code)

    def test_default_description_is_empty(self):
        """An unset description produces an empty docstring.

        Post Wave 9D #122: we intentionally do NOT substitute a
        ``<label> node`` placeholder for an empty description, because
        that mutation breaks the round-trip invariant (saved graph
        would come back with a synthetic description the user never
        typed).  The emitted code still compiles — the docstring is
        simply ``\"\"\"\"\"\"``.
        """
        import ast

        node = _make_node("polars", {"code": ""}, label="MyLabel")
        code = _node_to_code(node, source_names=["upstream"])
        _compile_node_code(code)
        # Wrap the generated node in a minimal pipeline preamble so we
        # can parse it and verify the docstring is empty.
        wrapper = (
            f"import polars as pl\nimport haute\npipeline = haute.Pipeline('test')\n\n{code}\n"
        )
        tree = ast.parse(wrapper)
        fn = next(
            n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "MyLabel"
        )
        assert (ast.get_docstring(fn) or "") == ""

    def test_description_with_newlines(self):
        """Newlines in descriptions are OK inside triple-quoted docstrings."""
        node = _make_node(
            "polars",
            {"code": ""},
            description="Line 1\nLine 2",
        )
        code = _node_to_code(node, source_names=["upstream"])
        _compile_node_code(code)

    def test_description_with_single_double_quote(self):
        """A single double-quote in description is fine."""
        node = _make_node(
            "polars",
            {"code": ""},
            description='Has a "quoted" word',
        )
        code = _node_to_code(node, source_names=["upstream"])
        _compile_node_code(code)
        assert "quoted" in code

    def test_description_with_two_double_quotes(self):
        """Two consecutive double-quotes in description is fine."""
        node = _make_node(
            "polars",
            {"code": ""},
            description='Has "" empty quotes',
        )
        code = _node_to_code(node, source_names=["upstream"])
        _compile_node_code(code)


# ---------------------------------------------------------------------------
# Remediation 5.2: braces in descriptions must round-trip exactly and be
# byte-stable across save/load cycles (no unbounded growth).
# ---------------------------------------------------------------------------


def _docstring_of(code: str, func_name: str) -> str:
    """Parse generated node code and return *func_name*'s docstring."""
    wrapper = f"import polars as pl\nimport haute\npipeline = haute.Pipeline('test')\n\n{code}\n"
    tree = ast.parse(wrapper)
    fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == func_name)
    return ast.get_docstring(fn) or ""


_BRACE_DESCRIPTIONS = [
    pytest.param("plain {} braces", id="empty-braces"),
    pytest.param("has {braces} inside", id="named-braces"),
    pytest.param("user wants {{doubled}} braces", id="user-doubled-braces"),
    pytest.param("{", id="lone-open"),
    pytest.param("}", id="lone-close"),
    pytest.param("unicode {数学} café — {α}", id="unicode-braces"),
    pytest.param("line1 {a}\nline2 {{b}}", id="newline-braces"),
]


class TestBraceDescriptionRoundTrip:
    """5.2: brace sanitization must be idempotent across save/load cycles.

    The description is always substituted into the per-type templates as a
    ``str.format`` *keyword argument* (or an f-string value) — never spliced
    into the template text itself — and ``str.format`` does not re-scan
    substituted values for replacement fields.  Doubling braces in the value
    therefore lands the doubled braces literally in the emitted docstring;
    the parser reads them back doubled, and the next save doubles again —
    the description grows without bound.  These tests pin exact round-trip
    and multi-cycle byte-stability.
    """

    @pytest.mark.parametrize("description", _BRACE_DESCRIPTIONS)
    def test_braces_roundtrip_via_fstring_path(self, description: str) -> None:
        """polars builder (f-string interpolation path)."""
        node = _make_node("polars", {"code": "df = upstream"}, description=description)
        code = _node_to_code(node, source_names=["upstream"])
        assert _docstring_of(code, "TestNode") == description

    @pytest.mark.parametrize("description", _BRACE_DESCRIPTIONS)
    def test_braces_roundtrip_via_format_template_path(self, description: str) -> None:
        """dataSink builder (``str.format`` template path)."""
        node = _make_node(
            "dataSink",
            {"path": "out.parquet", "format": "parquet"},
            description=description,
        )
        code = _node_to_code(node, source_names=["upstream"])
        assert _docstring_of(code, "TestNode") == description

    def test_sanitize_description_leaves_braces_alone(self) -> None:
        """Unit: the sanitizer must not escape braces (they need no escaping
        between triple quotes, and the compiler will not un-double them)."""
        assert _sanitize_description("a {b} c {{d}}") == "a {b} c {{d}}"

    @staticmethod
    def _save_load_cycle(graph, base_dir: Path):
        """One production save/load cycle: graph -> code (+sidecars) -> parse."""
        code = graph_to_code(graph, pipeline_name="cycle")
        for rel_path, content in collect_node_configs(graph).items():
            abs_path = base_dir / rel_path
            abs_path.parent.mkdir(parents=True, exist_ok=True)
            abs_path.write_text(content)
        parsed = parse_pipeline_source(code, source_file="cycle.py", _base_dir=base_dir)
        return code, parsed

    def test_braced_descriptions_byte_stable_across_cycles(self, tmp_path: Path) -> None:
        """Three save/load cycles: descriptions identical at every generation
        and the emitted file reaches a byte-stable fixpoint.  Before the 5.2
        fix the braces doubled each cycle ({a} -> {{a}} -> {{{{a}}}})."""
        src_desc = "loads {raw} data"
        clean_desc = "drops {nulls} and keeps {{literal}} braces"
        graph = _g(
            {
                "nodes": [
                    {
                        "id": "src",
                        "data": {
                            "label": "Src",
                            "nodeType": "dataSource",
                            "config": {"path": "d.parquet"},
                            "description": src_desc,
                        },
                    },
                    {
                        "id": "t",
                        "data": {
                            "label": "Clean",
                            "nodeType": "polars",
                            "config": {"code": "df = df.drop_nulls()"},
                            "description": clean_desc,
                        },
                    },
                ],
                "edges": [{"id": "e1", "source": "src", "target": "t"}],
            }
        )

        def _desc(parsed, label: str) -> str:
            return next(n for n in parsed.nodes if n.data.label == label).data.description

        code1, g1 = self._save_load_cycle(graph, tmp_path)
        assert _desc(g1, "Src") == src_desc
        assert _desc(g1, "Clean") == clean_desc

        code2, g2 = self._save_load_cycle(g1, tmp_path)
        assert _desc(g2, "Src") == src_desc
        assert _desc(g2, "Clean") == clean_desc

        code3, g3 = self._save_load_cycle(g2, tmp_path)
        assert _desc(g3, "Src") == src_desc
        assert _desc(g3, "Clean") == clean_desc

        # Fixpoint: once a graph has been through one parse cycle, every
        # further save must emit byte-identical code.
        assert code3 == code2


# ---------------------------------------------------------------------------
# Remediation 5.4: parens inside string literals must not shift the
# contract-injection point (production path through _node_to_code).
# ---------------------------------------------------------------------------


def _decorator_kwargs(code: str, func_name: str) -> dict[str, ast.expr]:
    """Parse generated node code, return *func_name*'s decorator kwargs by name."""
    wrapper = f"import polars as pl\nimport haute\npipeline = haute.Pipeline('test')\n\n{code}\n"
    tree = ast.parse(wrapper)
    fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == func_name)
    dec = fn.decorator_list[0]
    assert isinstance(dec, ast.Call), f"decorator is not a call: {ast.dump(dec)}"
    return {kw.arg: kw.value for kw in dec.keywords if kw.arg is not None}


class TestParenInsideStringDecoratorKwargs:
    """5.4: user strings containing ``(`` / ``)`` in decorator kwargs must not
    corrupt the emission.  The polars builder keeps its decorator inline
    (no config-file rewrite), so ``selected_columns`` entries reach the
    contract-injection paren scanner verbatim.
    """

    def test_close_paren_smiley_in_selected_columns(self) -> None:
        """A ``)`` inside a string used to be counted as the decorator's
        closing paren, splicing the contract kwarg INTO the string."""
        node = _make_node("polars", {"code": "df = upstream", "selected_columns": [":)"]})
        code = _node_to_code(node, source_names=["upstream"])
        kwargs = _decorator_kwargs(code, "TestNode")
        assert ast.literal_eval(kwargs["selected_columns"]) == [":)"]
        assert "contract" in kwargs

    def test_balanced_parens_in_selected_columns(self) -> None:
        node = _make_node(
            "polars",
            {"code": "df = upstream", "selected_columns": ["price (gbp)"]},
        )
        code = _node_to_code(node, source_names=["upstream"])
        kwargs = _decorator_kwargs(code, "TestNode")
        assert ast.literal_eval(kwargs["selected_columns"]) == ["price (gbp)"]
        assert "contract" in kwargs

    def test_unbalanced_open_paren_in_selected_columns(self) -> None:
        """A lone ``(`` inside a string made the scanner run past the real
        closing paren into the function body."""
        node = _make_node("polars", {"code": "df = upstream", "selected_columns": ["col("]})
        code = _node_to_code(node, source_names=["upstream"])
        kwargs = _decorator_kwargs(code, "TestNode")
        assert ast.literal_eval(kwargs["selected_columns"]) == ["col("]
        assert "contract" in kwargs

    def test_open_paren_column_with_smiley_body(self) -> None:
        """The review's example: unbalanced decorator string + a body string
        containing ``:)`` used to mis-position the injection into the BODY,
        emitting unparseable code."""
        body = 'df = df.filter(pl.col("a") == ":)")'
        node = _make_node(
            "polars",
            {"code": body, "selected_columns": ["a("]},
        )
        code = _node_to_code(node, source_names=["upstream"])
        kwargs = _decorator_kwargs(code, "TestNode")
        assert ast.literal_eval(kwargs["selected_columns"]) == ["a("]
        assert "contract" in kwargs
        # The body must survive verbatim.
        assert 'df = df.filter(pl.col("a") == ":)")' in code

    def test_paren_strings_roundtrip_through_parser(self, tmp_path: Path) -> None:
        """Full cycle: the emitted file with paren-bearing strings parses and
        the selected_columns survive a save/load cycle unchanged."""
        graph = _g(
            {
                "nodes": [
                    {
                        "id": "src",
                        "data": {
                            "label": "Src",
                            "nodeType": "dataSource",
                            "config": {"path": "d.parquet"},
                        },
                    },
                    {
                        "id": "t",
                        "data": {
                            "label": "Pick",
                            "nodeType": "polars",
                            "config": {
                                "code": "df = df.drop_nulls()",
                                "selected_columns": [":)", "price (gbp)", "a("],
                            },
                        },
                    },
                ],
                "edges": [{"id": "e1", "source": "src", "target": "t"}],
            }
        )
        code = graph_to_code(graph, pipeline_name="cycle")
        ast.parse(code)
        for rel_path, content in collect_node_configs(graph).items():
            abs_path = tmp_path / rel_path
            abs_path.parent.mkdir(parents=True, exist_ok=True)
            abs_path.write_text(content)
        parsed = parse_pipeline_source(code, source_file="cycle.py", _base_dir=tmp_path)
        pick = next(n for n in parsed.nodes if n.data.label == "Pick")
        assert pick.data.config.get("selected_columns") == [":)", "price (gbp)", "a("]
