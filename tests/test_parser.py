"""Tests for haute.parser - .py pipeline file -> React Flow graph JSON."""

from __future__ import annotations

from pathlib import Path

import pytest

from haute.errors import ConfigError, ParseError
from haute.parser import parse_pipeline_file
from tests.conftest import write_data_source_config

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_pipeline(tmp_path: Path, code: str) -> Path:
    """Write a pipeline .py file and return its path."""
    p = tmp_path / "test_pipeline.py"
    p.write_text(code)
    return p


# ---------------------------------------------------------------------------
# Basic parsing
# ---------------------------------------------------------------------------


class TestParsePipelineFile:
    def test_simple_pipeline(self, tmp_path):
        source_config = write_data_source_config(tmp_path, "load_data", "data.parquet")
        code = f'''\
import polars as pl
import haute

pipeline = haute.Pipeline("test", description="A test pipeline")


@pipeline.data_source(config="{source_config}")
def load_data() -> pl.DataFrame:
    """Load input data."""
    return pl.scan_parquet("data.parquet")


@pipeline.polars
def transform(load_data: pl.DataFrame) -> pl.DataFrame:
    """Transform the data."""
    return load_data


pipeline.connect("load_data", "transform")
'''
        p = _write_pipeline(tmp_path, code)
        graph = parse_pipeline_file(p)

        assert graph.pipeline_name == "test"
        assert len(graph.nodes) == 2
        assert len(graph.edges) >= 1

        # Check node types inferred correctly
        node_map = {n.id: n for n in graph.nodes}
        assert node_map["load_data"].data.nodeType == "dataSource"
        assert node_map["transform"].data.nodeType == "polars"

    def test_pipeline_name_extracted(self, tmp_path):
        code = """\
import polars as pl
import haute

pipeline = haute.Pipeline("my_pricing", description="Motor pricing")
"""
        p = _write_pipeline(tmp_path, code)
        graph = parse_pipeline_file(p)
        assert graph.pipeline_name == "my_pricing"

    def test_edges_from_connect_calls(self, tmp_path):
        source_config = write_data_source_config(tmp_path, "a", "data.parquet")
        code = f"""\
import polars as pl
import haute

pipeline = haute.Pipeline("edges_test")


@pipeline.data_source(config="{source_config}")
def a() -> pl.DataFrame:
    return pl.DataFrame()


@pipeline.polars
def b(a: pl.DataFrame) -> pl.DataFrame:
    return a


pipeline.connect("a", "b")
"""
        p = _write_pipeline(tmp_path, code)
        graph = parse_pipeline_file(p)
        edge_pairs = [(e.source, e.target) for e in graph.edges]
        assert ("a", "b") in edge_pairs

    def test_implicit_edges_from_param_names(self, tmp_path):
        source_config = write_data_source_config(tmp_path, "source", "data.parquet")
        code = f"""\
import polars as pl
import haute

pipeline = haute.Pipeline("implicit")


@pipeline.data_source(config="{source_config}")
def source() -> pl.DataFrame:
    return pl.DataFrame()


@pipeline.polars
def transform(source: pl.DataFrame) -> pl.DataFrame:
    return source
"""
        p = _write_pipeline(tmp_path, code)
        graph = parse_pipeline_file(p)
        edge_pairs = [(e.source, e.target) for e in graph.edges]
        assert ("source", "transform") in edge_pairs

    def test_node_config_extracted(self, tmp_path):
        source_config = write_data_source_config(tmp_path, "load_data", "data/input.parquet")
        code = f'''\
import polars as pl
import haute

pipeline = haute.Pipeline("config_test")


@pipeline.data_source(config="{source_config}")
def load_data() -> pl.DataFrame:
    """Read the data."""
    return pl.scan_parquet("data/input.parquet")
'''
        p = _write_pipeline(tmp_path, code)
        graph = parse_pipeline_file(p)
        node = graph.nodes[0]
        assert node.data.config["path"] == "data/input.parquet"

    def test_docstring_as_description(self, tmp_path):
        code = '''\
import polars as pl
import haute

pipeline = haute.Pipeline("doc_test")


@pipeline.polars
def my_node() -> pl.DataFrame:
    """This is the description."""
    return pl.DataFrame()
'''
        p = _write_pipeline(tmp_path, code)
        graph = parse_pipeline_file(p)
        assert graph.nodes[0].data.description == "This is the description."

    def test_explore_decorator_parses_as_explore_node(self, tmp_path):
        code = """\
import polars as pl
import haute

pipeline = haute.Pipeline("explore_test")


@pipeline.data_source(config="config/data_source/source.json")
def source() -> pl.LazyFrame:
    return pl.LazyFrame()


@pipeline.explore
def inspect_claims(source: pl.LazyFrame) -> pl.LazyFrame:
    return source


pipeline.connect("source", "inspect_claims")
"""
        cfg_dir = tmp_path / "config" / "data_source"
        cfg_dir.mkdir(parents=True)
        (cfg_dir / "source.json").write_text('{"path": "data.parquet"}')
        p = _write_pipeline(tmp_path, code)

        graph = parse_pipeline_file(p)
        node_map = {n.id: n for n in graph.nodes}

        assert node_map["inspect_claims"].data.nodeType == "explore"
        assert node_map["inspect_claims"].data.config == {}
        assert ("source", "inspect_claims") in [(e.source, e.target) for e in graph.edges]

    def test_explore_decorator_extracts_polars_code(self, tmp_path):
        code = """\
import polars as pl
import haute

pipeline = haute.Pipeline("explore_code")


@pipeline.data_source(config="config/data_source/source.json")
def source() -> pl.LazyFrame:
    return pl.LazyFrame()


@pipeline.explore
def inspect_claims(source: pl.LazyFrame) -> pl.LazyFrame:
    df = source
    df = df.filter(pl.col("premium") > 0)
    df = df.with_columns((pl.col("premium") * 2).alias("double_premium"))
    return df


pipeline.connect("source", "inspect_claims")
"""
        cfg_dir = tmp_path / "config" / "data_source"
        cfg_dir.mkdir(parents=True)
        (cfg_dir / "source.json").write_text('{"path": "data.parquet"}')
        p = _write_pipeline(tmp_path, code)

        graph = parse_pipeline_file(p)
        node_map = {n.id: n for n in graph.nodes}

        assert node_map["inspect_claims"].data.nodeType == "explore"
        assert node_map["inspect_claims"].data.config == {
            "code": (
                'df = df.filter(pl.col("premium") > 0)\n'
                'df = df.with_columns((pl.col("premium") * 2).alias("double_premium"))'
            )
        }

    def test_explore_decorator_with_overview_extracts_into_config(self, tmp_path):
        code = """\
import polars as pl
import haute

pipeline = haute.Pipeline("explore_overview")


@pipeline.data_source(config="config/data_source/source.json")
def source() -> pl.LazyFrame:
    return pl.LazyFrame()


@pipeline.explore(overview={"dataset_snapshot": True})
def inspect_claims(source: pl.LazyFrame) -> pl.LazyFrame:
    return source


pipeline.connect("source", "inspect_claims")
"""
        cfg_dir = tmp_path / "config" / "data_source"
        cfg_dir.mkdir(parents=True)
        (cfg_dir / "source.json").write_text('{"path": "data.parquet"}')
        p = _write_pipeline(tmp_path, code)

        graph = parse_pipeline_file(p)
        node_map = {n.id: n for n in graph.nodes}

        assert node_map["inspect_claims"].data.nodeType == "explore"
        assert node_map["inspect_claims"].data.config["overview"] == {"dataset_snapshot": True}

    def test_explore_decorator_with_schema_extracts_into_config(self, tmp_path):
        code = """\
import polars as pl
import haute

pipeline = haute.Pipeline("explore_overview_schema")


@pipeline.data_source(config="config/data_source/source.json")
def source() -> pl.LazyFrame:
    return pl.LazyFrame()


@pipeline.explore(overview={"schema": True})
def inspect_claims(source: pl.LazyFrame) -> pl.LazyFrame:
    return source


pipeline.connect("source", "inspect_claims")
"""
        cfg_dir = tmp_path / "config" / "data_source"
        cfg_dir.mkdir(parents=True)
        (cfg_dir / "source.json").write_text('{"path": "data.parquet"}')
        p = _write_pipeline(tmp_path, code)

        graph = parse_pipeline_file(p)
        node_map = {n.id: n for n in graph.nodes}

        assert node_map["inspect_claims"].data.nodeType == "explore"
        assert node_map["inspect_claims"].data.config["overview"] == {"schema": True}

    def test_explore_decorator_with_concise_overview_cards_extracts_into_config(self, tmp_path):
        code = """\
import polars as pl
import haute

pipeline = haute.Pipeline("explore_overview_concise")


@pipeline.data_source(config="config/data_source/source.json")
def source() -> pl.LazyFrame:
    return pl.LazyFrame()


@pipeline.explore(
    overview={
        "dataset_snapshot": True,
        "schema": True,
        "numeric_summary": True,
        "categorical_summary": True,
        "data_quality": True,
    }
)
def inspect_claims(source: pl.LazyFrame) -> pl.LazyFrame:
    return source


pipeline.connect("source", "inspect_claims")
"""
        cfg_dir = tmp_path / "config" / "data_source"
        cfg_dir.mkdir(parents=True)
        (cfg_dir / "source.json").write_text('{"path": "data.parquet"}')
        p = _write_pipeline(tmp_path, code)

        graph = parse_pipeline_file(p)
        node_map = {n.id: n for n in graph.nodes}

        assert node_map["inspect_claims"].data.nodeType == "explore"
        assert node_map["inspect_claims"].data.config["overview"] == {
            "dataset_snapshot": True,
            "schema": True,
            "numeric_summary": True,
            "categorical_summary": True,
            "data_quality": True,
        }

    def test_explore_decorator_with_empty_overview_does_not_set_config(self, tmp_path):
        code = """\
import polars as pl
import haute

pipeline = haute.Pipeline("explore_overview_empty")


@pipeline.data_source(config="config/data_source/source.json")
def source() -> pl.LazyFrame:
    return pl.LazyFrame()


@pipeline.explore(overview={})
def inspect_claims(source: pl.LazyFrame) -> pl.LazyFrame:
    return source


pipeline.connect("source", "inspect_claims")
"""
        cfg_dir = tmp_path / "config" / "data_source"
        cfg_dir.mkdir(parents=True)
        (cfg_dir / "source.json").write_text('{"path": "data.parquet"}')
        p = _write_pipeline(tmp_path, code)

        graph = parse_pipeline_file(p)
        node_map = {n.id: n for n in graph.nodes}

        assert node_map["inspect_claims"].data.nodeType == "explore"
        assert "overview" not in node_map["inspect_claims"].data.config

    @pytest.mark.parametrize(
        ("decorator_arg", "message"),
        [
            ('overview="yes"', "must be a dict"),
            ('overview={"schema": "yes"}', "must be booleans"),
            ("overview={1: True}", "keys must be strings"),
        ],
    )
    def test_explore_decorator_with_invalid_overview_fails_loudly(
        self,
        tmp_path,
        decorator_arg,
        message,
    ):
        code = f"""\
import polars as pl
import haute

pipeline = haute.Pipeline("explore_overview_invalid")


@pipeline.data_source(config="config/data_source/source.json")
def source() -> pl.LazyFrame:
    return pl.LazyFrame()


@pipeline.explore({decorator_arg})
def inspect_claims(source: pl.LazyFrame) -> pl.LazyFrame:
    return source


pipeline.connect("source", "inspect_claims")
"""
        cfg_dir = tmp_path / "config" / "data_source"
        cfg_dir.mkdir(parents=True)
        (cfg_dir / "source.json").write_text('{"path": "data.parquet"}')
        p = _write_pipeline(tmp_path, code)

        with pytest.raises(ConfigError, match=message):
            parse_pipeline_file(p)

    def test_explore_decorator_preserves_unknown_sane_overview_keys(self, tmp_path):
        code = """\
import polars as pl
import haute

pipeline = haute.Pipeline("explore_overview_unknown")


@pipeline.data_source(config="config/data_source/source.json")
def source() -> pl.LazyFrame:
    return pl.LazyFrame()


@pipeline.explore(
    overview={
        "schema": True,
        "custom_card": {
            "label": "Loss ratio",
            "columns": ["premium", "claims"],
            "enabled": False,
            "empty": None,
        },
    }
)
def inspect_claims(source: pl.LazyFrame) -> pl.LazyFrame:
    return source


pipeline.connect("source", "inspect_claims")
"""
        cfg_dir = tmp_path / "config" / "data_source"
        cfg_dir.mkdir(parents=True)
        (cfg_dir / "source.json").write_text('{"path": "data.parquet"}')
        p = _write_pipeline(tmp_path, code)

        graph = parse_pipeline_file(p)
        node_map = {n.id: n for n in graph.nodes}

        assert node_map["inspect_claims"].data.config["overview"] == {
            "schema": True,
            "custom_card": {
                "label": "Loss ratio",
                "columns": ["premium", "claims"],
                "enabled": False,
                "empty": None,
            },
        }

    def test_explore_decorator_with_outgoing_edge_raises(self, tmp_path):
        code = """\
import polars as pl
import haute

pipeline = haute.Pipeline("explore_bad")


@pipeline.data_source(config="config/data_source/source.json")
def source() -> pl.LazyFrame:
    return pl.LazyFrame()


@pipeline.explore
def inspect_claims(source: pl.LazyFrame) -> pl.LazyFrame:
    return source


@pipeline.polars
def downstream(inspect_claims: pl.LazyFrame) -> pl.LazyFrame:
    return inspect_claims


pipeline.connect("source", "inspect_claims")
pipeline.connect("inspect_claims", "downstream")
"""
        cfg_dir = tmp_path / "config" / "data_source"
        cfg_dir.mkdir(parents=True)
        (cfg_dir / "source.json").write_text('{"path": "data.parquet"}')
        p = _write_pipeline(tmp_path, code)

        with pytest.raises(ParseError, match="cannot have outgoing edges"):
            parse_pipeline_file(p)

    def test_empty_file_returns_empty_graph(self, tmp_path):
        p = _write_pipeline(tmp_path, "")
        graph = parse_pipeline_file(p)
        assert graph.nodes == []

    def test_preamble_extracted(self, tmp_path):
        code = """\
import polars as pl
import haute

from pathlib import Path

DATA_DIR = Path("data")

pipeline = haute.Pipeline("preamble_test")


@pipeline.polars
def src() -> pl.DataFrame:
    return pl.DataFrame()
"""
        p = _write_pipeline(tmp_path, code)
        graph = parse_pipeline_file(p)
        preamble = graph.preamble or ""
        assert "DATA_DIR" in preamble


