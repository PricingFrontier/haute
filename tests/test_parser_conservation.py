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
from haute._parser_regex import (
    _find_connect_calls,
    _find_function_blocks,
    _recover_submodel_registrations,
)
from haute._pipeline_recovery import load_pipeline_editor_document
from haute._submodel_instances import qualified_runtime_node_id
from haute.codegen import graph_to_code_multi
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
# _strip_docstring textually mis-counted quotes when a single-line docstring
# ended with an escaped quote directly before the closing triple-quote
# (content ``...\"`` renders as ``\""""`` = four quotes). The naive
# ``find('"""')`` locked onto the wrong triple-quote, saw a stray ``"`` after
# it, decided the docstring was multi-line, and swallowed the whole function
# body. A transform/data-source node whose description ended in ``"`` therefore
# round-tripped to EMPTY user code — silently dropping the user's combine
# logic (and, for a multi-source polars transform, tripping the W2 fail-loud
# guard on the second codegen pass).
# ---------------------------------------------------------------------------


class TestStripDocstringEscapedTrailingQuote:
    def test_escaped_quote_before_closing_triple_quote(self) -> None:
        # Source line: """C5 chain quote ' and double \"""" then user code.
        lines = [
            '"""C5 chain quote \' and double \\""""',
            "df = df.with_columns(pl.lit('').alias('note'))",
            "return df",
        ]
        assert _strip_docstring(lines) == [
            "df = df.with_columns(pl.lit('').alias('note'))",
            "return df",
        ]

    def test_indented_escaped_quote_docstring_preserves_body(self) -> None:
        lines = [
            '    """doc ending in a quote \\""""',
            "    df = source",
            "    return df",
        ]
        assert _strip_docstring(lines) == ["    df = source", "    return df"]

    def test_multiline_docstring_ending_in_escaped_quote(self) -> None:
        lines = [
            '"""first line',
            'second line ends in quote \\""""',
            "df = source",
            "return df",
        ]
        assert _strip_docstring(lines) == ["df = source", "return df"]


# ---------------------------------------------------------------------------
# F168 — positional-only and keyword-only node parameters were dropped, losing
# their implicit edges.
# ---------------------------------------------------------------------------


class TestParameterBucketConservation:
    def test_only_positional_params_yield_implicit_edges(self) -> None:
        source = textwrap.dedent(
            """\
            import polars as pl
            import haute

            pipeline = haute.Pipeline("m")

            @pipeline.polars
            def a():
                return pl.LazyFrame()

            @pipeline.polars
            def b():
                return pl.LazyFrame()

            @pipeline.polars
            def c():
                return pl.LazyFrame()

            @pipeline.polars
            def target(a, /, b, *, c):
                return a
            """
        )
        graph = parse_pipeline_source(source, source_file="m.py")
        pairs = {(e.source, e.target) for e in graph.edges}
        assert ("a", "target") in pairs  # positional-only
        assert ("b", "target") in pairs  # positional-or-keyword
        assert ("c", "target") not in pairs  # keyword-only configuration

    def test_live_switch_inputs_exclude_keyword_only_config(
        self,
        tmp_path: Path,
    ) -> None:
        config_dir = tmp_path / "config" / "source_switch"
        config_dir.mkdir(parents=True)
        (config_dir / "switch.json").write_text(
            '{"input_scenario_map":{"a":"live"},"inputs":["a","c"]}',
            encoding="utf-8",
        )
        pipeline_file = _write(
            tmp_path,
            "main.py",
            textwrap.dedent(
                """\
                import haute

                pipeline = haute.Pipeline("m")

                @pipeline.polars
                def a():
                    return None

                @pipeline.polars
                def c():
                    return None

                @pipeline.live_switch(config="config/source_switch/switch.json")
                def switch(a, *, c=None):
                    return a
                """
            ),
        )

        graph = parse_pipeline_file(pipeline_file)
        switch = next(node for node in graph.nodes if node.id == "switch")
        assert switch.data.config["inputs"] == ["a"]
        assert {(edge.source, edge.target) for edge in graph.edges} == {
            ("a", "switch"),
        }


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

    def test_from_import_alias_and_multiline_constructor_use_ast_boundaries(self) -> None:
        source = textwrap.dedent(
            """\
            import polars as pl
            from haute import Pipeline as BuildPipeline

            CONST = 1

            pipeline = BuildPipeline(
                "main",
                description="multiline",
            )

            @pipeline.polars
            def a(df):
                return df
            """
        )

        preamble = _extract_preamble(source)

        assert preamble == "CONST = 1"
        assert "BuildPipeline" not in preamble
        assert "pipeline =" not in preamble

    def test_from_import_alias_uses_same_boundary_in_syntax_fallback(self) -> None:
        source = textwrap.dedent(
            """\
            import polars as pl
            from haute import Pipeline as BuildPipeline

            CONST = 1

            pipeline = BuildPipeline(
                "main",
                description="multiline",
            )

            @pipeline.polars
            def broken(:
                pass
            """
        )

        preamble = _extract_preamble(source)

        assert preamble == "CONST = 1"


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
# F027 — editor recovery conserves submodels when the main file has a syntax error.
# ---------------------------------------------------------------------------


