"""Trace correlation / lineage tests for edge-join nodes.

W1.7 remediation: the post-hoc row correlator projected a join child's
row onto its parents by column *name* (``_trace_correlation``,
``match_row`` construction in ``_correlate_rows_posthoc``).  For the
JOIN-role (right) parent that is wrong twice over: Polars renames the
right frame's copy of every colliding column to ``<col><suffix>`` (so
the right parent's actual values were discarded), while the unsuffixed
``<col>`` in the child carries the BASE (left) frame's value (so left
values were matched against the right frame — poisoning correlation
onto whichever wrong right-row the left value happened to hit).

All tests drive ``execute_trace`` end-to-end on real pipeline graphs;
nothing feeds correlation internals by hand.  Assertions cover the
trace PATH (which upstream nodes appear) and per-step row VALUES only —
deliberately nothing about waterfall payloads (rewritten in Wave 3a),
and no fixture values anywhere near 2**53 (Int64 JSON semantics change
in Wave 7).
"""

from __future__ import annotations

import polars as pl
import pytest

from haute.trace import TraceResult, TraceStep, execute_trace
from tests.conftest import make_edge, make_graph, make_node, make_source_node

pytestmark = pytest.mark.usefixtures("_widen_sandbox_root")

LEFT_ID = "left_src"
RIGHT_ID = "right_src"
JOIN_ID = "join"


def _edge_join_graph(
    tmp_path,
    left_df: pl.DataFrame,
    right_df: pl.DataFrame,
    join_config: dict,
    *,
    reverse_edges: bool = False,
):
    """Two parquet sources feeding one edge-join node."""
    left_path = tmp_path / "left.parquet"
    right_path = tmp_path / "right.parquet"
    left_df.write_parquet(left_path)
    right_df.write_parquet(right_path)
    config = {"baseInput": LEFT_ID, "joinInput": RIGHT_ID, **join_config}
    edges = [
        make_edge(LEFT_ID, JOIN_ID, target_handle="base"),
        make_edge(RIGHT_ID, JOIN_ID, target_handle="join"),
    ]
    if reverse_edges:
        edges.reverse()
    return make_graph(
        {
            "nodes": [
                make_source_node(LEFT_ID, str(left_path)),
                make_source_node(RIGHT_ID, str(right_path)),
                make_node(
                    {
                        "id": JOIN_ID,
                        "data": {
                            "label": "Join",
                            "nodeType": "edgeJoin",
                            "config": config,
                        },
                    }
                ),
            ],
            "edges": edges,
        }
    )


def _step_ids(result: TraceResult) -> list[str]:
    return [s.node_id for s in result.steps]


def _step(result: TraceResult, node_id: str) -> TraceStep:
    matches = [s for s in result.steps if s.node_id == node_id]
    assert len(matches) == 1, f"expected exactly one step for {node_id!r}, got {_step_ids(result)}"
    return matches[0]


# ---------------------------------------------------------------------------
# Inner join — right-parent correlation through the suffix
# ---------------------------------------------------------------------------