# ---------------------------------------------------------------------------
# Gap-coverage tests
# ---------------------------------------------------------------------------


class TestRegexFallbackPath:
    """When ast.parse() fails, the regex fallback must still extract nodes.

    Production failure: a user saves a half-edited file (e.g. unclosed
    parenthesis). Without the regex path the GUI would show zero nodes,
    losing all visual feedback.
    """

    def test_syntax_error_triggers_regex_fallback(self, tmp_path):
        """A file with a syntax error should still parse nodes via regex."""
        source_config = write_data_source_config(tmp_path, "load_data", "data.parquet")
        code = f'''\
import polars as pl
import haute

pipeline = haute.Pipeline("broken", description="has syntax error")


@pipeline.data_source(config="{source_config}")
def load_data() -> pl.DataFrame:
    """Load data."""
    return pl.scan_parquet("data.parquet")


@pipeline.polars
def transform(load_data: pl.DataFrame) -> pl.DataFrame:
    """Transform."""
    return load_data.with_columns(
'''  # <-- unclosed paren = SyntaxError
        p = _write_pipeline(tmp_path, code)
        graph = parse_pipeline_file(p)

        # Regex fallback must still find the first (valid) node
        assert len(graph.nodes) >= 1
        assert graph.pipeline_name == "broken"
        assert graph.warning is not None
        assert "syntax error" in graph.warning.lower()

    def test_regex_fallback_extracts_connect_calls(self, tmp_path):
        """Regex fallback should still wire edges from pipeline.connect()."""
        source_config = write_data_source_config(tmp_path, "a", "a.parquet")
        code = f"""\
import polars as pl
import haute

pipeline = haute.Pipeline("edges_fallback")


@pipeline.data_source(config="{source_config}")
def a() -> pl.DataFrame:
    return pl.DataFrame()


@pipeline.polars
def b(a: pl.DataFrame) -> pl.DataFrame:
    return a


pipeline.connect("a", "b")

# syntax bomb below
x = {{
"""
        p = _write_pipeline(tmp_path, code)
        graph = parse_pipeline_file(p)

        edge_pairs = [(e.source, e.target) for e in graph.edges]
        assert ("a", "b") in edge_pairs

    def test_regex_fallback_keeps_multi_arg_connect_calls(self, tmp_path):
        """Remediation 5.7: the old fallback regex required ``connect("a", "b")``
        with the closing paren immediately after the second string, silently
        dropping every ``source_port=`` / ``target_port=`` form codegen emits —
        losing edges exactly when the user needs recovery."""
        source_config = write_data_source_config(tmp_path, "a", "a.parquet")
        code = f"""\
import polars as pl
import haute

pipeline = haute.Pipeline("edges_fallback_ports")


@pipeline.data_source(config="{source_config}")
def a() -> pl.DataFrame:
    return pl.DataFrame()


@pipeline.polars
def b(df: pl.DataFrame) -> pl.DataFrame:
    return df


pipeline.connect("a", "b", target_port="base")

# syntax bomb below
x = {{
"""
        p = _write_pipeline(tmp_path, code)
        graph = parse_pipeline_file(p)

        assert graph.warning is not None
        edges = {(e.source, e.target): e for e in graph.edges}
        assert ("a", "b") in edges
        assert edges[("a", "b")].targetHandle == "base"


