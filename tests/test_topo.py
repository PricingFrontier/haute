"""Tests for haute._topo — topological sorting and cycle detection."""

from __future__ import annotations

import pytest

from haute._topo import CycleError, ancestors, topo_sort_ids
from haute._types import GraphEdge


def _e(src: str, tgt: str) -> GraphEdge:
    return GraphEdge(id=f"e_{src}_{tgt}", source=src, target=tgt)


# ---------------------------------------------------------------------------
# topo_sort_ids — basic ordering
# ---------------------------------------------------------------------------


class TestTopoSortIds:
    def test_linear_chain(self):
        """a -> b -> c produces [a, b, c]."""
        order = topo_sort_ids(["a", "b", "c"], [_e("a", "b"), _e("b", "c")])
        assert order == ["a", "b", "c"]

    def test_diamond(self):
        """Diamond: a -> b, a -> c, b -> d, c -> d."""
        order = topo_sort_ids(
            ["a", "b", "c", "d"],
            [_e("a", "b"), _e("a", "c"), _e("b", "d"), _e("c", "d")],
        )
        assert order[0] == "a"
        assert order[-1] == "d"
        assert set(order) == {"a", "b", "c", "d"}

    def test_disconnected_nodes(self):
        """Disconnected nodes preserve insertion order (graphlib tie-break)."""
        order = topo_sort_ids(["c", "a", "b"], [])
        assert order == ["c", "a", "b"]

    def test_single_node(self):
        order = topo_sort_ids(["x"], [])
        assert order == ["x"]

    def test_empty_graph(self):
        order = topo_sort_ids([], [])
        assert order == []

    def test_unknown_edge_endpoints_ignored(self):
        """Edges referencing unknown nodes are silently skipped."""
        order = topo_sort_ids(["a", "b"], [_e("a", "b"), _e("x", "y")])
        assert set(order) == {"a", "b"}

    def test_duplicate_edges_handled(self):
        edges = [_e("a", "b"), _e("a", "b")]
        order = topo_sort_ids(["a", "b"], edges)
        assert order == ["a", "b"]

    def test_very_deep_chain(self):
        ids = [f"n{i:03d}" for i in range(60)]
        edges = [_e(ids[i], ids[i + 1]) for i in range(59)]
        order = topo_sort_ids(ids, edges)
        assert order == ids

    def test_wide_branching(self):
        children = [f"c{i:02d}" for i in range(25)]
        ids = ["root"] + children
        edges = [_e("root", c) for c in children]
        order = topo_sort_ids(ids, edges)
        assert order[0] == "root"
        assert set(order[1:]) == set(children)

    def test_deterministic_insertion_ordering(self):
        """Ties are broken by insertion order (graphlib stdlib behaviour)."""
        ids = ["z", "m", "a", "f"]
        for _ in range(5):
            order = topo_sort_ids(ids, [])
            assert order == ["z", "m", "a", "f"]


# ---------------------------------------------------------------------------
# topo_sort_ids — cycle detection (A14)
# ---------------------------------------------------------------------------


class TestCycleDetection:
    def test_simple_cycle_raises(self):
        """a -> b -> a raises CycleError."""
        with pytest.raises(CycleError, match="Cycle detected") as exc_info:
            topo_sort_ids(["a", "b"], [_e("a", "b"), _e("b", "a")])
        assert set(exc_info.value.cycle_nodes) == {"a", "b"}

    def test_self_loop_raises(self):
        """a -> a raises CycleError."""
        with pytest.raises(CycleError, match="Cycle detected") as exc_info:
            topo_sort_ids(["a"], [_e("a", "a")])
        assert exc_info.value.cycle_nodes == ["a"]

    def test_three_node_cycle(self):
        """a -> b -> c -> a raises CycleError."""
        with pytest.raises(CycleError, match="Cycle detected") as exc_info:
            topo_sort_ids(
                ["a", "b", "c"],
                [_e("a", "b"), _e("b", "c"), _e("c", "a")],
            )
        assert set(exc_info.value.cycle_nodes) == {"a", "b", "c"}

    def test_partial_cycle_only_reports_cycle_nodes(self):
        """d -> a -> b -> a: only a, b are in the cycle; d is not."""
        with pytest.raises(CycleError) as exc_info:
            topo_sort_ids(
                ["a", "b", "d"],
                [_e("d", "a"), _e("a", "b"), _e("b", "a")],
            )
        # d can be sorted (in_degree reaches 0); a and b are in the cycle
        assert set(exc_info.value.cycle_nodes) == {"a", "b"}

    def test_cycle_error_is_haute_error(self):
        """CycleError is a HauteError subclass."""
        from haute._types import HauteError

        with pytest.raises(HauteError):
            topo_sort_ids(["a", "b"], [_e("a", "b"), _e("b", "a")])

    def test_multiple_disconnected_cycles(self):
        ids = ["a", "b", "c", "d", "e"]
        edges = [_e("a", "b"), _e("b", "a"), _e("c", "d"), _e("d", "c")]
        with pytest.raises(CycleError) as exc_info:
            topo_sort_ids(ids, edges)
        assert {"a", "b", "c", "d"} <= set(exc_info.value.cycle_nodes)

    def test_no_cycle_does_not_raise(self):
        """Valid DAG does not raise CycleError."""
        order = topo_sort_ids(
            ["a", "b", "c"],
            [_e("a", "b"), _e("b", "c")],
        )
        assert order == ["a", "b", "c"]

    def test_cycle_error_message_includes_node_names(self):
        """Error message lists the nodes involved."""
        with pytest.raises(CycleError, match="a.*b"):
            topo_sort_ids(["a", "b"], [_e("a", "b"), _e("b", "a")])


# ---------------------------------------------------------------------------
# ancestors
# ---------------------------------------------------------------------------


class TestAncestors:
    def test_includes_self(self):
        result = ancestors("b", [_e("a", "b")], {"a", "b"})
        assert "b" in result

    def test_finds_all_ancestors(self):
        result = ancestors("c", [_e("a", "b"), _e("b", "c")], {"a", "b", "c"})
        assert result == {"a", "b", "c"}

    def test_no_ancestors(self):
        result = ancestors("a", [_e("a", "b")], {"a", "b"})
        assert result == {"a"}

    def test_diamond_ancestors(self):
        result = ancestors(
            "d",
            [_e("a", "b"), _e("a", "c"), _e("b", "d"), _e("c", "d")],
            {"a", "b", "c", "d"},
        )
        assert result == {"a", "b", "c", "d"}

    def test_target_not_in_all_ids(self):
        result = ancestors("unknown", [_e("a", "b")], {"a", "b"})
        assert result == {"unknown"}

    def test_very_deep_chain_ancestors(self):
        ids = [f"n{i:03d}" for i in range(55)]
        edges = [_e(ids[i], ids[i + 1]) for i in range(54)]
        result = ancestors(ids[-1], edges, set(ids))
        assert result == set(ids)

    def test_wide_fan_in(self):
        parents = [f"p{i:02d}" for i in range(25)]
        all_ids = set(parents) | {"child"}
        edges = [_e(p, "child") for p in parents]
        result = ancestors("child", edges, all_ids)
        assert result == all_ids

    def test_disconnected_node(self):
        result = ancestors("a", [_e("b", "c")], {"a", "b", "c"})
        assert result == {"a"}

    def test_diamond_no_duplicate_ancestors(self):
        edges = [_e("a", "b"), _e("a", "c"), _e("b", "d"), _e("c", "d")]
        result = ancestors("d", edges, {"a", "b", "c", "d"})
        assert len(result) == 4
        assert result == {"a", "b", "c", "d"}
