"""Phase 6 #137 — pin user-code return-boundary detection against the AST migration.

The current implementation in ``haute._code_extraction`` detects ``return``
statements in user-authored node bodies via **line-based string heuristics**.
Two specific hot spots:

* ``_strip_trailing_return`` (``_code_extraction.py:262``) — pops trailing
  lines whose ``.strip()`` equals ``"return <var>"`` or ``""``.  Because it
  looks *only* at the last line's textual form, any inner helper whose
  final line happens to be ``return df`` (the same variable name the outer
  matcher is trying to strip) gets its tail silently deleted.

* ``_finalise_polars`` (``_code_extraction.py:270``) — Pattern 2 iterates
  the body line-by-line and replaces the *first* line starting with
  ``"return "`` with ``"df = "``, regardless of the line's lexical scope.
  This misfires on:

    - ``return`` inside a nested ``def`` — the inner function's return is
      rewritten to ``df = …`` inside the inner body (mutating the user's
      code).
    - ``return`` inside a class method — same as above.
    - ``return`` inside an ``async def`` — same as above.
    - Any line whose ``.strip()`` starts with ``"return "`` inside an
      arbitrary lexical block (``if`` / ``try`` / ``while``) — which is
      legal Python, but the line-based rewrite still fires.

  The helper also has a single-flag ``in_return`` gate that converts
  *exactly one* ``return`` and leaves any later returns untouched — so
  early-exit bodies (``if cond: return early; return late``) emit code
  with a stray ``return late`` that will fail when the codegen template
  appends its own ``return df``.

Dev fix (#137):
  Replace the line-heuristic in ``_finalise_polars`` and
  ``_strip_trailing_return`` with an ``ast.parse`` walk that filters for
  ``Return`` nodes whose OUTERMOST enclosing function is the node body
  itself.  Nested ``FunctionDef`` / ``AsyncFunctionDef`` / ``ClassDef`` /
  ``Lambda`` bodies are excluded.

Location of the heuristic (file:line):

  * ``src/haute/_code_extraction.py:105``
    — ``_extract_sentinel_user_code`` trailing-return strip.
  * ``src/haute/_code_extraction.py:262-267``
    — ``_strip_trailing_return``.
  * ``src/haute/_code_extraction.py:287-301``
    — ``_finalise_polars`` Pattern 2 ``return`` → ``df =`` rewrite.
  * ``src/haute/_code_extraction.py:314-319``
    — ``_finalise_external`` flat ``== "return df"`` check.

Scope of the dev's fix:
  Four internal helpers in a single file.  The public surface
  (``_extract_user_code`` / ``_extract_source_user_code`` /
  ``_extract_model_score_user_code`` / ``_extract_external_user_code``)
  stays identical — only the implementation swaps from line-scanning to
  an AST walk.

Tests are split into:

  1. Regression guards — behaviours that work TODAY and must keep working.
  2. ``xfail(strict=True)`` — known misfires that the AST migration fixes.
     These will flip to XPASS once the dev's fix lands, which is the
     signal to unmark them.

No dev code is written in this file.  We use only the public extractor
API (``_extract_user_code`` & friends) so that if the dev reshapes the
internals (which they will), these tests still compile and still pin
the contract.
"""

from __future__ import annotations

import ast

import pytest

from haute._code_extraction import (
    _extract_external_user_code,
    _extract_model_score_user_code,
    _extract_source_user_code,
    _extract_user_code,
)

# ---------------------------------------------------------------------------
# Helpers — build function bodies as the extractors expect them
# ---------------------------------------------------------------------------


def _indent(source: str, prefix: str = "    ") -> str:
    """Indent every line of *source* with *prefix*.

    The public extractors receive *function body source* — i.e. already
    at one indent level deeper than the enclosing def.  Fixtures that use
    real Python are easier to read when written at indent 0 then shifted.
    """
    return "\n".join(prefix + line if line else line for line in source.splitlines())