class TestSubmodelFileParsing:
    """parse_submodel_file is a public API but had no direct test.

    Production failure: submodel import fails silently, merged graph
    is missing sub-pipeline nodes.
    """

    def test_parse_submodel_file_returns_graph(self, tmp_path):
        from haute.parser import parse_submodel_file

        code = '''\
import polars as pl
import haute

submodel = haute.Submodel("pricing_sub", description="sub-pipeline")


@submodel.polars
def step_a() -> pl.DataFrame:
    """First step."""
    return pl.DataFrame()


@submodel.polars
def step_b(step_a: pl.DataFrame) -> pl.DataFrame:
    """Second step."""
    return step_a
'''
        p = tmp_path / "sub.py"
        p.write_text(code)
        graph = parse_submodel_file(p)

        assert graph.pipeline_name == "pricing_sub"
        assert len(graph.nodes) == 2
        node_ids = {n.id for n in graph.nodes}
        assert "step_a" in node_ids
        assert "step_b" in node_ids


class TestFlattenParameter:
    """flatten=True dissolves submodel groupings into a flat graph.

    Production failure: executor receives a graph with collapsed submodel
    nodes it cannot execute. flatten=True is used to expand them.
    """

    def test_flatten_true_accepted(self, tmp_path):
        """parse_pipeline_file(flatten=True) must not raise."""
        source_config = write_data_source_config(tmp_path, "src", "d.parquet")
        code = f"""\
import polars as pl
import haute

pipeline = haute.Pipeline("flat_test")


@pipeline.data_source(config="{source_config}")
def src() -> pl.DataFrame:
    return pl.DataFrame()
"""
        p = _write_pipeline(tmp_path, code)
        graph = parse_pipeline_file(p, flatten=True)
        assert graph.pipeline_name == "flat_test"
        assert len(graph.nodes) == 1

    def test_flatten_default_is_false(self, tmp_path):
        """Default flatten=False should not alter simple pipeline."""
        code = """\
import polars as pl
import haute

pipeline = haute.Pipeline("noflat")


@pipeline.polars
def node_a() -> pl.DataFrame:
    return pl.DataFrame()
"""
        p = _write_pipeline(tmp_path, code)
        graph = parse_pipeline_file(p)
        assert len(graph.nodes) == 1


