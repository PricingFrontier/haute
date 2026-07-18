"""Adversarial repro for claim `fan-in-passthrough-parent-misroute`.

CLAIM: PolarsFanInRule's passthrough-parent inference can route an uncovered
("missing") base_contribution column to a parent that does NOT actually produce
that column. Under a left-join collision where the column exists in BOTH parents
but is "owned by the right parent", the projection would demand it from the LEFT
parent (the inferred passthrough) instead of the true producer -> silent wrong
physical read.

This script builds the exact adversarial graphs and asserts on the *value* of
plan.edge_demands (which parent each missing column is demanded from), then
cross-checks against actual Polars left-join semantics to decide whether the
routing the rule produced is semantically WRONG or CORRECT.

Run:
    uv run python review/02-findings/repro/projection-trace__fan-in-passthrough-parent-misroute.py
"""

from __future__ import annotations

import polars as pl

from haute._execute_lazy import _compute_projection_plan
from haute._types import GraphNode, NodeData, NodeType


def _node(nid: str, node_type: NodeType, **config) -> GraphNode:
    return GraphNode(id=nid, data=NodeData(label=nid, nodeType=node_type, config=config))


def _source(nid: str) -> GraphNode:
    return _node(nid, NodeType.DATA_SOURCE)


def _build_children_of(order, parents_of):
    children = {nid: [] for nid in order}
    for child, parents in parents_of.items():
        for p in parents:
            if p in children:
                children[p].append(child)
    return children


def _plan(nodes, parents_of, required, target):
    node_map = {n.id: n for n in nodes}
    order = list(parents_of.keys())
    children_of = _build_children_of(order, parents_of)
    return _compute_projection_plan(
        order,
        children_of,
        node_map,
        required_columns_by_node={target: required},
    )


# ---------------------------------------------------------------------------
# Ground-truth Polars semantics: where does an un-suffixed collision column
# actually come from in a LEFT join?
# ---------------------------------------------------------------------------
def polars_left_join_owner_of_unsuffixed_collision() -> str:
    """Return 'left' or 'right' for who owns the un-suffixed column `c`."""
    left = pl.DataFrame({"k": [1, 2], "c": ["L1", "L2"]})
    right = pl.DataFrame({"k": [1, 2], "c": ["R1", "R2"]})
    out = left.join(right, on="k", how="left")
    # The un-suffixed column `c` in the output:
    vals = out.sort("k")["c"].to_list()
    if vals == ["L1", "L2"]:
        return "left"
    if vals == ["R1", "R2"]:
        return "right"
    raise AssertionError(f"unexpected join output for c: {vals}; cols={out.columns}")


# ---------------------------------------------------------------------------
# Scenario A — the claim's central case.
# `c` (= 'shared_col') is referenced un-suffixed, present in BOTH parents,
# under-declared (not in inputs_by_parent), so it is "missing".
# left.join(right, on='k', how='left') => simple_left_join_passthrough_parent
# returns the LEFT parent. Claim: this is WRONG if the true producer is RIGHT.
# ---------------------------------------------------------------------------
def scenario_a_left_join_collision():
    required = {"k", "shared_col", "out_col"}
    nodes = [
        _source("left"),
        _source("right"),
        _node(
            "fanin",
            NodeType.POLARS,
            code=(
                "df = left.join(right, on='k', how='left')\n"
                "df = df.with_columns(out_col=pl.col('shared_col') + 1)"
            ),
            contract={
                "inputs": ["k", "shared_col"],
                "outputs": ["out_col"],
                # shared_col deliberately NOT declared under either parent -> missing
                "inputs_by_parent": {
                    "left": ["k"],
                    "right": ["k"],
                },
            },
        ),
    ]
    parents_of = {"left": [], "right": [], "fanin": ["left", "right"]}
    plan = _plan(nodes, parents_of, required, "fanin")
    left_demand = plan.edge_demands[("left", "fanin")]
    right_demand = plan.edge_demands[("right", "fanin")]
    routed_to = "left" if "shared_col" in left_demand else (
        "right" if "shared_col" in right_demand else "neither"
    )
    return routed_to, left_demand, right_demand


# ---------------------------------------------------------------------------
# Scenario B — unambiguous_passthrough_parent path (NO join in code).
# No join => simple_left_join_passthrough_parent returns None. The fallback
# picks the sole parent whose declared cols are all <= referenced.
# We try to make `referenced` fail to disambiguate so the WRONG parent is the
# sole subset candidate.
# ---------------------------------------------------------------------------
def scenario_b_unambiguous_subset():
    # referenced (node inputs) = {k, extra}. Parent "alpha" declares {k} which is
    # <= referenced; parent "beta" declares {k, beta_only} which is NOT <= referenced
    # (beta_only not in inputs). So alpha is the sole subset candidate and the
    # missing column `extra` is routed to alpha.
    required = {"k", "extra"}
    nodes = [
        _source("alpha"),
        _source("beta"),
        _node(
            "fanin",
            NodeType.POLARS,
            # No `.join` in code -> no inferred joins.
            code="df = pl.concat([alpha, beta], how='vertical')",
            contract={
                "inputs": ["k", "extra"],
                "outputs": [],
                "inputs_by_parent": {
                    "alpha": ["k"],
                    "beta": ["k", "beta_only"],
                },
            },
        ),
    ]
    parents_of = {"alpha": [], "beta": [], "fanin": ["alpha", "beta"]}
    plan = _plan(nodes, parents_of, required, "fanin")
    alpha_demand = plan.edge_demands[("alpha", "fanin")]
    beta_demand = plan.edge_demands[("beta", "fanin")]
    routed_to = "alpha" if "extra" in alpha_demand else (
        "beta" if "extra" in beta_demand else "neither"
    )
    return routed_to, alpha_demand, beta_demand


