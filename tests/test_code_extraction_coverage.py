"""Coverage tests for the internal helpers in ``haute._code_extraction``.

The headline target is the parse choke-point ``_parse_user_code`` — every
user-code rewrite in this module funnels through it, so the ``SyntaxError``
→ :class:`_UserCodeParseError` conversion (file ``_code_extraction.py:106-111``)
is the single most load-bearing failure path.  We assert it fails LOUDLY
with an actionable, context-tagged diagnostic and preserves the original
``SyntaxError`` via ``__cause__`` chaining (per CLAUDE.md "let code fail
loudly").

The remaining cases pin a handful of cheap, otherwise-uncovered defensive
branches in the surrounding helpers (bare-``return`` rewrites, empty-input
guards, no-match matcher results, passthrough finalisers, the unknown-kind
``KeyError`` in :func:`extract_user_code`).  All exercise the module's own
internal API directly so the contract is pinned regardless of how the
parser modules call in.
"""

from __future__ import annotations

import pytest

from haute._code_extraction import (
    _df_alias_target,
    _extract_user_code,
    _finalise_external,
    _finalise_polars,
    _finalise_source,
    _is_empty_chain_assignment,
    _match_external,
    _match_model_score,
    _match_scenario_expander,
    _parse_user_code,
    _rewrite_identifier_tokens,
    _rewrite_outer_returns_as_assignment,
    _statement_end_index,
    _strip_generated_passthrough_from_code,
    _strip_outer_trailing_return,
    _strip_redundant_rhs_wrapper_once,
    _strip_trailing_return,
    _UserCodeParseError,
    extract_user_code,
)
from haute.errors import ParseError

# ---------------------------------------------------------------------------
# _parse_user_code — the parse choke-point (file lines 106-111)
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
# _rewrite_outer_returns_as_assignment — bare-``return`` rewrites (174, 177)
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
# _strip_outer_trailing_return — empty / no-return guards (197)
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
# _df_alias_target — non-alias / unparseable lines (297)
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
# Empty-input guards (314, 345) and trailing-return empty list (595)
# ---------------------------------------------------------------------------


class TestEmptyInputGuards:
    def test_passthrough_empty_code(self) -> None:
        """Blank code strips to the empty string."""
        assert _strip_generated_passthrough_from_code("", ("df",)) == ""

    def test_strip_trailing_return_empty_list(self) -> None:
        """No lines in → empty list out."""
        assert _strip_trailing_return([], ("df",)) == []


# ---------------------------------------------------------------------------
# Boilerplate matchers — empty / no-match results (455, 497, 515)
# ---------------------------------------------------------------------------


class TestMatcherEdgeCases:
    def test_scenario_expander_empty_cleaned(self) -> None:
        """No cleaned lines → start at 0, return var ``df``."""
        result = _match_scenario_expander([], ())
        assert result.start_idx == 0
        assert result.return_vars == ("df",)

    def test_model_score_no_call_yields_out_of_range_start(self) -> None:
        """No ``score_from_config`` call → start index past the body end."""
        cleaned = ["x = 1"]
        result = _match_model_score(cleaned, ())
        assert result.start_idx > len(cleaned)

    def test_external_empty_cleaned(self) -> None:
        """No cleaned lines → start at 0."""
        result = _match_external([], ())
        assert result.start_idx == 0


# ---------------------------------------------------------------------------
# _rewrite_identifier_tokens — parse failure / no-edit passthrough (661-662, 675)
# ---------------------------------------------------------------------------


class TestRewriteIdentifierTokens:
    def test_unparseable_source_with_token_passthrough(self) -> None:
        """Invalid Python containing the token is returned unchanged."""
        src = "result = ="
        assert _rewrite_identifier_tokens(src, old="result", new="df", context="x") == src

    def test_token_only_in_string_literal_not_rewritten(self) -> None:
        """A bare token appearing only inside a string is left alone."""
        src = 'x = "result"'
        assert _rewrite_identifier_tokens(src, old="result", new="df", context="x") == src

    def test_token_absent_returns_source(self) -> None:
        """When the token is not present at all, source is returned verbatim."""
        src = "x = 1"
        assert _rewrite_identifier_tokens(src, old="result", new="df", context="x") == src

    def test_name_token_is_rewritten(self) -> None:
        """A genuine ``Name`` occurrence is rewritten."""
        assert _rewrite_identifier_tokens("y = result", old="result", new="df", context="x") == (
            "y = df"
        )


# ---------------------------------------------------------------------------
# _finalise_external — empty / invalid / lone-return / multi (725, 729-731, 735-741)
# ---------------------------------------------------------------------------


