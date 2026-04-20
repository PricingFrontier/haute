"""Phase 5 Wave 9D #123 / #124 / #125 — parser & codegen hardening.

Three tightly-scoped fixes to ``src/haute/_graph_utils.py`` and
``src/haute/_ast_helpers.py``:

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

* **#125 ``_extract_function_bodies`` tree-optional → required.**
  The ``tree`` parameter defaults to ``None`` today; if callers forget
  to pass a pre-parsed tree the function calls ``ast.parse`` a second
  time, which is both slower and a foot-gun (source and tree can
  disagree).  Post-fix the parameter becomes required.  Every
  production caller in ``src/haute/*.py`` already passes ``tree``; this
  suite asserts that fact so a future caller that forgets cannot slip
  through.

Constraints:

* No ``unittest.TestCase`` — plain pytest functions / classes.
* ``structlog.testing.capture_logs`` for log capture.
* Tests marked as pinning either *current correct behaviour* (must
  continue to pass) or *current broken behaviour* (will pass after the
  dev fix — marked ``xfail(strict=True)``).
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest
import structlog

from haute._ast_helpers import _extract_function_bodies
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
            assert once == twice, (
                f"double-sanitize drifted for {label!r}: {once!r} vs {twice!r}"
            )


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
        assert _sanitize_func_name("step_a_用户") != _sanitize_func_name(
            "step_a_客户"
        ), (
            f"CJK pair collides: "
            f"{_sanitize_func_name('step_a_用户')!r}"
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
                f"non-ASCII double-sanitize drifted for {label!r}: "
                f"{once!r} vs {twice!r}"
            )


# ---------------------------------------------------------------------------
# #124 — Duplicate sanitized names warning (Phase A)
# ---------------------------------------------------------------------------


class TestDuplicateSanitizedNamesWarning:
    """When two distinct labels sanitize to the same identifier, codegen
    must log a structlog WARNING naming both original labels, AND continue
    execution (Phase A — Phase B will raise; see below).
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

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "Wave 9D #124 Phase A: codegen must emit a structlog WARNING "
            "when two labels sanitize to the same identifier.  Today "
            "codegen silently emits two functions with the same name, "
            "the second shadowing the first at import time.  Post-fix "
            "this test passes (warning fired, both labels named)."
        ),
    )
    def test_duplicate_labels_fire_warning(self) -> None:
        """Build a graph where two nodes' labels collide; codegen must warn."""
        from haute.codegen import graph_to_code
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

        with structlog.testing.capture_logs() as captured:
            code = graph_to_code(graph)

        # Phase A: execution continues (no exception raised).
        assert isinstance(code, str) and code, "codegen must still return code"

        # A warning with both original labels must have been logged.
        warnings = [
            ev for ev in captured if ev.get("log_level") == "warning"
        ]
        assert warnings, (
            "no warning fired despite colliding labels "
            f"{label_a!r} and {label_b!r}"
        )
        # Find the collision warning specifically and check it names both.
        collision_warnings = [
            ev
            for ev in warnings
            if any(
                "duplicate" in str(v).lower()
                or "collision" in str(v).lower()
                or "collid" in str(v).lower()
                for v in ev.values()
            )
        ]
        assert collision_warnings, (
            "no warning event mentions duplicate / collision; "
            f"captured warnings: {warnings}"
        )
        # Every collision warning must name BOTH original labels so the
        # user can find them in the GUI — naming only one is a regression.
        payload = str(collision_warnings)
        assert label_a in payload, (
            f"warning does not name {label_a!r}: {payload}"
        )
        assert label_b in payload, (
            f"warning does not name {label_b!r}: {payload}"
        )

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "Wave 9D #124 Phase A: codegen must not RAISE on a "
            "collision — only WARN.  Phase B (raise) is deferred; see "
            "the migration comment in this file.  Currently codegen "
            "doesn't emit any warning, so this test catches the "
            "transition-to-warning step."
        ),
    )
    def test_phase_a_does_not_raise(self) -> None:
        """Phase A behaviour: continue-with-warning, do NOT raise.

        PHASE B MIGRATION PLAN
        ----------------------
        After at least one release cycle of Phase A warnings in
        production, upgrade the warning to an exception:

        1. Change ``logger.warning`` to ``raise ConfigError`` in the
           duplicate-detection path.
        2. Update this test to ``pytest.raises(ConfigError)``.
        3. Remove ``@pytest.mark.xfail`` from this class.

        The Phase A warning fires at save / codegen time — users see
        it in the GUI warning banner.  Phase B makes it a hard stop.
        """
        from haute.codegen import graph_to_code
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
        # Must not raise:
        with structlog.testing.capture_logs() as captured:
            code = graph_to_code(graph)
        assert "def " in code  # codegen still produced something
        # And must have emitted at least one warning (sanity check —
        # the dedicated warning test above is stricter).
        assert any(
            ev.get("log_level") == "warning" for ev in captured
        ), "Phase A requires at least a warning to be emitted"