class TestInnerJoinRightParentCorrelation:
    def test_right_parent_row_resolves_through_left_on_right_on_keys(self, tmp_path):
        """The suffixed copy identifies the right row; the left value must not.

        The right parent deliberately contains a decoy row whose
        ``premium`` equals the LEFT row's premium.  Correlating with the
        child's unsuffixed ``premium`` (a left value) locks onto the
        decoy; the child's ``premium_right`` (the right value) plus the
        leftOn/rightOn key pair identify the genuinely joined row.
        """
        left = pl.DataFrame({"policy": ["P1"], "premium": [100]})
        right = pl.DataFrame(
            {
                "ref": ["P1", "P2"],
                "premium": [999, 100],
                "tier": ["gold", "silver"],
            }
        )
        graph = _edge_join_graph(
            tmp_path,
            left,
            right,
            {"how": "inner", "leftOn": "policy", "rightOn": "ref"},
        )

        result = execute_trace(graph, row_index=0, target_node_id=JOIN_ID)

        assert set(_step_ids(result)) == {LEFT_ID, RIGHT_ID, JOIN_ID}
        assert _step(result, JOIN_ID).output_values == {
            "policy": "P1",
            "premium": 100,
            "premium_right": 999,
            "tier": "gold",
        }
        assert _step(result, LEFT_ID).output_values == {"policy": "P1", "premium": 100}
        assert _step(result, RIGHT_ID).output_values == {
            "ref": "P1",
            "premium": 999,
            "tier": "gold",
        }

    def test_right_parent_row_resolves_with_duplicate_join_keys(self, tmp_path):
        """1:many join — only the suffixed value can pick between key-twins."""
        left = pl.DataFrame({"key": ["a"], "premium": [100]})
        right = pl.DataFrame(
            {
                "key": ["a", "a"],
                "premium": [999, 100],
                "tier": ["gold", "silver"],
            }
        )
        graph = _edge_join_graph(
            tmp_path,
            left,
            right,
            {"how": "inner", "on": "key", "maintainOrder": "left_right"},
        )

        first = execute_trace(graph, row_index=0, target_node_id=JOIN_ID)
        assert _step(first, RIGHT_ID).output_values == {
            "key": "a",
            "premium": 999,
            "tier": "gold",
        }

        second = execute_trace(graph, row_index=1, target_node_id=JOIN_ID)
        assert _step(second, RIGHT_ID).output_values == {
            "key": "a",
            "premium": 100,
            "tier": "silver",
        }

    def test_custom_suffix_resolves_right_parent(self, tmp_path):
        """Suffix handling must come from the join config, not '_right' lore."""
        left = pl.DataFrame({"key": ["a"], "premium": [100]})
        right = pl.DataFrame(
            {
                "key": ["a", "a"],
                "premium": [999, 100],
                "tier": ["gold", "silver"],
            }
        )
        graph = _edge_join_graph(
            tmp_path,
            left,
            right,
            {"how": "inner", "on": "key", "suffix": "_lookup", "maintainOrder": "left_right"},
        )

        result = execute_trace(graph, row_index=0, target_node_id=JOIN_ID)

        assert _step(result, JOIN_ID).output_values["premium_lookup"] == 999
        assert _step(result, RIGHT_ID).output_values == {
            "key": "a",
            "premium": 999,
            "tier": "gold",
        }

    def test_right_parent_resolved_by_coalesced_key_when_values_tie(self, tmp_path):
        """leftOn/rightOn key lineage: the coalesced key must disambiguate.

        The right parent's non-key values are identical across rows, so
        only mapping the child's leftOn value onto the parent's rightOn
        column can identify which right row actually joined.
        """
        left = pl.DataFrame({"policy": ["P2"], "amount": [1]})
        right = pl.DataFrame({"ref": ["P1", "P2"], "factor": [7, 7]})
        graph = _edge_join_graph(
            tmp_path,
            left,
            right,
            {"how": "inner", "leftOn": "policy", "rightOn": "ref"},
        )

        result = execute_trace(graph, row_index=0, target_node_id=JOIN_ID)

        assert _step(result, LEFT_ID).output_values == {"policy": "P2", "amount": 1}
        assert _step(result, RIGHT_ID).output_values == {"ref": "P2", "factor": 7}


# ---------------------------------------------------------------------------
# Column-targeted traces — the three provenance cases
# ---------------------------------------------------------------------------


def _single_row_inner_graph(tmp_path):
    left = pl.DataFrame({"key": ["k1"], "premium": [100], "left_only": ["L"]})
    right = pl.DataFrame({"key": ["k1"], "premium": [999], "tier": ["gold"]})
    return _edge_join_graph(tmp_path, left, right, {"how": "inner", "on": "key"})


class TestColumnTraces:
    def test_left_origin_column_keeps_left_lineage_and_prunes_right(self, tmp_path):
        graph = _single_row_inner_graph(tmp_path)

        result = execute_trace(graph, row_index=0, target_node_id=JOIN_ID, column="left_only")

        assert set(_step_ids(result)) == {LEFT_ID, JOIN_ID}
        assert _step(result, LEFT_ID).output_values == {
            "key": "k1",
            "premium": 100,
            "left_only": "L",
        }
        assert _step(result, JOIN_ID).output_values["left_only"] == "L"

    def test_suffixed_right_column_traces_to_right_parent_row(self, tmp_path):
        """Tracing ``premium_right`` must show the RIGHT parent's actual row."""
        left = pl.DataFrame({"key": ["a"], "premium": [100]})
        right = pl.DataFrame(
            {
                "key": ["a", "a"],
                "premium": [999, 100],
                "tier": ["gold", "silver"],
            }
        )
        graph = _edge_join_graph(
            tmp_path,
            left,
            right,
            {"how": "inner", "on": "key", "maintainOrder": "left_right"},
        )

        result = execute_trace(graph, row_index=0, target_node_id=JOIN_ID, column="premium_right")

        # Both parents stay in the lineage of a column the join created.
        assert set(_step_ids(result)) == {LEFT_ID, RIGHT_ID, JOIN_ID}
        assert _step(result, JOIN_ID).output_values["premium_right"] == 999
        assert _step(result, RIGHT_ID).output_values == {
            "key": "a",
            "premium": 999,
            "tier": "gold",
        }

    def test_uncollided_right_column_keeps_right_lineage_and_prunes_left(self, tmp_path):
        graph = _single_row_inner_graph(tmp_path)

        result = execute_trace(graph, row_index=0, target_node_id=JOIN_ID, column="tier")

        assert set(_step_ids(result)) == {RIGHT_ID, JOIN_ID}
        assert _step(result, RIGHT_ID).output_values == {
            "key": "k1",
            "premium": 999,
            "tier": "gold",
        }
        assert _step(result, JOIN_ID).output_values["tier"] == "gold"


