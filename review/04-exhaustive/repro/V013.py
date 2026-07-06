"""V013 repro: the RATING_STEP column contract over-declares its produced
output for a table whose ``outputColumn`` is set but whose ``entries`` are
empty (an in-progress config). The projection planner then SUBTRACTS that
phantom output from upstream demand, pruning the real column away, while the
runtime (`_apply_rating_step_outputs` -> `_apply_rating_table`) skips the
empty table and never materialises the column -> the column is silently
neither carried upstream nor produced here.

ISOLATION: pure in-memory synthetic configs / graphs and a Polars frame.
No project root, no rating/, src/, tests/, or real project files touched.

The assertions pin the *wrong value*:
  (1) contract declares ``rate_factor`` produced even with empty entries;
  (2) projection prunes ``rate_factor`` from the source's output demand, so
      the source is NOT asked to carry it
      (needed["src"] == {"premium"}, NOT {"premium", "rate_factor"});
  (3) the runtime apply leaves a frame lacking ``rate_factor`` unchanged,
      so nothing creates it.
Together (2)+(3) mean a downstream consumer of ``rate_factor`` ends up with
a column that exists in neither the projected upstream nor this node's output.
"""

from __future__ import annotations

import polars as pl

from haute._builders import _rating_step_columns, get_column_contract
from haute._execute_lazy import _compute_needed_columns
from haute._rating import _apply_rating_step_outputs
from haute._rating_step_config import normalise_rating_tables
from haute._types import GraphNode, NodeData, NodeType


def _node(nid: str, node_type: NodeType, **config: object) -> GraphNode:
    return GraphNode(id=nid, data=NodeData(label=nid, nodeType=node_type, config=config))


# A rating-step config with ONE table: outputColumn is set, but entries are
# empty (a perfectly valid in-progress config — the analyst named the output
# and the join factor but has not pasted the lookup rows yet).
RATING_CONFIG: dict[str, object] = {
    "tables": [
        {
            "outputColumn": "rate_factor",
            "factors": ["age"],
            "entries": [],
        }
    ]
}


