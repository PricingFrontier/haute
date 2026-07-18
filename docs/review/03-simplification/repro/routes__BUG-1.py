"""Adversarial verification repro for BUG-1.

CLAIM: `preview_node`'s ``node_statuses`` comprehension (pipeline.py:608)
drops the ``nid in node_map`` guard that its 5 sibling comprehensions
(timings/memory/node_columns/node_available_columns/node_schema_warnings)
enforce. The claim says this is a latent consistency hazard: if ``relevant``
were ever seeded from a wider set than ``node_map`` (the falsy-``node_id``
``else`` branch at pipeline.py:586, ``relevant = set(results.keys())``),
``node_statuses`` would expose ids the sibling maps suppress.

This repro is ISOLATED: it uses only synthetic in-memory dicts that mirror
the two real, load-bearing invariants in the codebase. It touches no
project source, no rating/, no tests/, no disk I/O.

Two invariants are reproduced and checked:

  INV-A  Entry gate (src/haute/routes/pipeline.py:506-511): before the
         comprehensions run, ``preview_node`` raises 404 unless
         ``body.node_id in graph.node_map``. node_map keys are GraphNode.id
         strings (src/haute/_types.py:700 -> {n.id: n for n in nodes}). So
         the ``if body.node_id:`` test at line 574 is only ever falsy if an
         empty-string id ("") is a real node in node_map. For any genuine
         node the ``else`` branch is dead.

  INV-B  Executor result-key contract (src/haute/executor.py): ``results``
         is built ONLY by ``for nid in result_order`` (line 1165), and the
         body unconditionally indexes ``node_map[nid]`` (lines 1110/1148/
         1206). ``result_order`` is derived from the graph topo ``order``
         and ``ancestors(..., set(node_map))`` (lines 788-802 / 1082-1098).
         Therefore ``results.keys() ⊆ node_map.keys()`` ALWAYS — an id not
         in node_map would raise KeyError before execute_graph could return
         it. The claim's hypothesised "synthetic/aggregate ids not in
         node_map" cannot appear in ``results``.

If INV-B holds, then even in the dead ``else`` branch
(``relevant = set(results.keys())``) every nid is already in node_map, so
the missing guard on line 608 is REDUNDANT and node_statuses can never
diverge from the siblings. We assert exactly that.
"""

from __future__ import annotations


# ---- Synthetic mirror of the five sibling comprehensions + node_statuses.
# These mirror src/haute/routes/pipeline.py:588-621 EXACTLY in structure:
# siblings carry ``if nid in node_map and nid in relevant``; node_statuses
# carries only ``if nid in relevant``.


def build_maps(results: dict[str, str], node_map: dict[str, object], relevant: set[str]):
    node_statuses = {nid: r for nid, r in results.items() if nid in relevant}
    node_columns = {
        nid: r for nid, r in results.items() if nid in node_map and nid in relevant
    }
    return node_statuses, node_columns


def assert_keys_agree(node_statuses, node_columns, context: str) -> None:
    assert set(node_statuses) == set(node_columns), (
        f"DIVERGENCE in {context}: node_statuses keys {sorted(node_statuses)} "
        f"!= node_columns keys {sorted(node_columns)}"
    )


# ---------------------------------------------------------------------------
# Scenario 1 — reachable branch with a genuine node_id.
# INV-A forces body.node_id in node_map; relevant = ancestors(...) ⊆ node_map.
# This is the path the claim itself admits is safe. Confirm no divergence.
# ---------------------------------------------------------------------------
node_map_1 = {"src": object(), "transform": object(), "sink": object()}
# Executor returns results only for graph nodes (INV-B): subset of node_map.
results_1 = {"src": "ok", "transform": "ok", "sink": "ok"}
# relevant = ancestors(sink) pruned -> subset of node_map keys.
relevant_1 = {"src", "transform", "sink"}
ns1, nc1 = build_maps(results_1, node_map_1, relevant_1)
assert_keys_agree(ns1, nc1, "scenario-1 genuine node_id")


# ---------------------------------------------------------------------------
# Scenario 2 — the falsy-node_id ``else`` branch the claim targets
# (pipeline.py:586  relevant = set(results.keys())).
#
# The claim's failure REQUIRES results to contain an id absent from node_map
# ("synthetic/aggregate id"). We test whether the executor contract (INV-B)
# permits that. We model INV-B faithfully: results may only be keyed by ids
# the executor iterated, and the executor would KeyError on any nid not in
# node_map. So we construct results honoring INV-B and assert the maps still
# agree even though node_statuses lacks the explicit guard.
# ---------------------------------------------------------------------------
node_map_2 = {"src": object(), "transform": object(), "sink": object()}
# INV-B: results.keys() ⊆ node_map.keys(). Even partial coverage is a subset.
results_2 = {"src": "ok", "transform": "error"}  # sink not materialised
relevant_2 = set(results_2.keys())  # the ``else`` branch seeding
ns2, nc2 = build_maps(results_2, node_map_2, relevant_2)
assert_keys_agree(ns2, nc2, "scenario-2 else-branch under INV-B")


# ---------------------------------------------------------------------------
# Scenario 3 — VIOLATE INV-B deliberately to characterise the bug.
# Inject the hypothetical "synthetic id not in node_map" the claim needs.
# This is the ONLY way node_statuses diverges. We assert that it diverges,
# documenting that the guard would matter IF INV-B were ever broken — but
# INV-B is enforced by node_map[nid] indexing in execute_graph, so this
# state is unreachable in the real code path. This scenario is the
# counterfactual, not a real reproduction.
# ---------------------------------------------------------------------------
node_map_3 = {"src": object(), "sink": object()}
results_3 = {"src": "ok", "sink": "ok", "__aggregate__": "ok"}  # INV-B VIOLATED
relevant_3 = set(results_3.keys())
ns3, nc3 = build_maps(results_3, node_map_3, relevant_3)
# Under the violated invariant, node_statuses leaks "__aggregate__":
assert "__aggregate__" in ns3, "expected synthetic id to leak into node_statuses"
assert "__aggregate__" not in nc3, "siblings correctly suppress the synthetic id"
diverged = set(ns3) != set(nc3)
assert diverged, "counterfactual must diverge to characterise the guard's purpose"


print("RESULT: REFUTED — node_statuses cannot diverge under real invariants.")
print(" scenario-1 (genuine node_id, INV-A): keys agree =", set(ns1) == set(nc1))
print(" scenario-2 (else branch, INV-B honored): keys agree =", set(ns2) == set(nc2))
print(
    " scenario-3 (INV-B deliberately VIOLATED): keys diverge =",
    diverged,
    "(unreachable; execute_graph would KeyError on node_map['__aggregate__'])",
)
print()
print("CONCLUSION:")
print(" - The missing 'nid in node_map' guard on pipeline.py:608 is REDUNDANT.")
print(" - results.keys() is a subset of node_map.keys(), enforced by node_map[nid] in")
print("   execute_graph (executor.py:1110/1148/1206); a non-node id would")
print("   KeyError before the dict is returned, so it can never reach line 608.")
print(" - The falsy-node_id 'else' branch (line 586) is additionally dead for")
print("   any real node because the entry gate (line 507) requires node_id to")
print("   be a node_map key; only an empty-string node id could enter it.")
print(" - Divergence requires VIOLATING the executor contract (scenario 3),")
print("   which the code structurally prevents. No reachable failure exists.")
