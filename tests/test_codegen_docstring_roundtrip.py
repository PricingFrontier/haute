"""Phase 5 Wave 9D #122 pathological docstring round-trip tests.

Codegen builds function docstrings by interpolating a sanitized version of
``node.data.description`` into a triple-quoted-docstring template (see
``_codegen_builders._sanitize_description`` and the per-type templates
that reference ``{description}`` between triple quotes).  The sanitiser
makes a best-effort pass at triple-quote / trailing-backslash handling,
but it modifies the description in place -- so the docstring as-observed
via ``ast.get_docstring`` does NOT match the original description
bit-for-bit in general.

This file exhaustively pins the behaviour of that pipeline against
pathological inputs a user could reasonably put into a node description:

* triple-quote sequences near the boundary;
* Python escape sequences (backslash-n, backslash-t, etc.);
* raw-string-style descriptions;
* unicode content (multi-byte, combining marks, mathematical symbols);
* trailing backslashes (single and double);
* multi-line descriptions with varying internal indentation;
* the empty string and a single whitespace-only docstring;
* descriptions that contain the preserved-block sentinel marker verbatim
  (the docstring must not be mistaken for a preserved block);
* descriptions that start with lines resembling code comments;
* module-level bare string statements that Python treats as expression
  docstrings.

For every input we run two assertions:

1. The generated code compiles cleanly (``ast.parse`` raises nothing)
   -- this is the *syntactic safety* invariant.
2. The docstring as extracted by ``ast.get_docstring`` equals the
   original description -- the *round-trip* invariant.  This is the one
   Wave 9D tightens.  Some cases currently fail invariant (2) today
   (triple quotes get converted; escape sequences are stored as their
   resolved form, not their literal backslash-letter form; multi-line
   descriptions get dedented by ``ast.get_docstring``).  Those cases are
   marked ``pytest.mark.xfail(strict=True)`` so that when the dev fix
   lands they'll surface as unexpected passes and be flipped into
   regular passes.

The final ``TestTortureEndToEnd`` test embeds a pathological description
inside a submodel-nested function and runs the full ``graph_to_code_multi``
end-to-end -- the production surface that could silently emit corrupt
Python.
"""

from __future__ import annotations

import ast
from typing import Any

import pytest

from haute.codegen import _node_to_code, graph_to_code_multi
from haute.graph_utils import PipelineGraph

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_polars_node(description: str, *, label: str = "Transform") -> Any:
    """Build a minimal polars node carrying *description*.

    Uses the ``polars`` transform with a trivial code body so codegen
    produces the canonical ``@pipeline.polars`` decorator + function body
    + docstring.  The passthrough ``df = upstream`` keeps the body
    non-empty (a requirement for the polars builder when source_names
    are provided) without adding irrelevant noise.
    """
    from haute.graph_utils import GraphNode

    return GraphNode.model_validate(
        {
            "id": f"n_{label}",
            "data": {
                "label": label,
                "nodeType": "polars",
                "config": {"code": "df = upstream"},
                "description": description,
            },
        }
    )


def _generate_single_node_code(description: str, label: str = "Transform") -> str:
    """Generate code for a single polars node carrying *description*."""
    node = _build_polars_node(description, label=label)
    return _node_to_code(node, source_names=["upstream"])


def _extract_docstring(code: str, func_name: str) -> str:
    """Locate *func_name* in *code*, return its AST-level docstring.

    Wraps the node code in the minimum preamble required for parseability
    (``import polars as pl`` + pipeline stub) so standalone node code
    parses cleanly.
    """
    wrapper = f"import polars as pl\nimport haute\npipeline = haute.Pipeline('test')\n\n{code}\n"
    tree = ast.parse(wrapper)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == func_name:
            return ast.get_docstring(node) or ""
    raise AssertionError(f"function {func_name!r} not found in generated code")


def _assert_generated_parses(code: str) -> None:
    """Assert the generated node code is syntactically valid Python."""
    wrapper = f"import polars as pl\nimport haute\npipeline = haute.Pipeline('test')\n\n{code}\n"
    ast.parse(wrapper)  # raises SyntaxError on failure


