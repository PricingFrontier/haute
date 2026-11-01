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
from haute._code_extraction import _match_hook, extract_user_code
from haute._graph_builders import _build_edges
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
# had no recognised source-load line. A Data Input is now a ``df`` hook whose
# body holds no generated statement before the user's code.
# ---------------------------------------------------------------------------


class TestSourceLoadBoilerplateConservation:
    def test_hook_matcher_keeps_index_at_zero(self) -> None:
        cleaned = [
            "df = df.filter(pl.col('x') > 0)",
            "df = df.with_columns(pl.lit(1).alias('y'))",
        ]
        assert _match_hook(cleaned, ("df",)).start_idx == 0

    def test_source_extractor_keeps_all_user_code(self) -> None:
        body = "df = df.filter(pl.col('x') > 0)\ndf = df.with_columns(pl.lit(1).alias('y'))"
        code = extract_user_code(body, kind="hook", param_names=["df"])
        assert code == body


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
            def target(a, /, b, *, c=None):
                return a
            """
        )
        graph = parse_pipeline_source(source, source_file="m.py")
        pairs = {(e.source, e.target) for e in graph.edges}
        assert ("a", "target") in pairs  # positional-only
        assert ("b", "target") in pairs  # positional-or-keyword
        assert ("c", "target") not in pairs  # keyword-only configuration

    def test_live_switch_keyword_only_config_is_refused_at_its_line(
        self,
        tmp_path: Path,
    ) -> None:
        """A Live Switch is a declaration: codegen writes no keyword-only parameter
        for it, so the parse refuses one, at its line, rather than a save dropping it."""
        config_dir = tmp_path / "config" / "source_switch"
        config_dir.mkdir(parents=True)
        (config_dir / "switch.json").write_text(
            '{"input_scenario_map":{"a":"live"},"inputs":["a"]}',
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

                @pipeline.live_switch(config="config/source_switch/switch.json")
                def switch(a, *, c=None): ...
                """
            ),
        )

        with pytest.raises(ParseError, match="keyword-only parameter 'c'") as refused:
            parse_pipeline_file(pipeline_file)
        assert refused.value.context["node_id"] == "switch"
        assert refused.value.context["line"] == 10


_SCALED = """\
import polars as pl
import haute

factor = 0.9

pipeline = haute.Pipeline("m")


@pipeline.polars
def source() -> pl.LazyFrame:
    df = pl.LazyFrame({"x": [100.0]})
    return df


@pipeline.polars
def scaled(source: pl.LazyFrame, *, factor: float = 1.20, label="rated") -> pl.LazyFrame:
    df = source.with_columns(y=pl.col("x") * factor, label=pl.lit(label))
    return df


pipeline.connect("source", "scaled")
"""


def _canvas_row(graph) -> dict:
    from haute._execution_admission import create_admitted_execution_context
    from haute._execution_context import ExecutionProfile
    from haute.executor import execute_graph

    context = create_admitted_execution_context(
        operation="keyword-parameters-test", profile=ExecutionProfile.PREVIEW_EAGER
    )
    result = execute_graph(graph, target_node_id="scaled", execution_context=context)["scaled"]
    assert result.status == "ok", result.error
    (row,) = result.preview
    return row


def _live_row(source: str, tmp_path: Path) -> dict:
    module = tmp_path / "live.py"
    module.write_text(source, encoding="utf-8")
    namespace: dict[str, object] = {"__file__": str(module), "__name__": "live"}
    exec(compile(source, str(module), "exec"), namespace)  # noqa: S102 - the pipeline file under test
    frame = namespace["source"]()
    (row,) = namespace["scaled"](frame).collect().to_dicts()
    return row