# ---------------------------------------------------------------------------
# Left join — matched and unmatched rows
# ---------------------------------------------------------------------------


class TestLeftJoin:
    def test_matched_row_traces_both_parents(self, tmp_path):
        left = pl.DataFrame({"key": ["a", "z"], "premium": [100, 5]})
        right = pl.DataFrame({"key": ["a"], "premium": [999], "tier": ["gold"]})
        graph = _edge_join_graph(
            tmp_path,
            left,
            right,
            {"how": "left", "on": "key", "maintainOrder": "left"},
        )

        result = execute_trace(graph, row_index=0, target_node_id=JOIN_ID)

        assert set(_step_ids(result)) == {LEFT_ID, RIGHT_ID, JOIN_ID}
        assert _step(result, JOIN_ID).output_values == {
            "key": "a",
            "premium": 100,
            "premium_right": 999,
            "tier": "gold",
        }
        assert _step(result, LEFT_ID).output_values == {"key": "a", "premium": 100}
        assert _step(result, RIGHT_ID).output_values == {
            "key": "a",
            "premium": 999,
            "tier": "gold",
        }

    def test_unmatched_row_omits_right_parent_instead_of_inventing_one(self, tmp_path):
        """A left-join miss has NO right lineage — never show a spurious row.

        The right parent's only row shares the left row's ``premium`` and
        a null ``tier`` (matching the join output's null), so value
        matching on left-provenance columns would lock onto it even
        though the join never matched it.
        """
        left = pl.DataFrame({"key": ["z"], "premium": [5]})
        right = pl.DataFrame(
            {
                "key": ["a"],
                "premium": [5],
                "tier": pl.Series("tier", [None], dtype=pl.Utf8),
            }
        )
        graph = _edge_join_graph(tmp_path, left, right, {"how": "left", "on": "key"})

        result = execute_trace(graph, row_index=0, target_node_id=JOIN_ID)

        assert RIGHT_ID not in _step_ids(result)
        assert _step(result, LEFT_ID).output_values == {"key": "z", "premium": 5}
        assert _step(result, JOIN_ID).output_values == {
            "key": "z",
            "premium": 5,
            "premium_right": None,
            "tier": None,
        }


# ---------------------------------------------------------------------------
# Full join — uncoalesced keys travel through the suffix
# ---------------------------------------------------------------------------


class TestFullJoin:
    def test_right_only_row_maps_suffixed_key_back_to_right_parent(self, tmp_path):
        left = pl.DataFrame({"key": ["a"], "left_only": ["L"]})
        right = pl.DataFrame({"key": ["a", "b"], "tier": ["gold", "silver"]})
        graph = _edge_join_graph(
            tmp_path,
            left,
            right,
            {"how": "full", "on": "key", "maintainOrder": "left_right"},
        )

        result = execute_trace(graph, row_index=1, target_node_id=JOIN_ID)

        # The left frame contributed nothing to this row.
        assert LEFT_ID not in _step_ids(result)
        assert _step(result, JOIN_ID).output_values == {
            "key": None,
            "left_only": None,
            "key_right": "b",
            "tier": "silver",
        }
        assert _step(result, RIGHT_ID).output_values == {"key": "b", "tier": "silver"}

    def test_matched_row_traces_both_parents(self, tmp_path):
        left = pl.DataFrame({"key": ["a"], "left_only": ["L"]})
        right = pl.DataFrame({"key": ["a", "b"], "tier": ["gold", "silver"]})
        graph = _edge_join_graph(
            tmp_path,
            left,
            right,
            {"how": "full", "on": "key", "maintainOrder": "left_right"},
        )

        result = execute_trace(graph, row_index=0, target_node_id=JOIN_ID)

        assert set(_step_ids(result)) == {LEFT_ID, RIGHT_ID, JOIN_ID}
        assert _step(result, LEFT_ID).output_values == {"key": "a", "left_only": "L"}
        assert _step(result, RIGHT_ID).output_values == {"key": "a", "tier": "gold"}


# ---------------------------------------------------------------------------
# Graph wiring — role resolution must not depend on edge order
# ---------------------------------------------------------------------------


class TestEdgeOrderIndependence:
    def test_role_resolution_independent_of_edge_order(self, tmp_path):
        left = pl.DataFrame({"policy": ["P1"], "premium": [100]})
        right = pl.DataFrame(
            {
                "ref": ["P1", "P2"],
                "premium": [999, 100],
                "tier": ["gold", "silver"],
            }
        )
        graph = _edge_join_graph(
            tmp_path,
            left,
            right,
            {"how": "inner", "leftOn": "policy", "rightOn": "ref"},
            reverse_edges=True,
        )

        result = execute_trace(graph, row_index=0, target_node_id=JOIN_ID)

        assert _step(result, LEFT_ID).output_values == {"policy": "P1", "premium": 100}
        assert _step(result, RIGHT_ID).output_values == {
            "ref": "P1",
            "premium": 999,
            "tier": "gold",
        }
