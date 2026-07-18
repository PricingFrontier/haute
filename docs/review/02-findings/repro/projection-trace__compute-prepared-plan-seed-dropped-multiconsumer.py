"""Adversarial repro for claim:
   compute-prepared-plan-seed-dropped-multiconsumer

CLAIM: In `compute_prepared_plan`'s reverse-topo sweep, when a node's demand
is opaque (needed[node] is None, because >1 child each pushed opaque/None
demand) AND the caller supplied a projection seed (required_columns_by_node)
for that node, then:
  - strict profiles RAISE ProjectionImpossibleError, but
  - NON-strict profiles take NEITHER branch of
        `if len(children) <= 1: ... elif strict_projection: ...`
    and fall through, leaving needed[node] = None.
The caller's seed is silently discarded (no error, no seed-specific
diagnostic). The recorded node reason stays the generic 'child_demand'.

This script ASSERTS on the specific wrong behaviour (seed value silently
dropped + no diagnostic), not merely that "something raised". It also pins
down the contrast cases (single-child seed IS applied; strict DOES raise) so
that the dropped-seed behaviour is unambiguously attributable to the
multi-consumer + non-strict + opaque combination.

ISOLATION: pure in-memory synthetic graph. No disk I/O, no project files.

Run:
  uv run python review/02-findings/repro/projection-trace__compute-prepared-plan-seed-dropped-multiconsumer.py
"""

from __future__ import annotations

from haute._execution_context import ExecutionProfile
from haute.errors import ProjectionImpossibleError
from haute.graph_utils import GraphNode, NodeData
from haute.projection import compute_prepared_plan, strict_projection_required

SEEDED_NODE = "P"
SEED_COLUMNS = {"x"}


def _polars_node(node_id: str, code: str = "") -> GraphNode:
    """Minimal polars transform node (opaque when terminal)."""
    return GraphNode(
        id=node_id,
        data=NodeData(label=node_id, nodeType="polars", config={"code": code}),
    )


def _build_inputs(child_ids: list[str]) -> tuple[list[str], dict, dict]:
    """Build (order, children_of, node_map) for P fed by the given children.

    `P` is the (root) parent. Each child is a terminal non-OUTPUT polars node,
    so the sweep sets needed[child] = None (opaque). With >=1 opaque child the
    parent's accumulated demand collapses to None as well.

    `order` is parent-first; compute_prepared_plan iterates reversed(order),
    so children are processed before the parent (a valid reverse-topo sweep).
    """
    node_map = {SEEDED_NODE: _polars_node(SEEDED_NODE)}
    for child_id in child_ids:
        node_map[child_id] = _polars_node(child_id)
    order = [SEEDED_NODE, *child_ids]
    children_of = {SEEDED_NODE: list(child_ids)}
    return order, children_of, node_map


def _seed_specific_diagnostic_present(plan, node_id: str) -> bool:
    """True iff ANY recorded reason for node_id signals the seed took effect
    or was explicitly dropped/ignored.

    We look across node_reasons + opaque_reasons for either the
    'projection_seed' rule (seed applied) or any message hinting the seed was
    discarded. Absence of all of these is the silent-failure the claim asserts.
    """
    candidates = []
    reason = plan.diagnostics.node_reasons.get(node_id)
    if reason is not None:
        candidates.append(reason)
    opaque = plan.diagnostics.opaque_reasons.get(node_id)
    if opaque is not None:
        candidates.append(opaque)

    for reason in candidates:
        if reason.rule == "projection_seed":
            return True
        blob = f"{reason.rule} {reason.message} {reason.details}".lower()
        if "seed" in blob or "drop" in blob or "discard" in blob or "ignore" in blob:
            return True
    return False


