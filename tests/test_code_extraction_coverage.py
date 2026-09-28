"""Coverage tests for the internal helpers in ``haute._code_extraction``.

The headline target is the parse choke-point ``_parse_user_code`` — every
user-code rewrite in this module funnels through it, so the ``SyntaxError``
→ :class:`_UserCodeParseError` conversion is the single most load-bearing
failure path.  We assert it fails LOUDLY with an actionable, context-tagged
diagnostic and preserves the original ``SyntaxError`` via ``__cause__``
chaining (per CLAUDE.md "let code fail loudly").

The remaining cases pin the generated-statement rules and a handful of cheap
defensive branches in the surrounding helpers: declaration bodies (``...``,
``pass``, a docstring alone), the hook and External File matchers (only the
External File's ``df = <first input>`` binding is generated before the code),
bare-``return`` rewrites, empty-input guards, the finaliser, and the
unknown-kind ``KeyError`` in :func:`extract_user_code` and
:func:`normalise_user_code`.  All exercise the module's own internal API
directly so the contract is pinned regardless of how the parser modules
call in.
"""

from __future__ import annotations

import pytest

from haute._code_extraction import (
    INCOMPLETE_STEPS_BODY,
    _df_alias_target,
    _finalise_polars,
    _is_empty_chain_assignment,
    _match_external,
    _match_hook,
    _match_polars,
    _parse_user_code,
    _rewrite_outer_returns_as_assignment,
    _statement_end_index,
    _strip_outer_trailing_return,
    _strip_redundant_rhs_wrapper_once,
    _strip_trailing_return,
    _UserCodeParseError,
    extract_user_code,
    is_declaration_body,
    normalise_user_code,
)
from haute.errors import ParseError

_KINDS = ("polars", "hook", "external")

# ---------------------------------------------------------------------------
# _parse_user_code — the parse choke-point
# ---------------------------------------------------------------------------


class TestParseUserCode:
    """The single point through which every user-code AST parse flows."""

    def test_valid_source_returns_module(self) -> None:
        """Syntactically valid source parses to an ``ast.Module``."""
        module = _parse_user_code("df = source.filter(x)\n")
        # Module exposes a ``body`` list — enough to confirm we got a module.
        assert hasattr(module, "body")
        assert len(module.body) == 1

    def test_syntax_error_raises_user_code_parse_error(self) -> None:
        """Invalid Python fails loudly as :class:`_UserCodeParseError`."""
        with pytest.raises(_UserCodeParseError):
            _parse_user_code("def (:")

    def test_diagnostic_includes_context_and_location(self) -> None:
        """The message names the context plus the SyntaxError line/offset."""
        with pytest.raises(_UserCodeParseError) as exc_info:
            _parse_user_code("def (:", context="polars node")
        message = str(exc_info.value)
        assert "cannot parse polars node" in message
        assert "line" in message
        assert "offset" in message

    def test_default_context_is_user_code(self) -> None:
        """When no context is passed the diagnostic falls back to 'user code'."""
        with pytest.raises(_UserCodeParseError) as exc_info:
            _parse_user_code("x = (")
        assert "cannot parse user code" in str(exc_info.value)

    def test_original_syntax_error_is_chained_as_cause(self) -> None:
        """The underlying ``SyntaxError`` survives via ``__cause__``."""
        with pytest.raises(_UserCodeParseError) as exc_info:
            _parse_user_code("for x in", context="rating step")
        assert isinstance(exc_info.value.__cause__, SyntaxError)

    def test_is_in_haute_parse_error_hierarchy(self) -> None:
        """GUI callers that catch ``ParseError`` (or ``ValueError``) see it too."""
        with pytest.raises(ParseError):
            _parse_user_code("@@@")
        with pytest.raises(ValueError):
            _parse_user_code("@@@")


# ---------------------------------------------------------------------------
# _rewrite_outer_returns_as_assignment — bare-``return`` rewrites
# ---------------------------------------------------------------------------


class TestRewriteBareReturn:
    """Bare ``return`` at the outer scope becomes ``<target> = None``."""

    def test_bare_return_with_newline(self) -> None:
        """``return\\n`` rewrites to ``df = None`` keeping the newline."""
        assert _rewrite_outer_returns_as_assignment("return\n", "df") == "df = None\n"

    def test_bare_return_no_trailing_newline(self) -> None:
        """A trailing bare ``return`` (no newline) still rewrites cleanly."""
        result = _rewrite_outer_returns_as_assignment("if x:\n    pass\nreturn", "df")
        assert result == "if x:\n    pass\ndf = None"

    def test_no_returns_passthrough(self) -> None:
        """Source with no outer return is returned unchanged."""
        src = "df = df.filter(x)\n"
        assert _rewrite_outer_returns_as_assignment(src, "df") == src


