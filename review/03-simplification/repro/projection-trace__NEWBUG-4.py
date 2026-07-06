"""Isolated reproduction for NEWBUG-4.

Claim: detect_row_lineage_type returns 'filtered'/'expanded' from a row-count
delta for edge-join (NodeType.EDGE_JOIN) nodes whose `code` string lacks a
literal '.join(' token, mislabelling join fan-out/fan-in as a filter or
expansion. node_type "edgeJoin" is NOT special-cased in detect_row_lineage_type
(only dataSource/apiInput/liveSwitch are), so the function falls through to the
row-count heuristic.

This test touches NO real project files: it imports two pure functions from the
package under test and replicates ONLY the ~15-line sniff block from
_trace_enrichment.py:1781-1796 to feed detect_row_lineage_type the same way the
enrichment loop does.

Run: uv run python review/03-simplification/repro/projection-trace__NEWBUG-4.py
"""

from __future__ import annotations

from haute._codegen_builders import _gen_edge_join
from haute._trace_enrichment import detect_row_lineage_type
from haute._types import GraphNode


def sniff_operation_type(code: str) -> str:
    """Verbatim copy of the sniff block at _trace_enrichment.py:1781-1796."""
    operation_type = ""
    if code:
        code_lower = code.lower()
        if ".group_by(" in code_lower or ".groupby(" in code_lower:
            operation_type = "group_by"
        elif ".cross_join(" in code_lower:
            operation_type = "cross_join"
        elif ".join(" in code_lower:
            operation_type = "join"
        elif ".filter(" in code_lower:
            operation_type = "filter"
        elif ".sort(" in code_lower or ".sort_by(" in code_lower:
            operation_type = "sort"
        elif ".explode(" in code_lower:
            operation_type = "explode"
    return operation_type


def build_edge_join_node() -> GraphNode:
    """A realistic inner edge-join config (base joined to a lookup on 'key')."""
    return GraphNode.model_validate(
        {
            "id": "edgeJoin_1",
            "type": "edgeJoin",
            "position": {"x": 0, "y": 0},
            "data": {
                "label": "Join Rates",
                "nodeType": "edgeJoin",
                "config": {
                    "baseInput": "policies",
                    "joinInput": "rates",
                    "leftOn": "key",
                    "rightOn": "key",
                    "how": "inner",
                },
            },
        }
    )


def main() -> None:
    node = build_edge_join_node()

    # The real codegen builder emits the edge-join node body. This is what gets
    # round-tripped into config["code"] and read by the enrichment loop
    # (_trace_enrichment.py:1386 raw_code = cfg.get("code", ...)).
    emitted = _gen_edge_join(node, ["policies", "rates"])
    print("=== emitted edge-join code ===")
    print(emitted)

    # FACT 1: the emitted body performs the join via pipeline._apply_edge_join,
    # NOT a user dot-chain, so it contains no literal '.join(' token.
    assert ".join(" not in emitted.lower(), (
        "Premise broken: emitted edge-join code unexpectedly contains '.join(' — "
        "the sniff would then correctly classify it. Got:\n" + emitted
    )
    print("FACT 1 confirmed: emitted edge-join code has no literal '.join(' token")

    op = sniff_operation_type(emitted)
    print(f"sniffed operation_type = {op!r}  (expected '' -> falls to row-count heuristic)")
    assert op == "", f"Premise broken: sniff produced {op!r}, expected ''"

    # node_type for this step is NodeType.EDGE_JOIN -> the str value "edgeJoin".
    node_type = node.data.nodeType.value
    assert node_type == "edgeJoin", node_type

    # --- Scenario A: fan-in (inner join drops unmatched rows). 100 -> 40 ---
    result_fanin = detect_row_lineage_type(
        input_row_count=100,
        output_row_count=40,
        node_type=node_type,
        operation_type=op,
    )
    print(f"\nfan-in  (100 -> 40 rows): row_lineage_type = {result_fanin!r}")

    # --- Scenario B: fan-out (one-to-many join multiplies rows). 100 -> 250 ---
    result_fanout = detect_row_lineage_type(
        input_row_count=100,
        output_row_count=250,
        node_type=node_type,
        operation_type=op,
    )
    print(f"fan-out (100 -> 250 rows): row_lineage_type = {result_fanout!r}")

    # --- Control: if the SAME node had been sniffed as a join, it'd be 'joined' ---
    control = detect_row_lineage_type(
        input_row_count=100,
        output_row_count=40,
        node_type=node_type,
        operation_type="join",
    )
    print(f"control (op='join',     100 -> 40 ): row_lineage_type = {control!r}")

    # BUG ASSERTIONS: the actuary-facing badge mislabels the join.
    assert result_fanin == "filtered", (
        f"Expected the BUGGY label 'filtered' for an edge-join that drops rows, "
        f"got {result_fanin!r}. If this is 'joined', the bug is fixed/refuted."
    )
    assert result_fanout == "expanded", (
        f"Expected the BUGGY label 'expanded' for a one-to-many edge-join, "
        f"got {result_fanout!r}. If this is 'joined', the bug is fixed/refuted."
    )
    assert control == "joined", (
        f"Sanity: with operation_type='join' the function should say 'joined', "
        f"got {control!r}."
    )

    print(
        "\nBUG REPRODUCED: edge-join mislabelled as 'filtered' (fan-in) and "
        "'expanded' (fan-out) instead of 'joined'. node_type='edgeJoin' is not "
        "special-cased and the sniff cannot see a '.join(' token in "
        "pipeline._apply_edge_join(...)."
    )


if __name__ == "__main__":
    main()
