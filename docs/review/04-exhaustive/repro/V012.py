"""Isolated reproduction for V012.

Claim: ``_banding_columns`` (the BANDING column contract) declares a factor's
``outputColumn`` as *produced* whenever both ``column`` and ``outputColumn`` are
non-empty -- it does NOT check ``rules``.  The runtime ``_apply_banding_factors``
SKIPS any factor whose ``rules`` are empty (``if not col or not out or not rules:
continue``).  So a banding factor with ``column`` + ``outputColumn`` set but an
empty/absent ``rules`` list is *declared produced* by the contract yet is *never
materialised* at runtime.

The projection demand-propagation subtracts produced columns from upstream
demand (``base_contribution = (my_needed - produced) | referenced``).  So if a
downstream node demands that ``outputColumn``, the demand is removed at the
banding node (contract claims ownership) but no column is created -> downstream
breaks with column-not-found in projected profiles.

This script proves the contradiction with concrete expected-vs-actual values:

  (A) The column contract claims ``produced == {"age_band"}`` for a rule-less
      factor.
  (B) Running the *actual executor application* (``_apply_banding_factors``) on
      a frame that has ``age`` does NOT create ``age_band``.
  (C) End-to-end through the real projection planner
      (``_compute_needed_columns``): a downstream OUTPUT that demands
      ``age_band`` causes the SOURCE demand to drop ``age_band`` -- the planner
      believes the banding node will create it, but (B) proves it will not.

No disk I/O, no real project files -- pure in-memory synthetic graph/frame.
"""

from __future__ import annotations

import polars as pl

from haute._builders import get_column_contract
from haute._execute_lazy import _compute_needed_columns
from haute._rating import _apply_banding_factors
from haute._types import GraphNode, NodeData, NodeType


def _node(nid: str, node_type: NodeType, **config) -> GraphNode:
    return GraphNode(id=nid, data=NodeData(label=nid, nodeType=node_type, config=config))


def _build_children_of(order, parents_of):
    children_of = {nid: [] for nid in order}
    for nid, pids in parents_of.items():
        for pid in pids:
            if pid in children_of:
                children_of[pid].append(nid)
    return children_of


def main() -> None:
    # A rule-less banding factor: column + outputColumn set, NO rules.
    ruleless_factor = {
        "banding": "continuous",
        "column": "age",
        "outputColumn": "age_band",
        # NOTE: no "rules" key (and an empty list behaves identically).
    }
    banding_config = {"factors": [ruleless_factor]}

    # ---- (A) Contract claims age_band is produced -------------------------
    produced, referenced = get_column_contract(NodeType.BANDING, banding_config)
    print(f"[A] contract produced={sorted(produced)} referenced={sorted(referenced)}")
    assert produced == {"age_band"}, (
        f"expected contract to (wrongly) claim produced={{'age_band'}}, got {produced}"
    )
    assert referenced == {"age"}, f"expected referenced={{'age'}}, got {referenced}"

    # ---- (B) Executor does NOT create age_band ----------------------------
    # This is the EXACT application function shared by the executor node
    # builder (_build_banding) and the generated-code entrypoint.
    src = pl.LazyFrame({"age": [10, 30, 70]})
    out_df = _apply_banding_factors(src, [ruleless_factor]).collect()
    print(f"[B] columns after _apply_banding_factors={out_df.columns}")
    assert "age_band" not in out_df.columns, (
        "executor unexpectedly created age_band -- bug would not reproduce; "
        f"got columns {out_df.columns}"
    )

    # Contrast: an *identical* factor that HAS a rule DOES create age_band,
    # proving the divergence is purely the missing rules-guard in the contract.
    factor_with_rule = dict(ruleless_factor)
    factor_with_rule["rules"] = [
        {"op1": ">=", "val1": 0, "op2": "<", "val2": 50, "assignment": "young"}
    ]
    out_df2 = _apply_banding_factors(src, [factor_with_rule]).collect()
    print(f"[B'] columns when factor has a rule={out_df2.columns}")
    assert "age_band" in out_df2.columns, (
        "sanity check failed: a factor WITH rules should create age_band"
    )

    # ---- (C) Projection strips downstream demand for age_band -------------
    # Graph:  source -> banding(ruleless) -> output(fields=[age_band])
    source = _node("source", NodeType.DATA_SOURCE)
    banding = _node("banding", NodeType.BANDING, factors=[ruleless_factor])
    output = _node("output", NodeType.OUTPUT, fields=["age_band"])
    node_map = {n.id: n for n in (source, banding, output)}
    order = ["source", "banding", "output"]
    parents_of = {"source": [], "banding": ["source"], "output": ["banding"]}
    children_of = _build_children_of(order, parents_of)

    needed = _compute_needed_columns(order, children_of, node_map)
    print(f"[C] needed by node = {{k: sorted(v) if v is not None else None for ...}}")
    for nid in order:
        v = needed[nid]
        print(f"    {nid}: {sorted(v) if v is not None else None}")

    # The OUTPUT genuinely needs age_band.
    assert needed["output"] == {"age_band"}, needed["output"]

    # THE BUG: the banding contract claims to PRODUCE age_band, so the
    # planner removes age_band from the SOURCE's demand and instead asks the
    # source for the *referenced* input column `age`.  The source is therefore
    # projected to carry `age` but NOT `age_band`.
    source_needed = needed["source"]
    assert source_needed == {"age"}, (
        f"expected planner to strip age_band and demand only age from source, "
        f"got {source_needed}"
    )
    assert "age_band" not in source_needed, (
        "if age_band survived in source demand the bug would not bite"
    )

    # Now combine (B) + (C): the planner believes age_band materialises at the
    # banding node (it removed the demand there), but the executor (B) proves it
    # NEVER creates age_band.  So in a projected run the source supplies only
    # `age`, the banding node passes the frame through unchanged (no age_band),
    # and the OUTPUT's `select(["age_band"])` hits column-not-found.
    #
    # Demonstrate that terminal failure concretely against the projected frame:
    projected_source = pl.LazyFrame({"age": [10, 30, 70]})  # only `age`, per [C]
    after_banding = _apply_banding_factors(projected_source, [ruleless_factor])
    raised = False
    try:
        # OUTPUT node does lf.select(fields) with fields=["age_band"].
        after_banding.select(["age_band"]).collect()
    except Exception as exc:  # noqa: BLE001 - we assert on the specific cause
        raised = True
        msg = str(exc)
        print(f"[C] downstream select raised {type(exc).__name__}: {msg.splitlines()[0]}")
        assert "age_band" in msg, f"expected age_band-not-found, got: {msg}"
    assert raised, (
        "expected downstream select(['age_band']) to FAIL because the projected "
        "frame lacks age_band, but it succeeded"
    )

    print()
    print("REPRODUCED: contract claims produced={'age_band'} (A) and the planner")
    print("strips age_band from source demand (C: source needs only {'age'}), yet")
    print("the executor never creates age_band (B) -> projected downstream")
    print("select(['age_band']) fails with column-not-found.")


if __name__ == "__main__":
    main()