class TestDecoratorsWithoutPipelineConstructor:
    """File has @pipeline.X decorators but no `pipeline = haute.Pipeline(...)`.

    Production failure: codegen template might omit the Pipeline() call.
    Parser should still extract nodes, defaulting the pipeline name.
    """

    def test_no_pipeline_constructor_still_parses_nodes(self, tmp_path):
        code = '''\
import polars as pl
import haute

pipeline = object()  # not haute.Pipeline(...)


@pipeline.polars
def my_step() -> pl.DataFrame:
    """A step."""
    return pl.DataFrame()
'''
        p = _write_pipeline(tmp_path, code)
        graph = parse_pipeline_file(p)

        # The decorator checker looks for @pipeline.<type> on FunctionDefs.
        # Even without a proper Pipeline() constructor, nodes should parse.
        assert len(graph.nodes) == 1
        assert graph.nodes[0].id == "my_step"
        # Pipeline name defaults to "main" when no haute.Pipeline() found.
        assert graph.pipeline_name == "main"


class TestMissingConfigJsonFile:
    """Decorator references config="config/foo.json" that doesn't exist.

    Post Item #18: a missing config file raises ``ConfigError`` with the
    original path so the user learns about the broken reference loudly
    rather than getting a silently empty-config node.
    """

    def test_missing_config_file_raises_config_error(self, tmp_path):
        from haute.errors import ConfigError

        code = '''\
import polars as pl
import haute

pipeline = haute.Pipeline("cfg_test")


@pipeline.polars(config="config/nonexistent_node.json")
def broken_ref() -> pl.DataFrame:
    """Node with missing config file."""
    return pl.DataFrame()
'''
        p = _write_pipeline(tmp_path, code)
        with pytest.raises(ConfigError):
            parse_pipeline_file(p)