# ---------------------------------------------------------------------------
# _strip_outer_trailing_return — empty / no-return guards
# ---------------------------------------------------------------------------


class TestStripOuterTrailingReturn:
    def test_blank_source_returned_unchanged(self) -> None:
        """Whitespace-only source short-circuits before any AST parse."""
        blank = "   \n  "
        assert _strip_outer_trailing_return(blank, "df") == blank

    def test_no_returns_just_trims_blanks(self) -> None:
        """No outer return → only trailing blank lines are trimmed."""
        assert _strip_outer_trailing_return("df = x\n\n\n", "df") == "df = x"

    def test_sentinel_return_with_trailing_content_not_stripped(self) -> None:
        """A sentinel ``return df`` followed by non-blank content is left intact.

        The trailing-return strip only fires when the sentinel return is the
        LAST non-blank line.  Here real statements follow it, so the defensive
        scan of ``lines[end_line:]`` finds non-blank content and refuses to
        drop the return — the whole body survives (only trailing blanks trim).
        """
        source = "x = 1\nreturn df\ny = 2\n"
        assert _strip_outer_trailing_return(source, "df") == "x = 1\nreturn df\ny = 2"

    def test_sentinel_return_with_trailing_comment_not_stripped(self) -> None:
        """A trailing comment after the sentinel return also blocks stripping."""
        source = "return df\n# tail comment"
        assert _strip_outer_trailing_return(source, "df") == "return df\n# tail comment"


# ---------------------------------------------------------------------------
# _df_alias_target — non-alias / unparseable lines
# ---------------------------------------------------------------------------


class TestDfAliasTarget:
    def test_unparseable_line_returns_none(self) -> None:
        """A line that is not valid Python yields ``None`` (no crash)."""
        assert _df_alias_target("df = =") is None

    def test_non_df_target_returns_none(self) -> None:
        """An assignment whose target is not ``df`` yields ``None``."""
        assert _df_alias_target("x = source") is None

    def test_multi_target_assignment_returns_none(self) -> None:
        """A chained ``df = other = source`` is not a simple alias → ``None``."""
        assert _df_alias_target("df = other = source") is None

    def test_df_alias_returns_source_name(self) -> None:
        """``df = source`` yields the aliased source name."""
        assert _df_alias_target("df = source") == "source"


# ---------------------------------------------------------------------------
# Empty-input guards
# ---------------------------------------------------------------------------


class TestEmptyInputGuards:
    def test_finalise_blank_code_is_empty(self) -> None:
        """Blank code finalises to the empty string."""
        assert _finalise_polars("   \n  ") == ""

    def test_strip_trailing_return_empty_list(self) -> None:
        """No lines in → empty list out."""
        assert _strip_trailing_return([], ("df",)) == []

    @pytest.mark.parametrize("kind", _KINDS)
    def test_extract_blank_body_is_empty(self, kind: str) -> None:
        """A blank body holds no code, whatever the kind."""
        assert extract_user_code("  \n\n  ", kind=kind, param_names=("up",)) == ""


# ---------------------------------------------------------------------------
# Declaration bodies — nothing but ``...``, ``pass`` or a docstring
# ---------------------------------------------------------------------------

_DECLARATION_BODIES = [
    pytest.param("    ...", id="ellipsis"),
    pytest.param("    pass", id="pass"),
    pytest.param('    """Loads the quotes."""', id="docstring-only"),
    pytest.param('    """Loads the quotes."""\n    ...', id="docstring-then-ellipsis"),
    pytest.param('    """Loads the quotes."""\n    pass', id="docstring-then-pass"),
    pytest.param("\n\n    ...\n\n", id="surrounding-blank-lines"),
    pytest.param("...", id="unindented"),
]