def _body(*lines: str, docstring: str | None = None, indent: str = "    ") -> str:
    """Compose a function body from *lines*, prepending a one-liner docstring.

    Every non-empty line is indented with *indent* to match what the
    parser's body-extractor hands to the user-code extractors.
    """
    parts: list[str] = []
    if docstring is not None:
        parts.append(f'{indent}"""{docstring}"""')
    parts.extend(indent + ln if ln else ln for ln in lines)
    return "\n".join(parts)


def _assert_is_valid_python(code: str, *, context: str) -> None:
    """Parse *code* as a module and assert it has no syntax errors.

    Any code extracted from a user's node body must be valid Python —
    that's the basic contract the downstream codegen relies on.  A
    heuristic that rewrites ``return`` → ``df =`` inside a nested ``def``
    silently produces syntactically VALID but SEMANTICALLY CORRUPT code,
    so this check alone doesn't catch every misfire — we pair it with
    structural assertions below.
    """
    try:
        ast.parse(code)
    except SyntaxError as exc:  # pragma: no cover — informative only
        raise AssertionError(
            f"Extracted code is not valid Python ({context}):\n---\n{code}\n---\n{exc}"
        ) from exc


# ---------------------------------------------------------------------------
# Regression guards — these behaviours work TODAY and must keep working
# ---------------------------------------------------------------------------


class TestCurrentCorrectBehaviour:
    """Baseline: the simple paths that the line-heuristic handles correctly.

    After the AST migration these MUST still pass — if any of these
    break, the dev has regressed behaviour.
    """

    def test_simple_single_return_expression(self) -> None:
        """``return <expr>`` at the outer level → ``df = <expr>``."""
        body = _body(
            "return source.with_columns(x=pl.lit(1))",
            docstring="simple",
        )

        result = _extract_user_code(body, ["source"])

        _assert_is_valid_python(result, context="simple single return")
        assert "return" not in result, (
            "The outer `return` must be converted away — the codegen "
            "template re-adds `return df` on round-trip."
        )
        assert "df = source.with_columns(x=pl.lit(1))" in result

    def test_empty_function_no_return_raises_nothing(self) -> None:
        """A body that just has a docstring yields the empty string (no user code)."""
        # The `_extract_source_user_code` returns "" for pure boilerplate
        # and a polars body with only a docstring is the empty-body case.
        body = _body(docstring="empty polars")

        result = _extract_user_code(body, [])

        assert result == "", f"empty polars body must yield '', got {result!r}"

    def test_return_at_indent_zero_is_detected(self) -> None:
        """An outermost ``return`` (at the body's base indent) is converted."""
        # A typical polars node sans docstring:
        body = "    return df.filter(pl.col('a') > 0)"

        result = _extract_user_code(body, ["df"])

        _assert_is_valid_python(result, context="indent-0 return")
        assert result.startswith("df = "), (
            f"Top-level `return` must flip to `df = …`, got {result!r}"
        )

    def test_multi_line_return_expression(self) -> None:
        """``return (\\n  ...\\n)`` collapsed to a chain assignment stays valid."""
        body = _body(
            "return (",
            "    source",
            "    .filter(pl.col('a') > 0)",
            "    .select(pl.col('b'))",
            ")",
            docstring="multi-line return",
        )

        result = _extract_user_code(body, ["source"])

        _assert_is_valid_python(result, context="multi-line return")
        assert "df = (" in result or "source" in result, (
            f"multi-line `return (` must survive the rewrite, got {result!r}"
        )
        # No stray `return` keyword at the statement start anywhere.
        for line in result.splitlines():
            assert not line.lstrip().startswith("return "), (
                f"line {line!r} in {result!r} still begins with `return`"
            )

    def test_trailing_codegen_return_df_stripped(self) -> None:
        """Trailing auto-generated ``return df`` (codegen's sentinel) gets stripped."""
        body = _body(
            "df = df.filter(pl.col('a') > 0)",
            "return df",
            docstring="codegen trailing",
        )

        result = _extract_user_code(body, ["df"])

        _assert_is_valid_python(result, context="trailing return df")
        assert "return df" not in result, (
            f"trailing `return df` boilerplate must be stripped, got {result!r}"
        )
        assert "df = df.filter" in result

    def test_return_variable_named_return_underscore_is_not_confused(self) -> None:
        """A variable whose name begins with ``return_`` must not look like a return."""
        body = _body(
            "return_val = 42",
            "return source.with_columns(v=pl.lit(return_val))",
            docstring="return_underscore",
        )

        result = _extract_user_code(body, ["source"])

        _assert_is_valid_python(result, context="return_underscore")
        assert "return_val = 42" in result
        assert "df = source.with_columns(v=pl.lit(return_val))" in result


