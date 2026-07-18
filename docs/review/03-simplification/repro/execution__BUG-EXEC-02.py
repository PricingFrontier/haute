"""ISOLATED reproduction / adjudication for BUG-EXEC-02.

Claim
-----
When the pipeline preamble fails to compile, ``_eager_execute``
(src/haute/executor.py:1293-1306) swallows the ``PreambleError``, sets
``preamble_ns = {}``, records ``preamble_error``, then injects that message
ONLY into nodes whose ``nodeType`` is in::

    src/haute/executor.py:1326-1331
        preamble_types = {NodeType.POLARS, NodeType.LIVE_SWITCH}
        for nid in result.order:
            nd = node_map.get(nid)
            if nd and nd.data.nodeType in preamble_types and nid not in errors:
                errors[nid] = preamble_error

``_build_rating_step`` (src/haute/_builders.py:818,831) and friends also bind
the preamble via ``extra_ns=_preamble``.  The claim: a ratingStep whose user
code references a preamble helper raises an opaque
``NameError: name '<helper>' is not defined`` while "an adjacent POLARS node
would correctly show the preamble error".

This script drives the REAL ``_eager_execute`` (the function that contains the
injection) on synthetic in-memory graphs.  It imports ``haute`` READ-ONLY and
constructs only ephemeral pydantic graph objects; it does NOT touch / modify
src/, tests/, rating/, or any real file, and does no disk I/O.

It checks the claim under BOTH readings, because the verdict hinges on the
``nid not in errors`` guard at executor.py:1330 (injection fires ONLY for a
node that did not already fail):

  SCENARIO A (claim's literal wording — POLARS *and* ratingStep both
              reference the broken helper):
      Both nodes fail with NameError during execution, so the guard skips the
      injection for BOTH.  The POLARS node does NOT "correctly show the
      preamble error"; it shows the same opaque NameError.  => asymmetry
      claimed by the bug is ABSENT here.

  SCENARIO B (the only configuration that yields the asymmetry — POLARS does
              NOT reference the helper, ratingStep DOES):
      POLARS succeeds, injection fires, POLARS shows the preamble error;
      ratingStep fails with NameError and is not in preamble_types, so it
      shows the opaque NameError.  => asymmetry present, but ONLY because the
      two nodes run *different* code (one uses the preamble, one does not).

Run:  uv run python review/03-simplification/repro/execution__BUG-EXEC-02.py
Exit 0 prints the adjudication for both scenarios and the final VERDICT line.
"""

from __future__ import annotations

from haute._types import GraphEdge, GraphNode, NodeData, NodeType, PipelineGraph
from haute.executor import _eager_execute

# Preamble that fails to COMPILE (missing module -> PreambleError) and that
# would (if it compiled) define helper `my_factor`.
BROKEN_PREAMBLE = (
    "import a_module_that_does_not_exist_zzz\n"
    "def my_factor():\n"
    "    return pl.lit(1.0)\n"
)

USES_HELPER = "df = df.with_columns(y=my_factor())"
NO_HELPER = "df = df.with_columns(y=pl.col('x') + 1)"


def _const_src() -> GraphNode:
    return GraphNode(
        id="src",
        data=NodeData(
            label="src",
            nodeType=NodeType.CONSTANT,
            config={"values": [{"name": "x", "value": "1"}]},
        ),
    )


def _polars(nid: str, code: str) -> GraphNode:
    return GraphNode(
        id=nid,
        data=NodeData(label=nid, nodeType=NodeType.POLARS, config={"code": code}),
    )


def _rating(nid: str, code: str) -> GraphNode:
    # _build_rating_step runs user code only when config['code'] is non-empty
    # (src/haute/_builders.py:830).
    return GraphNode(
        id=nid,
        data=NodeData(
            label=nid,
            nodeType=NodeType.RATING_STEP,
            config={"code": code, "tables": [], "combinedColumn": ""},
        ),
    )


def _run(nodes: list[GraphNode], edges: list[GraphEdge]) -> dict[str, str]:
    graph = PipelineGraph(nodes=nodes, edges=edges, preamble=BROKEN_PREAMBLE)
    _o, _order, errors, *_ = _eager_execute(graph, target_node_id=None, row_limit=None)
    return errors