class TestDeclarationBodies:
    """A configured node without code is a declaration; its body holds no code."""

    @pytest.mark.parametrize("body", _DECLARATION_BODIES)
    def test_declaration_bodies_are_recognised(self, body: str) -> None:
        assert is_declaration_body(body) is True

    @pytest.mark.parametrize(
        "body",
        [
            pytest.param("    return None", id="return-none"),
            pytest.param("    x = 1", id="assignment"),
            pytest.param("    ...\n    ...", id="two-ellipses"),
            pytest.param("    df = df.head()\n    return df", id="hook-code"),
            pytest.param('    """Doc."""\n    return df', id="docstring-then-code"),
            pytest.param("    x = (", id="unparseable"),
        ],
    )
    def test_code_bodies_are_not_declarations(self, body: str) -> None:
        assert is_declaration_body(body) is False

    @pytest.mark.parametrize("kind", _KINDS)
    @pytest.mark.parametrize("body", _DECLARATION_BODIES)
    def test_declaration_body_extracts_to_empty_code(self, body: str, kind: str) -> None:
        assert extract_user_code(body, kind=kind, param_names=("up",)) == ""


# ---------------------------------------------------------------------------
# Matchers — where the user code starts
# ---------------------------------------------------------------------------


class TestMatcherEdgeCases:
    def test_hook_empty_cleaned(self) -> None:
        """No cleaned lines → start at 0, return var ``df``."""
        result = _match_hook([], ())
        assert result.start_idx == 0
        assert result.return_vars == ("df",)

    def test_hook_keeps_every_line(self) -> None:
        """A hook generates nothing before its code: every line is user code."""
        result = _match_hook(["x = 1", "df = df.head(x)"], ("df",))
        assert result.start_idx == 0
        assert result.return_vars == ("df",)

    def test_external_empty_cleaned(self) -> None:
        """No cleaned lines → start at 0."""
        result = _match_external([], ())
        assert result.start_idx == 0

    def test_polars_output_declaration_is_skipped(self) -> None:
        """The generated ``df: pl.LazyFrame`` declaration is not user code."""
        result = _match_polars(["df: pl.LazyFrame", "df = source.head()"], ("source",))
        assert result.start_idx == 1

    def test_polars_other_annotation_is_user_code(self) -> None:
        """A user's own, different annotation of ``df`` stays user code."""
        result = _match_polars(["df: pl.DataFrame", "df = source.collect()"], ("source",))
        assert result.start_idx == 0


class TestExternalBindingMatcher:
    """An External File hook's only generated leading statement is ``df = <first input>``."""

    def test_binding_to_the_first_input_is_skipped(self) -> None:
        result = _match_external(["df = quotes", "df = df.head()"], ("quotes", "regions"))
        assert result.start_idx == 1
        assert result.return_vars == ("df",)

    def test_binding_to_another_input_is_user_code(self) -> None:
        result = _match_external(["df = regions"], ("quotes", "regions"))
        assert result.start_idx == 0

    def test_binding_after_a_user_import_is_user_code(self) -> None:
        """The binding is generated only as the first statement."""
        result = _match_external(["import numpy as np", "df = quotes"], ("quotes",))
        assert result.start_idx == 0

    def test_without_inputs_nothing_is_skipped(self) -> None:
        """A disconnected External File has no binding line."""
        result = _match_external(["df = quotes"], ())
        assert result.start_idx == 0


# ---------------------------------------------------------------------------
# No identifier rewrites — extraction carries no aliases for retired names
# ---------------------------------------------------------------------------


class TestNoIdentifierRewrites:
    def test_a_result_variable_is_not_renamed(self) -> None:
        """A name the retired scoring scaffold used (``result``) is ordinary user code."""
        body = "    result = df.head()\n    df = result\n    return df"
        assert extract_user_code(body, kind="hook") == "result = df.head()\ndf = result"


# ---------------------------------------------------------------------------
# External File extraction — blank / invalid / lone-return / multi
# ---------------------------------------------------------------------------


class TestExternalKindEdgeCases:
    def test_blank_body_is_empty(self) -> None:
        """Whitespace-only body holds no code."""
        assert extract_user_code("   ", kind="external", param_names=("up",)) == ""

    def test_invalid_python_fails_loudly(self) -> None:
        """Invalid Python after the binding raises instead of passing through."""
        body = "    df = up\n    return df\n    x = ="
        with pytest.raises(_UserCodeParseError):
            extract_user_code(body, kind="external", param_names=("up",))

    def test_lone_return_df_is_empty(self) -> None:
        """A body that is only the closing ``return df`` holds no code."""
        assert extract_user_code("    return df", kind="external", param_names=("up",)) == ""

    def test_code_before_the_return_is_kept(self) -> None:
        """Code between the binding and the closing return is the user's."""
        body = "    df = up\n    x = 1\n    return df"
        assert extract_user_code(body, kind="external", param_names=("up",)) == "x = 1"

    def test_steps_placeholder_after_the_binding_is_generated(self) -> None:
        """An unrenderable step list reloads as empty code after the binding."""
        body = "    df = up\n" + INCOMPLETE_STEPS_BODY
        assert extract_user_code(body, kind="external", param_names=("up",)) == ""