# ---------------------------------------------------------------------------
# Line-heuristic misfires — these will flip from XFAIL → XPASS after #137
# ---------------------------------------------------------------------------


class TestLineHeuristicMisfires:
    """Pathological bodies where the line-heuristic rewrites the wrong line.

    Every test here is marked ``xfail(strict=True)``.  The dev's AST-walk
    fix will make them pass, which pytest reports as XPASSED — the signal
    for the reviewer to flip them to regular assertions.
    """

    @pytest.mark.xfail(
        strict=True,
        reason="#137 — nested def's `return` is rewritten as if it were the outer's",
    )
    def test_nested_function_return_is_not_rewritten(self) -> None:
        """Inner ``def helper(): return 1`` must NOT be mutated to ``df = 1``."""
        body = _body(
            "def inner():",
            "    return 1",
            "return source.with_columns(x=pl.lit(inner()))",
            docstring="nested fn",
        )

        result = _extract_user_code(body, ["source"])

        _assert_is_valid_python(result, context="nested fn")
        # The inner function body must still have its `return 1`:
        assert "return 1" in result, (
            "Inner `def inner(): return 1` must be preserved verbatim — "
            f"the line-heuristic corrupted it to:\n{result}"
        )
        # Inner `def` must NOT have `df = 1` in place of `return 1`:
        assert "df = 1" not in result, (
            f"Inner return wrongly rewritten as `df = 1`:\n{result}"
        )
        # Outer return is still converted:
        assert "df = source.with_columns(x=pl.lit(inner()))" in result

    @pytest.mark.xfail(
        strict=True,
        reason="#137 — nested def with final `return df` is clobbered by trailing-strip",
    )
    def test_nested_function_return_df_is_not_stripped(self) -> None:
        """Inner helper whose last line is ``return df`` must keep that return."""
        body = _body(
            "def helper(df):",
            "    df = df.filter(pl.col('x') > 0)",
            "    return df",
            "return helper(source)",
            docstring="nested return df",
        )

        result = _extract_user_code(body, ["source"])

        _assert_is_valid_python(result, context="nested return df")
        # The inner function must retain its terminal `return df`:
        assert "return df" in result, (
            "Inner function's `return df` is NOT the outer's auto-generated "
            f"return and must survive extraction.  Got:\n{result}"
        )
        # And must NOT have been rewritten to `df = df` (a line-heuristic
        # misfire that's syntactically valid but semantically wrong):
        assert "df = df\n" not in result and not result.endswith("df = df"), (
            f"Inner `return df` wrongly rewritten as `df = df`:\n{result}"
        )

    @pytest.mark.xfail(
        strict=True,
        reason="#137 — `return` inside async def rewritten as if outer-scope",
    )
    def test_async_def_nested_return_preserved(self) -> None:
        """``async def helper(): return 1`` nested inside a sync node body."""
        body = _body(
            "async def helper():",
            "    return 1",
            "return source",
            docstring="async inner",
        )

        result = _extract_user_code(body, ["source"])

        _assert_is_valid_python(result, context="async inner")
        # The async def's return must be preserved:
        assert "return 1" in result, (
            f"async def's inner return wrongly rewritten:\n{result}"
        )
        assert "async def helper" in result

    @pytest.mark.xfail(
        strict=True,
        reason="#137 — class method's `return` rewritten as if outer-scope",
    )
    def test_class_method_return_preserved(self) -> None:
        """Inner class method's ``return`` must stay untouched."""
        body = _body(
            "class Helper:",
            "    def m(self):",
            "        return 1",
            "return source",
            docstring="class inner",
        )

        result = _extract_user_code(body, ["source"])

        _assert_is_valid_python(result, context="class inner")
        assert "return 1" in result, (
            f"class method's `return` wrongly rewritten:\n{result}"
        )
        assert "class Helper" in result
        assert "df = source" in result

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "#137 — early-exit second `return` left un-rewritten; "
            "`in_return=True` latch converts only the first"
        ),
    )
    def test_multiple_returns_early_exit_all_converted(self) -> None:
        """Both branches of an early-exit must flip to ``df = …``.

        The current implementation uses an ``in_return=True`` latch that
        converts *only the first* top-level ``return`` and leaves any
        later returns textually intact — so the extracted user code has a
        stray ``return late`` that, once re-wrapped by codegen with its
        own ``return df``, yields a function body with two mismatched
        returns.  An AST pass will see both ``Return`` nodes at the outer
        scope and rewrite both.
        """
        body = _body(
            "if condition:",
            "    return early",
            "return late",
            docstring="early-exit",
        )

        result = _extract_user_code(body, ["condition", "early", "late"])

        _assert_is_valid_python(result, context="early-exit")
        # No top-level `return …` should survive:
        for line in result.splitlines():
            stripped = line.lstrip()
            assert not stripped.startswith("return ") or stripped.startswith("return_"), (
                f"early-exit branch still has `return` at outer scope:\n{result}"
            )
        # Both branches produce a `df = …` assignment:
        assert "df = early" in result, (
            f"early-exit branch not flipped to `df = early`:\n{result}"
        )
        assert "df = late" in result, (
            f"late-exit branch not flipped to `df = late`:\n{result}"
        )

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "#137 — source extractor's trailing-strip eats an inner fn's "
            "`return df` when it's the absolute last line of the body"
        ),
    )
    def test_source_node_nested_fn_return_df_preserved(self) -> None:
        """DataSource body: inner helper's terminal ``return df`` must NOT be eaten.

        Pathological body: the user ends the node body with an inner
        helper whose last line is ``return df``.  No outer return
        follows.  The line-heuristic ``_strip_trailing_return`` pops
        trailing lines whose ``.strip() == "return df"`` — and since the
        inner helper's indented ``return df`` has the same stripped
        form, it's wrongly eaten.  This produces invalid Python (the
        helper ``def`` is left with no body).
        """
        body = _body(
            'df = pl.scan_parquet("x.parquet")',
            "def helper():",
            "    df = pl.DataFrame()",
            "    return df",
            docstring="nested inside source with no outer return",
        )

        result = _extract_source_user_code(body)

        _assert_is_valid_python(result, context="source-nested")
        # The inner function's `return df` must survive:
        assert "def helper" in result
        assert "return df" in result, (
            "Inner helper's terminal `return df` was eaten by the "
            f"trailing-return strip:\n{result}"
        )

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "#137 — inner helper ending with `return df` loses its last "
            "line, producing syntactically invalid Python"
        ),
    )
    def test_source_node_nested_fn_only_return_df_preserved(self) -> None:
        """Inner helper's sole body line is ``return df`` — must survive.

        This is the MOST pathological variant: the inner helper body is
        just ``return df``, and the body ends there.  The trailing-strip
        eats ``return df``, leaving the ``def helper():`` header with no
        body — which is a SyntaxError.
        """
        body = _body(
            'df = pl.scan_parquet("x.parquet")',
            "x = 1",
            "def helper():",
            "    return df",
            docstring="inner = just return df",
        )

        result = _extract_source_user_code(body)

        # The outer wrapper for codegen will syntax-fail on import if
        # this result is missing the helper body.  An AST walk knows
        # that the `return df` is inside `helper` scope and leaves it
        # alone.
        _assert_is_valid_python(result, context="source-empty-helper")