# ---------------------------------------------------------------------------
# Pathological docstring inputs
# ---------------------------------------------------------------------------


_COMPILE_CASES_SAFE_TODAY = [
    # 1. Triple-quote-like sequences near the closing quote.
    pytest.param('a """ b', id="triple-quote-middle"),
    pytest.param('content ending """', id="triple-quote-end"),
    pytest.param('""" at start', id="triple-quote-start"),
    pytest.param('a """" b', id="four-quotes"),
    # 2. Escape sequences (stored as literal backslash-letter in the
    #    Python source, resolved by the interpreter at load time).
    pytest.param(r"newline=\n tab=\t", id="escape-n-t"),
    pytest.param(r"a \" b", id="escaped-quote"),
    # 4. Unicode (multi-byte, combining marks, mathematical symbols).
    pytest.param("café — 42°C — 数学 — 𝓜𝒶𝓉𝒽", id="unicode-mixed"),
    pytest.param("RTL: שלום עולם", id="rtl"),
    pytest.param("zero-width joiner: 👨\u200d👩\u200d👧", id="zwj"),
    # 5. Trailing backslashes.
    pytest.param("line1\\", id="trailing-single-backslash"),
    pytest.param("line1\\\\", id="trailing-double-backslash"),
    # 6. Multi-line with varying indentation.
    pytest.param("line1\n  line2\n    line3", id="multiline-indent"),
    pytest.param("line1\nline2\n  line3\n line4", id="multiline-ragged"),
    # 7. Empty / whitespace-only.
    pytest.param("", id="empty"),
    pytest.param(" ", id="single-space"),
    pytest.param("   \n   ", id="whitespace-lines"),
    # 8. Pathological embedded content.
    pytest.param('"""', id="pure-triple-quote"),
    pytest.param('""""""', id="two-triple-quotes"),
    # 9. Preserved-block sentinel inside a docstring.
    pytest.param("contains # haute:preserve-start marker", id="sentinel-inside"),
    pytest.param("# haute:preserve-end at the end", id="preserve-end-marker"),
    # 10. Leading # lines that look like code comments.
    pytest.param("# this looks like a comment\nreal text", id="comment-leader"),
    # 11. Other common gotchas.
    pytest.param("description with 'single' and \"double\" quotes", id="mixed-quotes"),
    pytest.param(
        "description with { format } braces {that mean} nothing",
        id="format-braces",
    ),
]

# Cases that historically broke the compile step because codegen
# interpolated backslash escape sequences (particularly backslash-U,
# backslash-N, backslash-u) from the description directly into a real
# Python triple-quoted string literal.  Those sequences were reinterpreted
# by the Python parser and raised SyntaxError at ``ast.parse`` time.
# Post-fix (Wave 9D #122): codegen now escapes every backslash in the
# description before interpolation, so user-typed backslash content stays
# literal and does not trigger re-parsing.
_COMPILE_CASES_BROKEN_TODAY = [
    pytest.param(
        r"C:\Users\foo\bar.txt",
        id="windows-path-unicode-escape",
    ),
    pytest.param(
        r"has \N{LATIN SMALL LETTER E} named escape",
        id="named-escape",
    ),
]


class TestDocstringCompiles:
    """Syntactic safety: every pathological input must compile.

    These tests assert invariant (1): the generated Python parses.  The
    safe-today matrix pins *current* correct behaviour -- a regression
    here means codegen started emitting syntactically invalid code,
    which is a higher-severity bug than round-trip drift.  The
    broken-today matrix is xfail(strict=True) so the dev fix flips it
    to unexpected-pass.
    """

    @pytest.mark.parametrize("description", _COMPILE_CASES_SAFE_TODAY)
    def test_compiles_safe_today(self, description: str) -> None:
        code = _generate_single_node_code(description)
        _assert_generated_parses(code)

    @pytest.mark.parametrize("description", _COMPILE_CASES_BROKEN_TODAY)
    def test_compiles_broken_today(self, description: str) -> None:
        """Currently-broken cases -- each parameter carries an xfail mark."""
        code = _generate_single_node_code(description)
        _assert_generated_parses(code)