class TestMalformedDecoratorKwargs:
    """Non-string / complex values in decorator kwargs.

    Production failure (remediation 5.5): a user hand-edits a decorator to
    carry a computed value, e.g. `@pipeline.polars(path=Path("x"))`.
    ast.literal_eval cannot evaluate it, and the old fallback stored the
    ``ast.dump(...)`` repr — `Call(func=Name(...))` — in the node config.
    Codegen then re-emitted that repr as the kwarg value on the next save,
    corrupting the decorator.  Machine-emitted decorators are always
    literals, so the only vector is hand-edits; the parser must reject
    them loudly, naming the kwarg, before garbage can reach the config.
    """

    def test_non_literal_kwarg_value_rejected_loudly(self, tmp_path):
        code = '''\
import polars as pl
import haute
from pathlib import Path

pipeline = haute.Pipeline("malformed_kw")


@pipeline.polars(selected_columns=[Path("data") / "input.parquet"])
def transform(df: pl.LazyFrame) -> pl.LazyFrame:
    """Transform with a non-literal kwarg."""
    return df
'''
        p = _write_pipeline(tmp_path, code)
        with pytest.raises(ParseError, match="selected_columns") as excinfo:
            parse_pipeline_file(p)
        # The corrupt AST repr must never appear anywhere, including errors.
        assert "Call(func=" not in str(excinfo.value)

    def test_name_reference_kwarg_rejected_loudly(self, tmp_path):
        code = '''\
import polars as pl
import haute

COLS = ["a", "b"]

pipeline = haute.Pipeline("name_ref_kw")


@pipeline.polars(selected_columns=COLS)
def transform(df: pl.LazyFrame) -> pl.LazyFrame:
    """Hand-edited to reference a module-level constant."""
    return df
'''
        p = _write_pipeline(tmp_path, code)
        with pytest.raises(ParseError, match="selected_columns"):
            parse_pipeline_file(p)