def scenario_c_inner_join_misroute():
    """INNER join => simple_left_join_passthrough_parent returns None, so
    unambiguous_passthrough_parent decides. We disqualify the genuinely-correct
    LEFT parent as a subset-candidate by over-declaring it with `left_only`
    (a column NOT in the node `inputs`), leaving RIGHT as the sole candidate.
    The un-suffixed `shared_col` is owned by the LEFT operand of the join, but
    the rule routes it to RIGHT -> misroute.
    """
    required = {"k", "shared_col", "out_col"}
    nodes = [
        _source("left"),
        _source("right"),
        _node(
            "fanin",
            NodeType.POLARS,
            code=(
                "df = left.join(right, on='k', how='inner')\n"
                "df = df.with_columns(out_col=pl.col('shared_col') + 1)"
            ),
            contract={
                "inputs": ["k", "shared_col"],
                "outputs": ["out_col"],
                # left over-declares `left_only` (not in inputs) -> disqualified as
                # subset-of-referenced candidate; right becomes the SOLE candidate.
                "inputs_by_parent": {
                    "left": ["k", "left_only"],
                    "right": ["k"],
                },
            },
        ),
    ]
    parents_of = {"left": [], "right": [], "fanin": ["left", "right"]}
    plan = _plan(nodes, parents_of, required, "fanin")
    ld = plan.edge_demands[("left", "fanin")]
    rd = plan.edge_demands[("right", "fanin")]
    routed_to = "left" if "shared_col" in ld else ("right" if "shared_col" in rd else "neither")
    return routed_to, ld, rd


def simulate_projected_execution_inner(left_demand, right_demand):
    """Replay the projected scans through a real Polars inner join and report
    out_col, to show whether the misroute flips the physical value silently."""
    left_full = pl.DataFrame({"k": [1, 2], "shared_col": [111, 222], "left_only": [9, 9]})
    right_full = pl.DataFrame({"k": [1, 2], "shared_col": [777, 888]})
    correct = left_full.join(right_full, on="k", how="inner").with_columns(
        out_col=pl.col("shared_col") + 1
    )
    lp = left_full.select([c for c in left_full.columns if c in left_demand])
    rp = right_full.select([c for c in right_full.columns if c in right_demand])
    got = lp.join(rp, on="k", how="inner").with_columns(out_col=pl.col("shared_col") + 1)
    return correct.sort("k")["out_col"].to_list(), got.sort("k")["out_col"].to_list()


def main() -> int:
    owner = polars_left_join_owner_of_unsuffixed_collision()
    print(f"[ground-truth] un-suffixed collision column `c` in left-join is owned by: {owner}")

    routed_a, lhs_a, rhs_a = scenario_a_left_join_collision()
    print(f"[scenario A] shared_col routed to: {routed_a}")
    print(f"             left demand : {sorted(lhs_a)}")
    print(f"             right demand: {sorted(rhs_a)}")

    # The rule routes shared_col to LEFT. Per Polars, the un-suffixed shared_col
    # IS the left parent's column. So routing to LEFT is CORRECT, not wrong.
    claim_a_misroute = routed_a == "left" and owner == "right"
    print(f"[scenario A] claim predicts misroute (routed=left but true owner=right)? {claim_a_misroute}")

    routed_b, alpha_b, beta_b = scenario_b_unambiguous_subset()
    print(f"[scenario B] extra routed to: {routed_b} (alpha={sorted(alpha_b)}, beta={sorted(beta_b)})")

    routed_c, ld_c, rd_c = scenario_c_inner_join_misroute()
    print(f"[scenario C] shared_col routed to: {routed_c} (left={sorted(ld_c)}, right={sorted(rd_c)})")
    correct_out, got_out = simulate_projected_execution_inner(ld_c, rd_c)
    print(f"[scenario C] correct out_col (left-owned)   : {correct_out}")
    print(f"[scenario C] projected out_col (as planned) : {got_out}")

    # Scenario A established the simple left-join path routes CORRECTLY (refutes the
    # claim's *stated* repro_strategy verbatim). But scenario C demonstrates the
    # underlying invariant ('never silently route a column to the wrong parent')
    # IS violated for an INNER join via unambiguous_passthrough_parent: shared_col,
    # owned by the LEFT operand, is demanded from RIGHT, and replaying the projected
    # scans through Polars silently yields the RIGHT value with no error.
    assert routed_a == "left" and owner == "left", (
        "expected simple left-join to route un-suffixed collision col to LEFT (correct)"
    )
    if routed_c == "right" and correct_out != got_out:
        raise AssertionError(
            "REPRODUCED (inner-join misroute): shared_col is owned by the LEFT join "
            f"operand but was demanded from RIGHT only (left demand={sorted(ld_c)}); "
            f"replaying projected scans silently changes out_col from {correct_out} "
            f"(correct) to {got_out} (wrong) with NO error."
        )

    print("\nVERDICT: no misroute reproduced — inspect output above.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