class TestDocstringRoundTrip:
    """Round-trip invariant: ``ast.get_docstring(fn) == original_description``.

    This is the tightened invariant in Wave 9D #122.  Some inputs
    currently fail — those are marked ``xfail(strict=True)`` so the
    dev fix flips them to unexpected-pass and the developer is forced
    to remove the marker.  Unmarked cases are tests that currently pass
    and must continue to pass after the fix.
    """

    def test_simple_ascii_description_roundtrips(self) -> None:
        desc = "A simple description with no special characters."
        code = _generate_single_node_code(desc)
        assert _extract_docstring(code, "Transform") == desc

    def test_single_line_with_single_and_double_quotes(self) -> None:
        desc = "has 'single' and \"double\" quotes"
        code = _generate_single_node_code(desc)
        assert _extract_docstring(code, "Transform") == desc

    def test_trailing_single_backslash_roundtrips(self) -> None:
        """Trailing single backslash currently round-trips (sanitiser pads evenly)."""
        desc = "line1\\"
        code = _generate_single_node_code(desc)
        assert _extract_docstring(code, "Transform") == desc

    def test_comment_looking_first_line_roundtrips(self) -> None:
        desc = "# this looks like a comment\nreal text"
        code = _generate_single_node_code(desc)
        assert _extract_docstring(code, "Transform") == desc

    # ----- Currently-broken cases pinned to xfail(strict=True) --------------

    def test_triple_quote_content_roundtrips(self) -> None:
        desc = 'a """ b'
        code = _generate_single_node_code(desc)
        assert _extract_docstring(code, "Transform") == desc

    def test_pure_triple_quote_roundtrips(self) -> None:
        desc = '"""'
        code = _generate_single_node_code(desc)
        assert _extract_docstring(code, "Transform") == desc

    def test_literal_backslash_n_roundtrips(self) -> None:
        desc = r"newline=\n tab=\t backslash=\\"
        code = _generate_single_node_code(desc)
        assert _extract_docstring(code, "Transform") == desc

    def test_multiline_varying_indent_roundtrips(self) -> None:
        desc = "line1\n  line2\n    line3"
        code = _generate_single_node_code(desc)
        assert _extract_docstring(code, "Transform") == desc

    def test_empty_description_roundtrips(self) -> None:
        desc = ""
        code = _generate_single_node_code(desc)
        assert _extract_docstring(code, "Transform") == desc

    def test_whitespace_only_description_roundtrips(self) -> None:
        desc = " "
        code = _generate_single_node_code(desc)
        assert _extract_docstring(code, "Transform") == desc

    # ----- Cases expected to currently pass --------------------------------

    def test_unicode_bmp_roundtrips(self) -> None:
        """Latin-1/BMP unicode survives intact today."""
        desc = "café — 42°C"
        code = _generate_single_node_code(desc)
        assert _extract_docstring(code, "Transform") == desc

    def test_unicode_astral_roundtrips(self) -> None:
        """Astral-plane unicode (> U+FFFF) survives intact today."""
        desc = "astral: 𝓜𝒶𝓉𝒽"
        code = _generate_single_node_code(desc)
        assert _extract_docstring(code, "Transform") == desc

    def test_rtl_unicode_roundtrips(self) -> None:
        """Right-to-left scripts survive intact today."""
        desc = "RTL: שלום עולם"
        code = _generate_single_node_code(desc)
        assert _extract_docstring(code, "Transform") == desc

    def test_preserve_sentinel_literal_roundtrips(self) -> None:
        """Preserved-block marker text inside a docstring must NOT be
        treated as an actual preserved block by the parser.  Because the
        marker is inside the function body (a docstring), the preserved-
        block extractor does not look inside it — it scans top-level
        source lines only.  This test pins that the sentinel string
        can appear in a docstring without the parser getting confused.
        """
        desc = "contains # haute:preserve-start marker"
        code = _generate_single_node_code(desc)
        assert _extract_docstring(code, "Transform") == desc

    def test_module_bare_string_literal_is_module_docstring(self) -> None:
        """A module-level bare string *is* the module docstring.

        ``graph_to_code`` emits ``\"\"\"Pipeline: <name>\"\"\"`` as the
        module docstring.  Round-trip must preserve that exact content
        via ``ast.get_docstring`` at module level.
        """
        from haute.codegen import graph_to_code

        graph = PipelineGraph.model_validate(
            {
                "nodes": [
                    {
                        "id": "src",
                        "data": {
                            "label": "Src",
                            "nodeType": "dataInput",
                            "config": {"path": "d.parquet"},
                        },
                    }
                ],
                "edges": [],
            }
        )
        code = graph_to_code(graph, pipeline_name="my_pipe")
        tree = ast.parse(code)
        assert ast.get_docstring(tree) == "Pipeline: my_pipe"


