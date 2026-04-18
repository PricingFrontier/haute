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

import pytest

from haute.codegen import (
    _instance_to_code,
    _node_to_code,
    _sanitize_description,
    graph_to_code,
)
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
    """Unit tests for _sanitize_description."""

    def test_triple_quotes_replaced(self):
        result = _sanitize_description('Has """triple""" quotes')
        assert '"""' not in result
        assert "'''" in result

    def test_single_triple_quote(self):
        result = _sanitize_description('Ends with """')
        assert '"""' not in result

    def test_multiple_triple_quotes(self):
        result = _sanitize_description('A """ B """ C')
        assert result.count('"""') == 0
        assert result.count("'''") == 2

    def test_no_triple_quotes_unchanged(self):
        original = "Normal description"
        assert _sanitize_description(original) == original

    def test_single_double_quote_interior_unchanged(self):
        original = 'Has a " quote'
        assert _sanitize_description(original) == original

    def test_two_double_quotes_interior_unchanged(self):
        original = 'Has "" two quotes'
        assert _sanitize_description(original) == original

    def test_four_double_quotes_partially_replaced(self):
        """Four consecutive double-quotes contain one triple-quote run."""
        result = _sanitize_description('Has """" four')
        # """" is """ + " — the """ part gets replaced with '''
        assert '"""' not in result
        assert "'''" in result

    def test_single_quotes_not_affected(self):
        original = "Has '''single triple''' quotes"
        assert _sanitize_description(original) == original

    def test_trailing_backslash_handled(self):
        result = _sanitize_description("ends with backslash\\")
        # Should end with \\ (double backslash) so generated code has
        # a valid escaped backslash that doesn't escape the closing triple-quote
        assert result.endswith("\\\\")
        # Verify the result produces valid Python when embedded in a docstring
        code = f'def f():\n    """{result}"""\n    pass'
        ast.parse(code)

    def test_empty_string(self):
        assert _sanitize_description("") == ""

    def test_mixed_quotes(self):
        result = _sanitize_description('''Has " and "" and """ and '  ''')
        assert '"""' not in result

    def test_trailing_single_double_quote_escaped(self):
        """A trailing double-quote would merge with closing triple-quote."""
        result = _sanitize_description('ends with"')
        code = f'def f():\n    """{result}"""\n    pass'
        ast.parse(code)

    def test_trailing_two_double_quotes_escaped(self):
        """Two trailing double-quotes would also cause issues."""
        result = _sanitize_description('ends with""')
        code = f'def f():\n    """{result}"""\n    pass'
        ast.parse(code)

    def test_trailing_four_double_quotes(self):
        """Four trailing quotes: triple-quote replaced, one remaining escaped."""
        result = _sanitize_description("ends with" + '"' * 4)
        assert '"""' not in result
        code = f'def f():\n    """{result}"""\n    pass'
        ast.parse(code)

    def test_trailing_backslash_then_quote(self):
        r"""Trailing backslash + quote (e.g. ``some\_text\"``) must not break."""
        result = _sanitize_description('ends with\\"')
        code = f'def f():\n    """{result}"""\n    pass'
        ast.parse(code)

    def test_trailing_double_backslash_then_quote(self):
        r"""Trailing double-backslash + quote (``\\"``) must be safe."""
        result = _sanitize_description('ends with\\\\"')
        code = f'def f():\n    """{result}"""\n    pass'
        ast.parse(code)

    def test_just_a_single_double_quote(self):
        """Description that is a single double-quote."""
        result = _sanitize_description('"')
        code = f'def f():\n    """{result}"""\n    pass'
        ast.parse(code)

    def test_just_two_double_quotes(self):
        """Description that is two double-quotes."""
        result = _sanitize_description('""')
        code = f'def f():\n    """{result}"""\n    pass'
        ast.parse(code)

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
        """Every node type handles triple-quote in description safely."""
        node = _make_node(
            node_type,
            config,
            label="TestNode",
            description='Has """triple""" quotes',
        )
        code = _node_to_code(node, source_names=["upstream"])
        _compile_node_code(code)
        _ast_parse_node_code(code)
        # The literal """ should not appear unescaped in the docstring
        assert '"""Has' not in code or "'''" in code

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

    def test_default_description_uses_label(self):
        node = _make_node("polars", {"code": ""}, label="MyLabel")
        code = _node_to_code(node, source_names=["upstream"])
        assert "MyLabel node" in code
        _compile_node_code(code)

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
