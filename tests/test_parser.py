"""Tests for haute.parser - .py pipeline file -> React Flow graph JSON."""

from __future__ import annotations

from pathlib import Path

import pytest

from haute.parser import parse_pipeline_file


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
        code = '''\
import polars as pl
import haute

pipeline = haute.Pipeline("test", description="A test pipeline")


@pipeline.data_source(path="data.parquet")
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
        code = """\
import polars as pl
import haute

pipeline = haute.Pipeline("edges_test")


@pipeline.data_source(path="data.parquet")
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
        code = """\
import polars as pl
import haute

pipeline = haute.Pipeline("implicit")


@pipeline.data_source(path="data.parquet")
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
        code = '''\
import polars as pl
import haute

pipeline = haute.Pipeline("config_test")


@pipeline.data_source(path="data/input.parquet")
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
        code = '''\
import polars as pl
import haute

pipeline = haute.Pipeline("broken", description="has syntax error")


@pipeline.data_source(path="data.parquet")
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
        code = """\
import polars as pl
import haute

pipeline = haute.Pipeline("edges_fallback")


@pipeline.data_source(path="a.parquet")
def a() -> pl.DataFrame:
    return pl.DataFrame()


@pipeline.polars
def b(a: pl.DataFrame) -> pl.DataFrame:
    return a


pipeline.connect("a", "b")

# syntax bomb below
x = {
"""
        p = _write_pipeline(tmp_path, code)
        graph = parse_pipeline_file(p)

        edge_pairs = [(e.source, e.target) for e in graph.edges]
        assert ("a", "b") in edge_pairs


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
        code = """\
import polars as pl
import haute

pipeline = haute.Pipeline("flat_test")


@pipeline.data_source(path="d.parquet")
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

    Production failure: a user writes `@pipeline.polars(path=Path("x"))`.
    ast.literal_eval cannot handle arbitrary expressions, so
    _eval_ast_literal falls back to ast.dump(). The parser must not crash.
    """

    def test_non_literal_kwarg_value(self, tmp_path):
        code = '''\
import polars as pl
import haute
from pathlib import Path

pipeline = haute.Pipeline("malformed_kw")


@pipeline.data_source(path=Path("data") / "input.parquet")
def load() -> pl.DataFrame:
    """Load with Path object."""
    return pl.DataFrame()
'''
        p = _write_pipeline(tmp_path, code)
        graph = parse_pipeline_file(p)

        # Parser must not crash. The path config will be an ast.dump string
        # instead of the real path, but the node must still exist.
        assert len(graph.nodes) == 1
        assert graph.nodes[0].id == "load"


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

        code = '''\
import polars as pl
import haute

pipeline = haute.Pipeline("roundtrip")


@pipeline.data_source(path="data.parquet")
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
        main_code = """\
import polars as pl
import haute

pipeline = haute.Pipeline("circular_main")


@pipeline.data_source(path="d.parquet")
def src() -> pl.DataFrame:
    return pl.DataFrame()


pipeline.submodel("sub_b.py")
"""
        sub_b_code = """\
import polars as pl
import haute

pipeline = haute.Pipeline("circular_b")

pipeline.submodel("test_pipeline.py")

@pipeline.data_source(path="d.parquet")
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
        code = """\
import polars as pl
import haute

pipeline = haute.Pipeline("missing_sub")


@pipeline.data_source(path="d.parquet")
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
        main_code = """\
import polars as pl
import haute

pipeline = haute.Pipeline("collision_parent")


@pipeline.data_source(path="d.parquet")
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
        main_code = """\
import polars as pl
import haute

pipeline = haute.Pipeline("empty_sub_parent")


@pipeline.data_source(path="d.parquet")
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
        main_code = """\
import polars as pl
import haute

pipeline = haute.Pipeline("syntax_err_parent")


@pipeline.data_source(path="d.parquet")
def src() -> pl.DataFrame:
    return pl.DataFrame()


pipeline.submodel("broken_sub.py")
"""
        (tmp_path / "broken_sub.py").write_text(broken_sub_code)
        p = _write_pipeline(tmp_path, main_code)
        graph = parse_pipeline_file(p)

        assert graph.pipeline_name == "syntax_err_parent"
        assert len(graph.nodes) >= 1