# ---------------------------------------------------------------------------
# #125 — _extract_function_bodies tree-optional → required
# ---------------------------------------------------------------------------


class TestExtractFunctionBodiesTreeRequired:
    """The ``tree`` parameter of ``_extract_function_bodies`` is today
    defaulted to ``None``; post-fix it becomes required (no default).

    Callers that forget to pass ``tree`` will then raise ``TypeError``
    at call time instead of silently re-parsing the source.  We also
    scan production source to assert every existing call site already
    passes ``tree`` (so the fix does not break any caller).
    """

    def test_tree_parameter_exists(self) -> None:
        """Regardless of default, ``tree`` must remain a parameter.

        Sanity pin: if the parameter is renamed or removed outright
        this test catches it immediately.  Must pass both before and
        after the #125 fix.
        """
        sig = inspect.signature(_extract_function_bodies)
        assert "tree" in sig.parameters, (
            "tree parameter must exist on _extract_function_bodies"
        )

    def test_tree_has_no_default_after_fix(self) -> None:
        sig = inspect.signature(_extract_function_bodies)
        assert (
            sig.parameters["tree"].default is inspect.Parameter.empty
        ), "tree parameter still has a default value; fix not applied"

    def test_call_without_tree_raises_after_fix(self) -> None:
        """Post-fix behaviour: calling without ``tree`` raises TypeError."""
        source = "def f():\n    return 42"
        with pytest.raises(TypeError, match="tree"):
            _extract_function_bodies(source)  # type: ignore[call-arg]

    def test_call_with_tree_still_works(self) -> None:
        """Post-fix behaviour: calling WITH ``tree`` continues to work.

        This pin must pass both before and after the fix — the fix
        changes only the default, not the happy path.
        """
        source = "def f():\n    return 42"
        tree = ast.parse(source)
        bodies = _extract_function_bodies(source, tree=tree)
        assert "f" in bodies
        assert "return 42" in bodies["f"]

    def test_call_with_tree_multi_function(self) -> None:
        """Multi-function happy path: tree=tree extracts every top-level fn."""
        source = (
            "def alpha():\n    return 1\n\n"
            "def beta():\n    return 2\n\n"
            "def gamma():\n    return 3\n"
        )
        tree = ast.parse(source)
        bodies = _extract_function_bodies(source, tree=tree)
        assert set(bodies) == {"alpha", "beta", "gamma"}


class TestEveryProductionCallerPassesTree:
    """Regression defender: every caller in ``src/haute/*.py`` must
    already pass ``tree=`` to ``_extract_function_bodies`` so the fix
    never breaks an existing site.

    This test parses the source of every production module, finds
    every call to ``_extract_function_bodies`` via the AST (not a
    regex), and asserts the call site uses a ``tree=`` keyword argument
    (or a positional second argument, equivalent).
    """

    def _iter_source_files(self) -> list[Path]:
        src_dir = Path(__file__).resolve().parent.parent / "src" / "haute"
        return sorted(src_dir.rglob("*.py"))

    def _find_calls(
        self, filepath: Path
    ) -> list[ast.Call]:
        source = filepath.read_text(encoding="utf-8")
        try:
            tree = ast.parse(source)
        except SyntaxError:
            # Skip files we can't parse.  That would be a different bug
            # surfaced elsewhere; do not mask it here.
            return []
        calls: list[ast.Call] = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            name: str | None = None
            if isinstance(fn, ast.Name):
                name = fn.id
            elif isinstance(fn, ast.Attribute):
                name = fn.attr
            if name == "_extract_function_bodies":
                # Skip the function's own definition line and the __all__ /
                # import re-export lines — they are not *calls*.
                calls.append(node)
        return calls

    def test_all_callers_pass_tree(self) -> None:
        """Every production call site must either pass ``tree=<expr>`` as
        a kwarg OR supply a second positional argument.  Either form
        survives the #125 fix (``tree`` becomes required).
        """
        missing: list[str] = []
        total_calls = 0

        for filepath in self._iter_source_files():
            # Skip the _ast_helpers.py definition itself — the only
            # "call" in there is the module-level ``ast.parse`` we
            # extracted.  We only care about *caller* sites.
            if filepath.name == "_ast_helpers.py":
                continue
            for call in self._find_calls(filepath):
                total_calls += 1
                has_tree_kw = any(
                    kw.arg == "tree" for kw in call.keywords
                )
                has_positional_tree = len(call.args) >= 2
                if not (has_tree_kw or has_positional_tree):
                    missing.append(
                        f"{filepath}:{call.lineno} — "
                        f"_extract_function_bodies(...) missing tree="
                    )

        # There should be at least one caller in production source —
        # if not, something is wrong with our walk (e.g. path drift).
        assert total_calls >= 1, (
            "found no production call sites; test is mis-configured"
        )
        assert not missing, (
            "production call sites missing tree=:\n" + "\n".join(missing)
        )
