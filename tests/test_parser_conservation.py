"""Structure-conservation regression tests for the pipeline parser.

Each test pins a distinct silent-loss defect where the parser dropped or
corrupted authored graph structure (a fail-loud violation). The parser must
either preserve every authored node/edge/submodel or fail loudly — never
silently degrade.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from haute._ast_helpers import _extract_preamble, _strip_docstring
from haute._code_extraction import (
    _source_load_boilerplate_end_index,
    extract_user_code,
)
from haute._graph_builders import _build_edges
from haute._parser_regex import _find_connect_calls, _find_function_blocks, fallback_parse
from haute.errors import ParseError
from haute.parser import parse_pipeline_file, parse_pipeline_source


def _write(tmp_path: Path, name: str, code: str) -> Path:
    p = tmp_path / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(textwrap.dedent(code))
    return p


# ---------------------------------------------------------------------------
# F135 — _match_source dropped the first user statement when a DataSource body
# had no recognised source-load line.
# ---------------------------------------------------------------------------


class TestSourceLoadBoilerplateConservation:
    def test_no_source_load_keeps_index_at_zero(self) -> None:
        cleaned = [
            "df = df.filter(pl.col('x') > 0)",
            "df = df.with_columns(pl.lit(1).alias('y'))",
        ]
        assert _source_load_boilerplate_end_index(cleaned) == 0

    def test_source_extractor_keeps_all_user_code(self) -> None:
        body = "df = df.filter(pl.col('x') > 0)\ndf = df.with_columns(pl.lit(1).alias('y'))"
        code = extract_user_code(body, kind="source", param_names=["df"])
        assert "df.filter" in code
        assert "with_columns" in code


# ---------------------------------------------------------------------------
# F161 — _strip_docstring swallowed the whole body when a single-line docstring
# had a trailing comment after the closing triple-quote.
# ---------------------------------------------------------------------------


class TestStripDocstringTrailingComment:
    def test_trailing_comment_after_single_line_docstring(self) -> None:
        lines = ['"""one liner"""  # trailing comment', "x = 1", "return x"]
        assert _strip_docstring(lines) == ["x = 1", "return x"]

    def test_plain_single_line_docstring_still_stripped(self) -> None:
        assert _strip_docstring(['"""doc"""', "x = 1"]) == ["x = 1"]

    def test_mixed_quote_single_line_docstring_still_stripped(self) -> None:
        assert _strip_docstring(["\"\"\"It's a '''test'''\"\"\""]) == []


# ---------------------------------------------------------------------------
# F168 — positional-only and keyword-only node parameters were dropped, losing
# their implicit edges.
# ---------------------------------------------------------------------------


class TestParameterBucketConservation:
    def test_posonly_and_kwonly_params_yield_implicit_edges(self) -> None:
        source = textwrap.dedent(
            """\
            import polars as pl
            import haute

            pipeline = haute.Pipeline("m")

            @pipeline.polars
            def a(df):
                return df

            @pipeline.polars
            def b(df):
                return df

            @pipeline.polars
            def c(df):
                return df

            @pipeline.polars
            def target(a, /, b, *, c):
                return a
            """
        )
        graph = parse_pipeline_source(source, source_file="m.py")
        pairs = {(e.source, e.target) for e in graph.edges}
        assert ("a", "target") in pairs  # positional-only
        assert ("b", "target") in pairs  # positional-or-keyword
        assert ("c", "target") in pairs  # keyword-only


# ---------------------------------------------------------------------------
# F024 / F025 — duplicate and async @pipeline node functions must fail loud.
# ---------------------------------------------------------------------------


class TestNodeFunctionConservation:
    def test_async_node_rejected(self) -> None:
        source = textwrap.dedent(
            """\
            import polars as pl
            import haute

            pipeline = haute.Pipeline("m")

            @pipeline.polars
            async def node(df):
                return df
            """
        )
        with pytest.raises(ParseError, match="async def"):
            parse_pipeline_source(source, source_file="m.py")


# ---------------------------------------------------------------------------
# F272 — ModelScore boilerplate matcher anchored on a string literal that
# merely mentioned score_from_config(, dropping the real user code.
# ---------------------------------------------------------------------------


class TestModelScoreBoilerplateAnchoring:
    def test_decoy_string_does_not_mis_anchor(self) -> None:
        body = "\n".join(
            [
                'decoy = "score_from_config("  # not a real call',
                "result = score_from_config(cfg, df)",
                'final = result.select("prediction")',
            ]
        )
        code = extract_user_code(body, kind="model_score", param_names=["df"])
        # The real call is located by AST; the pre-call decoy string is
        # boilerplate-side and the genuine post-call user code survives.
        assert 'select("prediction")' in code
        assert "decoy" not in code


# ---------------------------------------------------------------------------
# F031 — preamble over-captured the Pipeline construction under an aliased
# haute import, duplicating it on round-trip.
# ---------------------------------------------------------------------------


class TestPreambleAliasAware:
    def test_aliased_haute_import_does_not_overcapture(self) -> None:
        source = textwrap.dedent(
            """\
            import polars as pl
            import haute as ht

            CONST = 1

            pipeline = ht.Pipeline("main")

            @pipeline.polars
            def a(df):
                return df
            """
        )
        preamble = _extract_preamble(source)
        assert preamble == "CONST = 1"
        assert "Pipeline" not in preamble


# ---------------------------------------------------------------------------
# F312 — implicit param-name edges were not de-duplicated, so a duplicated
# parameter name produced two GraphEdges with identical ids.
# ---------------------------------------------------------------------------


class TestImplicitEdgeDedup:
    def test_duplicate_param_name_yields_single_edge(self) -> None:
        raw_nodes = [
            {"func_name": "a", "param_names": []},
            {"func_name": "f", "param_names": ["a", "a"]},
        ]
        edges = _build_edges(raw_nodes, [])
        ids = [e.id for e in edges]
        assert ids.count("e_a_f") == 1


# ---------------------------------------------------------------------------
# F323 / F324 — regex fallback robustness: a wrapped def signature must not
# abort the whole parse, and a backslash inside a comment must not swallow a
# following top-level connect.
# ---------------------------------------------------------------------------


class TestFallbackScanConservation:
    def test_multiline_def_signature_recovered(self) -> None:
        source = "@pipeline.polars\ndef wrapped(\n    df,\n    other,\n):\n    return df\n"
        blocks = _find_function_blocks(source)
        assert len(blocks) == 1
        assert blocks[0]["func_name"] == "wrapped"
        assert blocks[0]["param_names"] == ["df", "other"]
        assert "return df" in blocks[0]["body_text"]

    def test_async_def_in_fallback_rejected(self) -> None:
        source = "@pipeline.polars\nasync def node(df):\n    return df\n"
        with pytest.raises(ParseError, match="async def"):
            _find_function_blocks(source)

    def test_backslash_in_comment_does_not_swallow_connect(self) -> None:
        source = 'x = 1  # trailing backslash \\\npipeline.connect("a", "b")\n'
        pairs = _find_connect_calls(source)
        assert ("a", "b", None, None) in pairs


# ---------------------------------------------------------------------------
# F027 — the regex fallback silently discarded every submodel when the main
# file had any syntax error.
# ---------------------------------------------------------------------------


class TestFallbackSubmodelRecovery:
    def test_submodels_survive_syntax_error(self, tmp_path: Path) -> None:
        _write(
            tmp_path,
            "modules/scoring.py",
            """\
            import polars as pl
            import haute

            submodel = haute.Submodel("scoring")

            @submodel.polars
            def Transform(df: pl.LazyFrame) -> pl.LazyFrame:
                return df.select("x")
            """,
        )
        # A trailing syntax error forces the regex fallback path, but the
        # top-level submodel() call is still intact and recoverable.
        _write(
            tmp_path,
            "main.py",
            """\
            import polars as pl
            import haute

            pipeline = haute.Pipeline("test")

            @pipeline.polars
            def transform(df):
                return df.select("x")

            pipeline.submodel("modules/scoring.py")

            x = = 5
            """,
        )
        graph = parse_pipeline_file(tmp_path / "main.py")
        assert graph.warning is not None and "regex fallback" in graph.warning
        assert "scoring" in (graph.submodels or {})
        node_ids = {n.id for n in graph.nodes}
        assert "submodel__scoring" in node_ids

    def test_flattened_submodel_child_survives_syntax_error(self, tmp_path: Path) -> None:
        _write(
            tmp_path,
            "modules/scoring.py",
            """\
            import polars as pl
            import haute

            submodel = haute.Submodel("scoring")

            @submodel.polars
            def Transform(df: pl.LazyFrame) -> pl.LazyFrame:
                return df.select("x")
            """,
        )
        _write(
            tmp_path,
            "main.py",
            """\
            import polars as pl
            import haute

            pipeline = haute.Pipeline("test")

            @pipeline.polars
            def transform(df):
                return df.select("x")

            pipeline.submodel("modules/scoring.py")

            x = = 5
            """,
        )
        graph = parse_pipeline_file(tmp_path / "main.py", flatten=True)
        node_ids = {n.id for n in graph.nodes}
        assert "Transform" in node_ids  # child dissolved into the flat graph


# ---------------------------------------------------------------------------
# F325 — the fallback path skipped _validate_user_contract, so a drifted
# contract= annotation was not cross-checked at parse time.
# ---------------------------------------------------------------------------


class TestFallbackContractValidation:
    def test_fallback_invokes_contract_validator(self, tmp_path: Path, monkeypatch) -> None:
        calls: list[tuple] = []

        def _spy(node_type, config, user_declared, func_name):  # noqa: ANN001, ANN202
            calls.append((node_type, func_name, user_declared))

        monkeypatch.setattr("haute._parser_regex._validate_user_contract", _spy)

        source = textwrap.dedent(
            """\
            import polars as pl
            import haute

            pipeline = haute.Pipeline("m")

            @pipeline.polars(contract=Contract(inputs=("a",), outputs=("b",)))
            def node(df):
                return df

            def broken(:
                pass
            """
        )
        fallback_parse(
            source,
            str(tmp_path / "main.py"),
            SyntaxError("broken"),
        )
        assert len(calls) == 1
        assert calls[0][1] == "node"