class TestKeywordOnlyParameterConservation:
    """PCFG-R21: a transform's keyword-only defaults survive parse, save and every run."""

    def test_the_parameter_shadows_the_preamble_before_and_after_a_save(
        self, tmp_path: Path
    ) -> None:
        from haute.codegen import graph_to_code

        original = parse_pipeline_source(_SCALED, source_file="main.py")
        scaled = next(node for node in original.nodes if node.id == "scaled")
        assert scaled.data.config["keyword_parameters"] == [
            {"name": "factor", "default": 1.2},
            {"name": "label", "default": "rated"},
        ]
        regenerated = graph_to_code(original, pipeline_name="m", preamble=original.preamble)
        assert '*, factor=1.2, label="rated") -> pl.LazyFrame:' in regenerated
        reparsed = parse_pipeline_source(regenerated, source_file="main.py")
        assert (
            next(node for node in reparsed.nodes if node.id == "scaled").data.config
            == scaled.data.config
        )
        assert graph_to_code(reparsed, pipeline_name="m", preamble=reparsed.preamble) == (
            regenerated
        )

        expected = {"x": 100.0, "y": 120.0, "label": "rated"}
        assert _live_row(_SCALED, tmp_path) == expected
        assert _canvas_row(original) == expected
        assert _canvas_row(reparsed) == expected
        assert _live_row(regenerated, tmp_path) == expected

    def test_a_parameter_shadows_the_physical_name_its_input_was_renamed_from(self) -> None:
        """With inputMapping the signature's input is the logical name, so a parameter may
        share the upstream node's name; the canvas binds the parameter there, as the
        generated function does."""
        source = (
            _SCALED.replace("def source() -> pl.LazyFrame:", "def factor() -> pl.LazyFrame:")
            .replace(
                "@pipeline.polars\ndef scaled(",
                '@pipeline.polars(inputMapping={"source": "factor"})\ndef scaled(',
            )
            .replace('pipeline.connect("source", "scaled")', 'pipeline.connect("factor", "scaled")')
        )
        graph = parse_pipeline_source(source, source_file="main.py")

        assert _canvas_row(graph) == {"x": 100.0, "y": 120.0, "label": "rated"}

    @pytest.mark.parametrize(
        ("signature", "line", "message"),
        [
            pytest.param("source, *, factor", 16, "has no default", id="required"),
            pytest.param("source, *, factor=FACTOR", 16, "must be a literal", id="name"),
            pytest.param("source, *, factor=1 + 1", 16, "must be a literal", id="expression"),
            pytest.param("source, *, factor=(1, 2)", 16, "must be a literal", id="tuple"),
            pytest.param("source, *, factor={1}", 16, "must be a literal", id="set"),
            pytest.param("source, *, factor=b'x'", 16, "must be a literal", id="bytes"),
            pytest.param("source, *, factor=1e999", 16, "must be a literal", id="non-finite"),
            pytest.param("source, *, factor={1: 2}", 16, "must be a literal", id="int-key"),
            # One object every call shares, which a canvas run could not reproduce.
            pytest.param("source, *, factor=[1, 2]", 16, "must be a literal", id="list"),
            pytest.param('source, *, factor={"a": 1}', 16, "must be a literal", id="dict"),
            pytest.param("source, *, df=1", 16, "reserved for node code", id="df"),
            pytest.param("source, *, pl=1", 16, "reserved for node code", id="pl"),
            pytest.param("source, **settings", 16, "**settings", id="var-keyword"),
        ],
    )
    def test_a_form_the_file_cannot_carry_is_refused_at_its_line(
        self, signature: str, line: int, message: str
    ) -> None:
        source = _SCALED.replace(
            'source: pl.LazyFrame, *, factor: float = 1.20, label="rated"', signature
        )
        with pytest.raises(ParseError, match=message.replace("*", r"\*")) as refused:
            parse_pipeline_source(source, source_file="main.py")
        assert refused.value.context["node_id"] == "scaled"
        assert refused.value.context["line"] == line

    def test_a_refused_node_opens_unavailable_and_the_file_cannot_be_saved(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "haute.toml").write_text('[project]\nname = "m"\n', encoding="utf-8")
        main = _write(tmp_path, "main.py", _SCALED.replace("factor: float = 1.20", "factor"))
        before = main.read_bytes()

        document = load_pipeline_editor_document(main, project_root=tmp_path)

        scaled = next(node for node in document.nodes if node.authored_id == "scaled")
        assert scaled.availability == "unavailable"
        (diagnostic,) = [d for d in document.diagnostics if d.element_id == scaled.recovery_id]
        assert "'factor' has no default" in diagnostic.message
        assert document.capabilities.can_save is False
        assert main.read_bytes() == before

    def test_a_stepped_transform_refuses_a_keyword_only_parameter(self, tmp_path: Path) -> None:
        sidecar = tmp_path / "config" / "polars" / "scaled.json"
        sidecar.parent.mkdir(parents=True)
        sidecar.write_text(
            '{"steps": [{"id": "s", "kind": "source", "input": "source"}]}', encoding="utf-8"
        )
        main = _write(
            tmp_path,
            "main.py",
            _SCALED.replace(
                "@pipeline.polars\ndef scaled(",
                '@pipeline.polars(config="config/polars/scaled.json")\ndef scaled(',
            ),
        )
        with pytest.raises(ParseError, match="Transform built from steps") as refused:
            parse_pipeline_file(main)
        assert refused.value.context["line"] == 16

    @pytest.mark.parametrize(
        ("signature", "refused"),
        [
            pytest.param("df: pl.LazyFrame, *, obj", None, id="obj-hook"),
            pytest.param("df: pl.LazyFrame, *, obj=None", "'obj'", id="obj-default"),
            pytest.param("df: pl.LazyFrame, *, obj, extra=1", "'extra'", id="extra"),
        ],
    )
    def test_a_load_file_hook_takes_only_obj(
        self, tmp_path: Path, signature: str, refused: str | None
    ) -> None:
        sidecar = tmp_path / "config" / "load_file" / "loaded.json"
        sidecar.parent.mkdir(parents=True)
        sidecar.write_text('{"path": "model.pkl", "fileType": "pickle"}', encoding="utf-8")
        main = _write(
            tmp_path,
            "main.py",
            _SCALED.replace(
                "@pipeline.polars\ndef scaled(",
                '@pipeline.external_file(config="config/load_file/loaded.json")\ndef loaded(',
            )
            .replace('source: pl.LazyFrame, *, factor: float = 1.20, label="rated"', signature)
            .replace('pipeline.connect("source", "scaled")', 'pipeline.connect("source", "loaded")')
            .replace(
                'df = source.with_columns(y=pl.col("x") * factor, label=pl.lit(label))',
                "df = df.head(1)",
            ),
        )
        if refused is None:
            loaded = next(node for node in parse_pipeline_file(main).nodes if node.id == "loaded")
            assert "keyword_parameters" not in loaded.data.config
            return
        with pytest.raises(ParseError, match=f"declares {refused}") as error:
            parse_pipeline_file(main)
        assert error.value.context["line"] == 16


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
# merely mentioned score_from_config(, dropping the real user code. A Model
# Score is now a ``df`` hook: nothing is anchored on any call, so no user
# statement can be dropped.
# ---------------------------------------------------------------------------