class TestFunctionNameCollision:
    """Two functions with the same name produce duplicate node IDs.

    Production failure: the graph dict uses func_name as node.id.
    If two functions share a name (e.g. after a copy-paste mistake),
    the second silently overwrites the first when the frontend builds
    its node map. This test documents the current behavior.
    """

    def test_duplicate_function_names_both_appear(self, tmp_path):
        # Python itself allows redefining a function; ast.parse succeeds.
        code = '''\
import polars as pl
import haute

pipeline = haute.Pipeline("collision")


@pipeline.polars
def step() -> pl.DataFrame:
    """First definition."""
    return pl.DataFrame()


@pipeline.polars
def step() -> pl.DataFrame:
    """Second definition (same name)."""
    return pl.DataFrame()
'''
        p = _write_pipeline(tmp_path, code)
        graph = parse_pipeline_file(p)

        # Both decorated functions are extracted as raw_nodes.
        # This means two GraphNodes share the same id="step".
        ids = [n.id for n in graph.nodes]
        assert ids.count("step") == 2, (
            "Duplicate function names should produce two nodes (collision). "
            "If the parser de-duplicates, update this test."
        )


class TestStripDocstringMixedQuotes:
    """_strip_docstring with nested quote styles could terminate early.

    Production failure: a docstring like  \"\"\"It's a '''test'''\"\"\"
    contains triple single-quotes inside triple double-quotes. The
    naive check `if ''' in stripped` would match the inner quotes and
    prematurely end the docstring, leaking docstring text into the
    function body / user code.
    """

    def test_mixed_quote_docstring_fully_stripped(self):
        from haute._parser_helpers import _strip_docstring

        lines = [
            "    \"\"\"It's a '''test'''\"\"\"",
        ]
        result = _strip_docstring(lines)
        # The single-line docstring should be fully consumed.
        assert result == [], "Single-line docstring with mixed quotes should be fully stripped"

    def test_multiline_mixed_quote_docstring(self):
        """_strip_docstring now tracks opening_quote style, so inner ''' no longer
        causes early termination of a \"\"\" docstring.
        """
        from haute._parser_helpers import _strip_docstring

        lines = [
            '    """',
            "    It's a '''test''' inside.",
            '    """',
            "    return df",
        ]
        result = _strip_docstring(lines)

        # Bug is fixed: docstring only closes with matching quote style
        assert len(result) == 1
        assert "return df" in result[0]


class TestPreambleExtractionEdgeCases:
    """A preamble line starting with @pipeline. prematurely ends extraction.

    Production failure: user defines a variable like
    `@pipeline.polars` early (before the real Pipeline constructor),
    or a comment mentions `@pipeline.polars`. The preamble extraction
    should only stop at actual known decorator types.
    """

    def test_preamble_stops_at_real_decorator_not_comment(self, tmp_path):
        code = """\
import polars as pl
import haute

# Example usage: @pipeline.polars
THRESHOLD = 0.5

pipeline = haute.Pipeline("preamble_edge")


@pipeline.polars
def node() -> pl.DataFrame:
    return pl.DataFrame()
"""
        p = _write_pipeline(tmp_path, code)
        graph = parse_pipeline_file(p)
        preamble = graph.preamble or ""
        # The comment mentions @pipeline.polars but starts with #,
        # so it should be included in preamble (it's not a decorator).
        assert "THRESHOLD" in preamble

    def test_preamble_with_unknown_decorator_attr(self, tmp_path):
        """@pipeline.custom_thing is not a known type, preamble continues."""
        code = """\
import polars as pl
import haute

from pathlib import Path

@pipeline.custom_thing
def helper():
    pass

pipeline = haute.Pipeline("unknown_dec")


@pipeline.polars
def node() -> pl.DataFrame:
    return pl.DataFrame()
"""
        p = _write_pipeline(tmp_path, code)
        graph = parse_pipeline_file(p)
        preamble = graph.preamble or ""
        # @pipeline.custom_thing is not in DECORATOR_TO_NODE_TYPE, so
        # the preamble extraction should NOT stop there.
        assert "from pathlib import Path" in preamble