# ---------------------------------------------------------------------------
# Textual-match misfires — returns that only LOOK like returns
# ---------------------------------------------------------------------------


class TestTextualReturnMisfires:
    """Cases where the string ``"return"`` appears in contexts that a pure
    text scanner might be fooled by but an AST walk ignores.

    Most of these already work because the heuristic checks
    ``.startswith("return ")`` on the ``.strip()``ed line, which is
    tighter than a naive substring match.  We pin them here as regression
    guards — the AST migration must not regress these.
    """

    def test_comment_containing_return_not_detected(self) -> None:
        """``# TODO return something useful`` is a comment, not a return."""
        body = _body(
            "# TODO return something more useful",
            "return source.with_columns(z=pl.lit(1))",
            docstring="comment with return",
        )

        result = _extract_user_code(body, ["source"])

        _assert_is_valid_python(result, context="comment return")
        # The comment is user code — keep it:
        assert "# TODO return something more useful" in result
        # The real return is converted:
        assert "df = source.with_columns(z=pl.lit(1))" in result

    def test_string_literal_containing_return_not_detected(self) -> None:
        """A string literal containing ``"return"`` must not be misread."""
        body = _body(
            'msg = "return to sender"',
            "return source.with_columns(msg=pl.lit(msg))",
            docstring="string with return",
        )

        result = _extract_user_code(body, ["source"])

        _assert_is_valid_python(result, context="string return")
        assert 'msg = "return to sender"' in result
        assert "df = source.with_columns(msg=pl.lit(msg))" in result

    def test_decorator_text_containing_return_not_detected(self) -> None:
        """Decorator names / attributes with ``return`` in the identifier."""
        body = _body(
            "@some.returning_decorator",
            "def helper():",
            "    pass",
            "return source",
            docstring="decorator text",
        )

        result = _extract_user_code(body, ["source"])

        _assert_is_valid_python(result, context="decorator text")
        assert "@some.returning_decorator" in result
        assert "df = source" in result