class TestModelScoreBoilerplateAnchoring:
    def test_decoy_string_does_not_mis_anchor(self) -> None:
        body = "\n".join(
            [
                'decoy = "score_from_config("  # not a real call',
                "result = score_from_config(cfg, df)",
                'df = result.select("prediction")',
                "return df",
            ]
        )
        code = extract_user_code(body, kind="hook", param_names=["df"])
        # Every statement before the closing ``return df`` is the user's,
        # including a call named like the retired scoring helper.
        assert code == "\n".join(body.splitlines()[:-1])


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


# ---------------------------------------------------------------------------
# F312 — implicit param-name edges were not de-duplicated, so a duplicated
# parameter name produced two GraphEdges with identical ids.
# ---------------------------------------------------------------------------


class TestImplicitEdgeDedup:
    def test_duplicate_param_name_yields_single_edge(self) -> None:
        raw_nodes = [
            {"func_name": "a", "param_names": [], "edge_param_names": []},
            {"func_name": "f", "param_names": ["a", "a"], "edge_param_names": ["a", "a"]},
        ]
        edges = _build_edges(raw_nodes, [])
        ids = [e.id for e in edges]
        assert ids.count("e_a_f") == 1


class TestSubmodelResolutionRoot:
    def test_in_memory_submodel_parse_without_base_fails_loudly(self) -> None:
        source = textwrap.dedent(
            """\
            import haute

            from modules import scoring

            pipeline = haute.Pipeline("main")
            pipeline.submodel(scoring.submodel, "scoring")
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
        _write(
            tmp_path,
            "modules/child.py",
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
                pipeline_dir="..",
            )

            @submodel.polars
            def transform(source: pl.LazyFrame) -> pl.LazyFrame:
                return source
            """,
        )
        parent = _write(
            tmp_path,
            "main.py",
            """
            import polars as pl
            import haute

            from modules import child

            pipeline = haute.Pipeline("main")

            @pipeline.polars
            def source() -> pl.LazyFrame:
                return pl.LazyFrame({"x": [1]})

            @pipeline.polars
            def sink(result: pl.LazyFrame) -> pl.LazyFrame:
                return result

            pipeline.submodel(child.submodel, "child")
            pipeline.connect("source", "child", target_port="source")
            pipeline.connect("child", "sink", source_port="result")
            """,
        )

        hierarchical = parse_pipeline_file(parent)
        assert any(
            edge.source == "source" and edge.target == "child" and edge.targetHandle == "in__source"
            for edge in hierarchical.edges
        )
        assert any(
            edge.source == "child" and edge.target == "sink" and edge.sourceHandle == "out__result"
            for edge in hierarchical.edges
        )

        flattened = parse_pipeline_file(parent, flatten=True)
        runtime_transform = qualified_runtime_node_id("child", "transform")
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
        _write(
            tmp_path,
            "modules/ported_child.py",
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
                pipeline_dir="..",
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
            """
            import polars as pl
            import haute

            from modules import ported_child

            pipeline = haute.Pipeline("ported_main")

            @pipeline.polars
            def source() -> pl.LazyFrame:
                return pl.LazyFrame({"x": [1]})

            @pipeline.polars
            def sink(quotes: pl.LazyFrame) -> pl.LazyFrame:
                return quotes

            pipeline.submodel(ported_child.submodel, "ported_child")
            pipeline.connect("source", "ported_child", target_port="base")
            pipeline.connect("ported_child", "sink", source_port="quotes")
            """,
        )

        hierarchical = parse_pipeline_file(parent)
        inbound = next(
            edge
            for edge in hierarchical.edges
            if edge.source == "source" and edge.target == "ported_child"
        )
        outbound = next(
            edge
            for edge in hierarchical.edges
            if edge.source == "ported_child" and edge.target == "sink"
        )
        assert inbound.targetHandle == "in__base"
        assert outbound.sourceHandle == "out__quotes"

        flattened = parse_pipeline_file(parent, flatten=True)
        runtime_in = qualified_runtime_node_id("ported_child", "child_in")
        runtime_out = qualified_runtime_node_id("ported_child", "child_out")
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

            @pipeline.not_a_node_type
            def other(df):
                return df
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
        _write(
            tmp_path,
            "modules/child.py",
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
                pipeline_dir="..",
            )

            @submodel.polars
            def transform(source: pl.LazyFrame) -> pl.LazyFrame:
                return source
            """,
        )
        parent = _write(
            tmp_path,
            "main.py",
            """
            import polars as pl
            import haute

            from modules import child

            pipeline = haute.Pipeline("main")

            @pipeline.polars
            def source() -> pl.LazyFrame:
                return pl.LazyFrame({"x": [1]})

            @pipeline.polars
            def sink(transform: pl.LazyFrame) -> pl.LazyFrame:
                return transform

            pipeline.submodel(child.submodel, "child")
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
        _write(
            tmp_path,
            "modules/child.py",
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
                pipeline_dir="..",
            )

            @submodel.polars
            def transform(source: pl.LazyFrame) -> pl.LazyFrame:
                return source
            """,
        )
        parent = _write(
            tmp_path,
            "main.py",
            """
            import polars as pl
            import haute

            from modules import child

            pipeline = haute.Pipeline("main")

            @pipeline.polars
            def source() -> pl.LazyFrame:
                return pl.LazyFrame({"x": [1]})

            @pipeline.polars
            def sink(transform: pl.LazyFrame) -> pl.LazyFrame:
                return transform

            pipeline.submodel(child.submodel, "child")
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
        _write(
            tmp_path,
            "modules/child.py",
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
                pipeline_dir="..",
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
            """
            import polars as pl
            import haute

            from modules import child

            pipeline = haute.Pipeline("main")

            @pipeline.polars
            def source() -> pl.LazyFrame:
                return pl.LazyFrame({"x": [1]})

            @pipeline.polars
            def sink(other: pl.LazyFrame) -> pl.LazyFrame:
                return other

            pipeline.submodel(child.submodel, "child")
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
        _write(
            tmp_path,
            "modules/child.py",
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
                pipeline_dir="..",
            )

            @submodel.polars
            def transform(source: pl.LazyFrame) -> pl.LazyFrame:
                return source
            """,
        )
        parent_valid = _write(
            tmp_path,
            "main_valid.py",
            """
            import polars as pl
            import haute

            from modules import child

            pipeline = haute.Pipeline("main_valid")

            @pipeline.polars
            def source() -> pl.LazyFrame:
                return pl.LazyFrame({"x": [1]})

            @pipeline.polars
            def sink_a(result: pl.LazyFrame) -> pl.LazyFrame:
                return result

            @pipeline.polars
            def sink_b(b: pl.LazyFrame) -> pl.LazyFrame:
                return b

            pipeline.submodel(child.submodel, "a")
            pipeline.submodel(child.submodel, "b", instance_of="a")
            pipeline.connect("source", "a", target_port="source")
            pipeline.connect("a", "sink_a", source_port="result")
            pipeline.connect("source", "b", target_port="source")
            pipeline.connect("b", "sink_b", source_port="result")
            """,
        )

        hierarchical = parse_pipeline_file(parent_valid)
        assert any(
            edge.source == "source" and edge.target == "a" and edge.targetHandle == "in__source"
            for edge in hierarchical.edges
        )
        assert any(
            edge.source == "a" and edge.target == "sink_a" and edge.sourceHandle == "out__result"
            for edge in hierarchical.edges
        )
        assert any(
            edge.source == "source" and edge.target == "b" and edge.targetHandle == "in__source"
            for edge in hierarchical.edges
        )
        assert any(
            edge.source == "b" and edge.target == "sink_b" and edge.sourceHandle == "out__result"
            for edge in hierarchical.edges
        )

        parent_invalid = _write(
            tmp_path,
            "main_invalid.py",
            """
            import polars as pl
            import haute

            from modules import child

            pipeline = haute.Pipeline("main_invalid")

            @pipeline.polars
            def source() -> pl.LazyFrame:
                return pl.LazyFrame({"x": [1]})

            @pipeline.polars
            def sink_a(result: pl.LazyFrame) -> pl.LazyFrame:
                return result

            @pipeline.polars
            def sink_b(b: pl.LazyFrame) -> pl.LazyFrame:
                return b

            pipeline.submodel(child.submodel, "a")
            pipeline.submodel(child.submodel, "b", instance_of="a")
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
        _write(
            tmp_path,
            "modules/child.py",
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
                pipeline_dir="..",
            )

            @submodel.polars
            def transform(source: pl.LazyFrame) -> pl.LazyFrame:
                return source
            """,
        )
        parent_path = _write(
            tmp_path,
            "main.py",
            """
            import polars as pl
            import haute

            from modules import child

            pipeline = haute.Pipeline("main")

            @pipeline.polars
            def source() -> pl.LazyFrame:
                return pl.LazyFrame({"x": [1]})

            @pipeline.polars
            def sink(transform: pl.LazyFrame) -> pl.LazyFrame:
                return transform

            pipeline.submodel(child.submodel, "child")
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
        return [
            {"func_name": name, "param_names": params, "edge_param_names": params}
            for name, params in specs
        ]

    @pytest.mark.parametrize(
        "keyword", ["submodel_instance_paths", "submodel_graphs", "submodel_files", "unknown"]
    )
    def test_removed_and_unknown_keywords_are_rejected(self, keyword: str) -> None:
        from haute._parser_conservation import assert_parser_structure_conserved

        with pytest.raises(TypeError, match="unexpected keyword argument"):
            assert_parser_structure_conserved(
                raw_nodes=[],
                explicit_connects=[],
                root_nodes=[],
                root_edges=[],
                **{keyword: []},
            )

    def test_missing_positional_parameter_metadata_is_rejected(self) -> None:
        from haute._graph_builders import _build_edges
        from haute._parser_bindings import assert_polars_parameters_bound
        from haute._types import NodeType

        graph = self._graph(["a"], [])
        raw = {"func_name": "a", "node_type": NodeType.POLARS, "param_names": []}
        with pytest.raises(KeyError, match="edge_param_names"):
            _build_edges([raw], [])
        with pytest.raises(KeyError, match="edge_param_names"):
            assert_polars_parameters_bound(graph, [raw])

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
                submodel_occurrence_paths=["child.py"],
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
                submodel_occurrence_paths=loaded,
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

    def test_occurrence_output_parameter_is_the_public_port_name(
        self,
        tmp_path: Path,
    ) -> None:
        _write(
            tmp_path,
            "modules/child.py",
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
                pipeline_dir="..",
            )

            @submodel.polars
            def transform(source: pl.LazyFrame) -> pl.LazyFrame:
                return source
            """,
        )
        parent = _write(
            tmp_path,
            "main.py",
            """
            import polars as pl
            import haute

            from modules import child

            pipeline = haute.Pipeline("main")

            @pipeline.polars
            def source() -> pl.LazyFrame:
                return pl.LazyFrame({"x": [1]})

            @pipeline.polars
            def sink(result: pl.LazyFrame) -> pl.LazyFrame:
                return result

            pipeline.submodel(child.submodel, "a")
            pipeline.connect("source", "a", target_port="source")
            pipeline.connect("a", "sink", source_port="result")
            """,
        )
        hierarchical = parse_pipeline_file(parent)
        assert len(hierarchical.nodes) == 3
        assert len(hierarchical.edges) == 2

        from haute.executor import execute_graph
        from haute.graph_utils import flatten_graph

        # Run as every executor runs it: the occurrence expanded into its nodes.
        results = execute_graph(flatten_graph(hierarchical), target_node_id="sink")
        assert results["sink"].status == "ok"
        assert results["sink"].preview == [{"x": 1}]

        parent_invalid = _write(
            tmp_path,
            "main_invalid.py",
            """
            import polars as pl
            import haute

            from modules import child

            pipeline = haute.Pipeline("main_invalid")

            @pipeline.polars
            def source() -> pl.LazyFrame:
                return pl.LazyFrame({"x": [1]})

            @pipeline.polars
            def sink(a: pl.LazyFrame) -> pl.LazyFrame:
                return a

            pipeline.submodel(child.submodel, "a")
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
        assert exc_info.value.context["unbound_parameters"] == ["a"]
        assert exc_info.value.context["connected_inputs"] == ["result"]

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
        node_a = next(node for node in document.nodes if node.authored_id == "a")
        node_b = next(node for node in document.nodes if node.authored_id == "b")
        assert node_a.availability == "ready"
        assert node_b.availability == "unavailable"
        b_diagnostics = [
            diagnostic
            for diagnostic in document.diagnostics
            if diagnostic.diagnostic_id in node_b.diagnostic_ids
        ]
        assert any(
            diagnostic.code == "node_parse_invalid"
            and diagnostic.scope == "node"
            and diagnostic.element_id == node_b.recovery_id
            and "Pipeline function parameters do not match" in diagnostic.message
            for diagnostic in b_diagnostics
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
        _write(
            tmp_path,
            "modules/child_valid.py",
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
                pipeline_dir="..",
            )

            @submodel.polars
            def first_step(source: pl.LazyFrame) -> pl.LazyFrame:
                return source
            """,
        )
        parent_valid = _write(
            tmp_path,
            "main_valid.py",
            """
            import polars as pl
            import haute

            from modules import child_valid

            pipeline = haute.Pipeline("main_valid")

            @pipeline.polars
            def source() -> pl.LazyFrame:
                return pl.LazyFrame({"x": [1]})

            @pipeline.polars
            def sink(result: pl.LazyFrame) -> pl.LazyFrame:
                return result

            pipeline.submodel(child_valid.submodel, "a")
            pipeline.connect("source", "a", target_port="source")
            pipeline.connect("a", "sink", source_port="result")
            """,
        )
        graph = parse_pipeline_file(parent_valid)
        assert graph is not None

        _write(
            tmp_path,
            "modules/child_invalid.py",
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
                pipeline_dir="..",
            )

            @submodel.polars
            def first_step(unbound_name: pl.LazyFrame) -> pl.LazyFrame:
                return unbound_name
            """,
        )
        parent_invalid = _write(
            tmp_path,
            "main_invalid.py",
            """
            import polars as pl
            import haute

            from modules import child_invalid

            pipeline = haute.Pipeline("main_invalid")

            @pipeline.polars
            def source() -> pl.LazyFrame:
                return pl.LazyFrame({"x": [1]})

            @pipeline.polars
            def sink(result: pl.LazyFrame) -> pl.LazyFrame:
                return result

            pipeline.submodel(child_invalid.submodel, "a")
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