class TestDocstringExpressionStatement:
    """#11: description is a bare string at module top.

    ``graph_to_code`` emits a module-level docstring as the first
    statement of the generated file.  This test pins that a description
    with pathological content produces a *valid* module-level docstring
    even when the pipeline name contains bad content.  The pipeline name
    is not sanitised the same way the description is, so some pathological
    ``pipeline_name`` values may or may not survive — we test both
    invariants (compiles; and, where possible, round-trips via
    ``ast.get_docstring(tree)``).
    """

    def test_pipeline_name_simple_ascii(self) -> None:
        from haute.codegen import graph_to_code

        graph = PipelineGraph.model_validate({"nodes": [], "edges": []})
        code = graph_to_code(graph, pipeline_name="simple_name")
        tree = ast.parse(code)  # must parse
        module_docstring = ast.get_docstring(tree)
        assert module_docstring is not None
        assert "simple_name" in module_docstring


# ---------------------------------------------------------------------------
# Remediation 5.3: the pipeline/submodel NAME is user-controlled and must be
# sanitized before interpolation into the module docstring — a name with
# triple quotes or backslashes must neither break the file nor escape the
# docstring, and the name must round-trip stably (fixpoint).
# ---------------------------------------------------------------------------


# Names that exactly round-trip into the module docstring content
# (single-line, no edge whitespace — cleandoc is a no-op for them).
_EXACT_HEADER_NAMES = [
    pytest.param('evil""" breakout', id="triple-quote"),
    pytest.param('x""" + __import__("os").system("echo pwned") + """y', id="code-injection"),
    pytest.param("trailing\\", id="trailing-backslash"),
    pytest.param(r"C:\Users\pipe", id="windows-path-unicode-escape"),
]