def main() -> None:
    # --- Precondition: normalise_rating_tables keeps the empty-entries table.
    tables = normalise_rating_tables(RATING_CONFIG)
    assert len(tables) == 1, f"expected 1 table to survive normalisation, got {len(tables)}"
    assert tables[0].get("entries") == [], (
        f"precondition: table entries must be empty, got {tables[0].get('entries')!r}"
    )

    # --- (1) The contract DECLARES rate_factor as produced from outputColumn
    #         presence alone, ignoring the empty entries.
    produced, referenced = _rating_step_columns(RATING_CONFIG)
    print("contract produced:", sorted(produced), "| referenced:", sorted(referenced))
    assert "rate_factor" in produced, (
        "BUG NOT REPRODUCED: contract did not declare rate_factor produced; "
        f"got produced={produced!r}"
    )
    # Sanity: the public dispatcher resolves to the same over-declared contract.
    disp_produced, _ = get_column_contract(NodeType.RATING_STEP, RATING_CONFIG)
    assert disp_produced == produced

    # --- (2) Projection: src -> rating -> output(needs rate_factor + premium).
    #         The phantom 'rate_factor' is subtracted from upstream demand, so
    #         the SOURCE is never asked to carry it.
    nodes = [
        _node("src", NodeType.DATA_SOURCE),
        _node("rating", NodeType.RATING_STEP, **RATING_CONFIG),
        _node("out", NodeType.OUTPUT, fields=["rate_factor", "premium"]),
    ]
    node_map = {n.id: n for n in nodes}
    order = ["src", "rating", "out"]
    children_of = {"src": ["rating"], "rating": ["out"], "out": []}

    needed = _compute_needed_columns(order, node_map=node_map, children_of=children_of)
    print("needed[out]   :", sorted(needed["out"]))
    print("needed[rating]:", sorted(needed["rating"]))
    print("needed[src]   :", sorted(needed["src"]))

    # The downstream OUTPUT genuinely demands rate_factor...
    assert needed["out"] == {"rate_factor", "premium"}, (
        f"precondition: output must demand rate_factor; got {needed['out']!r}"
    )
    # ...but projection PRUNES it from the source's required output set:
    #   needed[src] = (needed[out] - produced) | referenced
    #               = ({rate_factor, premium} - {rate_factor}) | {age}  (factor referenced)
    #               = {premium, age}
    # The factor 'age' is still demanded (good); the phantom 'rate_factor' is
    # gone (the bug) -- the source is never asked to carry the column the
    # runtime will fail to create.
    assert needed["src"] == {"premium", "age"}, (
        "expected source demand {'premium','age'} after pruning rate_factor, "
        f"got {needed['src']!r}"
    )
    assert "rate_factor" not in needed["src"], (
        "BUG NOT REPRODUCED: rate_factor was NOT pruned from upstream demand; "
        f"needed[src]={needed['src']!r}"
    )

    # --- (3) Runtime: with rate_factor absent from the input frame (because it
    #         was pruned upstream), the rating step leaves the frame unchanged
    #         for the empty table -> rate_factor is never created.
    upstream_frame = pl.LazyFrame({"age": [25, 40], "premium": [100.0, 200.0]})
    combined_outputs: list[dict[str, object]] = []
    result_cols = _apply_rating_step_outputs(
        upstream_frame, tables, combined_outputs
    ).collect().columns
    print("runtime output columns:", result_cols)
    assert "rate_factor" not in result_cols, (
        "BUG NOT REPRODUCED: the runtime unexpectedly produced rate_factor "
        f"for an empty-entries table; got columns={result_cols!r}"
    )

    # --- (4) What actually surfaces under the DEFAULT executor config?
    #         ENFORCE_CONTRACTS defaults True (executor.py:123). The SAME
    #         over-declared contract is also checked on the node's OWN output
    #         via _assert_outputs_satisfy_contract. Since the runtime output
    #         lacks rate_factor, the RATING node itself raises
    #         ContractMismatchError -- a loud, local failure -- rather than the
    #         claimed silent downstream "column not found at materialisation".
    from haute._execute_lazy import _assert_outputs_satisfy_contract, _effective_contract
    from haute.errors import ContractMismatchError

    rating_node = node_map["rating"]
    eff = _effective_contract(rating_node)
    print("effective contract outputs:", sorted(eff.outputs or set()))
    assert eff.outputs is not None and "rate_factor" in eff.outputs, (
        f"precondition: effective output contract must promise rate_factor; "
        f"got {eff.outputs!r}"
    )
    raised = False
    try:
        # The actual runtime output of the rating node is {age, premium}.
        _assert_outputs_satisfy_contract(
            rating_node, eff, frozenset({"age", "premium"})
        )
    except ContractMismatchError as exc:
        raised = True
        print("output-side check raised ContractMismatchError:", exc.context.get("missing"))
        assert exc.context.get("missing") == ["rate_factor"], (
            f"expected missing=['rate_factor'], got {exc.context.get('missing')!r}"
        )
    assert raised, (
        "EXPECTED the node's own output-side contract check to raise under the "
        "default ENFORCE_CONTRACTS=True, but it did not"
    )

    print(
        "\nV013 REPRODUCED (with caveat): the RATING_STEP contract over-declares "
        "'rate_factor' for an empty-entries table; projection prunes it from the "
        "source demand (needed[src] lost rate_factor); the runtime never creates "
        "it. UNDER THE DEFAULT ENFORCE_CONTRACTS=True the same over-declared "
        "contract is checked on the node's own output, so the RATING node raises "
        "ContractMismatchError (loud, local) -- NOT the claimed silent downstream "
        "'column not found at materialisation'. The contract/runtime mismatch is "
        "real; the predicted *symptom* is intercepted by the node's own check."
    )


if __name__ == "__main__":
    main()