# ---------------------------------------------------------------------------
# AST-specific invariants — once the migration lands, the detector's
# signature / behaviour should pin these.  Kept loose so the dev can pick
# the exact name.
# ---------------------------------------------------------------------------


class TestASTInvariants:
    """Invariants that are natural consequences of using an AST walk.

    These are the behaviours an AST-based detector exhibits *for free*.
    Each is phrased against the public extractor API, so the tests do
    not assume any particular internal structure for the new walker.
    """

    def test_extractor_accepts_plain_string_source(self) -> None:
        """``_extract_user_code`` takes a ``str`` — no tree / node / CST required."""
        body = _body(
            "return source",
            docstring="plain string",
        )
        # Pre-existing signature — must not become tree-only:
        result = _extract_user_code(body, ["source"])
        assert isinstance(result, str)

    def test_extractor_handles_unusual_whitespace(self) -> None:
        """Mixed tabs / trailing whitespace / Windows line endings survive extraction.

        An AST walk doesn't care about exact whitespace; the heuristic
        does.  This test would regress if the dev inadvertently coupled
        the AST walk to a textual reconstitution that normalises
        whitespace differently from today.
        """
        # Windows-style line endings — the extractor strips & splitlines
        # so this is a regression guard.
        body = '    """windows\r\n"""\r\n    return source\r\n'

        result = _extract_user_code(body, ["source"])

        _assert_is_valid_python(result, context="windows")
        assert "df = source" in result

    @pytest.mark.xfail(
        strict=True,
        reason="#137 — nested function at deep indent is still mis-rewritten",
    )
    def test_deeply_nested_function_return_preserved(self) -> None:
        """Triple-nested ``def`` inside ``if`` inside ``def`` — still scope-correct.

        The current line-heuristic flips the FIRST top-level-looking
        ``return``.  An AST walk with outermost-function filtering sees
        only the outer function's direct ``Return`` nodes.
        """
        body = _body(
            "if True:",
            "    def outer_inner():",
            "        def inner_inner():",
            "            return 42",
            "        return inner_inner()",
            "    x = outer_inner()",
            "else:",
            "    x = 0",
            "return source.with_columns(val=pl.lit(x))",
            docstring="deeply nested",
        )

        result = _extract_user_code(body, ["source"])

        _assert_is_valid_python(result, context="deeply nested")
        # Both inner returns must survive:
        assert "return 42" in result, (
            f"deepest inner `return 42` was rewritten:\n{result}"
        )
        assert "return inner_inner()" in result, (
            f"mid-level inner return was rewritten:\n{result}"
        )
        # Outer return converted:
        assert "df = source.with_columns(val=pl.lit(x))" in result

    def test_lambda_body_is_not_a_return_target(self) -> None:
        """``lambda x: x`` uses no ``return`` keyword — confirms AST scope filtering.

        Lambdas have no ``Return`` node at the AST level — their body
        IS the return expression.  An AST walker naturally ignores them;
        a line walker might be spooked by the literal token ``"return"``
        appearing in a nearby line.  We pin the observable outcome.
        """
        body = _body(
            "f = lambda x: x * 2",
            "return source.with_columns(y=pl.lit(f(5)))",
            docstring="lambda",
        )

        result = _extract_user_code(body, ["source"])

        _assert_is_valid_python(result, context="lambda")
        assert "f = lambda x: x * 2" in result
        assert "df = source.with_columns(y=pl.lit(f(5)))" in result