class TestPipelineNameDocstringSafety:
    """Adversarial pipeline names through the module-docstring header."""

    @pytest.mark.parametrize("name", _EXACT_HEADER_NAMES)
    def test_adversarial_name_parses_and_stays_inside_docstring(self, name: str) -> None:
        from haute.codegen import graph_to_code

        graph = PipelineGraph.model_validate({"nodes": [], "edges": []})
        code = graph_to_code(graph, pipeline_name=name)
        tree = ast.parse(code)  # must not SyntaxError
        # The header must be a plain constant-string docstring — a name like
        # 'x""" + __import__(...) + """y' must NOT become an executable
        # module-level expression.
        first = tree.body[0]
        assert isinstance(first, ast.Expr)
        assert isinstance(first.value, ast.Constant)
        assert isinstance(first.value.value, str)
        assert ast.get_docstring(tree) == f"Pipeline: {name}"

    def test_newline_name_parses_and_contains_name_lines(self) -> None:
        from haute.codegen import graph_to_code

        graph = PipelineGraph.model_validate({"nodes": [], "edges": []})
        code = graph_to_code(graph, pipeline_name="two\nlines")
        tree = ast.parse(code)
        docstring = ast.get_docstring(tree)
        assert docstring is not None
        assert docstring.startswith("Pipeline:")
        assert "two" in docstring
        assert "lines" in docstring

    @pytest.mark.parametrize("name", _EXACT_HEADER_NAMES)
    def test_adversarial_name_roundtrips_to_fixpoint(self, name: str) -> None:
        """The parse side recovers the exact name (from the ``haute.Pipeline``
        literal) and re-saving reaches a byte-stable fixpoint immediately."""
        from haute.codegen import graph_to_code
        from haute.parser import parse_pipeline_source

        graph = PipelineGraph.model_validate({"nodes": [], "edges": []})
        code1 = graph_to_code(graph, pipeline_name=name)
        parsed1 = parse_pipeline_source(code1, source_file="p.py")
        assert parsed1.pipeline_name == name

        code2 = graph_to_code(parsed1, pipeline_name=parsed1.pipeline_name)
        parsed2 = parse_pipeline_source(code2, source_file="p.py")
        assert parsed2.pipeline_name == name
        code3 = graph_to_code(parsed2, pipeline_name=parsed2.pipeline_name)
        assert code3 == code2 == code1

    @pytest.mark.parametrize(
        "sm_name",
        [
            pytest.param('evil""" breakout', id="triple-quote"),
            pytest.param("trailing\\", id="trailing-backslash"),
        ],
    )
    def test_submodel_name_shares_the_sanitizer(self, sm_name: str) -> None:
        """The submodel header must use the SAME sanitizer as the pipeline
        header: the file parses, the header stays a constant docstring, and
        ``haute.Submodel(...)`` carries the exact name."""
        from haute._ast_helpers import _extract_submodel_meta

        graph = PipelineGraph.model_validate(
            {
                "nodes": [
                    {
                        "id": "instance_sm",
                        "type": "submodel",
                        "data": {
                            "label": "sm_alias",
                            "nodeType": "submodel",
                            "config": {"definitionId": "definition_sm", "alias": "sm_alias"},
                        },
                    }
                ],
                "edges": [],
                "submodels": {
                    "definition_sm": {
                        "definitionId": "definition_sm",
                        "file": "modules/sm.py",
                        "graph": {
                            "pipeline_name": sm_name,
                            "nodes": [
                                {
                                    "id": "src",
                                    "data": {
                                        "label": "Src",
                                        "nodeType": "dataInput",
                                        "config": {"path": "d.parquet"},
                                    },
                                }
                            ],
                            "edges": [],
                        },
                        "inputPorts": [],
                        "outputPorts": [],
                    },
                },
            }
        )
        files = graph_to_code_multi(graph, pipeline_name="main")
        sm_code = files["modules/sm.py"]
        sm_tree = ast.parse(sm_code)
        first = sm_tree.body[0]
        assert isinstance(first, ast.Expr)
        assert isinstance(first.value, ast.Constant)
        assert ast.get_docstring(sm_tree) == f"Submodel: {sm_name}"
        parsed_name, _ = _extract_submodel_meta(sm_tree)
        assert parsed_name == sm_name
        # Determinism: a second emission is byte-identical.
        files_again = graph_to_code_multi(graph, pipeline_name="main")
        assert files_again["modules/sm.py"] == sm_code


# ---------------------------------------------------------------------------
# Truly pathological end-to-end torture test
# ---------------------------------------------------------------------------