# ---------------------------------------------------------------------------
# _finalise_polars and the per-kind generated statements
# ---------------------------------------------------------------------------


class TestFinalisers:
    def test_polars_alias_line_is_authored_code(self) -> None:
        """A ``df = <param>`` line is user code, never strippable scaffold —
        polars codegen never prepends an input alias."""
        assert _finalise_polars("df = source") == "df = source"

    def test_external_kind_strips_its_binding_line(self) -> None:
        """The External File kind treats a leading ``df = <first input>`` as its
        generated binding and strips exactly that line."""
        body = "    df = source\n    df = df.filter(x)\n    return df"
        assert (
            extract_user_code(body, kind="external", param_names=("source",)) == "df = df.filter(x)"
        )

    def test_external_kind_keeps_a_binding_to_another_name(self) -> None:
        """A first line binding df from a non-parameter name is user code."""
        body = "    df = other\n    return df"
        assert extract_user_code(body, kind="external", param_names=("source",)) == "df = other"

    def test_hook_kind_keeps_a_leading_alias(self) -> None:
        """A hook generates no binding: a leading ``df = <name>`` is the user's."""
        body = "    df = source\n    df = df.filter(x)\n    return df"
        assert extract_user_code(body, kind="hook") == "df = source\ndf = df.filter(x)"

    def test_polars_empty_chain_collapses_to_empty(self) -> None:
        """An empty chain ``df = (\\n)`` unwraps to the empty string."""
        assert _finalise_polars("df = (\n)") == ""

    def test_plain_code_passthrough(self) -> None:
        """Non-chain code is returned unchanged."""
        assert _finalise_polars("df.filter(x)") == "df.filter(x)"

    def test_normalise_applies_only_the_finaliser(self) -> None:
        """A rendering is finalised the way an extracted body is."""
        assert normalise_user_code("df = (df.head(2))", kind="hook") == "df = df.head(2)"


class TestExtractUserCode:
    def test_unknown_kind_raises_key_error(self) -> None:
        """An unregistered matcher kind fails loudly with ``KeyError``."""
        with pytest.raises(KeyError) as exc_info:
            extract_user_code("x = 1", kind="nope")
        assert "Unknown boilerplate matcher kind" in str(exc_info.value)

    @pytest.mark.parametrize(
        "kind", ["source", "model_score", "rating_step", "scenario_expander", "explore"]
    )
    def test_retired_scaffold_kinds_are_unknown(self, kind: str) -> None:
        """The per-type scaffold kinds are gone; there is no compatibility alias."""
        with pytest.raises(KeyError, match="Unknown boilerplate matcher kind"):
            extract_user_code("    ...", kind=kind)

    def test_normalise_unknown_kind_raises_key_error(self) -> None:
        with pytest.raises(KeyError, match="Unknown boilerplate matcher kind"):
            normalise_user_code("df = df.head()", kind="nope")

    def test_leading_and_trailing_blank_lines_trimmed(self) -> None:
        """The engine pops leading/trailing blank lines before extraction."""
        body = "\n\n    df = source\n    df = df.filter(x)\n\n\n"
        assert (
            extract_user_code(body, kind="polars", param_names=["source"])
            == "df = source\ndf = df.filter(x)"
        )

    def test_hook_steps_placeholder_is_generated(self) -> None:
        """A hook whose steps cannot be rendered reloads as empty code."""
        assert extract_user_code(INCOMPLETE_STEPS_BODY, kind="hook") == ""


# ---------------------------------------------------------------------------
# _strip_redundant_rhs_wrapper_once — assignment-shape guards
# ---------------------------------------------------------------------------