def main() -> None:
    profile = ExecutionProfile.PREVIEW_EAGER
    assert strict_projection_required(profile, None) is False, (
        "Precondition failed: PREVIEW_EAGER must be a non-strict profile for "
        "this repro to exercise the fall-through branch."
    )

    failures: list[str] = []

    # ------------------------------------------------------------------ #
    # Case A (the bug): two children -> opaque parent demand, non-strict,
    # caller supplies a seed for P. EXPECT: needed[P] stays None (seed
    # dropped) AND no seed-specific diagnostic is recorded.
    # ------------------------------------------------------------------ #
    order, children_of, node_map = _build_inputs(["C1", "C2"])
    plan_multi = compute_prepared_plan(
        order,
        children_of,
        node_map,
        required_columns_by_node={SEEDED_NODE: set(SEED_COLUMNS)},
        strict_projection=False,
    )
    multi_needed = plan_multi.needed_by_node.get(SEEDED_NODE)
    multi_reason = plan_multi.diagnostics.node_reasons.get(SEEDED_NODE)
    multi_reason_rule = multi_reason.rule if multi_reason is not None else None
    has_seed_diag = _seed_specific_diagnostic_present(plan_multi, SEEDED_NODE)

    print("=== Case A: 2 children (opaque), non-strict, seeded {'x'} ===")
    print(f"  needed_by_node[{SEEDED_NODE!r}]      = {multi_needed!r}")
    print(f"  node_reason.rule for {SEEDED_NODE!r}  = {multi_reason_rule!r}")
    print(f"  seed-specific diagnostic present? = {has_seed_diag}")
    print(f"  opaque_boundaries                 = {sorted(plan_multi.opaque_boundaries)}")

    # The seed was {'x'} but the planner left the node fully opaque (None).
    if multi_needed is not None:
        failures.append(
            f"[A] EXPECTED seed silently dropped (needed=None) but got "
            f"needed={multi_needed!r} -- seed appears to have applied."
        )
    # No seed-applied reason and no seed-dropped diagnostic == silent failure.
    if has_seed_diag:
        failures.append(
            "[A] EXPECTED no seed-specific diagnostic (silent drop) but a "
            "seed-related reason WAS recorded -- not silent after all."
        )
    # The recorded reason is the generic child_demand, masking the drop.
    if multi_reason_rule != "child_demand":
        failures.append(
            f"[A] EXPECTED recorded reason to remain generic 'child_demand' "
            f"(masking the dropped seed) but got {multi_reason_rule!r}."
        )

    # ------------------------------------------------------------------ #
    # Contrast B: single child -> opaque parent demand, non-strict, same
    # seed. EXPECT: seed IS applied (needed == {'x'}), reason 'projection_seed'.
    # This proves the seed machinery works and that Case A's drop is
    # specifically caused by the >1-children fall-through.
    # ------------------------------------------------------------------ #
    order1, children_of1, node_map1 = _build_inputs(["C1"])
    plan_single = compute_prepared_plan(
        order1,
        children_of1,
        node_map1,
        required_columns_by_node={SEEDED_NODE: set(SEED_COLUMNS)},
        strict_projection=False,
    )
    single_needed = plan_single.needed_by_node.get(SEEDED_NODE)
    single_reason = plan_single.diagnostics.node_reasons.get(SEEDED_NODE)
    single_rule = single_reason.rule if single_reason is not None else None

    print("=== Contrast B: 1 child (opaque), non-strict, seeded {'x'} ===")
    print(f"  needed_by_node[{SEEDED_NODE!r}]      = {single_needed!r}")
    print(f"  node_reason.rule for {SEEDED_NODE!r}  = {single_rule!r}")

    if single_needed != frozenset(SEED_COLUMNS):
        failures.append(
            f"[B] EXPECTED single-child seed to apply -> {set(SEED_COLUMNS)!r} "
            f"but got {single_needed!r}. Seed machinery not behaving as the "
            "claim assumes; Case A drop not attributable to >1-children."
        )
    if single_rule != "projection_seed":
        failures.append(
            f"[B] EXPECTED reason 'projection_seed' for applied seed but got "
            f"{single_rule!r}."
        )

    # ------------------------------------------------------------------ #
    # Contrast C: two children (opaque), STRICT. EXPECT: raises
    # ProjectionImpossibleError. Proves strict is the only path that
    # surfaces the impossible-seed condition, so non-strict (Case A) is a
    # genuine silent divergence rather than a uniformly-tolerated no-op.
    # ------------------------------------------------------------------ #
    order2, children_of2, node_map2 = _build_inputs(["C1", "C2"])
    strict_raised = False
    strict_exc_repr = None
    try:
        compute_prepared_plan(
            order2,
            children_of2,
            node_map2,
            required_columns_by_node={SEEDED_NODE: set(SEED_COLUMNS)},
            strict_projection=True,
        )
    except ProjectionImpossibleError as exc:
        strict_raised = True
        strict_exc_repr = repr(exc)

    print("=== Contrast C: 2 children (opaque), STRICT, seeded {'x'} ===")
    print(f"  raised ProjectionImpossibleError? = {strict_raised}")
    if strict_exc_repr is not None:
        print(f"  exc = {strict_exc_repr}")

    if not strict_raised:
        failures.append(
            "[C] EXPECTED strict profile to raise ProjectionImpossibleError "
            "for the multi-consumer opaque seed, but it did not. The strict "
            "vs non-strict divergence the claim relies on is not present."
        )

    # ------------------------------------------------------------------ #
    print()
    if failures:
        print("REPRO RESULT: NOT REPRODUCED (claim not substantiated)")
        for line in failures:
            print("  " + line)
        raise SystemExit(1)

    print("REPRO RESULT: REPRODUCED")
    print(
        "  In a non-strict profile, a caller-supplied projection seed for a "
        "node with >1 children that pushed opaque demand is SILENTLY DROPPED: "
        f"needed_by_node[{SEEDED_NODE!r}] stayed None instead of {set(SEED_COLUMNS)!r}, "
        "the recorded reason remained generic 'child_demand', and no "
        "seed-applied/seed-dropped diagnostic was emitted. The same seed "
        "applies cleanly with a single child, and strict mode raises "
        "ProjectionImpossibleError -- isolating the silent drop to the "
        "multi-consumer + non-strict + opaque fall-through."
    )


if __name__ == "__main__":
    main()