def _transform_graph(config: dict) -> dict:
    """A frame source feeding a transform named ``scaled`` with *config*."""
    return {
        "nodes": [
            {
                "id": "source",
                "data": {
                    "label": "source",
                    "nodeType": "polars",
                    "config": {"code": 'df = pl.LazyFrame({"x": [100.0]})'},
                },
            },
            {"id": "scaled", "data": {"label": "scaled", "nodeType": "polars", "config": config}},
        ],
        "edges": [{"id": "e1", "source": "source", "target": "scaled"}],
    }


class TestKeywordOnlyParametersAtSave:
    """Both saves hold a submitted binding to the contract the parser records."""

    @pytest.mark.parametrize(
        ("parameters", "extra", "message"),
        [
            pytest.param(
                [{"name": "source", "default": 1}], {}, "the name of an input", id="input"
            ),
            pytest.param(
                [{"name": "factor", "default": 1}], {"steps": []}, "beside steps", id="steps"
            ),
            pytest.param([{"name": "factor"}], {}, "{name, default} records", id="shape"),
            pytest.param([{"name": "df", "default": 1}], {}, "reserved", id="reserved"),
            pytest.param([], {}, "non-empty list", id="empty"),
            pytest.param([{"name": "class", "default": 1}], {}, "not a Python name", id="keyword"),
            pytest.param(
                [{"name": "f", "default": 1}, {"name": "f", "default": 2}],
                {},
                "appears twice",
                id="duplicate",
            ),
            pytest.param([{"name": "f", "default": [1]}], {}, "must be None", id="list"),
        ],
    )
    def test_the_full_save_refuses_a_binding_the_file_cannot_carry(
        self, client, monkeypatch, tmp_path, parameters, extra, message
    ) -> None:
        from tests.conftest import current_source_revision

        monkeypatch.chdir(tmp_path)
        config = {"code": "df = source", "keyword_parameters": parameters, **extra}
        response = client.post(
            "/api/pipeline/save",
            json={
                "name": "main",
                "description": "",
                "graph": _transform_graph(config),
                "source_file": "main.py",
                "base_revision": current_source_revision(tmp_path / "main.py", tmp_path),
            },
        )
        assert response.status_code == 400, response.text
        assert message in response.json()["detail"]
        assert "scaled" in response.json()["detail"]
        assert not (tmp_path / "main.py").exists()

    def test_the_full_save_writes_a_binding_back(self, client, monkeypatch, tmp_path) -> None:
        from tests.conftest import current_source_revision

        monkeypatch.chdir(tmp_path)
        config = {
            "code": "df = source.with_columns(y=pl.col('x') * factor)",
            "keyword_parameters": [{"name": "factor", "default": 1.2}],
        }
        response = client.post(
            "/api/pipeline/save",
            json={
                "name": "main",
                "description": "",
                "graph": _transform_graph(config),
                "source_file": "main.py",
                "base_revision": current_source_revision(tmp_path / "main.py", tmp_path),
            },
        )
        assert response.status_code == 200, response.text
        saved = parse_pipeline_file(tmp_path / "main.py")
        scaled = next(node for node in saved.nodes if node.id == "scaled")
        assert scaled.data.config["keyword_parameters"] == [{"name": "factor", "default": 1.2}]
