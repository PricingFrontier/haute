"""Parser and codegen hardening contracts for safe generated identifiers.

Two tightly-scoped fixes, to ``src/haute/_graph_utils.py`` and to codegen:

* **#123 ``_sanitize_func_name`` non-ASCII preservation.**
  Today the sanitiser strips every non-ASCII character, which means
  distinct labels like ``"café"`` and ``"caf"`` collapse to the same
  function name.  Post-fix the sanitiser must map non-ASCII characters
  into a reversible escape (e.g. ``_uXXXX``) so distinct labels yield
  distinct Python identifiers.

* **#124 Duplicate sanitized names warning (Phase A).**
  When two distinct labels sanitize to the same identifier, codegen
  currently emits two functions with the same name — the second
  shadows the first at import time.  Phase A is a structlog WARNING
  that names both colliding labels.  Phase B (raise on duplicate)
  is a future migration step pinned by a comment here.

Constraints:

* No ``unittest.TestCase`` — plain pytest functions / classes.
* ``structlog.testing.capture_logs`` for log capture.
* Tests marked as pinning either *current correct behaviour* (must
  continue to pass) or *current broken behaviour* (will pass after the
  dev fix — marked ``xfail(strict=True)``).
"""

from __future__ import annotations

import pytest

from haute._graph_utils import _sanitize_func_name

# ---------------------------------------------------------------------------
# #123 — _sanitize_func_name non-ASCII preservation
# ---------------------------------------------------------------------------


class TestSanitizeFuncNameCurrentBehaviour:
    """Pin the invariants that hold today AND after the fix.

    These are the boundary conditions the fix must not break:
    ASCII inputs unchanged, output is always a valid identifier,
    round-trip stability, and never empty.
    """

    def test_ascii_inputs_unchanged(self) -> None:
        """Pure ASCII labels must pass through to the obvious identifier."""
        assert _sanitize_func_name("my_func") == "my_func"
        assert _sanitize_func_name("Load_Data") == "Load_Data"
        assert _sanitize_func_name("step1") == "step1"

    def test_spaces_and_hyphens_become_underscores(self) -> None:
        """Pin the canonical ASCII-to-identifier transform."""
        assert _sanitize_func_name("My Func") == "My_Func"
        assert _sanitize_func_name("load-data") == "load_data"
        assert _sanitize_func_name("Mixed Label-Name") == "Mixed_Label_Name"

    def test_digit_prefix_gets_node_prefix(self) -> None:
        assert _sanitize_func_name("1st_place") == "node_1st_place"

    def test_keyword_gets_node_prefix(self) -> None:
        """Python keywords must not be used as identifiers."""
        assert _sanitize_func_name("class") == "node_class"
        assert _sanitize_func_name("def") == "node_def"

    def test_empty_string_fallback(self) -> None:
        assert _sanitize_func_name("") == "unnamed_node"

    def test_all_special_chars_fallback(self) -> None:
        assert _sanitize_func_name("!@#$%") == "unnamed_node"

    def test_output_is_always_a_valid_identifier(self) -> None:
        """The return value must always be usable as a Python function name."""
        candidates = [
            "my_func",
            "Load Data",
            "1st_place",
            "class",
            "!@#$%",
            "",
            "   ",
            "café",
            "用户1",
            "用户",
            "café data",
            "my-func-ü",
            "数学",
            "a" * 500,
            "with newline\nin it",
            "with tab\tin it",
        ]
        for label in candidates:
            sanitized = _sanitize_func_name(label)
            assert sanitized.isidentifier(), (
                f"sanitize({label!r}) -> {sanitized!r} is not a valid identifier"
            )

    def test_round_trip_stability_on_ascii(self) -> None:
        """sanitize(sanitize(x)) == sanitize(x) for ASCII inputs.

        This is a prerequisite for the fix: whatever mapping is chosen,
        applying it twice must not drift.
        """
        for label in ["my_func", "Load Data", "1st_place", "class", ""]:
            once = _sanitize_func_name(label)
            twice = _sanitize_func_name(once)
            assert once == twice, f"double-sanitize drifted for {label!r}: {once!r} vs {twice!r}"


