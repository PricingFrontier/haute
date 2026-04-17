"""Tests for the typed error hierarchy in ``haute.errors``.

F1 of the codebase review execution plan. Written TDD-style — these tests
fail (ModuleNotFoundError) until ``src/haute/errors.py`` is implemented.

Hierarchy::

    HauteError(Exception)
        ConfigError             — config loading / validation failures
        ParseError              — pipeline source parsing failures
        ExecutionError          — runtime execution failures
        DeployError             — deploy validation / bundling failures
        FeatureMismatchError    — feature / categorical train-vs-score mismatch

Contract::

    raise ConfigError("msg", path="/x", node_id="n1")

    err.context == {"path": "/x", "node_id": "n1"}
    str(err)    == "msg (path=/x, node_id=n1)"

The str() format is pinned precisely so downstream tooling (log parsers,
CLI error renderers, test assertions) can rely on it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from haute.errors import (
    ConfigError,
    DeployError,
    ExecutionError,
    FeatureMismatchError,
    HauteError,
    ParseError,
)

SUBCLASSES = [ConfigError, ParseError, ExecutionError, DeployError, FeatureMismatchError]
SUBCLASS_IDS = [cls.__name__ for cls in SUBCLASSES]


# ===========================================================================
# 1. Hierarchy
# ===========================================================================


class TestHierarchy:
    def test_haute_error_inherits_exception(self):
        assert issubclass(HauteError, Exception)

    @pytest.mark.parametrize("cls", SUBCLASSES, ids=SUBCLASS_IDS)
    def test_subclass_inherits_haute_error(self, cls):
        assert issubclass(cls, HauteError)
        assert issubclass(cls, Exception)

    def test_subclasses_are_distinct(self):
        """No subclass accidentally inherits from a sibling."""
        for a in SUBCLASSES:
            for b in SUBCLASSES:
                if a is not b:
                    assert not issubclass(a, b), (
                        f"{a.__name__} unexpectedly inherits from {b.__name__}"
                    )


# ===========================================================================
# 2. Basic raise / catch
# ===========================================================================


class TestRaiseAndCatch:
    @pytest.mark.parametrize("cls", SUBCLASSES, ids=SUBCLASS_IDS)
    def test_raise_with_message_only(self, cls):
        with pytest.raises(cls, match="boom"):
            raise cls("boom")

    @pytest.mark.parametrize("cls", SUBCLASSES, ids=SUBCLASS_IDS)
    def test_catch_as_haute_error(self, cls):
        with pytest.raises(HauteError):
            raise cls("anything")

    def test_specific_class_does_not_catch_sibling(self):
        with pytest.raises(ParseError):
            try:
                raise ParseError("bad AST")
            except ConfigError:
                pytest.fail("ConfigError should not catch ParseError")


# ===========================================================================
# 3. Context kwargs
# ===========================================================================


class TestContextKwargs:
    def test_context_stored_as_dict(self):
        err = ConfigError("msg", path="/x", node_id="n1")
        assert err.context == {"path": "/x", "node_id": "n1"}

    def test_no_kwargs_empty_context(self):
        assert ConfigError("msg").context == {}

    def test_context_is_dict_type(self):
        assert isinstance(ExecutionError("msg", node="n1").context, dict)

    def test_context_preserves_insertion_order(self):
        """Python dicts preserve insertion order; str() relies on it."""
        err = ConfigError("msg", z="last", a="first", m="middle")
        assert list(err.context.keys()) == ["z", "a", "m"]

    @pytest.mark.parametrize("cls", SUBCLASSES, ids=SUBCLASS_IDS)
    def test_each_subclass_accepts_kwargs(self, cls):
        err = cls("msg", foo="bar", count=3)
        assert err.context == {"foo": "bar", "count": 3}

    def test_message_attribute_preserved(self):
        """The original message is accessible separately from context.

        Useful for callers that want to log message and context
        to structured-log fields instead of a single string.
        """
        err = ConfigError("the message", path="/x")
        assert err.message == "the message"


# ===========================================================================
# 4. str() rendering
# ===========================================================================


class TestStrRendering:
    def test_str_no_kwargs_is_just_message(self):
        assert str(ConfigError("simple message")) == "simple message"

    def test_str_with_single_kwarg(self):
        assert str(ConfigError("msg", path="/foo/bar.py")) == "msg (path=/foo/bar.py)"

    def test_str_with_multiple_kwargs(self):
        err = ConfigError("Missing source config", path="/foo/bar.py", node_id="src1")
        assert str(err) == "Missing source config (path=/foo/bar.py, node_id=src1)"

    def test_str_kwargs_in_insertion_order(self):
        assert str(ExecutionError("boom", z=1, a=2, m=3)) == "boom (z=1, a=2, m=3)"

    def test_str_integer_value(self):
        assert str(ExecutionError("too many retries", attempts=5)) == (
            "too many retries (attempts=5)"
        )

    def test_str_none_value(self):
        assert str(ConfigError("missing value", field=None)) == "missing value (field=None)"

    @pytest.mark.parametrize("cls", SUBCLASSES, ids=SUBCLASS_IDS)
    def test_str_empty_message_no_kwargs(self, cls):
        assert str(cls("")) == ""

    def test_str_empty_message_with_kwargs(self):
        """Empty message with kwargs drops the leading space."""
        assert str(ConfigError("", path="/x")) == "(path=/x)"

    @pytest.mark.parametrize("cls", SUBCLASSES, ids=SUBCLASS_IDS)
    def test_repr_contains_class_name(self, cls):
        """``repr`` should include the concrete class name for debugging."""
        assert cls.__name__ in repr(cls("msg", k=1))


# ===========================================================================
# 5. Traceback / exception chaining
# ===========================================================================


class TestChaining:
    def test_raise_from_preserves_cause(self):
        original = KeyError("missing")
        with pytest.raises(ConfigError) as exc_info:
            try:
                raise original
            except KeyError as exc:
                raise ConfigError("config lookup failed", key="foo") from exc
        assert exc_info.value.__cause__ is original

    def test_raise_from_none_suppresses_cause(self):
        with pytest.raises(ConfigError) as exc_info:
            try:
                raise KeyError("x")
            except KeyError:
                raise ConfigError("clean error") from None
        assert exc_info.value.__cause__ is None
        assert exc_info.value.__suppress_context__ is True

    def test_implicit_context_preserved(self):
        """Re-raising inside an ``except`` preserves ``__context__``."""
        with pytest.raises(ExecutionError) as exc_info:
            try:
                raise RuntimeError("inner")
            except RuntimeError:
                raise ExecutionError("outer")
        assert isinstance(exc_info.value.__context__, RuntimeError)


# ===========================================================================
# 6. Empty message edge case
# ===========================================================================


class TestEmptyMessage:
    @pytest.mark.parametrize("cls", SUBCLASSES, ids=SUBCLASS_IDS)
    def test_construct_with_empty_message(self, cls):
        err = cls("")
        assert err.message == ""
        assert err.context == {}
        assert str(err) == ""

    def test_empty_message_with_kwargs(self):
        err = ParseError("", line=42, col=3)
        assert err.message == ""
        assert err.context == {"line": 42, "col": 3}
        assert str(err) == "(line=42, col=3)"


# ===========================================================================
# 7. Kwargs with complex values
# ===========================================================================


class TestComplexKwargValues:
    def test_pathlib_path(self):
        p = Path("/tmp/foo/bar.py")
        err = ConfigError("bad path", path=p)
        rendered = str(err)
        assert "bad path" in rendered
        assert "path=" in rendered
        assert str(p) in rendered
        assert err.context["path"] is p

    def test_integer_kwarg(self):
        err = ExecutionError("failed", retries=3, timeout=30)
        assert err.context == {"retries": 3, "timeout": 30}
        assert str(err) == "failed (retries=3, timeout=30)"

    def test_none_kwarg(self):
        err = ConfigError("missing", value=None)
        assert str(err) == "missing (value=None)"

    def test_nested_dict_kwarg(self):
        payload = {"a": 1, "b": [2, 3]}
        err = DeployError("bundle failed", payload=payload)
        assert err.context["payload"] is payload
        rendered = str(err)
        assert "bundle failed" in rendered
        assert "payload=" in rendered

    def test_list_and_bool_kwargs(self):
        err = FeatureMismatchError(
            "cols differ",
            missing=["age", "region"],
            strict=True,
            optional=False,
        )
        rendered = str(err)
        assert "missing=" in rendered
        assert "strict=True" in rendered
        assert "optional=False" in rendered

    def test_float_kwarg(self):
        assert "duration=1.23" in str(ExecutionError("slow", duration=1.23))


# ===========================================================================
# 8. Unicode safety
# ===========================================================================


class TestUnicode:
    def test_unicode_message(self):
        err = ConfigError("invalid côlumn name: naïve")
        assert "côlumn" in str(err)
        assert "naïve" in str(err)

    def test_unicode_kwarg_value(self):
        err = ConfigError("bad", name="€uro-sign")
        assert "€uro-sign" in str(err)

    def test_unicode_kwarg_value_multibyte(self):
        """Multi-byte kwarg values (CJK) render without encoding errors."""
        err = ConfigError("bad", label="日本語")
        assert "日本語" in str(err)

    def test_unicode_does_not_crash_repr(self):
        err = ParseError("accénted", col="côl")
        assert repr(err)


# ===========================================================================
# 9. Not caught by stdlib exceptions
# ===========================================================================


class TestNotSubclassOfStdlib:
    """Users catching ``except ValueError`` must not accidentally catch ours.

    A ``ConfigError`` is a Haute config problem, not a generic bad value —
    the two are semantically distinct and should not share catch blocks.
    """

    STDLIB = [ValueError, KeyError, TypeError, RuntimeError, LookupError, OSError]

    @pytest.mark.parametrize("stdlib_exc", STDLIB)
    def test_haute_error_is_not_stdlib_subclass(self, stdlib_exc):
        assert not issubclass(HauteError, stdlib_exc)

    @pytest.mark.parametrize("cls", SUBCLASSES, ids=SUBCLASS_IDS)
    def test_subclass_is_not_value_error(self, cls):
        """Sanity guard for the most common stdlib-catch mistake."""
        assert not issubclass(cls, ValueError)

    def test_except_valueerror_does_not_catch_config_error(self):
        with pytest.raises(ConfigError):
            try:
                raise ConfigError("this should propagate")
            except ValueError:
                pytest.fail("ValueError handler should not catch ConfigError")


# ===========================================================================
# 10. Representative wiring — regression guard for proof-of-wiring
# ===========================================================================


class TestRepresentativeWiring:
    """Regression guard for the dev's proof-of-wiring step.

    The canonical wiring candidate is ``haute.deploy._schema._find_node``,
    which today raises ``ValueError("Node '...' not found in graph")``.
    After conversion it should raise ``ConfigError`` (or any ``HauteError``
    subclass) with ``node_id`` in context.

    These tests do not prescribe which typed class is chosen — they verify
    the behavior that must hold before AND after wiring.
    """

    def test_wiring_site_imports(self):
        from haute.deploy._schema import _find_node  # noqa: F401

    def test_find_node_raises_on_missing(self):
        """Must raise, whether ``ValueError`` (pre-wiring) or ``HauteError``
        (post-wiring). Silent return is unacceptable.
        """
        from haute.deploy._schema import _find_node
        from haute.graph_utils import PipelineGraph

        graph = PipelineGraph(nodes=[], edges=[])
        with pytest.raises((ValueError, HauteError)):
            _find_node(graph, "nonexistent_node_id")

    def test_typed_error_preserves_catchability_via_haute_error(self):
        """Callers catching ``HauteError`` should catch any subclass."""
        try:
            raise ConfigError("Node not found", node_id="missing_id")
        except HauteError as exc:
            assert exc.context["node_id"] == "missing_id"
            assert "Node not found" in str(exc)
        else:  # pragma: no cover
            pytest.fail("ConfigError should have been caught as HauteError")

    def test_typed_error_context_round_trips_through_chain(self):
        """Chaining from a stdlib exc preserves both cause and context."""
        original = KeyError("missing_id")
        try:
            try:
                raise original
            except KeyError as exc:
                raise ConfigError("Node not found", node_id="n1") from exc
        except ConfigError as caught:
            assert caught.__cause__ is original
            assert caught.context == {"node_id": "n1"}
            assert "node_id=n1" in str(caught)


# ===========================================================================
# Instance behavior — args tuple contract
# ===========================================================================


class TestInstanceBehavior:
    def test_args_tuple_contains_rendered_message(self):
        """``Exception.args[0]`` must match ``str(err)`` — the fully
        rendered form (message + context), not just the bare message.

        This keeps ``logging.exception`` and traceback formatters showing
        the full error information, which is the whole point of context kwargs.
        """
        err = ConfigError("boom", path="/x")
        assert err.args == (str(err),)
        assert err.args == ("boom (path=/x)",)

    def test_args_tuple_with_no_kwargs(self):
        err = ConfigError("plain")
        assert err.args == ("plain",)