class TestTortureEndToEnd:
    """Kitchen-sink torture: pathological docstring, nested inside a function,
    inside a submodel, under ``graph_to_code_multi``.

    This is the production surface — if it silently emits syntactically
    invalid Python, a ``Save Pipeline`` call would write a broken .py
    file to disk that the user might not notice until they try to run
    or reload the project.  The test must hold for *every* pathological
    description in the matrix below.
    """

    TORTURE_DESCRIPTIONS = [
        # triple + sentinel + unicode — the combined smoke test
        'a """ b — café — # haute:preserve-start — 数学',
        # escape soup
        r"escapes: \n \t \\ \" and more text",
        # trailing backslash + triple
        'ends with \\\\"""',
        # multi-line with unicode + indent
        "header\n  café\n    数学",
    ]

    @pytest.mark.parametrize("description", TORTURE_DESCRIPTIONS)
    def test_submodel_nested_pathological_description_compiles(self, description: str) -> None:
        """End-to-end: submodel file with pathological docstring must compile.

        Asserts invariant (1): the generated submodel file is
        syntactically valid Python.  Round-trip (invariant 2) is not
        asserted here — the per-input round-trip cases above already
        pin it.  This test defends the *aggregate* surface: no matter
        how codegen composes preserved blocks, submodel headers, node
        bodies and wire-up, the emitted file must always be parseable.
        """
        graph = PipelineGraph.model_validate(
            {
                "nodes": [
                    {
                        "id": "torture_instance",
                        "type": "submodel",
                        "data": {
                            "label": "torture_sm",
                            "nodeType": "submodel",
                            "config": {"definitionId": "torture_sm", "alias": "torture_sm"},
                        },
                    }
                ],
                "edges": [],
                "submodels": {
                    "torture_sm": {
                        "definitionId": "torture_sm",
                        "file": "modules/torture_sm.py",
                        "graph": {
                            "nodes": [
                                {
                                    "id": "src",
                                    "data": {
                                        "label": "Src",
                                        "nodeType": "dataInput",
                                        "config": {"path": "d.parquet"},
                                    },
                                },
                                {
                                    "id": "inner",
                                    "data": {
                                        "label": "Inner",
                                        "nodeType": "polars",
                                        "config": {"code": "df = Src"},
                                        "description": description,
                                    },
                                },
                            ],
                            "edges": [
                                {"id": "e", "source": "src", "target": "inner"},
                            ],
                        },
                        "inputPorts": [],
                        "outputPorts": [],
                    },
                },
            }
        )
        files = graph_to_code_multi(graph, pipeline_name="main")
        assert "modules/torture_sm.py" in files
        sm_code = files["modules/torture_sm.py"]
        # Must parse
        tree = ast.parse(sm_code)
        # Must contain the Inner function
        inner_fns = [
            n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "Inner"
        ]
        assert len(inner_fns) == 1, f"expected 1 Inner() function, got {len(inner_fns)}"
        # The function must have a non-None docstring.  We do not assert
        # equality with *description* here because round-trip is tested
        # per-input above; this test defends the aggregate compile
        # surface.
        assert ast.get_docstring(inner_fns[0]) is not None

    def test_submodel_nested_torture_roundtrip(self) -> None:
        """Strictest end-to-end: pathological docstring nested in a submodel
        inside graph_to_code_multi must compile AND round-trip.

        This demonstrates Wave 9D #122's real user impact: a user editing
        a pipeline in the GUI, saving, then reloading would see a
        description change under them if the current sanitiser mutates
        it.  The strict round-trip assertion below is the invariant
        we want; it fails today because ``_sanitize_description`` swaps
        triple double-quotes for triple single-quotes.
        """
        description = 'a """ b — café — # haute:preserve-start — 数学'
        graph = PipelineGraph.model_validate(
            {
                "nodes": [
                    {
                        "id": "torture_instance",
                        "type": "submodel",
                        "data": {
                            "label": "torture_sm",
                            "nodeType": "submodel",
                            "config": {"definitionId": "torture_sm", "alias": "torture_sm"},
                        },
                    }
                ],
                "edges": [],
                "submodels": {
                    "torture_sm": {
                        "definitionId": "torture_sm",
                        "file": "modules/torture_sm.py",
                        "graph": {
                            "nodes": [
                                {
                                    "id": "src",
                                    "data": {
                                        "label": "Src",
                                        "nodeType": "dataInput",
                                        "config": {"path": "d.parquet"},
                                    },
                                },
                                {
                                    "id": "inner",
                                    "data": {
                                        "label": "Inner",
                                        "nodeType": "polars",
                                        "config": {"code": "df = Src"},
                                        "description": description,
                                    },
                                },
                            ],
                            "edges": [
                                {"id": "e", "source": "src", "target": "inner"},
                            ],
                        },
                        "inputPorts": [],
                        "outputPorts": [],
                    },
                },
            }
        )
        files = graph_to_code_multi(graph, pipeline_name="main")
        tree = ast.parse(files["modules/torture_sm.py"])
        inner_fn = next(
            n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "Inner"
        )
        # Strict round-trip: assert equality.  Fails today; post-fix
        # this must pass and the xfail marker must be removed.
        assert ast.get_docstring(inner_fn) == description