class TestSanitizeFuncNameNonAsciiPostFix:
    """Tests for the #123 fix: non-ASCII must be preserved reversibly.

    Today's sanitiser strips non-ASCII — ``"café"`` → ``"caf"``.  The
    fix will preserve the CJK / Latin-1 glyphs via some escape mechanism
    so distinct inputs produce distinct outputs.  These tests will fail
    today and pass after the fix — all marked ``xfail(strict=True)``.
    """

    def test_cafe_distinct_from_caf(self) -> None:
        """``café`` and ``caf`` must map to different identifiers."""
        assert _sanitize_func_name("café") != _sanitize_func_name("caf")

    def test_cjk_label_yields_distinct_identifiers(self) -> None:
        """Two CJK-only labels that differ only by leading glyphs must
        produce DIFFERENT identifiers after the fix.

        Today ``用户1`` → ``node_1`` and ``客户1`` → ``node_1``
        (both collapse to the same digit-only stem).  This is the
        collision the fix eliminates.
        """
        a = _sanitize_func_name("用户1")
        b = _sanitize_func_name("客户1")
        assert a != b, f"{a!r} == {b!r}: CJK labels collided"

    def test_non_ascii_result_is_still_a_valid_identifier(self) -> None:
        """Non-ASCII inputs produce valid identifiers AFTER the fix."""
        for label in ["café", "用户1", "数学", "naïve", "Zürich"]:
            result = _sanitize_func_name(label)
            assert result.isidentifier(), (
                f"sanitize({label!r}) -> {result!r} is not a valid identifier"
            )
            # And distinct from the ASCII-stripped version
            ascii_stripped = "".join(c for c in label if c.isascii())
            if ascii_stripped.strip():
                ascii_result = _sanitize_func_name(ascii_stripped)
                assert result != ascii_result, (
                    f"{label!r} and {ascii_stripped!r} both -> {result!r}"
                )

    def test_distinct_non_ascii_inputs_distinct_outputs(self) -> None:
        """Labels differing ONLY in their non-ASCII characters must not collide.

        ``café`` and ``cafó`` today both sanitize to ``caf`` -- the "é"
        and "ó" both get stripped.  Same for the CJK pair below:
        ``step_a_用户`` and ``step_a_客户`` both sanitize to ``step_a_``.
        Post-fix these must be distinct.
        """
        assert _sanitize_func_name("café") != _sanitize_func_name("cafó"), (
            f"café/cafó collide: {_sanitize_func_name('café')!r}"
        )
        assert _sanitize_func_name("step_a_用户") != _sanitize_func_name("step_a_客户"), (
            f"CJK pair collides: {_sanitize_func_name('step_a_用户')!r}"
        )

    def test_round_trip_stability_on_non_ascii_today(self) -> None:
        """sanitize(sanitize(non_ascii)) == sanitize(non_ascii).

        This must continue to hold AFTER the fix.  Today it holds
        trivially because non-ASCII is stripped (so the second pass
        sees ASCII only); post-fix it must hold because the encoded
        form (e.g. ``_u00E9`` for ``é``) is itself all-ASCII and
        stable under re-application.  Pinned as a currently-passing
        test that must stay green.
        """
        for label in ["café", "用户1", "naïve", "Zürich", "数学"]:
            once = _sanitize_func_name(label)
            twice = _sanitize_func_name(once)
            assert once == twice, (
                f"non-ASCII double-sanitize drifted for {label!r}: {once!r} vs {twice!r}"
            )


# ---------------------------------------------------------------------------
# #124 — Duplicate sanitized names raise ParseError (shipped direct as Phase B)
# ---------------------------------------------------------------------------


class TestDuplicateSanitizedNamesRaise:
    """When two distinct labels sanitize to the same identifier, codegen
    must raise :class:`ParseError` naming every colliding bucket.

    Historical note: the original plan split this into Phase A (warn)
    and Phase B (raise) with a release-window migration.  Haute has no
    deployed user base that needs a migration path, so we shipped
    directly as Phase B — silently-warning codegen with a collision
    corrupts the pipeline (second ``def`` shadows first), so failing
    loud at codegen time is the correct behaviour for a new tool.
    """

    def _find_collision_pair(self) -> tuple[str, str]:
        """Find two distinct labels that collide under the current sanitiser.

        We do not hard-code because the collision mapping changes with
        the #123 fix — we want a test that adapts to whatever mapping
        is in force.

        ``"My Func"`` and ``"My_Func"`` both sanitize to ``"My_Func"``
        today (space → underscore).  That collision is stable under
        either sanitiser implementation because it is entirely
        ASCII-driven, making it a reliable test vector.
        """
        a, b = "My Func", "My_Func"
        assert _sanitize_func_name(a) == _sanitize_func_name(b), (
            f"expected {a!r} and {b!r} to collide, but "
            f"{_sanitize_func_name(a)!r} != {_sanitize_func_name(b)!r}"
        )
        return a, b

    def test_collision_pair_exists_today(self) -> None:
        """Meta-test: the fixture labels really do collide.

        If this test fails the rest of the class is meaningless, so
        we assert explicitly here with a helpful message.
        """
        a, b = self._find_collision_pair()
        assert _sanitize_func_name(a) == _sanitize_func_name(b)

    def test_duplicate_labels_raise_parse_error(self) -> None:
        """Build a graph where two nodes' labels collide; codegen must raise
        :class:`ParseError` and the message must name both original labels."""
        from haute.codegen import graph_to_code
        from haute.errors import ParseError
        from haute.graph_utils import PipelineGraph

        label_a, label_b = self._find_collision_pair()
        graph = PipelineGraph.model_validate(
            {
                "nodes": [
                    {
                        "id": "n1",
                        "data": {
                            "label": label_a,
                            "nodeType": "polars",
                            "config": {"code": "df = upstream"},
                        },
                    },
                    {
                        "id": "n2",
                        "data": {
                            "label": label_b,
                            "nodeType": "polars",
                            "config": {"code": "df = upstream"},
                        },
                    },
                ],
                "edges": [],
            }
        )

        with pytest.raises(ParseError) as excinfo:
            graph_to_code(graph)

        # Every raised error must name BOTH original labels so the user
        # can find them in the GUI — naming only one is a regression.
        message = str(excinfo.value)
        assert label_a in message, f"ParseError does not name {label_a!r}: {message}"
        assert label_b in message, f"ParseError does not name {label_b!r}: {message}"