# ---------------------------------------------------------------------------
# End-to-end regression — the model_score path exercises the trailing-
# return strip through a different code path than _finalise_polars, and
# could regress independently.
# ---------------------------------------------------------------------------


class TestModelScoreExtractor:
    """``_extract_model_score_user_code`` uses the same trailing-return
    strip.  Pin its boundary so the AST migration doesn't regress it.
    """

    def test_model_score_post_processing_preserved(self) -> None:
        """Post-processing after ``score_from_config(...)`` is extracted whole."""
        body = _body(
            "from pathlib import Path",
            "from haute.graph_utils import score_from_config",
            'result = score_from_config(source, config="score.json")',
            'df = result.with_columns(doubled=pl.col("prediction") * 2)',
            "return result",
            docstring="score with post",
        )

        result = _extract_model_score_user_code(body)

        _assert_is_valid_python(result, context="modelScore post")
        assert "doubled" in result
        assert "score_from_config" not in result
        assert "return result" not in result

    def test_model_score_inner_fn_return_result_preserved(self) -> None:
        """Inner helper returning ``result`` must NOT have that return stripped.

        Regression guard: this case happens to work today because the
        trailing-return strip checks ``.strip() == "return result"`` on
        the FINAL line, and the inner helper's ``return r`` differs
        textually (indented).  But we still pin it so the AST migration
        can't regress it.
        """
        body = _body(
            "from pathlib import Path",
            "from haute.graph_utils import score_from_config",
            'result = score_from_config(source, config="score.json")',
            "def process(r):",
            "    r = r.with_columns(x=pl.lit(1))",
            "    return r",
            "result = process(result)",
            "return result",
            docstring="nested process",
        )

        result = _extract_model_score_user_code(body)

        _assert_is_valid_python(result, context="modelScore nested")
        # The inner helper's return must survive:
        assert "def process" in result
        assert "return r" in result, (
            f"Inner `return r` (different variable!) should survive.  Got:\n{result}"
        )
        # Outer codegen `return result` stripped:
        assert not result.rstrip().endswith("return result"), (
            f"Outer trailing `return result` not stripped:\n{result}"
        )

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "#137 — when the inner `return result` is the ABSOLUTE LAST "
            "line, the trailing-strip eats it because .strip() matches "
            "`return result`, producing a syntactically-invalid helper"
        ),
    )
    def test_model_score_inner_fn_return_result_at_end_preserved(self) -> None:
        """Inner helper whose body's ABSOLUTE LAST line is ``return result``.

        This is the pathological case: the body ends with an inner
        ``def identity(): return result`` — no outer return after it.
        The trailing-strip eats the inner ``    return result`` line
        (its ``.strip() == "return result"`` matches), leaving
        ``def identity():`` with no body → SyntaxError when codegen
        re-wraps this.
        """
        body = _body(
            "from pathlib import Path",
            "from haute.graph_utils import score_from_config",
            'result = score_from_config(source, config="score.json")',
            "def identity():",
            "    return result",
            docstring="inner return result at end",
        )

        result = _extract_model_score_user_code(body)

        _assert_is_valid_python(result, context="modelScore inner at end")
        # Inner fn's `return result` must survive — it belongs to
        # `identity`, not to the outer node body:
        assert "def identity" in result
        assert "return result" in result, (
            "Inner `return result` eaten by trailing-strip.  An AST walk "
            f"would see that it's inside `identity`, not the outer scope:\n{result}"
        )