class TestSyntaxRecoverySubmodels:
    def test_submodels_survive_syntax_error(self, tmp_path: Path) -> None:
        _write(
            tmp_path,
            "modules/scoring.py",
            """\
            import polars as pl
            import haute

            submodel = haute.Submodel(
                "scoring",
                definition_id="scoring",
                input_ports=[],
                output_ports=[],
            )

            @submodel.polars
            def Transform(df: pl.LazyFrame) -> pl.LazyFrame:
                return df.select("x")
            """,
        )
        # A trailing syntax error forces editor recovery, but the
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

            pipeline.submodel(
                "modules/scoring.py",
                definition_id="scoring",
                instance_id="submodel__scoring",
                alias="scoring",
            )

            x = = 5
            """,
        )
        with pytest.raises(ParseError, match="syntax"):
            parse_pipeline_file(tmp_path / "main.py")
        document = load_pipeline_editor_document(
            tmp_path / "main.py",
            project_root=tmp_path,
        )
        assert "scoring" in (document.submodels or {})
        node_ids = {node.authored_id for node in document.nodes}
        assert "submodel__scoring" in node_ids

    def test_recovered_submodel_child_survives_syntax_error(self, tmp_path: Path) -> None:
        _write(
            tmp_path,
            "modules/scoring.py",
            """\
            import polars as pl
            import haute

            submodel = haute.Submodel(
                "scoring",
                definition_id="scoring",
                input_ports=[],
                output_ports=[],
            )

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

            pipeline.submodel(
                "modules/scoring.py",
                definition_id="scoring",
                instance_id="submodel__scoring",
                alias="scoring",
            )

            x = = 5
            """,
        )
        document = load_pipeline_editor_document(
            tmp_path / "main.py",
            project_root=tmp_path,
        )
        assert document.submodels is not None
        child = document.submodels["scoring"]
        assert child.availability == "ready"
        assert {node.authored_id for node in child.graph.nodes} == {"Transform"}

    def test_unrecoverable_paths_are_reported_together(self) -> None:
        source = textwrap.dedent(
            """\
            pipeline.submodel(SCORING_PATH)
            pipeline.submodel(path=OTHER_PATH)
            """
        )

        with pytest.raises(ParseError, match="submodel reference") as exc_info:
            _recover_submodel_registrations(source)

        assert exc_info.value.context["unrecoverable_references"] == [
            {"line": 1, "source": "pipeline.submodel(SCORING_PATH)"},
            {"line": 2, "source": "pipeline.submodel(path=OTHER_PATH)"},
        ]

    def test_unclosed_chained_reference_uses_the_grouped_diagnostic(self) -> None:
        source = 'pipeline.submodel("scoring.py").submodel(\n'

        with pytest.raises(ParseError, match="submodel reference") as exc_info:
            _recover_submodel_registrations(source)

        assert exc_info.value.context["unrecoverable_references"] == [
            {
                "line": 1,
                "source": 'pipeline.submodel("scoring.py").submodel(',
            }
        ]


class TestSubmodelResolutionRoot:
    def test_in_memory_submodel_parse_without_base_fails_loudly(self) -> None:
        source = textwrap.dedent(
            """\
            import haute

            pipeline = haute.Pipeline("main")
            pipeline.submodel(
                "modules/scoring.py",
                definition_id="scoring",
                instance_id="submodel__scoring",
                alias="scoring",
            )
            """
        )

        with pytest.raises(ParseError, match="source/base directory") as exc_info:
            parse_pipeline_source(source)

        assert exc_info.value.context["unresolved_paths"] == [
            "modules/scoring.py",
        ]


class TestGraphStructureConservationGate:
    def test_duplicate_connect_uses_specific_diagnostic(self) -> None:
        source = textwrap.dedent(
            """\
            import haute

            pipeline = haute.Pipeline("duplicates")

            @pipeline.polars
            def source():
                return None

            @pipeline.polars
            def sink(source):
                return source

            pipeline.connect("source", "sink", source_port="quotes")
            pipeline.connect("source", "sink", source_port="quotes")
            """
        )

        with pytest.raises(ParseError, match="duplicate edge identities") as exc_info:
            parse_pipeline_source(source, source_file="duplicates.py")

        assert exc_info.value.context["duplicate_edges"] == [
            {
                "source": "source",
                "target": "sink",
                "source_handle": "quotes",
                "target_handle": None,
            }
        ]

    def test_canonical_boundary_edges_survive_hierarchical_and_flattened_views(
        self,
        tmp_path: Path,
    ) -> None:
        child = _write(
            tmp_path,
            "child.py",
            """
            import polars as pl
            import haute

            submodel = haute.Submodel(
                "child",
                definition_id="child",
                input_ports=[
                    {
                        "name": "source",
                        "targets": [{"nodeId": "transform", "handleId": None}],
                    }
                ],
                output_ports=[
                    {
                        "name": "result",
                        "source": {"nodeId": "transform", "handleId": None},
                    }
                ],
            )

            @submodel.polars
            def transform(source: pl.LazyFrame) -> pl.LazyFrame:
                return source
            """,
        )
        parent = _write(
            tmp_path,
            "main.py",
            f"""
            import polars as pl
            import haute

            pipeline = haute.Pipeline("main")

            @pipeline.polars
            def source() -> pl.LazyFrame:
                return pl.LazyFrame({{"x": [1]}})

            @pipeline.polars
            def sink(child: pl.LazyFrame) -> pl.LazyFrame:
                return child

            pipeline.submodel(
                {child.name!r},
                definition_id="child",
                instance_id="submodel__child",
                alias="child",
            )
            pipeline.connect("source", "child", target_port="source")
            pipeline.connect("child", "sink", source_port="result")
            """,
        )

        hierarchical = parse_pipeline_file(parent)
        assert any(
            edge.source == "source"
            and edge.target == "submodel__child"
            and edge.targetHandle == "in__source"
            for edge in hierarchical.edges
        )
        assert any(
            edge.source == "submodel__child"
            and edge.target == "sink"
            and edge.sourceHandle == "out__result"
            for edge in hierarchical.edges
        )

        flattened = parse_pipeline_file(parent, flatten=True)
        runtime_transform = qualified_runtime_node_id("submodel__child", "transform")
        assert any(
            edge.source == "source" and edge.target == runtime_transform for edge in flattened.edges
        )
        assert any(
            edge.source == runtime_transform and edge.target == "sink" for edge in flattened.edges
        )

    def test_boundary_side_handles_survive_hierarchical_and_flattened_views(
        self,
        tmp_path: Path,
    ) -> None:
        child = _write(
            tmp_path,
            "ported_child.py",
            """
            import polars as pl
            import haute

            submodel = haute.Submodel(
                "ported_child",
                definition_id="ported_child",
                input_ports=[
                    {
                        "name": "base",
                        "targets": [
                            {
                                "nodeId": "child_in",
                                "handleId": "child_input_handle",
                            }
                        ],
                    }
                ],
                output_ports=[
                    {
                        "name": "quotes",
                        "source": {
                            "nodeId": "child_out",
                            "handleId": "child_output_handle",
                        },
                    }
                ],
            )

            @submodel.polars
            def child_in(base: pl.LazyFrame) -> pl.LazyFrame:
                return base

            @submodel.polars
            def child_out(child_in: pl.LazyFrame) -> pl.LazyFrame:
                return child_in
            """,
        )
        parent = _write(
            tmp_path,
            "ported_main.py",
            f"""
            import polars as pl
            import haute

            pipeline = haute.Pipeline("ported_main")

            @pipeline.polars
            def source() -> pl.LazyFrame:
                return pl.LazyFrame({{"x": [1]}})

            @pipeline.polars
            def sink(ported_child: pl.LazyFrame) -> pl.LazyFrame:
                return ported_child

            pipeline.submodel(
                {child.name!r},
                definition_id="ported_child",
                instance_id="submodel__ported_child",
                alias="ported_child",
            )
            pipeline.connect("source", "ported_child", target_port="base")
            pipeline.connect("ported_child", "sink", source_port="quotes")
            """,
        )

        hierarchical = parse_pipeline_file(parent)
        inbound = next(
            edge
            for edge in hierarchical.edges
            if edge.source == "source" and edge.target == "submodel__ported_child"
        )
        outbound = next(
            edge
            for edge in hierarchical.edges
            if edge.source == "submodel__ported_child" and edge.target == "sink"
        )
        assert inbound.targetHandle == "in__base"
        assert inbound.targetPort is None
        assert outbound.sourceHandle == "out__quotes"
        assert outbound.sourcePort is None

        flattened = parse_pipeline_file(parent, flatten=True)
        runtime_in = qualified_runtime_node_id("submodel__ported_child", "child_in")
        runtime_out = qualified_runtime_node_id("submodel__ported_child", "child_out")
        flat_inbound = next(
            edge
            for edge in flattened.edges
            if edge.source == "source" and edge.target == runtime_in
        )
        flat_outbound = next(
            edge for edge in flattened.edges if edge.source == runtime_out and edge.target == "sink"
        )
        assert flat_inbound.targetHandle == "child_input_handle"
        assert flat_outbound.sourceHandle == "child_output_handle"

        payload_roundtrip = type(hierarchical).model_validate(hierarchical.model_dump())
        generated = graph_to_code_multi(
            payload_roundtrip,
            pipeline_name="ported_main",
            source_file=parent.name,
        )
        regenerated_dir = tmp_path / "regenerated"
        for relative_path, content in generated.items():
            generated_path = regenerated_dir / relative_path
            generated_path.parent.mkdir(parents=True, exist_ok=True)
            generated_path.write_text(content)

        reparsed = parse_pipeline_file(
            regenerated_dir / parent.name,
            flatten=True,
        )
        reparsed_inbound = next(
            edge for edge in reparsed.edges if edge.source == "source" and edge.target == runtime_in
        )
        reparsed_outbound = next(
            edge for edge in reparsed.edges if edge.source == runtime_out and edge.target == "sink"
        )
        assert reparsed_inbound.targetHandle == "child_input_handle"
        assert reparsed_outbound.sourceHandle == "child_output_handle"

    def test_dangling_connect_reports_exact_edge_and_handles(self) -> None:
        source = textwrap.dedent(
            """\
            import polars as pl
            import haute

            pipeline = haute.Pipeline("dangling")

            @pipeline.polars
            def source() -> pl.LazyFrame:
                return pl.LazyFrame({"x": [1]})

            pipeline.connect(
                "source",
                "missing",
                source_port="quotes",
                target_port="base",
            )
            """
        )

        with pytest.raises(ParseError, match="dangling") as exc_info:
            parse_pipeline_source(source, source_file="dangling.py")

        assert exc_info.value.context["dangling_edges"] == [
            {
                "source": "source",
                "target": "missing",
                "source_handle": "quotes",
                "target_handle": "base",
            }
        ]


# ---------------------------------------------------------------------------
# F325 — editor recovery must cross-check a drifted contract annotation.
# ---------------------------------------------------------------------------


class TestRecoveryContractValidation:
    def test_recovery_preserves_a_resolved_contract(self, tmp_path: Path) -> None:
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
        pipeline_path = _write(tmp_path, "main.py", source)

        document = load_pipeline_editor_document(pipeline_path, project_root=tmp_path)

        assert document.load_status == "degraded"
        assert document.nodes[0].authored_id == "node"
        assert document.nodes[0].availability == "ready"
        assert document.nodes[0].config is not None
        assert document.nodes[0].config["contract"] == {
            "inputs": ("a",),
            "outputs": ("b",),
        }


# ---------------------------------------------------------------------------
# F3 — parent connect to private child nodes rejected as dangling endpoints.
# ---------------------------------------------------------------------------


class TestPrivateChildEndpoints:
    def test_parent_connect_to_private_child_is_rejected_as_dangling(
        self,
        tmp_path: Path,
    ) -> None:
        child = _write(
            tmp_path,
            "child.py",
            """
            import polars as pl
            import haute

            submodel = haute.Submodel(
                "child",
                definition_id="child",
                input_ports=[
                    {
                        "name": "source",
                        "targets": [{"nodeId": "transform", "handleId": None}],
                    }
                ],
                output_ports=[
                    {
                        "name": "result",
                        "source": {"nodeId": "transform", "handleId": None},
                    }
                ],
            )

            @submodel.polars
            def transform(source: pl.LazyFrame) -> pl.LazyFrame:
                return source
            """,
        )
        parent = _write(
            tmp_path,
            "main.py",
            f"""
            import polars as pl
            import haute

            pipeline = haute.Pipeline("main")

            @pipeline.polars
            def source() -> pl.LazyFrame:
                return pl.LazyFrame({{"x": [1]}})

            @pipeline.polars
            def sink(transform: pl.LazyFrame) -> pl.LazyFrame:
                return transform

            pipeline.submodel(
                {child.name!r},
                definition_id="child",
                instance_id="submodel__child",
                alias="child",
            )
            pipeline.connect("source", "transform")
            pipeline.connect("transform", "sink")
            """,
        )

        graph = None
        with pytest.raises(ParseError, match="dangling") as exc_info:
            graph = parse_pipeline_file(parent)

        assert graph is None
        assert exc_info.value.context["dangling_edges"] == [
            {
                "source": "source",
                "target": "transform",
                "source_handle": None,
                "target_handle": None,
            },
            {
                "source": "transform",
                "target": "sink",
                "source_handle": None,
                "target_handle": None,
            },
        ]

    def test_private_child_as_source_only_is_rejected(
        self,
        tmp_path: Path,
    ) -> None:
        child = _write(
            tmp_path,
            "child.py",
            """
            import polars as pl
            import haute

            submodel = haute.Submodel(
                "child",
                definition_id="child",
                input_ports=[
                    {
                        "name": "source",
                        "targets": [{"nodeId": "transform", "handleId": None}],
                    }
                ],
                output_ports=[
                    {
                        "name": "result",
                        "source": {"nodeId": "transform", "handleId": None},
                    }
                ],
            )

            @submodel.polars
            def transform(source: pl.LazyFrame) -> pl.LazyFrame:
                return source
            """,
        )
        parent = _write(
            tmp_path,
            "main.py",
            f"""
            import polars as pl
            import haute

            pipeline = haute.Pipeline("main")

            @pipeline.polars
            def source() -> pl.LazyFrame:
                return pl.LazyFrame({{"x": [1]}})

            @pipeline.polars
            def sink(transform: pl.LazyFrame) -> pl.LazyFrame:
                return transform

            pipeline.submodel(
                {child.name!r},
                definition_id="child",
                instance_id="submodel__child",
                alias="child",
            )
            pipeline.connect("transform", "sink")
            """,
        )

        graph = None
        with pytest.raises(ParseError, match="dangling") as exc_info:
            graph = parse_pipeline_file(parent)

        assert graph is None
        assert exc_info.value.context["dangling_edges"] == [
            {
                "source": "transform",
                "target": "sink",
                "source_handle": None,
                "target_handle": None,
            },
        ]

    def test_child_to_child_connect_is_rejected(
        self,
        tmp_path: Path,
    ) -> None:
        child = _write(
            tmp_path,
            "child.py",
            """
            import polars as pl
            import haute

            submodel = haute.Submodel(
                "child",
                definition_id="child",
                input_ports=[
                    {
                        "name": "source",
                        "targets": [{"nodeId": "transform", "handleId": None}],
                    }
                ],
                output_ports=[
                    {
                        "name": "result",
                        "source": {"nodeId": "other", "handleId": None},
                    }
                ],
            )

            @submodel.polars
            def transform(source: pl.LazyFrame) -> pl.LazyFrame:
                return source

            @submodel.polars
            def other(transform: pl.LazyFrame) -> pl.LazyFrame:
                return transform
            """,
        )
        parent = _write(
            tmp_path,
            "main.py",
            f"""
            import polars as pl
            import haute

            pipeline = haute.Pipeline("main")

            @pipeline.polars
            def source() -> pl.LazyFrame:
                return pl.LazyFrame({{"x": [1]}})

            @pipeline.polars
            def sink(other: pl.LazyFrame) -> pl.LazyFrame:
                return other

            pipeline.submodel(
                {child.name!r},
                definition_id="child",
                instance_id="submodel__child",
                alias="child",
            )
            pipeline.connect("transform", "other")
            """,
        )

        graph = None
        with pytest.raises(ParseError, match="dangling") as exc_info:
            graph = parse_pipeline_file(parent)

        assert graph is None
        assert exc_info.value.context["dangling_edges"] == [
            {
                "source": "transform",
                "target": "other",
                "source_handle": None,
                "target_handle": None,
            },
        ]

    def test_shared_definition_two_occurrences_public_ports_succeed_but_child_id_fails(
        self,
        tmp_path: Path,
    ) -> None:
        child = _write(
            tmp_path,
            "child.py",
            """
            import polars as pl
            import haute

            submodel = haute.Submodel(
                "child",
                definition_id="child",
                input_ports=[
                    {
                        "name": "source",
                        "targets": [{"nodeId": "transform", "handleId": None}],
                    }
                ],
                output_ports=[
                    {
                        "name": "result",
                        "source": {"nodeId": "transform", "handleId": None},
                    }
                ],
            )

            @submodel.polars
            def transform(source: pl.LazyFrame) -> pl.LazyFrame:
                return source
            """,
        )
        parent_valid = _write(
            tmp_path,
            "main_valid.py",
            f"""
            import polars as pl
            import haute

            pipeline = haute.Pipeline("main_valid")

            @pipeline.polars
            def source() -> pl.LazyFrame:
                return pl.LazyFrame({{"x": [1]}})

            @pipeline.polars
            def sink_a(a: pl.LazyFrame) -> pl.LazyFrame:
                return a

            @pipeline.polars
            def sink_b(b: pl.LazyFrame) -> pl.LazyFrame:
                return b

            pipeline.submodel(
                {child.name!r},
                definition_id="child",
                instance_id="submodel__a",
                alias="a",
            )
            pipeline.submodel(
                {child.name!r},
                definition_id="child",
                instance_id="submodel__b",
                alias="b",
                instance_of="submodel__a",
            )
            pipeline.connect("source", "a", target_port="source")
            pipeline.connect("a", "sink_a", source_port="result")
            pipeline.connect("source", "b", target_port="source")
            pipeline.connect("b", "sink_b", source_port="result")
            """,
        )

        hierarchical = parse_pipeline_file(parent_valid)
        assert any(
            edge.source == "source"
            and edge.target == "submodel__a"
            and edge.targetHandle == "in__source"
            for edge in hierarchical.edges
        )
        assert any(
            edge.source == "submodel__a"
            and edge.target == "sink_a"
            and edge.sourceHandle == "out__result"
            for edge in hierarchical.edges
        )
        assert any(
            edge.source == "source"
            and edge.target == "submodel__b"
            and edge.targetHandle == "in__source"
            for edge in hierarchical.edges
        )
        assert any(
            edge.source == "submodel__b"
            and edge.target == "sink_b"
            and edge.sourceHandle == "out__result"
            for edge in hierarchical.edges
        )

        parent_invalid = _write(
            tmp_path,
            "main_invalid.py",
            f"""
            import polars as pl
            import haute

            pipeline = haute.Pipeline("main_invalid")

            @pipeline.polars
            def source() -> pl.LazyFrame:
                return pl.LazyFrame({{"x": [1]}})

            @pipeline.polars
            def sink_a(a: pl.LazyFrame) -> pl.LazyFrame:
                return a

            @pipeline.polars
            def sink_b(b: pl.LazyFrame) -> pl.LazyFrame:
                return b

            pipeline.submodel(
                {child.name!r},
                definition_id="child",
                instance_id="submodel__a",
                alias="a",
            )
            pipeline.submodel(
                {child.name!r},
                definition_id="child",
                instance_id="submodel__b",
                alias="b",
                instance_of="submodel__a",
            )
            pipeline.connect("source", "a", target_port="source")
            pipeline.connect("a", "sink_a", source_port="result")
            pipeline.connect("source", "b", target_port="source")
            pipeline.connect("b", "sink_b", source_port="result")
            pipeline.connect("source", "transform")
            """,
        )

        graph = None
        with pytest.raises(ParseError, match="dangling") as exc_info:
            graph = parse_pipeline_file(parent_invalid)

        assert graph is None
        assert exc_info.value.context["dangling_edges"] == [
            {
                "source": "source",
                "target": "transform",
                "source_handle": None,
                "target_handle": None,
            }
        ]

    def test_private_child_connect_leaves_document_non_ready_with_the_diagnostic(
        self,
        tmp_path: Path,
    ) -> None:
        child = _write(
            tmp_path,
            "child.py",
            """
            import polars as pl
            import haute

            submodel = haute.Submodel(
                "child",
                definition_id="child",
                input_ports=[
                    {
                        "name": "source",
                        "targets": [{"nodeId": "transform", "handleId": None}],
                    }
                ],
                output_ports=[
                    {
                        "name": "result",
                        "source": {"nodeId": "transform", "handleId": None},
                    }
                ],
            )

            @submodel.polars
            def transform(source: pl.LazyFrame) -> pl.LazyFrame:
                return source
            """,
        )
        parent_path = _write(
            tmp_path,
            "main.py",
            f"""
            import polars as pl
            import haute

            pipeline = haute.Pipeline("main")

            @pipeline.polars
            def source() -> pl.LazyFrame:
                return pl.LazyFrame({{"x": [1]}})

            @pipeline.polars
            def sink(transform: pl.LazyFrame) -> pl.LazyFrame:
                return transform

            pipeline.submodel(
                {child.name!r},
                definition_id="child",
                instance_id="submodel__child",
                alias="child",
            )
            pipeline.connect("source", "transform")
            pipeline.connect("transform", "sink")
            """,
        )

        document = load_pipeline_editor_document(parent_path, project_root=tmp_path)
        assert document.load_status != "ready"
        assert document.load_status == "degraded"
        endpoint_diagnostics = [
            diagnostic
            for diagnostic in document.diagnostics
            if diagnostic.code == "connection_endpoint_missing"
        ]
        assert len(endpoint_diagnostics) == 2
        assert {diagnostic.scope for diagnostic in endpoint_diagnostics} == {"edge"}
        assert all(
            diagnostic.source_file == "main.py" and diagnostic.source_span is not None
            for diagnostic in endpoint_diagnostics
        )


class TestConservationGateRaisesOnEveryMismatch:
    """Direct witnesses for the gate's own failure branches (ENG-T12 mutation target).

    A correct parser never reaches these branches, so they are driven directly:
    each mismatch is exercised in both lexicographic directions (a lost item and
    an extra item) so that a comparison weakened to ``<`` or ``>`` cannot pass
    unnoticed, and the consistent input is the known-good control.
    """

    @staticmethod
    def _graph(node_ids: list[str], edges: list[tuple[str, str]]):
        from tests.conftest import make_graph

        return make_graph(
            {
                "nodes": [
                    {
                        "id": node_id,
                        "data": {"label": node_id, "nodeType": "polars", "config": {"code": ""}},
                    }
                    for node_id in node_ids
                ],
                "edges": [
                    {"id": f"e_{source}_{target}", "source": source, "target": target}
                    for source, target in edges
                ],
            }
        )

    @staticmethod
    def _raw(*specs: tuple[str, list[str]]) -> list[dict]:
        return [{"func_name": name, "param_names": params} for name, params in specs]

    def test_consistent_structure_is_accepted(self) -> None:
        from haute._parser_conservation import assert_parser_structure_conserved

        graph = self._graph(["a", "b"], [("a", "b")])
        assert (
            assert_parser_structure_conserved(
                raw_nodes=self._raw(("a", []), ("b", ["a"])),
                explicit_connects=[],
                root_nodes=graph.nodes,
                root_edges=graph.edges,
                submodel_paths=["child.py"],
                submodel_instance_paths=["child.py"],
            )
            is None
        )

    @pytest.mark.parametrize(
        ("authored", "parsed"),
        [(["a", "b"], ["a"]), (["a"], ["a", "b"]), (["a", "b"], ["b", "a"])],
        ids=["lost-node", "extra-node", "reordered-nodes"],
    )
    def test_node_identity_mismatch_is_rejected(
        self, authored: list[str], parsed: list[str]
    ) -> None:
        from haute._parser_conservation import assert_parser_structure_conserved

        graph = self._graph(parsed, [])
        with pytest.raises(ParseError, match="conserve authored node identities") as exc_info:
            assert_parser_structure_conserved(
                raw_nodes=self._raw(*((name, []) for name in authored)),
                explicit_connects=[],
                root_nodes=graph.nodes,
                root_edges=graph.edges,
            )
        assert exc_info.value.context["authored_node_ids"] == authored
        assert exc_info.value.context["parsed_node_ids"] == parsed

    @pytest.mark.parametrize(
        ("parsed_edges", "expected_parsed"),
        [
            ([], []),
            ([("a", "b"), ("b", "a")], [("a", "b"), ("b", "a")]),
            ([("b", "a")], [("b", "a")]),
        ],
        ids=["lost-edge", "extra-edge", "wrong-edge"],
    )
    def test_edge_identity_mismatch_is_rejected(
        self,
        parsed_edges: list[tuple[str, str]],
        expected_parsed: list[tuple[str, str]],
    ) -> None:
        from haute._parser_conservation import assert_parser_structure_conserved

        graph = self._graph(["a", "b"], parsed_edges)
        with pytest.raises(ParseError, match="conserve authored edge") as exc_info:
            assert_parser_structure_conserved(
                raw_nodes=self._raw(("a", []), ("b", ["a"])),
                explicit_connects=[],
                root_nodes=graph.nodes,
                root_edges=graph.edges,
            )
        assert exc_info.value.context["authored_edges"] == [
            {"source": "a", "target": "b", "source_handle": None, "target_handle": None}
        ]
        assert exc_info.value.context["parsed_edges"] == [
            {"source": s, "target": t, "source_handle": None, "target_handle": None}
            for s, t in expected_parsed
        ]

    @pytest.mark.parametrize(
        ("authored", "loaded"),
        [(["child.py"], []), ([], ["child.py"]), (["a.py", "b.py"], ["b.py", "a.py"])],
        ids=["lost-reference", "extra-reference", "reordered-references"],
    )
    def test_submodel_reference_mismatch_is_rejected(
        self, authored: list[str], loaded: list[str]
    ) -> None:
        from haute._parser_conservation import assert_parser_structure_conserved

        graph = self._graph(["a"], [])
        with pytest.raises(ParseError, match="conserve authored submodel references") as exc_info:
            assert_parser_structure_conserved(
                raw_nodes=self._raw(("a", [])),
                explicit_connects=[],
                root_nodes=graph.nodes,
                root_edges=graph.edges,
                submodel_paths=authored,
                submodel_instance_paths=loaded,
            )
        assert exc_info.value.context["authored_submodel_paths"] == authored
        assert exc_info.value.context["loaded_submodel_paths"] == loaded


# ---------------------------------------------------------------------------
# F13 -- Strict parameter binding for Polars nodes.
# ---------------------------------------------------------------------------


class TestPolarsParameterBinding:
    def test_positional_param_not_matching_incoming_edge_rejected(self) -> None:
        source = textwrap.dedent(
            """\
            import polars as pl
            import haute

            pipeline = haute.Pipeline("test_unbound")

            @pipeline.polars
            def a() -> pl.LazyFrame:
                return pl.LazyFrame({"x": [1]})

            @pipeline.polars
            def b(unbound: pl.LazyFrame) -> pl.LazyFrame:
                return unbound

            pipeline.connect("a", "b")
            """
        )
        with pytest.raises(
            ParseError,
            match="Pipeline function parameters do not match the node's connected inputs.",
        ) as exc_info:
            parse_pipeline_source(source, source_file="main.py")

        assert exc_info.value.context["node_id"] == "b"
        assert exc_info.value.context["unbound_parameters"] == ["unbound"]
        assert exc_info.value.context["unconsumed_inputs"] == ["a"]
        assert exc_info.value.context["connected_inputs"] == ["a"]
        assert (
            exc_info.value.context["remediation"]
            == "Name each parameter after the node or frame connected to it, "
            "or declare inputMapping={logical: connected} on the decorator."
        )

    def test_incoming_edge_not_matching_any_parameter_rejected(self) -> None:
        source = textwrap.dedent(
            """\
            import polars as pl
            import haute

            pipeline = haute.Pipeline("test_unconsumed")

            @pipeline.polars
            def a() -> pl.LazyFrame:
                return pl.LazyFrame({"x": [1]})

            @pipeline.polars
            def b() -> pl.LazyFrame:
                return pl.LazyFrame({"y": [2]})

            pipeline.connect("a", "b")
            """
        )
        with pytest.raises(
            ParseError,
            match="Pipeline function parameters do not match the node's connected inputs.",
        ) as exc_info:
            parse_pipeline_source(source, source_file="main.py")

        assert exc_info.value.context["node_id"] == "b"
        assert exc_info.value.context["unbound_parameters"] == []
        assert exc_info.value.context["unconsumed_inputs"] == ["a"]
        assert exc_info.value.context["connected_inputs"] == ["a"]
        assert (
            exc_info.value.context["remediation"]
            == "Name each parameter after the node or frame connected to it, "
            "or declare inputMapping={logical: connected} on the decorator."
        )

    def test_input_mapping_succeeds_executes_and_roundtrips(self) -> None:
        from haute.executor import execute_graph

        source = textwrap.dedent(
            """\
            import polars as pl
            import haute

            pipeline = haute.Pipeline("test_mapping")

            @pipeline.polars
            def src() -> pl.LazyFrame:
                return pl.LazyFrame({"x": [1, 2, 3]})

            @pipeline.polars(inputMapping={"raw_data": "src"})
            def transform(raw_data: pl.LazyFrame) -> pl.LazyFrame:
                return raw_data.with_columns((pl.col("x") * 2).alias("x2"))

            pipeline.connect("src", "transform")
            """
        )
        graph = parse_pipeline_source(source, source_file="main.py")
        assert len(graph.nodes) == 2
        assert len(graph.edges) == 1

        results = execute_graph(graph, target_node_id="transform")
        assert results["transform"].status == "ok"
        assert results["transform"].preview == [
            {"x": 1, "x2": 2},
            {"x": 2, "x2": 4},
            {"x": 3, "x2": 6},
        ]

        files = graph_to_code_multi(graph, pipeline_name="test_mapping", source_file="main.py")
        assert "def transform(raw_data: pl.LazyFrame)" in files["main.py"]
        assert 'inputMapping={"raw_data": "src"}' in files["main.py"].replace("'", '"')
        reparsed = parse_pipeline_source(files["main.py"], source_file="main.py")
        assert len(reparsed.nodes) == 2
        assert len(reparsed.edges) == 1
        regenerated = graph_to_code_multi(
            reparsed, pipeline_name="test_mapping", source_file="main.py"
        )
        assert regenerated == files

    def test_occurrence_output_parameter_is_the_occurrence_name(
        self,
        tmp_path: Path,
    ) -> None:
        child = _write(
            tmp_path,
            "child.py",
            """
            import polars as pl
            import haute

            submodel = haute.Submodel(
                "child",
                definition_id="child",
                input_ports=[
                    {
                        "name": "source",
                        "targets": [{"nodeId": "transform", "handleId": None}],
                    }
                ],
                output_ports=[
                    {
                        "name": "result",
                        "source": {"nodeId": "transform", "handleId": None},
                    }
                ],
            )

            @submodel.polars
            def transform(source: pl.LazyFrame) -> pl.LazyFrame:
                return source
            """,
        )
        parent = _write(
            tmp_path,
            "main.py",
            f"""
            import polars as pl
            import haute

            pipeline = haute.Pipeline("main")

            @pipeline.polars
            def source() -> pl.LazyFrame:
                return pl.LazyFrame({{"x": [1]}})

            @pipeline.polars
            def sink(a: pl.LazyFrame) -> pl.LazyFrame:
                return a

            pipeline.submodel(
                {child.name!r},
                definition_id="child",
                instance_id="submodel__a",
                alias="a",
            )
            pipeline.connect("source", "a", target_port="source")
            pipeline.connect("a", "sink", source_port="result")
            """,
        )
        hierarchical = parse_pipeline_file(parent)
        assert len(hierarchical.nodes) == 3
        assert len(hierarchical.edges) == 2

        from haute.executor import execute_graph

        results = execute_graph(hierarchical, target_node_id="sink")
        assert results["sink"].status == "ok"
        assert results["sink"].preview == [{"x": 1}]

        parent_invalid = _write(
            tmp_path,
            "main_invalid.py",
            f"""
            import polars as pl
            import haute

            pipeline = haute.Pipeline("main_invalid")

            @pipeline.polars
            def source() -> pl.LazyFrame:
                return pl.LazyFrame({{"x": [1]}})

            @pipeline.polars
            def sink(Result: pl.LazyFrame) -> pl.LazyFrame:
                return Result

            pipeline.submodel(
                {child.name!r},
                definition_id="child",
                instance_id="submodel__a",
                alias="a",
            )
            pipeline.connect("source", "a", target_port="source")
            pipeline.connect("a", "sink", source_port="result")
            """,
        )
        with pytest.raises(
            ParseError,
            match="Pipeline function parameters do not match the node's connected inputs.",
        ) as exc_info:
            parse_pipeline_file(parent_invalid)

        assert exc_info.value.context["node_id"] == "sink"
        assert exc_info.value.context["unbound_parameters"] == ["Result"]
        assert exc_info.value.context["connected_inputs"] == ["a"]

    def test_degraded_document_with_parameter_mismatch(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from fastapi.testclient import TestClient

        from haute.server import app

        pipeline_file = _write(
            tmp_path,
            "main.py",
            """
            import polars as pl
            import haute

            pipeline = haute.Pipeline("main")

            @pipeline.polars
            def a() -> pl.LazyFrame:
                return pl.LazyFrame({"x": [1]})

            @pipeline.polars
            def b(unbound: pl.LazyFrame) -> pl.LazyFrame:
                return unbound

            pipeline.connect("a", "b")
            """,
        )

        document = load_pipeline_editor_document(pipeline_file, project_root=tmp_path)
        assert document.load_status == "degraded"
        assert any(
            diagnostic.code == "pipeline_parse_invalid"
            and "Pipeline function parameters do not match" in diagnostic.message
            for diagnostic in document.diagnostics
        )

        monkeypatch.chdir(tmp_path)
        client = TestClient(app, raise_server_exceptions=False)
        get_response = client.get("/api/pipeline")
        assert get_response.status_code == 200
        assert get_response.json()["load_status"] == "degraded"

        save_response = client.post(
            "/api/pipeline/save",
            json={
                "name": "main",
                "source_file": "main.py",
                "base_revision": "posted-ready-revision",
                "graph": {"nodes": [], "edges": []},
            },
        )
        assert save_response.status_code == 409
        assert "not ready" in save_response.json()["detail"]

    def test_submodel_definition_input_port_binds_logical_name(
        self,
        tmp_path: Path,
    ) -> None:
        child_valid = _write(
            tmp_path,
            "child_valid.py",
            """
            import polars as pl
            import haute

            submodel = haute.Submodel(
                "child",
                definition_id="child",
                input_ports=[
                    {
                        "name": "source",
                        "targets": [{"nodeId": "first_step", "handleId": None}],
                    }
                ],
                output_ports=[
                    {
                        "name": "result",
                        "source": {"nodeId": "first_step", "handleId": None},
                    }
                ],
            )

            @submodel.polars
            def first_step(source: pl.LazyFrame) -> pl.LazyFrame:
                return source
            """,
        )
        parent_valid = _write(
            tmp_path,
            "main_valid.py",
            f"""
            import polars as pl
            import haute

            pipeline = haute.Pipeline("main_valid")

            @pipeline.polars
            def source() -> pl.LazyFrame:
                return pl.LazyFrame({{"x": [1]}})

            @pipeline.polars
            def sink(a: pl.LazyFrame) -> pl.LazyFrame:
                return a

            pipeline.submodel(
                {child_valid.name!r},
                definition_id="child",
                instance_id="submodel__a",
                alias="a",
            )
            pipeline.connect("source", "a", target_port="source")
            pipeline.connect("a", "sink", source_port="result")
            """,
        )
        graph = parse_pipeline_file(parent_valid)
        assert graph is not None

        child_invalid = _write(
            tmp_path,
            "child_invalid.py",
            """
            import polars as pl
            import haute

            submodel = haute.Submodel(
                "child",
                definition_id="child",
                input_ports=[
                    {
                        "name": "source",
                        "targets": [{"nodeId": "first_step", "handleId": None}],
                    }
                ],
                output_ports=[
                    {
                        "name": "result",
                        "source": {"nodeId": "first_step", "handleId": None},
                    }
                ],
            )

            @submodel.polars
            def first_step(unbound_name: pl.LazyFrame) -> pl.LazyFrame:
                return unbound_name
            """,
        )
        parent_invalid = _write(
            tmp_path,
            "main_invalid.py",
            f"""
            import polars as pl
            import haute

            pipeline = haute.Pipeline("main_invalid")

            @pipeline.polars
            def source() -> pl.LazyFrame:
                return pl.LazyFrame({{"x": [1]}})

            @pipeline.polars
            def sink(a: pl.LazyFrame) -> pl.LazyFrame:
                return a

            pipeline.submodel(
                {child_invalid.name!r},
                definition_id="child",
                instance_id="submodel__a",
                alias="a",
            )
            pipeline.connect("source", "a", target_port="source")
            pipeline.connect("a", "sink", source_port="result")
            """,
        )
        with pytest.raises(
            ParseError,
            match="Pipeline function parameters do not match the node's connected inputs.",
        ) as exc_info:
            parse_pipeline_file(parent_invalid)

        assert exc_info.value.context["node_id"] == "first_step"
        assert exc_info.value.context["unbound_parameters"] == ["unbound_name"]
        assert exc_info.value.context["unconsumed_inputs"] == ["source"]
        assert exc_info.value.context["connected_inputs"] == ["source"]
        assert (
            exc_info.value.context["remediation"]
            == "Name each parameter after the node or frame connected to it, "
            "or declare inputMapping={logical: connected} on the decorator."
        )
