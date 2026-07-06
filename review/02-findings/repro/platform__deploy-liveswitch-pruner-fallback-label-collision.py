"""Adversarial repro for claim:
deploy-liveswitch-pruner-fallback-label-collision

Claim: For a liveSwitch node whose config lacks ``input_scenario_map``, the
deploy pruner fallback (src/haute/deploy/_pruner.py:46-57) selects the live
branch by matching ``inputs[0]`` against ``_sanitize_func_name(source.label)``
WITHOUT any guard, so:

  (A) two distinct upstream labels that sanitize to the same identifier both
      "match"; whichever incoming edge is iterated first wins -> may select the
      wrong branch (order-dependent), and

  (B) if ``inputs[0]`` matches NO incoming edge (e.g. a renamed label), the
      switch silently keeps ZERO input edges (switch_live_source[sid] never set)
      and the node is pruned to a parentless source -- NO ValueError, unlike the
      mapped (input_scenario_map) path which raises at lines 65-69.

This script builds small in-memory graphs (no real project files touched),
optionally pins a tempdir project root for total isolation, and ASSERTS on the
specific wrong behaviour (which branch survives / whether a raise occurs).
"""

from __future__ import annotations

import tempfile
from pathlib import Path

# Total-isolation: pin an empty tempdir as the project root so nothing can
# accidentally reach into the real rating/ or src/ trees.  prune_for_deploy
# itself is a pure graph op and never touches disk, but we honour the rule.
import haute._sandbox as _sandbox

_TMP = tempfile.mkdtemp(prefix="haute_repro_pruner_")
_sandbox.set_project_root(Path(_TMP))

from haute._graph_utils import _sanitize_func_name
from haute._types import PipelineGraph
from haute.deploy._pruner import prune_for_deploy


def _node(nid: str, label: str, ntype: str = "polars", config: dict | None = None) -> dict:
    return {
        "id": nid,
        "position": {"x": 0.0, "y": 0.0},
        "data": {"label": label, "nodeType": ntype, "config": config or {}},
    }


def _edge(src: str, tgt: str) -> dict:
    return {"id": f"e_{src}_{tgt}", "source": src, "target": tgt}


def _graph(d: dict) -> PipelineGraph:
    return PipelineGraph.model_validate(d)


def _switch_parents(pruned: PipelineGraph, switch_id: str) -> set[str]:
    """Source ids that still feed *switch_id* after pruning."""
    return {e.source for e in pruned.edges if e.target == switch_id}


results: list[str] = []


# ---------------------------------------------------------------------------
# Sanity: confirm the sanitiser collision the claim relies on.
# The claim's literal example ('live source' vs 'live_source') does NOT collide
# because sanitize preserves casing and only maps space/hyphen -> underscore.
# But space-vs-hyphen DOES collide: 'Live Source' and 'Live-Source' -> 'Live_Source'.
# ---------------------------------------------------------------------------
s_space = _sanitize_func_name("Live Source")
s_hyphen = _sanitize_func_name("Live-Source")
results.append(f"sanitize('Live Source')  = {s_space!r}")
results.append(f"sanitize('Live-Source')  = {s_hyphen!r}")
assert s_space == s_hyphen == "Live_Source", (
    "precondition: space and hyphen labels must collide under sanitize"
)
# Document that the claim's *literal* example does not collide (casing kept):
results.append(
    f"NOTE claim's literal example: sanitize('live source')="
    f"{_sanitize_func_name('live source')!r} vs sanitize('live_source')="
    f"{_sanitize_func_name('live_source')!r} (these DIFFER -> claim example imprecise)"
)


# ===========================================================================
# SUB-CLAIM A: colliding sanitized labels -> first edge iterated wins,
# selection is order-dependent (may pick the wrong upstream branch).
# ===========================================================================
# Two source nodes whose labels both sanitize to 'Live_Source' == inputs[0].
# 'wrong_first' is wired FIRST in the edge list; 'live_real' second.  The
# fallback iterates `edges` in order and breaks on the first match, so the
# *first-listed* colliding edge wins regardless of which is the true live src.
def _build_collision_graph(first_src_id: str, second_src_id: str) -> PipelineGraph:
    return _graph(
        {
            "nodes": [
                _node("a", "Live Source", "dataSource"),      # sanitizes Live_Source
                _node("b", "Live-Source", "dataSource"),      # sanitizes Live_Source (collision)
                _node(
                    "switch",
                    "Switch",
                    "liveSwitch",
                    {"inputs": ["Live_Source", "Other"]},     # NO input_scenario_map
                ),
                _node("output", "Output", "output"),
            ],
            "edges": [
                _edge(first_src_id, "switch"),
                _edge(second_src_id, "switch"),
                _edge("switch", "output"),
            ],
        }
    )


# Order 1: 'a' first.
g1 = _build_collision_graph("a", "b")
pruned1, _k1, _r1 = prune_for_deploy(g1, "output")
surv1 = _switch_parents(pruned1, "switch")

# Order 2: edges swapped so 'b' is first.
g2 = _build_collision_graph("b", "a")
pruned2, _k2, _r2 = prune_for_deploy(g2, "output")
surv2 = _switch_parents(pruned2, "switch")