# ---------------------------------------------------------------------------
# External-file node — same pattern, different kind.
# ---------------------------------------------------------------------------


class TestExternalFileExtractor:
    """``_extract_external_user_code`` also uses the trailing-return strip."""

    def test_external_file_trailing_return_stripped(self) -> None:
        """Baseline: trailing ``return df`` in external-file body is stripped."""
        body = _body(
            "import pickle",
            'with open("m.pkl", "rb") as _f:',
            "    obj = pickle.load(_f)",
            "df = df.with_columns(pred=pl.lit(obj.predict()))",
            "return df",
            docstring="pickled model",
        )

        result = _extract_external_user_code(body, ["df"])

        _assert_is_valid_python(result, context="external")
        assert "return df" not in result
        assert "pred=pl.lit" in result

    def test_external_file_inner_fn_return_preserved(self) -> None:
        """Inner helper's ``return df`` inside external-file body must survive.

        Regression guard: works today only because the inner helper is
        followed by a ``df = transform(df)`` line, so the trailing-strip
        never reaches the inner ``return df``.  Pinned here so the AST
        migration doesn't regress.
        """
        body = _body(
            "import pickle",
            'with open("m.pkl", "rb") as _f:',
            "    obj = pickle.load(_f)",
            "def transform(df):",
            "    df = df.with_columns(pred=pl.lit(obj.predict()))",
            "    return df",
            "df = transform(df)",
            "return df",
            docstring="external with helper",
        )

        result = _extract_external_user_code(body, ["df"])

        _assert_is_valid_python(result, context="external inner")
        assert "def transform" in result
        # Inner return preserved (it's at scope of `transform`, not outer):
        inner_return_count = sum(
            1 for line in result.splitlines() if line.strip() == "return df"
        )
        assert inner_return_count >= 1, (
            f"Inner helper's `return df` was stripped:\n{result}"
        )

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "#137 — inner helper's `return df` eaten when it's the "
            "ABSOLUTE LAST line of the extracted tail, producing a "
            "helper def with no body (SyntaxError)"
        ),
    )
    def test_external_file_inner_fn_return_df_at_end_preserved(self) -> None:
        """Pathological case: inner helper's sole body line is ``return df``.

        The trailing-strip pops the final ``    return df`` because its
        stripped form matches.  The helper ``def helper():`` is then
        left with no body — a SyntaxError.  An AST walk would see the
        ``return`` is scoped to ``helper``, not the outer body, and
        leave it alone.
        """
        body = _body(
            "import pickle",
            'with open("m.pkl", "rb") as _f:',
            "    obj = pickle.load(_f)",
            "x = 1",
            "def helper():",
            "    return df",
            docstring="external inner at end",
        )

        result = _extract_external_user_code(body, ["df"])

        _assert_is_valid_python(result, context="external inner at end")
        assert "def helper" in result
        # Inner return preserved — it's inside `helper`, not the outer body:
        assert "return df" in result, (
            "Inner helper's `return df` eaten by trailing-strip — "
            f"AST walk would scope it to `helper`:\n{result}"
        )