class TestPreservedBlockUnmatchedMarker:
    """A # haute:preserve-start with no matching end marker.

    Production failure: user deletes the end marker by accident. The
    parser should silently ignore the unmatched start marker rather
    than capturing the entire rest of the file or crashing.
    """

    def test_unmatched_preserve_start_is_ignored(self, tmp_path):
        code = """\
import polars as pl
import haute

pipeline = haute.Pipeline("preserve_test")


@pipeline.polars
def node() -> pl.DataFrame:
    return pl.DataFrame()


# haute:preserve-start
LEAKED_CONSTANT = 42
# no matching end marker!
"""
        p = _write_pipeline(tmp_path, code)
        graph = parse_pipeline_file(p)

        # Unmatched start marker should produce zero preserved blocks
        assert graph.preserved_blocks == []

    def test_matched_preserve_block_extracted(self, tmp_path):
        code = """\
import polars as pl
import haute

pipeline = haute.Pipeline("preserve_ok")


@pipeline.polars
def node() -> pl.DataFrame:
    return pl.DataFrame()


# haute:preserve-start
KEEP_ME = True
# haute:preserve-end
"""
        p = _write_pipeline(tmp_path, code)
        graph = parse_pipeline_file(p)

        assert len(graph.preserved_blocks) == 1
        assert "KEEP_ME" in graph.preserved_blocks[0]


class TestParsePipelineRoundtrip:
    """Test that parse -> codegen -> parse produces consistent results."""

    def test_roundtrip_preserves_structure(self, tmp_path):
        import json

        from haute.codegen import graph_to_code

        source_config = write_data_source_config(tmp_path, "source", "data.parquet")
        code = f'''\
import polars as pl
import haute

pipeline = haute.Pipeline("roundtrip")


@pipeline.data_source(config="{source_config}")
def source() -> pl.DataFrame:
    """Load data."""
    return pl.scan_parquet("data.parquet")


@pipeline.polars
def transform(source: pl.DataFrame) -> pl.DataFrame:
    """Transform."""
    return source


pipeline.connect("source", "transform")
'''
        p = _write_pipeline(tmp_path, code)
        graph1 = parse_pipeline_file(p)

        # Codegen emits @pipeline.data_source(config="config/data_source/source.json")
        # for data-source nodes.  Write the sidecar JSON so the reparse path
        # resolves the config fail-loudly contract (Item #18).
        cfg_dir = tmp_path / "config" / "data_source"
        cfg_dir.mkdir(parents=True, exist_ok=True)
        (cfg_dir / "source.json").write_text(
            json.dumps({"path": "data.parquet", "sourceType": "flat_file"})
        )

        generated = graph_to_code(graph1, pipeline_name="roundtrip")
        p2 = tmp_path / "roundtrip2.py"
        p2.write_text(generated)
        graph2 = parse_pipeline_file(p2)

        assert len(graph1.nodes) == len(graph2.nodes)
        assert len(graph1.edges) == len(graph2.edges)

        names1 = {n.id for n in graph1.nodes}
        names2 = {n.id for n in graph2.nodes}
        assert names1 == names2


# ---------------------------------------------------------------------------
# Submodel edge-case tests
# ---------------------------------------------------------------------------


class TestCircularSubmodelReferences:
    def test_circular_submodel_refs_terminate(self, tmp_path):
        main_src_config = write_data_source_config(tmp_path, "src", "d.parquet")
        sub_src_config = write_data_source_config(tmp_path, "b_node", "d.parquet")
        main_code = f"""\
import polars as pl
import haute

pipeline = haute.Pipeline("circular_main")


@pipeline.data_source(config="{main_src_config}")
def src() -> pl.DataFrame:
    return pl.DataFrame()


pipeline.submodel("sub_b.py")
"""
        sub_b_code = f"""\
import polars as pl
import haute

pipeline = haute.Pipeline("circular_b")

pipeline.submodel("test_pipeline.py")

@pipeline.data_source(config="{sub_src_config}")
def b_node() -> pl.DataFrame:
    return pl.DataFrame()
"""
        (tmp_path / "test_pipeline.py").write_text(main_code)
        (tmp_path / "sub_b.py").write_text(sub_b_code)

        graph = parse_pipeline_file(tmp_path / "test_pipeline.py")

        assert graph.pipeline_name == "circular_main"
        assert len(graph.nodes) >= 1


class TestNonExistentSubmodelFilePath:
    def test_nonexistent_submodel_skipped(self, tmp_path):
        source_config = write_data_source_config(tmp_path, "src", "d.parquet")
        code = f"""\
import polars as pl
import haute

pipeline = haute.Pipeline("missing_sub")


@pipeline.data_source(config="{source_config}")
def src() -> pl.DataFrame:
    return pl.DataFrame()


pipeline.submodel("nonexistent.py")
"""
        p = _write_pipeline(tmp_path, code)
        graph = parse_pipeline_file(p)

        assert graph.pipeline_name == "missing_sub"
        assert len(graph.nodes) == 1
        assert graph.nodes[0].id == "src"