class TestFinaliseExternal:
    def test_blank_code_passthrough(self) -> None:
        """Whitespace-only code is returned unchanged."""
        assert _finalise_external("   ", ()) == "   "

    def test_invalid_python_passthrough(self) -> None:
        """Invalid Python defers the error to the caller (passthrough)."""
        src = "return df\nx = ="
        assert _finalise_external(src, ()) == src

    def test_lone_outer_return_df_wiped(self) -> None:
        """A body that is only a lone outer ``return df`` collapses to empty."""
        assert _finalise_external("return df", ()) == ""

    def test_multi_statement_preserved(self) -> None:
        """More than one outer statement → leave the body intact."""
        src = "x = 1\nreturn df"
        assert _finalise_external(src, ()) == src


# ---------------------------------------------------------------------------
# _finalise_polars / _finalise_source (628, 638, 649-650)
# ---------------------------------------------------------------------------


class TestFinalisers:
    def test_polars_alias_line_is_authored_code(self) -> None:
        """A ``df = <param>`` line is user code, never strippable scaffold —
        polars codegen no longer prepends an input alias, and legacy modules'
        alias line must round-trip into visible code to keep working."""
        assert _finalise_polars("df = source", ("source",)) == "df = source"

    def test_explore_kind_strips_alias_scaffold(self) -> None:
        """The explore kind still treats a leading ``df = <param>`` as its
        generated binding scaffold and strips exactly that line."""
        body = "    df = source\n    df = df.filter(x)\n    return df"
        assert (
            extract_user_code(body, kind="explore", param_names=("source",)) == "df = df.filter(x)"
        )

    def test_explore_kind_keeps_non_param_first_line(self) -> None:
        """A first line binding df from a non-parameter name is user code."""
        body = "    df = other\n    return df"
        assert extract_user_code(body, kind="explore", param_names=("source",)) == "df = other"

    def test_polars_empty_chain_collapses_to_empty(self) -> None:
        """An empty chain ``df = (\\n)`` unwraps to the empty string."""
        assert _finalise_polars("df = (\n)", ("source",)) == ""

    def test_source_plain_code_passthrough(self) -> None:
        """Non-chain source code is returned unchanged."""
        assert _finalise_source("df.filter(x)", ()) == "df.filter(x)"


class TestExtractUserCode:
    def test_unknown_kind_raises_key_error(self) -> None:
        """An unregistered matcher kind fails loudly with ``KeyError``."""
        with pytest.raises(KeyError) as exc_info:
            extract_user_code("x = 1", kind="nope")
        assert "Unknown boilerplate matcher kind" in str(exc_info.value)

    def test_leading_and_trailing_blank_lines_trimmed(self) -> None:
        """The engine pops leading/trailing blank lines before extraction."""
        body = "\n\n    df = source\n    df = df.filter(x)\n\n\n"
        assert _extract_user_code(body, ["source"]) == "df = source\ndf = df.filter(x)"


# ---------------------------------------------------------------------------
# _strip_redundant_rhs_wrapper_once — assignment-shape guards (254-280)
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
# _is_empty_chain_assignment — parse / shape guards (375-387)
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
# _statement_end_index — statement that never closes (496-503)
# ---------------------------------------------------------------------------


class TestStatementEndIndex:
    """A statement whose parens never balance runs to the end of the body."""

    def test_balanced_single_line_ends_after_that_line(self) -> None:
        """A self-contained statement ends right after its own line."""
        assert (
            _statement_end_index(
                ["df = resolve_data_input_from_config('config/data_input/a.json')", "df.head()"],
                0,
            )
            == 1
        )

    def test_unbalanced_statement_runs_to_end_of_lines(self) -> None:
        """An open paren that never closes consumes every remaining line.

        Depth stays positive for the whole scan, so the loop falls through to
        ``return len(lines)`` rather than an early per-statement boundary — the
        boilerplate skipper treats the malformed tail as one statement.
        """
        lines = ["df = resolve_data_input_from_config(", "    'config/data_input/a.json'"]
        assert _statement_end_index(lines, 0) == len(lines)


# ---------------------------------------------------------------------------
# _match_scenario_expander — import line mentioning the helper (549-554)
# ---------------------------------------------------------------------------


class TestMatchScenarioExpanderImportSkip:
    """An import that names the expansion helper is not the generated call."""

    def test_import_line_naming_helper_is_skipped(self) -> None:
        """A ``from ... import`` line textually mentioning ``expand_scenarios_from_config(``
        must not anchor boilerplate stripping.

        The scaffold marker is the generated *call*; an import that merely
        references the helper name (here inside a trailing comment) is skipped
        via ``continue`` so the user's post-expansion code below it survives.
        With no real outer-scope call or generated alias, extraction keeps the
        whole body from index 0.
        """
        cleaned = [
            "from helpers import expand_scenarios_from_config  # expand_scenarios_from_config(x)",
            "df = df.with_columns(pl.lit(1))",
        ]
        result = _match_scenario_expander(cleaned, ("df",))
        assert result.start_idx == 0
        assert result.generated_scaffold is False
