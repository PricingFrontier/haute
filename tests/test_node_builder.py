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
        name, fn, is_source = wrapped(node, source_names=["src"])
        assert name == "test"
        hook.assert_called_once_with(node, ["src"])

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

        def capture_hook(node, names):
            received_names.append(names)
            return None

        hooks = NodeBuildHooks(before_build=capture_hook)
        wrapped = wrap_builder(_dummy_base, hooks)
        node = _make_node("test")
        wrapped(node, source_names=None)
        assert received_names == [[]]

    def test_kwargs_forwarded_to_base(self) -> None:
        base = MagicMock(return_value=("b", lambda: None, False))
        hooks = NodeBuildHooks()
        wrapped = wrap_builder(base, hooks)
        node = _make_node("test")
        wrapped(node, source_names=["s"], extra_kwarg="val")
        base.assert_called_once_with(node, source_names=["s"], extra_kwarg="val")


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
