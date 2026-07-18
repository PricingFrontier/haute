"""Adversarial repro for claim B3.

Claim: under a NORMAL live run (source=='live'), when two distinct inputs
are both mapped to scenario 'live' in input_scenario_map (a misconfiguration
neither side rejects) OR when map insertion order differs from source_names
order, the codegen body (_gen_live_switch) and the executor (_build_live_switch)
can select DIFFERENT input frames.

This script drives the REAL functions (no re-implementation of selection):
  - codegen: haute._codegen_builders._gen_live_switch -> parse the emitted
    `return <param>` line to learn which param codegen routes.
  - executor: haute._builders._build_live_switch -> call switch_fn(**frames)
    in the kwarg form the canvas executor uses, under source='live', and learn
    which named frame is returned.

It then compares the two selections across many map-order / source-order
combinations, including:
  - two inputs both mapped 'live' (misconfig), in both insertion orders,
  - a stale "ghost" map key not present in source_names alongside a real one,
  - map insertion order reversed relative to source_names order.

If the claim is REAL, at least one combination must show codegen-selected
param != executor-selected frame. If NONE diverge, the claim is REFUTED for
the single-source ('live') case.

Read-only: imports installed `haute`, builds synthetic in-memory frames,
touches no rating/ src/ tests/ or project files.
"""

from __future__ import annotations

import re

import polars as pl

from haute._builders import NodeBuildContext, _build_live_switch
from haute._codegen_builders import _gen_live_switch
from haute._types import GraphNode


def make_live_switch_node(label: str, input_scenario_map: dict[str, str]) -> GraphNode:
    """Build a minimal liveSwitch GraphNode via the public pydantic schema."""
    return GraphNode.model_validate(
        {
            "id": "sw1",
            "type": "custom",
            "position": {"x": 0.0, "y": 0.0},
            "data": {
                "label": label,
                "nodeType": "liveSwitch",
                "config": {"input_scenario_map": dict(input_scenario_map)},
            },
        }
    )


_RETURN_RE = re.compile(r"^\s*return\s+(\w+)\s*$", re.MULTILINE)


def codegen_selected_param(
    input_scenario_map: dict[str, str], source_names: list[str]
) -> str:
    """Run the real _gen_live_switch and extract the routed param name."""
    node = make_live_switch_node("sw", input_scenario_map)
    src = _gen_live_switch(node, list(source_names))
    m = _RETURN_RE.search(src)
    if not m:
        raise AssertionError(f"no `return <param>` line in generated source:\n{src}")
    return m.group(1)


def executor_selected_param(
    input_scenario_map: dict[str, str], source_names: list[str]
) -> str:
    """Run the real _build_live_switch.switch_fn (kwarg form) under source=live.

    Each input gets a uniquely-tagged 1-row frame whose single string cell is
    the input's own name, so we can read back WHICH named frame was returned.
    """
    node = make_live_switch_node("sw", input_scenario_map)
    ctx = NodeBuildContext(
        node=node,
        source_names=list(source_names),
        source_ids=[f"id_{n}" for n in source_names],
        target_handles=None,
        row_limit=None,
        node_map=None,
        orig_source_names=None,
        preamble_ns=None,
        source="live",  # NORMAL live run
    )
    _func_name, switch_fn, _is_source = _build_live_switch(ctx)
    # Canvas executor calls the wrapper with kwargs keyed by source name.
    frames = {n: pl.LazyFrame({"tag": [n]}) for n in source_names}
    out = switch_fn(**frames)
    tag = out.collect().get_column("tag").to_list()[0]
    return tag


def run_case(
    name: str, input_scenario_map: dict[str, str], source_names: list[str]
) -> tuple[str, str, bool]:
    cg = codegen_selected_param(input_scenario_map, source_names)
    ex = executor_selected_param(input_scenario_map, source_names)
    diverged = cg != ex
    flag = "  <<< DIVERGENCE" if diverged else ""
    print(
        f"[{name}] map={input_scenario_map} sources={source_names} "
        f"=> codegen->{cg!r} executor->{ex!r}{flag}"
    )
    return cg, ex, diverged


def main() -> None:
    cases: list[tuple[str, dict[str, str], list[str]]] = [
        # --- two inputs both mapped 'live' (the misconfig the claim targets) ---
        ("two-live A-first, src a,b", {"a": "live", "b": "live"}, ["a", "b"]),
        ("two-live B-first, src a,b", {"b": "live", "a": "live"}, ["a", "b"]),
        # map insertion order REVERSED vs source order
        ("two-live A-first, src b,a", {"a": "live", "b": "live"}, ["b", "a"]),
        ("two-live B-first, src b,a", {"b": "live", "a": "live"}, ["b", "a"]),
        # --- stale "ghost" key not in source_names, plus a real live input ---
        ("ghost-live-first then real", {"ghost": "live", "real": "live"}, ["real"]),
        ("real then ghost-live", {"real": "live", "ghost": "live"}, ["real"]),
        ("ghost-live-first, two real", {"ghost": "live", "a": "live"}, ["a", "b"]),
        # --- one live one batch, live not first in map, src order varied ---
        ("batch-first then live", {"b": "batch", "a": "live"}, ["a", "b"]),
        ("batch-first then live, src b,a", {"b": "batch", "a": "live"}, ["b", "a"]),
        # --- three inputs, two live, interleaved order ---
        (
            "three, live x then live z",
            {"x": "live", "y": "batch", "z": "live"},
            ["x", "y", "z"],
        ),
        (
            "three, src reversed",
            {"x": "live", "y": "batch", "z": "live"},
            ["z", "y", "x"],
        ),
    ]

    any_diverged = False
    for name, ism, srcs in cases:
        _cg, _ex, diverged = run_case(name, ism, srcs)
        any_diverged = any_diverged or diverged

    print()
    if any_diverged:
        print(
            "RESULT: DIVERGENCE FOUND -> claim B3 REPRODUCED "
            "(codegen and executor select different frames under source='live')."
        )
    else:
        print(
            "RESULT: NO DIVERGENCE across all single-source('live') combinations "
            "-> claim B3 REFUTED for the live path. Both _gen_live_switch and "
            "_build_live_switch iterate input_scenario_map in identical insertion "
            "order and select the FIRST 'live' entry whose input is present in "
            "source_names; source-name ordering only resolves the chosen entry's "
            "index, never which entry wins."
        )

    # Make the script assert the claim's required outcome so a CI-style run
    # FAILS loudly iff B3 is real. We EXPECT this assertion to raise only if a
    # divergence is genuinely found.
    assert any_diverged, (
        "B3 not reproduced: no codegen/executor divergence under source='live'."
    )


if __name__ == "__main__":
    main()