class TestFileWithUtf8Bom:
    def test_bom_prefix_parses_correctly(self, tmp_path):
        code = '''\
import polars as pl
import haute

pipeline = haute.Pipeline("bom_test")


@pipeline.polars
def node_a() -> pl.DataFrame:
    """A node."""
    return pl.DataFrame()
'''
        p = tmp_path / "bom_pipeline.py"
        p.write_bytes(b"\xef\xbb\xbf" + code.encode("utf-8"))
        graph = parse_pipeline_file(p)

        assert graph.pipeline_name == "bom_test"
        assert len(graph.nodes) == 1
        assert graph.nodes[0].id == "node_a"


class TestSubmodelNameCollision:
    def test_same_submodel_name_is_deterministic(self, tmp_path):
        sub_a_code = """\
import polars as pl
import haute

submodel = haute.Submodel("shared_name", description="first")


@submodel.polars
def step_from_a() -> pl.DataFrame:
    return pl.DataFrame()
"""
        sub_b_code = """\
import polars as pl
import haute

submodel = haute.Submodel("shared_name", description="second")


@submodel.polars
def step_from_b() -> pl.DataFrame:
    return pl.DataFrame()
"""
        source_config = write_data_source_config(tmp_path, "src", "d.parquet")
        main_code = f"""\
import polars as pl
import haute

pipeline = haute.Pipeline("collision_parent")


@pipeline.data_source(config="{source_config}")
def src() -> pl.DataFrame:
    return pl.DataFrame()


pipeline.submodel("sub_a.py")
pipeline.submodel("sub_b.py")
"""
        (tmp_path / "sub_a.py").write_text(sub_a_code)
        (tmp_path / "sub_b.py").write_text(sub_b_code)
        p = _write_pipeline(tmp_path, main_code)
        graph = parse_pipeline_file(p)

        assert graph.pipeline_name == "collision_parent"
        assert len(graph.nodes) >= 1


class TestEmptySubmodelFile:
    def test_empty_submodel_handled_gracefully(self, tmp_path):
        source_config = write_data_source_config(tmp_path, "src", "d.parquet")
        main_code = f"""\
import polars as pl
import haute

pipeline = haute.Pipeline("empty_sub_parent")


@pipeline.data_source(config="{source_config}")
def src() -> pl.DataFrame:
    return pl.DataFrame()


pipeline.submodel("empty_sub.py")
"""
        (tmp_path / "empty_sub.py").write_text("")
        p = _write_pipeline(tmp_path, main_code)
        graph = parse_pipeline_file(p)

        assert graph.pipeline_name == "empty_sub_parent"
        assert len(graph.nodes) >= 1
        assert graph.nodes[0].id == "src"


class TestSubmodelOnlyParentPipeline:
    def test_parent_with_only_submodel_call_still_builds_hierarchical_graph(self, tmp_path):
        child_source_config = write_data_source_config(tmp_path, "raw_rows", "data/in.parquet")
        child_code = f"""\
import polars as pl
import haute

submodel = haute.Submodel("scoring")


@submodel.data_source(config="{child_source_config}")
def raw_rows() -> pl.LazyFrame:
    return pl.scan_parquet("data/in.parquet")


@submodel.polars
def enriched(raw_rows: pl.LazyFrame) -> pl.LazyFrame:
    return raw_rows.with_columns(pl.lit(1).alias("x"))


submodel.connect("raw_rows", "enriched")
"""
        main_code = """\
import haute

pipeline = haute.Pipeline("submodel_only")

pipeline.submodel("modules/scoring.py")
"""
        (tmp_path / "modules").mkdir()
        (tmp_path / "modules" / "scoring.py").write_text(child_code)
        p = _write_pipeline(tmp_path, main_code)

        graph = parse_pipeline_file(p)

        assert graph.pipeline_name == "submodel_only"
        assert {n.id for n in graph.nodes} == {"submodel__scoring"}
        assert graph.submodels is not None
        assert "scoring" in graph.submodels
        assert graph.submodels["scoring"]["file"] == "modules/scoring.py"


class TestSubmodelFileWithSyntaxError:
    def test_syntax_error_submodel_no_crash(self, tmp_path):
        broken_sub_code = """\
import polars as pl
import haute

submodel = haute.Submodel("broken_sub")


@submodel.polars
def broken_step() -> pl.DataFrame:
    return pl.DataFrame(
"""
        source_config = write_data_source_config(tmp_path, "src", "d.parquet")
        main_code = f"""\
import polars as pl
import haute

pipeline = haute.Pipeline("syntax_err_parent")


@pipeline.data_source(config="{source_config}")
def src() -> pl.DataFrame:
    return pl.DataFrame()


pipeline.submodel("broken_sub.py")
"""
        (tmp_path / "broken_sub.py").write_text(broken_sub_code)
        p = _write_pipeline(tmp_path, main_code)
        graph = parse_pipeline_file(p)

        assert graph.pipeline_name == "syntax_err_parent"
        assert len(graph.nodes) >= 1
