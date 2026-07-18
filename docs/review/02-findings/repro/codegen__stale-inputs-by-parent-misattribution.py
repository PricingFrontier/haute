"""Adversarial repro for: stale-inputs-by-parent-misattribution.

Two phases:

PHASE 1 — codegen reassignment (`_format_contract_source`):
    Demonstrate that the single-stale / single-unmatched branch
    (src/haute/codegen.py:150-160) re-attributes a stale parent's column
    set to an arbitrary *current* unmatched parent, in a MULTI-parent
    topology (so the resulting map can be consumed by fan-in narrowing).

PHASE 2 — projection silent column drop:
    Feed a misattributed `inputs_by_parent` (the kind PHASE 1 emits) into
    the real projection planner (`_compute_projection_plan`) on a 2-parent
    fan-in node.  The column `only_good` is genuinely produced by parent
    `Good` and referenced by the join, but the contract (mis)attributes it
    to `NewParent`.  We assert that projection routes the demand for
    `only_good` to `NewParent` (wrong) and SILENTLY DROPS it from `Good`
    (the true producer) — without any guard raising.

ISOLATION: pure in-memory; no disk I/O; no real project files touched.
"""

from __future__ import annotations

import sys

from haute._execute_lazy import _compute_projection_plan
from haute._types import GraphNode, NodeData, NodeType
from haute.codegen import _format_contract_source
from haute._contracts import Contract


def _node(nid: str, node_type: NodeType, **config) -> GraphNode:
    return GraphNode(id=nid, data=NodeData(label=nid, nodeType=node_type, config=config))


def _source(nid: str) -> GraphNode:
    return _node(nid, NodeType.DATA_SOURCE)


def _build_children_of(order, parents_of):
    children_of = {nid: [] for nid in order}
    for nid, pids in parents_of.items():
        for pid in pids:
            if pid in children_of:
                children_of[pid].append(nid)
    return children_of


# ---------------------------------------------------------------------------
# PHASE 1 — codegen reassignment misattributes a stale parent's columns.
# ---------------------------------------------------------------------------

def phase1_codegen_reassignment() -> str:
    # Contract carries:
    #   - a CORRECT key for a current parent (id "good_id")
    #   - a STALE key "old_parent" left behind after a UI rewire, owning
    #     column set {"only_good"}.
    # Current graph parents are {good_id -> "Good", p_new -> "NewParent"}.
    # The user actually rewired to a *genuinely different* upstream
    # (NewParent), which does NOT own "only_good".
    contract = Contract(
        inputs=frozenset({"shared", "good_col", "only_good"}),
        outputs=frozenset(),
        inputs_by_parent={
            "good_id": frozenset({"shared", "good_col"}),
            "old_parent": frozenset({"shared", "only_good"}),
        },
    )
    parent_name_by_id = {"good_id": "Good", "p_new": "NewParent"}

    emitted = _format_contract_source(contract, parent_name_by_id=parent_name_by_id)
    print("PHASE 1 emitted source:")
    print("   ", emitted)

    # The heuristic must have re-attributed the stale {shared, only_good}
    # to the single unmatched current parent NewParent.
    assert "'NewParent': ['only_good', 'shared']" in emitted, emitted
    assert "old_parent" not in emitted, emitted
    # And it kept the correctly-matched parent.
    assert "'Good': ['good_col', 'shared']" in emitted, emitted
    print("PHASE 1 OK: stale {shared, only_good} silently re-attributed to NewParent.\n")
    return emitted


# ---------------------------------------------------------------------------
# PHASE 2 — projection consumes the misattributed map -> silent column drop.
# ---------------------------------------------------------------------------

def phase2_projection_silent_drop() -> None:
    # Build the fan-in node carrying EXACTLY the misattributed map PHASE 1
    # emits (keyed by parent identity == node id in the graph world):
    #   Good      owns {shared, good_col}
    #   NewParent owns {shared, only_good}   <-- WRONG: only_good is Good's
    #
    # Ground truth in this rewired graph: "only_good" is produced by Good and
    # NewParent cannot produce it. A correct projection MUST demand only_good
    # from Good. The misattribution instead routes it to NewParent.
    misattributed_contract = {
        "inputs": ["shared", "good_col", "only_good"],
        "outputs": [],
        "inputs_by_parent": {
            "Good": ["shared", "good_col"],
            "NewParent": ["shared", "only_good"],
        },
    }

    join = _node(
        "join",
        NodeType.POLARS,
        code="df = Good.join(NewParent, on='shared', how='left')",
        contract=misattributed_contract,
    )
    nodes = [_source("Good"), _source("NewParent"), join]
    node_map = {n.id: n for n in nodes}
    order = ["Good", "NewParent", "join"]
    parents_of = {"Good": [], "NewParent": [], "join": ["Good", "NewParent"]}
    children_of = _build_children_of(order, parents_of)

    required = {"shared", "good_col", "only_good"}
    plan = _compute_projection_plan(
        order,
        children_of,
        node_map,
        required_columns_by_node={"join": required},
    )

    good_demand = plan.edge_demands[("Good", "join")]
    newparent_demand = plan.edge_demands[("NewParent", "join")]
    print("PHASE 2 projected edge demands:")
    print("    Good      ->", sorted(good_demand) if good_demand is not None else None)
    print("    NewParent ->", sorted(newparent_demand) if newparent_demand is not None else None)

    # THE BUG, stated as expected-vs-actual:
    #   EXPECTED (correct projection): Good is asked for only_good (its real column).
    #   ACTUAL  (misattribution):      Good is NOT asked for only_good, NewParent is.
    assert good_demand is not None and newparent_demand is not None

    good_drops_only_good = "only_good" not in good_demand
    newparent_wrongly_demands = "only_good" in newparent_demand

    print()
    print("    only_good dropped from Good (true producer)? ", good_drops_only_good)
    print("    only_good wrongly demanded from NewParent?   ", newparent_wrongly_demands)

    assert good_drops_only_good, (
        "Projection still demanded only_good from Good — misattribution did NOT "
        "cause a silent drop; claim's projection consequence is refuted."
    )
    assert newparent_wrongly_demands, (
        "Projection did not route only_good to NewParent — the misattribution "
        "was not consumed as the claim describes."
    )
    print("\nPHASE 2 OK: only_good silently pruned from Good and mis-demanded from NewParent.")
    print("No guard raised; parent-set equality guard passed because keys still match.")


def main() -> int:
    phase1_codegen_reassignment()
    phase2_projection_silent_drop()
    print("\nREPRODUCED: codegen reassignment -> projection silent column drop.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