def main() -> int:
    src = _const_src()

    # ---- SCENARIO A: both POLARS and ratingStep reference the helper. -------
    a_errors = _run(
        [src, _polars("poly", USES_HELPER), _rating("rat", USES_HELPER)],
        [
            GraphEdge(id="e1", source="src", target="poly"),
            GraphEdge(id="e2", source="src", target="rat"),
        ],
    )
    a_poly = a_errors.get("poly")
    a_rat = a_errors.get("rat")

    print("=== SCENARIO A: POLARS and ratingStep BOTH use the broken helper ===")
    print(f"  poly (POLARS)      error: {a_poly!r}")
    print(f"  rat  (RATING_STEP) error: {a_rat!r}")

    a_poly_is_nameerror = a_poly is not None and "my_factor" in a_poly
    a_poly_is_preamble = a_poly is not None and "a_module_that_does_not_exist_zzz" in a_poly
    a_symmetric = a_poly == a_rat
    print(f"  -> POLARS shows preamble error? {a_poly_is_preamble}")
    print(f"  -> POLARS shows opaque NameError? {a_poly_is_nameerror}")
    print(f"  -> both nodes identical (no asymmetry)? {a_symmetric}")

    # The claim says the adjacent POLARS node "would correctly show the
    # preamble error". Under Scenario A it does NOT: the `nid not in errors`
    # guard (executor.py:1330) skips injection because POLARS already failed.
    assert a_poly_is_nameerror and not a_poly_is_preamble, (
        "Scenario A expectation: when POLARS code also references the helper, "
        "POLARS fails with the SAME opaque NameError (injection skipped by the "
        f"`nid not in errors` guard). Got poly={a_poly!r}"
    )
    assert a_symmetric, (
        "Scenario A expectation: both nodes show the identical opaque error — "
        f"no asymmetry. poly={a_poly!r} rat={a_rat!r}"
    )

    # ---- SCENARIO B: POLARS does NOT use the helper; ratingStep DOES. -------
    b_errors = _run(
        [src, _polars("poly", NO_HELPER), _rating("rat", USES_HELPER)],
        [
            GraphEdge(id="e1", source="src", target="poly"),
            GraphEdge(id="e2", source="src", target="rat"),
        ],
    )
    b_poly = b_errors.get("poly")
    b_rat = b_errors.get("rat")

    print("\n=== SCENARIO B: POLARS preamble-free; ratingStep uses helper =====")
    print(f"  poly (POLARS)      error: {b_poly!r}")
    print(f"  rat  (RATING_STEP) error: {b_rat!r}")

    b_poly_is_preamble = b_poly is not None and "a_module_that_does_not_exist_zzz" in b_poly
    b_rat_is_nameerror = b_rat is not None and "my_factor" in b_rat
    print(f"  -> POLARS shows preamble error (injected)? {b_poly_is_preamble}")
    print(f"  -> ratingStep shows opaque NameError?      {b_rat_is_nameerror}")

    # This is the ONLY configuration that yields the asymmetry the claim
    # describes — and only because the two nodes run DIFFERENT code.
    assert b_poly_is_preamble, (
        "Scenario B expectation: a preamble-free POLARS node succeeds, so the "
        "injection fires and it shows the preamble error. Got "
        f"poly={b_poly!r}"
    )
    assert b_rat_is_nameerror, (
        "Scenario B expectation: the helper-using ratingStep fails with an "
        f"opaque NameError. Got rat={b_rat!r}"
    )

    # ---- Verdict ----
    print("\n--- ADJUDICATION ---")
    print(
        "The claim's stated failure ('a ratingStep referencing a preamble "
        "helper shows an opaque NameError WHEREAS an adjacent POLARS node would "
        "correctly show the preamble error') is REFUTED as written: the "
        "injection at executor.py:1330 is gated on `nid not in errors`, so a "
        "POLARS node that ALSO references the broken helper fails with the very "
        "same opaque NameError (Scenario A). The POLARS node only shows the "
        "clean preamble error when it does NOT use the preamble (Scenario B), "
        "in which case the asymmetry is an artifact of the two nodes running "
        "different code, not of the broken preamble being mis-attributed on a "
        "like-for-like node."
    )
    print(
        "RESIDUAL (real but narrower) defect: in Scenario B the ratingStep / "
        "scenarioExpander / dataSource / explore / externalFile builders are "
        "missing from preamble_types, so a preamble-USING node of those types "
        "that happens NOT to reference the failed symbol does not receive the "
        "'preamble is broken' annotation a POLARS node would — i.e. the "
        "injection SET is stale, but the user-facing 'opaque NameError instead "
        "of the real cause' framing only holds when the node genuinely "
        "references the missing helper, and in that case POLARS is equally "
        "opaque."
    )
    print("\nVERDICT: REFUTED (as written) — asymmetry requires different code per node.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
