"""Round-trip safety of code extraction — remediation 5.1 (C5) + 5.6.

CODE_REVIEW.md C5: ``_unwrap_chain_assignment`` assumed the first ``(`` and
the last ``)`` of a ``df = (...)`` statement were a matched wrapping pair.
They often aren't (``df = (a + b) * c``, ``df = (x.filter(...)).join(...)``),
so one save/load cycle through the GUI corrupted the user's code into
invalid Python.  The fix proves redundancy with the AST before unwrapping
and keeps everything unprovable verbatim.

CODE_REVIEW.md round-trip cluster (item 5.6): External File extraction once
treated *every* ``import``/``from`` line in the body prefix as generated
loader boilerplate, so a user import was silently dropped on extraction and
the re-emitted file no longer ran standalone.  An External File hook now
generates exactly one statement before the user's code — ``df = <first
input>`` — so every import is the user's, wherever it sits, and the binding
is generated only as the first statement.

The production path under test (the GUI save/load cycle):

    .py source -> parse_pipeline_source -> config["code"] (the code box)
               -> graph_to_code         -> .py source again

Every fixture must satisfy:

* the extracted code box is valid Python (never silently corrupted),
* the re-emitted file compiles,
* extraction is a fixpoint: re-extracting the re-emitted file yields the
  identical code box (save/load/save stability),
* re-emitting again produces an identical node block (idempotence).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from haute._code_extraction import (
    INCOMPLETE_TRANSFORM_BODY,
    POLARS_OUTPUT_DECLARATION,
    _unwrap_chain_assignment,
    extract_user_code,
)
from haute.codegen import graph_to_code
from haute.errors import ParseError
from haute.parser import parse_pipeline_source
from tests.conftest import write_data_input_config

# ---------------------------------------------------------------------------
# Helpers — drive the real production save/load cycle
# ---------------------------------------------------------------------------

_PIPELINE_HEADER = '''import polars as pl
from haute import Pipeline

pipeline = Pipeline("demo")


@pipeline.data_input(config="config/data_input/up.json")
def up():
    """src"""

'''

_POLARS_NODE = '''
@pipeline.polars
def clean(up: pl.LazyFrame) -> pl.LazyFrame:
    """t"""
{body}
'''

_EXTERNAL_NODE = '''
@pipeline.external_file(config="config/load_file/lookup.json")
def lookup(up: pl.LazyFrame, *, obj) -> pl.LazyFrame:
    """ext"""
{body}
'''


_DISCONNECTED_EXTERNAL_NODE = '''
@pipeline.external_file(config="config/load_file/lookup.json")
def lookup(*, obj) -> pl.LazyFrame:
    """ext"""
{body}
'''


def _write_sidecars(base_dir: Path) -> None:
    write_data_input_config(base_dir, "up", "data.parquet")
    load_dir = base_dir / "config" / "load_file"
    load_dir.mkdir(parents=True, exist_ok=True)
    (load_dir / "lookup.json").write_text(
        json.dumps({"path": "models/lookup.pkl", "fileType": "pickle"})
    )


def _indent_body(lines: list[str]) -> str:
    return "\n".join(f"    {line}" if line.strip() else line for line in lines)


def _node_block(source: str, func_name: str) -> str:
    """Return the emitted top-level block containing ``def {func_name}(``.

    Codegen separates top-level blocks with blank lines; none of the
    fixtures contain blank lines inside the node body, so splitting on a
    blank line isolates the decorator + function block.
    """
    blocks = [b for b in source.split("\n\n") if f"def {func_name}(" in b]
    assert len(blocks) == 1, f"expected exactly one block for {func_name}, got {len(blocks)}"
    return blocks[0]


def _roundtrip_node_code(
    node_template: str,
    body_lines: list[str],
    node_id: str,
    base_dir: Path,
) -> tuple[str, str, str, str]:
    """Run source -> parse -> codegen -> parse -> codegen.

    Returns ``(box1, regen1, box2, regen2)`` where ``boxN`` is the
    extracted code-box content after the Nth parse and ``regenN`` the Nth
    re-emitted module source.  Asserts both regenerated modules compile —
    a save must never produce an unrunnable file.
    """
    _write_sidecars(base_dir)
    source = _PIPELINE_HEADER + node_template.format(body=_indent_body(body_lines))
    compile(source, "<fixture>", "exec")  # the fixture itself must be valid

    graph1 = parse_pipeline_source(source, source_file="demo.py", _base_dir=base_dir)
    box1 = next(n for n in graph1.nodes if n.id == node_id).data.config.get("code", "")

    regen1 = graph_to_code(graph1, pipeline_name="demo")
    compile(regen1, "<regen1>", "exec")

    graph2 = parse_pipeline_source(regen1, source_file="demo.py", _base_dir=base_dir)
    box2 = next(n for n in graph2.nodes if n.id == node_id).data.config.get("code", "")

    regen2 = graph_to_code(graph2, pipeline_name="demo")
    compile(regen2, "<regen2>", "exec")
    return box1, regen1, box2, regen2


# ---------------------------------------------------------------------------
# 5.1 / C5 — _unwrap_chain_assignment unit contract
# ---------------------------------------------------------------------------


class TestUnwrapChainAssignmentProof:
    """The unwrap must PROVE the parens are a redundant whole-RHS wrapper.

    Anything unprovable stays verbatim (``None``) — round-trip safety
    beats cosmetic unwrapping.
    """

    # --- the review's corruption cases must no longer unwrap ------------

    def test_binop_with_leading_paren_group_stays_verbatim(self):
        # Previously mangled to the invalid "a + b) * c".
        assert _unwrap_chain_assignment("df = (a + b) * c") is None

    def test_wrapped_call_then_method_stays_verbatim(self):
        # Previously mangled to the unbalanced
        # 'up.filter(pl.col("x") > 0)).join(other, on="id"'.
        code = 'df = (up.filter(pl.col("x") > 0)).join(other, on="id")'
        assert _unwrap_chain_assignment(code) is None

    def test_multi_statement_body_stays_verbatim(self):
        # Previously the trailing ')' of the LAST statement was stripped.
        code = 'df = (up.head(5))\ndf = df.select("x")'
        assert _unwrap_chain_assignment(code) is None

    def test_unparseable_body_stays_verbatim(self):
        assert _unwrap_chain_assignment("df = (broken") is None

    # --- load-bearing parens are never stripped --------------------------

    def test_multiline_chain_parens_are_load_bearing(self):
        # The wrapper provides line continuation; stripping it would make
        # the statement invalid AND the save path emits code boxes
        # verbatim, so expression-form output corrupts the file.
        code = "df = (\n    up\n    .filter(pl.col('x') > 0)\n)"
        assert _unwrap_chain_assignment(code) is None

    def test_generator_expression_parens_are_load_bearing(self):
        assert _unwrap_chain_assignment("df = (x for x in up)") is None

    def test_walrus_parens_are_load_bearing(self):
        assert _unwrap_chain_assignment("df = (x := up.select('a'))") is None

    def test_empty_tuple_stays_verbatim(self):
        assert _unwrap_chain_assignment("df = ()") is None

    # --- provably redundant wrappers unwrap, statement form preserved ----

    def test_single_line_redundant_wrapper_unwraps_to_statement(self):
        assert _unwrap_chain_assignment('df = (up.select("a"))') == 'df = up.select("a")'

    def test_nested_redundant_wrappers_unwrap_fully(self):
        assert _unwrap_chain_assignment('df = ((up.select("a")))') == 'df = up.select("a")'

    def test_no_space_variant_unwraps(self):
        assert _unwrap_chain_assignment('df=(up.select("a"))') == 'df=up.select("a")'

    def test_string_literal_parens_do_not_confuse_the_proof(self):
        code = 'df = (up.filter(pl.col("x") == ")unbalanced("))'
        assert _unwrap_chain_assignment(code) == 'df = up.filter(pl.col("x") == ")unbalanced(")'

    def test_multiline_unwraps_when_inner_parens_carry_continuation(self):
        code = 'df = (pl.col(\n    "x"\n))'
        assert _unwrap_chain_assignment(code) == 'df = pl.col(\n    "x"\n)'

    def test_unwrapped_result_is_always_valid_python(self):
        for code in (
            'df = (up.select("a"))',
            'df = ((up.select("a")))',
            'df = (up.filter(pl.col("x") == ")unbalanced("))',
            'df = (pl.col(\n    "x"\n))',
        ):
            result = _unwrap_chain_assignment(code)
            assert result is not None
            compile(result, "<unwrapped>", "exec")

    # --- non-matching inputs --------------------------------------------

    def test_non_matching_patterns_return_none(self):
        assert _unwrap_chain_assignment("x = 1") is None
        assert _unwrap_chain_assignment("result = foo()") is None
        assert _unwrap_chain_assignment("") is None
        assert _unwrap_chain_assignment('df = df.filter(pl.col("x") == "(weird)")') is None


# ---------------------------------------------------------------------------
# 5.1 / C5 — production save/load/save stability (polars)
# ---------------------------------------------------------------------------

# (fixture id, body lines, expected code box after the first parse)
_POLARS_FIXTURES = [
    pytest.param(
        ["df = (a + b) * c", "return df"],
        "df = (a + b) * c",
        id="binop-with-leading-paren-group",
    ),
    pytest.param(
        ['df = (up.filter(pl.col("x") > 0)).join(up, on="x")', "return df"],
        'df = (up.filter(pl.col("x") > 0)).join(up, on="x")',
        id="wrapped-call-then-method",
    ),
    pytest.param(
        ['df = df.filter(pl.col("x") == "(weird)")', "return df"],
        'df = df.filter(pl.col("x") == "(weird)")',
        id="parens-inside-string-literal",
    ),
    pytest.param(
        ['df = (up.filter(pl.col("x") == ")unbalanced("))', "return df"],
        'df = up.filter(pl.col("x") == ")unbalanced(")',
        id="evil-string-inside-redundant-wrapper",
    ),
    pytest.param(
        ['df = ((up.select("x")))', "return df"],
        'df = up.select("x")',
        id="nested-redundant-wrappers",
    ),
    pytest.param(
        [
            "df = (",
            "    up",
            '    .filter(pl.col("x") > 0)',
            ")",
            "return df",
        ],
        'df = (\n    up\n    .filter(pl.col("x") > 0)\n)',
        id="multiline-parenthesized-chain",
    ),
    pytest.param(
        ["df = (up.head(5))", 'df = df.select("x")', "return df"],
        'df = (up.head(5))\ndf = df.select("x")',
        id="multi-statement-starting-with-wrapper",
    ),
    pytest.param(
        ['df = up.filter(pl.col("x") > 0)', "return df"],
        'df = up.filter(pl.col("x") > 0)',
        id="plain-statement-control",
    ),
]


class TestChainAssignmentSaveLoadSave:
    """One save/load cycle must never corrupt the file (CODE_REVIEW C5)."""

    @pytest.mark.parametrize(("body_lines", "expected_box"), _POLARS_FIXTURES)
    def test_extraction_fixpoint_through_production_path(
        self,
        body_lines: list[str],
        expected_box: str,
        tmp_path: Path,
    ) -> None:
        box1, regen1, box2, regen2 = _roundtrip_node_code(
            _POLARS_NODE, body_lines, "clean", tmp_path
        )
        # Extraction never emits invalid Python into the code box.
        compile(box1, "<box>", "exec")
        assert box1 == expected_box
        # Fixpoint: extract -> re-emit -> extract is stable.
        assert box2 == box1
        # Node-level idempotence: saving twice emits the identical block.
        assert _node_block(regen2, "clean") == _node_block(regen1, "clean")

    def test_semantics_survive_the_roundtrip(self, tmp_path: Path) -> None:
        """The re-emitted transform must still ASSIGN df from the chain.

        Guards against "valid but dead" output: an expression-form code
        box re-emits as a no-op expression statement and the transform
        silently degrades to a passthrough.
        """
        _, regen1, _, _ = _roundtrip_node_code(
            _POLARS_NODE,
            ['df = ((up.select("x")))', "return df"],
            "clean",
            tmp_path,
        )
        fn_body = regen1.split("def clean(")[1]
        assert "df = up.select" in fn_body, (
            "the select chain must be re-emitted as an assignment to df, "
            f"not a dead expression statement:\n{fn_body}"
        )


class TestExtractionFailsLoudOnUnparseableBodies:
    """Established contract: unparseable bodies raise ParseError loudly.

    Extraction must never silently hand back (or invent) invalid Python.
    """

    def test_polars_unparseable_body_raises(self) -> None:
        with pytest.raises(ParseError):
            extract_user_code("    df = (a + b\n    return df", kind="polars", param_names=["up"])

    def test_hook_unparseable_body_raises(self) -> None:
        with pytest.raises(ParseError):
            extract_user_code("    df = df.foo(\n    return df", kind="hook")

    def test_external_unparseable_body_raises(self) -> None:
        with pytest.raises(ParseError):
            extract_user_code(
                "    df = up\n    df = df.foo(\n    return df",
                kind="external",
                param_names=["up"],
            )


def test_generated_polars_output_declaration_is_not_user_code() -> None:
    body = "df: pl.LazyFrame\n_ = source\nreturn df"

    assert extract_user_code(body, kind="polars", param_names=["source"]) == "_ = source"


def test_generated_output_declaration_and_empty_placeholder_are_both_scaffold() -> None:
    body = POLARS_OUTPUT_DECLARATION + INCOMPLETE_TRANSFORM_BODY

    assert extract_user_code(body, kind="polars", param_names=["source"]) == ""


# ---------------------------------------------------------------------------
# 5.6 — external-file imports survive extraction wherever they appear
# ---------------------------------------------------------------------------

#: The External File hook's one generated leading statement, for input ``up``.
_GENERATED_BINDING = ["df = up"]


def _external(body_lines: list[str], param_names: list[str] | None = None) -> str:
    return extract_user_code(
        "\n".join(body_lines), kind="external", param_names=param_names or ["up"]
    )


class TestExternalImportPreservation:
    """User imports are user code regardless of where they sit.

    The only generated statement before an External File hook's code is
    ``df = <first input>`` (see ``_configured_node`` in the codegen
    builders), and only as the first statement. Every import is the user's:
    after the binding, between statements, or before a binding the user
    wrote themselves.
    """

    def test_import_directly_after_the_binding_is_preserved(self) -> None:
        result = _external(
            [
                *_GENERATED_BINDING,
                "import numpy as np",
                "df = df.with_columns(pred=pl.lit(float(np.float64(0.5))))",
                "return df",
            ]
        )
        assert result == (
            "import numpy as np\ndf = df.with_columns(pred=pl.lit(float(np.float64(0.5))))"
        )

    def test_generated_binding_is_stripped(self) -> None:
        result = _external(
            [
                "df = features",
                "df = df.with_columns(prediction=pl.lit(obj))",
                "return df",
            ],
            ["features"],
        )

        assert result == "df = df.with_columns(prediction=pl.lit(obj))"

    def test_assignment_from_a_non_first_input_is_user_code(self) -> None:
        result = _external(["df = regions", "return df"], ["features", "regions"])

        assert result == "df = regions"

    def test_from_import_after_the_binding_is_preserved(self) -> None:
        result = _external(
            [
                *_GENERATED_BINDING,
                "from json import dumps",
                'df = df.with_columns(meta=pl.lit(dumps({"k": 1})))',
                "return df",
            ]
        )
        assert result.startswith("from json import dumps\n")
        assert "dumps" in result

    def test_import_between_user_statements_is_preserved(self) -> None:
        result = _external(
            [
                *_GENERATED_BINDING,
                "df = df.with_columns(a=pl.lit(1))",
                "import json",
                'df = df.with_columns(b=pl.lit(json.dumps({"k": 1})))',
                "return df",
            ]
        )
        assert result == (
            "df = df.with_columns(a=pl.lit(1))\n"
            "import json\n"
            'df = df.with_columns(b=pl.lit(json.dumps({"k": 1})))'
        )

    def test_multiple_imports_interleaved_with_code_are_preserved(self) -> None:
        result = _external(
            [
                *_GENERATED_BINDING,
                "import numpy as np",
                "df = df.with_columns(a=pl.lit(float(np.pi)))",
                "from json import dumps",
                'df = df.with_columns(b=pl.lit(dumps({"k": 1})))',
                "return df",
            ]
        )
        assert result == (
            "import numpy as np\n"
            "df = df.with_columns(a=pl.lit(float(np.pi)))\n"
            "from json import dumps\n"
            'df = df.with_columns(b=pl.lit(dumps({"k": 1})))'
        )

    def test_body_without_the_binding_keeps_its_imports(self) -> None:
        # No binding was generated (the user starts from the input by name),
        # so nothing is stripped — the imports belong to the user.
        result = _external(
            ["import numpy as np", "df = up.with_columns(y=pl.lit(float(np.pi)))", "return df"]
        )
        assert result == "import numpy as np\ndf = up.with_columns(y=pl.lit(float(np.pi)))"

    def test_import_before_the_binding_keeps_the_binding_as_user_code(self) -> None:
        # The binding is generated only as the first statement: after a user
        # import, ``df = up`` is the user's own line.
        result = _external(["import numpy as np", "df = up", "return df"])
        assert result == "import numpy as np\ndf = up"

    def test_pure_boilerplate_body_still_returns_empty(self) -> None:
        assert _external([*_GENERATED_BINDING, "return df"]) == ""

    def test_production_roundtrip_keeps_import_after_binding(self, tmp_path: Path) -> None:
        """RED before the fix: the import vanished from the code box and
        the re-emitted file referenced ``np`` without importing it —
        the saved file no longer ran standalone."""
        body_lines = [
            *_GENERATED_BINDING,
            "import numpy as np",
            "df = df.with_columns(pred=pl.lit(float(np.float64(0.5))))",
            "return df",
        ]
        box1, regen1, box2, regen2 = _roundtrip_node_code(
            _EXTERNAL_NODE, body_lines, "lookup", tmp_path
        )
        assert box1 == (
            "import numpy as np\ndf = df.with_columns(pred=pl.lit(float(np.float64(0.5))))"
        )
        # The re-emitted hook binds its input, then still imports numpy for
        # standalone use.
        fn_body = regen1.split("def lookup(")[1]
        assert fn_body.startswith('up: pl.LazyFrame, *, obj) -> pl.LazyFrame:\n    """ext"""\n')
        assert "    df = up\n    import numpy as np\n" in fn_body
        assert box2 == box1
        assert _node_block(regen2, "lookup") == _node_block(regen1, "lookup")

    def test_a_disconnected_hook_keeps_its_own_binding_of_obj(self, tmp_path: Path) -> None:
        """With no input nothing is generated before the code: ``df = obj`` is the user's.

        The keyword-only ``obj`` is not an input, so the binding matcher must
        not mistake the user's first line for the generated one.
        """
        box1, regen1, box2, regen2 = _roundtrip_node_code(
            _DISCONNECTED_EXTERNAL_NODE,
            ["df = obj", "df = df.head(1)", "return df"],
            "lookup",
            tmp_path,
        )
        assert box1 == "df = obj\ndf = df.head(1)"
        assert "def lookup(*, obj) -> pl.LazyFrame:\n" in regen1
        assert '    """ext"""\n    df = obj\n    df = df.head(1)\n    return df\n' in regen1
        assert box2 == box1
        assert _node_block(regen2, "lookup") == _node_block(regen1, "lookup")

    def test_production_roundtrip_interleaved_imports(self, tmp_path: Path) -> None:
        body_lines = [
            *_GENERATED_BINDING,
            "import numpy as np",
            "df = df.with_columns(a=pl.lit(float(np.pi)))",
            "from json import dumps",
            'df = df.with_columns(b=pl.lit(dumps({"k": 1})))',
            "return df",
        ]
        box1, regen1, box2, regen2 = _roundtrip_node_code(
            _EXTERNAL_NODE, body_lines, "lookup", tmp_path
        )
        assert box1 == (
            "import numpy as np\n"
            "df = df.with_columns(a=pl.lit(float(np.pi)))\n"
            "from json import dumps\n"
            'df = df.with_columns(b=pl.lit(dumps({"k": 1})))'
        )
        assert box2 == box1
        assert _node_block(regen2, "lookup") == _node_block(regen1, "lookup")