class TestStripRedundantRhsWrapper:
    """The single-step wrapper reducer only touches a ``df = (...)`` assignment."""

    def test_non_df_prefix_returns_none(self) -> None:
        """Code not starting with the ``df = (`` marker is left alone."""
        assert _strip_redundant_rhs_wrapper_once("x = (source)") is None

    def test_unparseable_code_returns_none(self) -> None:
        """A ``df = (`` prefix that cannot parse (unclosed paren) yields ``None``."""
        assert _strip_redundant_rhs_wrapper_once("df = (source") is None

    def test_multi_target_assignment_returns_none(self) -> None:
        """A multi-target ``df = (a) = b`` is not a single wrapped RHS → ``None``.

        The reducer only reasons about a lone ``df = (<expr>)`` assignment;
        a chained assignment target (``len(stmt.targets) != 1``) is refused so
        no paren surgery corrupts a statement it cannot prove redundant.
        """
        assert _strip_redundant_rhs_wrapper_once("df = (a) = b") is None

    def test_provably_redundant_wrapper_is_removed(self) -> None:
        """A genuinely redundant wrapper pair is reduced to bare statement form."""
        assert _strip_redundant_rhs_wrapper_once("df = (source.filter(x))") == (
            "df = source.filter(x)"
        )

    def test_load_bearing_parens_are_kept(self) -> None:
        """Parens that are not one whole-RHS wrapper are proven non-redundant."""
        assert _strip_redundant_rhs_wrapper_once("df = (a + b) * c") is None


# ---------------------------------------------------------------------------
# _is_empty_chain_assignment — parse / shape guards
# ---------------------------------------------------------------------------


class TestIsEmptyChainAssignment:
    """Only a degenerate ``df = ()`` empty-tuple scaffold is an empty chain."""

    def test_unparseable_code_returns_false(self) -> None:
        """Invalid Python (unclosed paren) is not an empty chain → ``False``."""
        assert _is_empty_chain_assignment("df = (") is False

    def test_multi_target_assignment_returns_false(self) -> None:
        """A chained ``df = x = ()`` assignment is not the empty-chain scaffold."""
        assert _is_empty_chain_assignment("df = x = ()") is False

    def test_non_df_target_returns_false(self) -> None:
        """An empty tuple assigned to a non-``df`` name is not the scaffold."""
        assert _is_empty_chain_assignment("x = ()") is False

    def test_non_empty_value_returns_false(self) -> None:
        """``df = (source)`` has a real RHS and is not an empty chain."""
        assert _is_empty_chain_assignment("df = (source)") is False

    def test_empty_tuple_scaffold_returns_true(self) -> None:
        """The degenerate ``df = (\\n)`` cleared-box scaffold is an empty chain."""
        assert _is_empty_chain_assignment("df = (\n)") is True


# ---------------------------------------------------------------------------
# _statement_end_index — statement that never closes
# ---------------------------------------------------------------------------


class TestStatementEndIndex:
    """A statement whose parens never balance runs to the end of the body."""

    def test_balanced_single_line_ends_after_that_line(self) -> None:
        """A self-contained statement ends right after its own line."""
        assert _statement_end_index(["df: pl.LazyFrame", "df = source.head()"], 0) == 1

    def test_balanced_multi_line_statement_ends_after_its_close(self) -> None:
        """The generated placeholder spans three lines and ends after its ``)``."""
        lines = [line.strip() for line in INCOMPLETE_STEPS_BODY.splitlines()] + ["x = 1"]
        assert _statement_end_index(lines, 0) == 3

    def test_unbalanced_statement_runs_to_end_of_lines(self) -> None:
        """An open paren that never closes consumes every remaining line.

        Depth stays positive for the whole scan, so the loop falls through to
        ``return len(lines)`` rather than an early per-statement boundary — the
        generated-statement recognisers treat the malformed tail as one statement.
        """
        lines = ["raise NotImplementedError(", '    "a message"']
        assert _statement_end_index(lines, 0) == len(lines)


# ---------------------------------------------------------------------------
# A hook generates nothing before its code
# ---------------------------------------------------------------------------


class TestHookKeepsEveryStatement:
    """Nothing but the closing ``return df`` is generated in a hook."""

    def test_import_line_naming_a_helper_is_user_code(self) -> None:
        """An import the user wrote first, naming a retired helper, is kept.

        Retired scaffold names anchor nothing: the whole body up to the closing
        ``return df`` is the user's code.
        """
        body = "\n".join(
            [
                "    from helpers import expand_scenarios_from_config  # helper(x)",
                "    df = df.with_columns(pl.lit(1))",
                "    return df",
            ]
        )
        assert extract_user_code(body, kind="hook") == (
            "from helpers import expand_scenarios_from_config  # helper(x)\n"
            "df = df.with_columns(pl.lit(1))"
        )