results.append(f"[A] edge order (a,b) -> surviving switch parent = {surv1}")
results.append(f"[A] edge order (b,a) -> surviving switch parent = {surv2}")

# The DEMONSTRABLE bug: which branch survives flips purely with edge order,
# and exactly one collides-but-wrong source is silently chosen.  Both orders
# keep exactly one parent, and that parent is whichever was listed first.
collision_order_dependent = (
    surv1 == {"a"} and surv2 == {"b"}
)
assert collision_order_dependent, (
    f"[A] expected order-dependent selection (a-first->{{a}}, b-first->{{b}}); "
    f"got surv1={surv1}, surv2={surv2}"
)
# And crucially: there is NO guard/raise for the ambiguous collision -- both
# calls returned normally.
results.append(
    "[A] CONFIRMED: colliding-label selection is order-dependent and silent "
    "(no ambiguity guard, no raise)."
)


# ===========================================================================
# SUB-CLAIM B: inputs[0] matches NO incoming edge -> switch keeps ZERO input
# edges, pruned to a parentless source, NO ValueError.  Contrast with the
# mapped path which RAISES on the identical no-match.
# ===========================================================================
def _build_nomatch_fallback_graph() -> PipelineGraph:
    # inputs[0]='renamed' but the only source label sanitizes to 'live_real'.
    return _graph(
        {
            "nodes": [
                _node("live_real", "live_real", "dataSource"),  # sanitizes live_real
                _node("batch_src", "batch_src", "dataSource"),  # sanitizes batch_src
                _node(
                    "switch",
                    "Switch",
                    "liveSwitch",
                    {"inputs": ["renamed", "batch_src"]},       # NO input_scenario_map; inputs[0] matches nothing
                ),
                _node("output", "Output", "output"),
            ],
            "edges": [
                _edge("live_real", "switch"),
                _edge("batch_src", "switch"),
                _edge("switch", "output"),
            ],
        }
    )


g_fallback = _build_nomatch_fallback_graph()
raised_fallback = False
try:
    pruned_fb, kept_fb, removed_fb = prune_for_deploy(g_fallback, "output")
except ValueError as exc:  # pragma: no cover - we expect NO raise
    raised_fallback = True
    results.append(f"[B] fallback unexpectedly raised: {exc}")

assert not raised_fallback, "[B] fallback path raised, contradicting the claim"

surv_fb = _switch_parents(pruned_fb, "switch")
results.append(f"[B] fallback no-match: surviving switch parents = {surv_fb}")
results.append(f"[B] fallback no-match: kept node ids = {sorted(kept_fb)}")

# The bug: switch keeps ZERO input edges (both upstreams pruned away).
assert surv_fb == set(), (
    f"[B] expected switch pruned to ZERO parents on no-match; got {surv_fb}"
)
# And the switch node itself is still kept (ancestor of output) but is now a
# source with no parents -> silent divergence from canvas/live intent, which
# at runtime would route to the first declared source (_builders.switch_fn dfs[0]).
assert "switch" in kept_fb, "[B] switch should remain in pruned graph"
results.append(
    "[B] CONFIRMED: fallback no-match silently drops ALL switch inputs "
    "(switch left parentless), NO ValueError."
)


# ---------------------------------------------------------------------------
# CONTRAST: identical no-match on the MAPPED (input_scenario_map) path RAISES.
# Same graph but the switch supplies input_scenario_map with a live key that
# matches no connected source -> ValueError per _pruner.py:65-69.
# ---------------------------------------------------------------------------
def _build_nomatch_mapped_graph() -> PipelineGraph:
    return _graph(
        {
            "nodes": [
                _node("live_real", "live_real", "dataSource"),
                _node("batch_src", "batch_src", "dataSource"),
                _node(
                    "switch",
                    "Switch",
                    "liveSwitch",
                    {
                        # live key 'renamed' matches no connected source label.
                        "input_scenario_map": {"renamed": "live", "batch_src": "batch"},
                        "inputs": ["renamed", "batch_src"],
                    },
                ),
                _node("output", "Output", "output"),
            ],
            "edges": [
                _edge("live_real", "switch"),
                _edge("batch_src", "switch"),
                _edge("switch", "output"),
            ],
        }
    )


g_mapped = _build_nomatch_mapped_graph()
raised_mapped = False
mapped_msg = ""
try:
    prune_for_deploy(g_mapped, "output")
except ValueError as exc:
    raised_mapped = True
    mapped_msg = str(exc)

results.append(f"[contrast] mapped no-match raised = {raised_mapped}; msg={mapped_msg!r}")
assert raised_mapped, (
    "[contrast] mapped path was expected to RAISE on no-match but did not"
)

# The asymmetry is the heart of the finding: same structural no-match,
# fallback stays silent, mapped raises loudly.
results.append(
    "ASYMMETRY CONFIRMED: identical no-match -> fallback silent (no raise), "
    "mapped path raises ValueError."
)


print("\n".join(results))
print("\nALL ASSERTIONS PASSED -> claim reproduced.")
