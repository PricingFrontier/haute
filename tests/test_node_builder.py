"""Tests for haute._node_builder — NodeBuildHooks and wrap_builder."""

from __future__ import annotations

from unittest.mock import MagicMock

from haute._node_builder import NodeBuildHooks, node_fn_name, wrap_builder
from haute.graph_utils import GraphNode
from tests.conftest import make_node

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_node(label: str, node_type: str = "polars") -> GraphNode:
    return make_node(
        {
            "id": label,
            "data": {"label": label, "nodeType": node_type, "config": {}},
        }
    )


def _dummy_base(node, source_names=None, **kwargs):
    """A fake base builder that returns a predictable result."""
    return (node.data.label, lambda df: df, False)


# ---------------------------------------------------------------------------
# NodeBuildHooks
# ---------------------------------------------------------------------------


class TestNodeBuildHooks:
    def test_default_before_build_is_none(self) -> None:
        hooks = NodeBuildHooks()
        assert hooks.before_build is None

    def test_with_before_build(self) -> None:
        fn = MagicMock()
        hooks = NodeBuildHooks(before_build=fn)
        assert hooks.before_build is fn


# ---------------------------------------------------------------------------
# wrap_builder
# ---------------------------------------------------------------------------


class TestWrapBuilder:
    def test_falls_through_to_base_when_no_hook(self) -> None:
        hooks = NodeBuildHooks()
        wrapped = wrap_builder(_dummy_base, hooks)
        node = _make_node("test")
        name, fn, is_source = wrapped(node)
        assert name == "test"
        assert is_source is False

    def test_falls_through_when_hook_returns_none(self) -> None:
        hook = MagicMock(return_value=None)
        hooks = NodeBuildHooks(before_build=hook)
        wrapped = wrap_builder(_dummy_base, hooks)
        node = _make_node("test")
        name, fn, is_source = wrapped(node, source_names=["src"], source_ids=["src-node"])
        assert name == "test"
        hook.assert_called_once_with(node, ["src"], ["src-node"])

    def test_hook_overrides_base(self) -> None:
        sentinel_fn = lambda df: df  # noqa: E731
        hook = MagicMock(return_value=("custom", sentinel_fn, True))
        hooks = NodeBuildHooks(before_build=hook)
        wrapped = wrap_builder(_dummy_base, hooks)
        node = _make_node("test")
        name, fn, is_source = wrapped(node)
        assert name == "custom"
        assert fn is sentinel_fn
        assert is_source is True

    def test_base_not_called_when_hook_overrides(self) -> None:
        hook = MagicMock(return_value=("x", lambda: None, False))
        hooks = NodeBuildHooks(before_build=hook)
        base = MagicMock(return_value=("base", lambda: None, False))
        wrapped = wrap_builder(base, hooks)
        node = _make_node("test")
        wrapped(node)
        base.assert_not_called()

    def test_source_names_default_to_empty_list(self) -> None:
        """When source_names is None, the hook receives an empty list."""
        received_names = []

        def capture_hook(node, names, source_ids):
            received_names.append(names)
            return None

        hooks = NodeBuildHooks(before_build=capture_hook)
        wrapped = wrap_builder(_dummy_base, hooks)
        node = _make_node("test")
        wrapped(node, source_names=None)
        assert received_names == [[]]

    def test_source_ids_default_to_empty_list(self) -> None:
        """When source_ids is absent, the hook receives an empty list."""
        received_ids = []

        def capture_hook(node, names, source_ids):
            received_ids.append(source_ids)
            return None

        hooks = NodeBuildHooks(before_build=capture_hook)
        wrapped = wrap_builder(_dummy_base, hooks)
        node = _make_node("test")
        wrapped(node, source_names=["src"])
        assert received_ids == [[]]

    def test_kwargs_forwarded_to_base(self) -> None:
        base = MagicMock(return_value=("b", lambda: None, False))
        hooks = NodeBuildHooks()
        wrapped = wrap_builder(base, hooks)
        node = _make_node("test")
        wrapped(node, source_names=["s"], extra_kwarg="val")
        # The wrapper checks the config before its hook and tells the base builder so.
        base.assert_called_once_with(
            node, source_names=["s"], source_ids=None, config_checked=True, extra_kwarg="val"
        )


# ---------------------------------------------------------------------------
# node_fn_name
# ---------------------------------------------------------------------------


class TestNodeFnName:
    def test_simple_label(self) -> None:
        node = _make_node("load_data")
        assert node_fn_name(node) == "load_data"

    def test_label_with_spaces(self) -> None:
        node = _make_node("Load Data")
        assert node_fn_name(node) == "Load_Data"

    def test_label_with_dashes(self) -> None:
        node = _make_node("my-node")
        assert node_fn_name(node) == "my_node"

    def test_label_starting_with_digit(self) -> None:
        node = _make_node("1st_node")
        assert node_fn_name(node) == "node_1st_node"

    def test_reserved_word_label(self) -> None:
        node = _make_node("return")
        assert node_fn_name(node) == "node_return"

    def test_empty_label(self) -> None:
        node = _make_node("")
        assert node_fn_name(node) == "unnamed_node"

    def test_special_characters_stripped(self) -> None:
        node = _make_node("rate@2024!")
        result = node_fn_name(node)
        assert "@" not in result
        assert "!" not in result


# ===========================================================================
# _node_builder.py — additional coverage for wrap_builder
# ===========================================================================


class TestWrapBuilderAdditional:
    """Additional edge cases for wrap_builder."""

    def test_hook_receives_correct_source_names_list(self):
        """When source_names is a populated list, the hook gets that list."""
        from haute._node_builder import NodeBuildHooks, wrap_builder

        received = []

        def capture(node, names, source_ids):
            received.append(list(names))
            return None

        def base(node, source_names=None, **kw):
            return (node.data.label, lambda df: df, False)

        hooks = NodeBuildHooks(before_build=capture)
        wrapped = wrap_builder(base, hooks)
        node = make_node({"id": "t", "data": {"label": "t", "nodeType": "polars", "config": {}}})
        wrapped(node, source_names=["src_a", "src_b", "src_c"])
        assert received == [["src_a", "src_b", "src_c"]]

    def test_hook_override_skips_base_completely(self):
        """When hook returns a result, base is never invoked."""
        from haute._node_builder import NodeBuildHooks, wrap_builder

        base_called = []

        def base(node, source_names=None, **kw):
            base_called.append(True)
            return ("base", lambda: None, False)

        override_fn = lambda df: df  # noqa: E731
        hooks = NodeBuildHooks(before_build=lambda n, s, ids: ("override", override_fn, True))
        wrapped = wrap_builder(base, hooks)
        node = make_node({"id": "t", "data": {"label": "t", "nodeType": "polars", "config": {}}})
        name, fn, is_source = wrapped(node, source_names=["a"])
        assert name == "override"
        assert fn is override_fn
        assert is_source is True
        assert base_called == []
