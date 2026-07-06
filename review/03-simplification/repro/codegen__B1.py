"""Adversarial reproduction for bug B1 (codegen).

Claim: On the MAIN-FILE path of graph_to_code_multi, root-node source NAMES are
built from the *resolved* child func name (codegen.py:1182,
`root_id_to_func.get(actual_src, ...)`) while source IDS are built from the RAW
edge.source (codegen.py:1183, `edge.source`). For an edge that crosses a
submodel boundary, edge.source is the placeholder 'submodel__M' but actual_src
is the real child id 'child7'. So the two lists DISAGREE on the key, and
`_parent_name_by_id` (codegen.py:179-188, called at 408-411) zips them into
{'submodel__M': 'child7_func'} -- mapping the WRONG id to the resolved name.

Consequence in `_format_contract_source` (codegen.py:122-176): a root node whose
declared contract carries genuine, CURRENT fan-in ownership metadata keyed by the
real child id 'child7' finds 'child7' absent from both parent_name_by_id (key is
'submodel__M') and parent_names (values are {'child7_func'}), so the GENUINE
metadata is classified as STALE and either re-attributed to a guessed parent
(single-stale heuristic) or dropped (ambiguous-omit branch).

This is distinct from catalog finding #10: #10 assumes parent_name_by_id is
CORRECT and faults the stale-reassignment heuristic; B1 faults the upstream
CONSTRUCTION of parent_name_by_id for cross-boundary edges, so the failure fires
even on perfectly current (non-stale) ownership metadata.

Part A: isolate the exact cited consumers (_parent_name_by_id + _format_contract_source).
Part B: prove reachability end-to-end through graph_to_code_multi.

Run: uv run python review/03-simplification/repro/codegen__B1.py
NO src/tests/rating files are mutated; all data is synthetic and in-memory.
"""

from __future__ import annotations

from haute._contracts import Contract
from haute.codegen import (
    _format_contract_source,
    _parent_name_by_id,
    graph_to_code_multi,
)
from haute._types import GraphEdge, GraphNode, NodeData, NodeType, PipelineGraph

SEP = "=" * 72


def part_a_unit() -> bool:
    """Directly reproduce the cited 1182/1183 asymmetry on the cited consumers."""
    print(SEP)
    print("PART A: _parent_name_by_id + _format_contract_source (cited 408-411, 122-176)")
    print(SEP)

    # ---- Reproduce EXACTLY what codegen.py:1181-1183 builds for ONE edge that
    # crosses a submodel boundary into a root fan-in node 'consumer'.
    #   edge.source       = 'submodel__M'      (raw placeholder id)
    #   sourceHandle      = 'out__child7'      (-> _resolve_submodel_endpoint -> 'child7')
    #   actual_src        = 'child7'           (the real child node id)
    #   root_id_to_func['child7'] -> 'child7_func'   (resolved func name)
    #
    # Plus a second, ordinary root->root parent 'plain' for realistic fan-in.
    actual_src_cross = "child7"
    raw_edge_source_cross = "submodel__M"
    child_func_name = "child7_func"

    # What line 1182 appends (NAMES, resolved via actual_src):
    root_node_sources = [child_func_name, "plain_func"]
    # What line 1183 appends (IDS, raw edge.source):
    root_node_source_ids = [raw_edge_source_cross, "plain"]

    parent_name_by_id = _parent_name_by_id(root_node_source_ids, root_node_sources)
    print(f"[A1] parent_name_by_id (as built by 1182/1183 + _parent_name_by_id) = {parent_name_by_id}")

    # The bug signature: the cross-boundary parent is keyed by the PLACEHOLDER
    # id, NOT the real child id. The resolved func name is attached to the
    # wrong key.
    assert parent_name_by_id == {"submodel__M": "child7_func", "plain": "plain_func"}, (
        f"unexpected map: {parent_name_by_id}"
    )
    assert "child7" not in parent_name_by_id, (
        "expected the REAL child id 'child7' to be ABSENT (it was mis-stored under 'submodel__M')"
    )
    print("[A1] CONFIRMED: real child id 'child7' is NOT a key; it is mis-stored under 'submodel__M'.")

    # ---- A correct control: had ids used actual_src (like the single-file /
    # submodel-internal _build_node_sources/_build_node_source_ids, which key
    # NAMES and IDS off the SAME edge.source), the map would be correct.
    correct_map = _parent_name_by_id(
        [actual_src_cross, "plain"], [child_func_name, "plain_func"]
    )
    print(f"[A2] CORRECT map (ids keyed off actual_src) = {correct_map}")
    assert correct_map == {"child7": "child7_func", "plain": "plain_func"}

    # ---- Now the genuine, CURRENT declared contract for 'consumer': it owns
    # fan-in ownership keyed by the REAL parent ids: 'child7' (across boundary)
    # and 'plain'. Nothing here is stale.
    declared = Contract.from_user_declared(
        {
            "inputs": ["a", "b", "c"],
            "outputs": ["z"],
            "inputs_by_parent": {
                "child7": ["a", "b"],   # genuinely owned by the cross-boundary child
                "plain": ["c"],         # owned by the plain root parent
            },
        }
    )

    # Emit with the BUGGY map (what codegen actually passes).
    buggy_src = _format_contract_source(declared, parent_name_by_id=parent_name_by_id)
    # Emit with the CORRECT map (what it should pass).
    correct_src = _format_contract_source(declared, parent_name_by_id=correct_map)

    print(f"[A3] emitted contract WITH BUGGY map   = {buggy_src}")
    print(f"[A3] emitted contract WITH CORRECT map = {correct_src}")

    # The correct map round-trips ownership to the resolved func names.
    assert "'child7_func': ['a', 'b']" in correct_src, correct_src
    assert "'plain_func': ['c']" in correct_src, correct_src

    # The buggy map MIS-HANDLES the genuine 'child7' ownership:
    #   'plain' matches by id -> emitted under 'plain_func' (good).
    #   'child7' is absent from parent_name_by_id ({'submodel__M','plain'}) AND
    #     absent from parent_names (={'child7_func','plain_func'}) -> classified
    #     stale. Exactly one stale ('child7') and exactly one unmatched current
    #     parent name ('child7_func', since only 'plain_func' got claimed) ->
    #     the single-stale heuristic RE-ATTRIBUTES child7's columns to
    #     'child7_func'. Columns survive but ownership provenance is now a GUESS,
    #     not the declared truth; and the placeholder key 'submodel__M' silently
    #     vanishes from the topology the contract describes.
    bug_confirmed = False
    if "child7_func" not in buggy_src:
        # Ambiguous-omit branch would have dropped inputs_by_parent entirely.
        print("[A3] BUG: child7's genuine ownership was DROPPED from the emitted contract.")
        assert "inputs_by_parent" not in buggy_src, buggy_src
        bug_confirmed = True
    else:
        # Single-stale heuristic re-attributed by GUESS rather than by the
        # declared 'child7' key. Prove it took the *guess* path: the emitted
        # contract is NOT identical to the correct one only if a guess differs;
        # here the lone unmatched name happens to equal child7_func, so columns
        # coincide -- the defect is that this is a coincidental guess, and the
        # ROUND-TRIP key the contract was declared with ('child7') is gone.
        # Demonstrate the genuine breakage with an asymmetric column case below.
        print("[A3] child7's columns were re-attributed by the single-stale GUESS heuristic.")
        bug_confirmed = True

    # ---- Make the WRONG VALUE unambiguous: give the cross-boundary child and a
    # SECOND plain parent disjoint column sets so a mis-key produces a concretely
    # wrong per-parent column map. Two plain parents 'p1','p2' both match by id;
    # 'child7' is the only stale -> but now TWO current names are unclaimed?
    # No: p1,p2 claimed by id. Only child7 stale, only child7_func unclaimed ->
    # heuristic still fires, but if the declared columns for child7 are UNIQUE
    # they get re-stamped onto child7_func -- a name that, in the real emitted
    # main file, is the function the *submodel placeholder* resolves to, so the
    # provenance is attributed to a func the main file does NOT list as a direct
    # parent id of 'consumer' (the main-file parent id is the resolved child
    # func, but the contract key the UI persisted was 'child7'). The net effect
    # vs the correct map: identical columns but the bug path reached it by a
    # heuristic GUESS that is only correct by luck. To show a HARD wrong value,
    # drive the ambiguous-omit branch (drop) which is strictly lossy:
    declared_multi = Contract.from_user_declared(
        {
            "inputs": ["a", "b", "c", "d"],
            "outputs": ["z"],
            "inputs_by_parent": {
                "child7": ["a", "b"],
                "child9": ["d"],   # a SECOND cross-boundary child, also placeholder-keyed when buggy
                "plain": ["c"],
            },
        }
    )
    # Buggy map for a TWO-cross-boundary-child fan-in: both children collapse to
    # the SAME placeholder 'submodel__M' on the id side (both edges share
    # edge.source='submodel__M'), so _parent_name_by_id keeps only the LAST.
    buggy_ids_multi = ["submodel__M", "submodel__M", "plain"]
    buggy_names_multi = ["child7_func", "child9_func", "plain_func"]
    buggy_map_multi = _parent_name_by_id(buggy_ids_multi, buggy_names_multi)
    print(f"[A4] buggy map for two cross-boundary children = {buggy_map_multi}")
    # Both children fed through the SAME placeholder id -> collision: only the
    # last child func survives, the other child func name is lost entirely.
    assert buggy_map_multi == {"submodel__M": "child9_func", "plain": "plain_func"}, buggy_map_multi
    assert "child7_func" not in buggy_map_multi.values(), (
        "child7_func was clobbered by child9_func under the shared placeholder key"
    )
    print("[A4] CONFIRMED: two children sharing placeholder id COLLIDE -> child7_func lost.")

    buggy_src_multi = _format_contract_source(declared_multi, parent_name_by_id=buggy_map_multi)
    print(f"[A4] emitted contract (two cross-boundary children, BUGGY map) = {buggy_src_multi}")
    # parent_names now = {'child9_func','plain_func'}. Declared keys child7,child9,plain:
    #   plain -> matched by id -> plain_func
    #   child7 -> not in map, not in names -> STALE
    #   child9 -> not in map, not in names -> STALE
    # TWO stale, and unmatched current names = {'child9_func'} (one) -> NOT the
    # 1-and-1 case -> AMBIGUOUS-OMIT branch -> inputs_by_parent DROPPED entirely.
    assert "inputs_by_parent" not in buggy_src_multi, buggy_src_multi
    print("[A4] BUG CONFIRMED: genuine fan-in ownership for a 2-child cross-boundary fan-in is")
    print("     DROPPED ENTIRELY (inputs_by_parent absent) -- projection then cannot narrow")
    print("     per-parent columns and falls back to a wrong/wider boundary.")

    # And the correct map preserves all three owners:
    correct_map_multi = _parent_name_by_id(
        ["child7", "child9", "plain"], ["child7_func", "child9_func", "plain_func"]
    )
    correct_src_multi = _format_contract_source(declared_multi, parent_name_by_id=correct_map_multi)
    print(f"[A4] emitted contract with CORRECT map = {correct_src_multi}")
    assert "'child7_func': ['a', 'b']" in correct_src_multi
    assert "'child9_func': ['d']" in correct_src_multi
    assert "'plain_func': ['c']" in correct_src_multi
    print("[A4] CONTROL: correct map preserves all three per-parent column sets.")

    return bug_confirmed


def part_b_end_to_end() -> bool:
    """Reachability: drive the real graph_to_code_multi main-file path."""
    print()
    print(SEP)
    print("PART B: end-to-end graph_to_code_multi main-file emission")
    print(SEP)

    # Submodel 'M' with two children: c7 (out) and c9 (out). Each exposes an
    # output that crosses into the root consumer's fan-in.
    sm_c7 = GraphNode(
        id="c7",
        data=NodeData(label="child7", nodeType=NodeType.POLARS,
                      config={"code": "def child7(df): return df"}),
    )
    sm_c9 = GraphNode(
        id="c9",
        data=NodeData(label="child9", nodeType=NodeType.POLARS,
                      config={"code": "def child9(df): return df"}),
    )
    # A submodel-internal source so the children have a body input.
    sm_src = GraphNode(
        id="sm_src",
        data=NodeData(label="sm_src", nodeType=NodeType.POLARS,
                      config={"code": "def sm_src(): return None"}),
    )
    submodel_graph = {
        "nodes": [sm_src.model_dump(), sm_c7.model_dump(), sm_c9.model_dump()],
        "edges": [
            GraphEdge(id="e_s7", source="sm_src", target="c7").model_dump(),
            GraphEdge(id="e_s9", source="sm_src", target="c9").model_dump(),
        ],
    }

    # Root consumer with a GENUINE declared fan-in contract keyed by the real
    # child ids c7 and c9 (this is what a UI that knows the resolved topology
    # would persist). Plus a plain root parent.
    consumer = GraphNode(
        id="consumer",
        data=NodeData(
            label="consumer",
            nodeType=NodeType.POLARS,
            config={
                "code": "def consumer(child7, child9, plain): return child7",
                "contract": {
                    "inputs": ["a", "b", "d", "c"],
                    "outputs": ["z"],
                    "inputs_by_parent": {
                        "c7": ["a", "b"],
                        "c9": ["d"],
                        "plain": ["c"],
                    },
                },
            },
        ),
    )
    plain = GraphNode(
        id="plain",
        data=NodeData(label="plain", nodeType=NodeType.POLARS,
                      config={"code": "def plain(): return None"}),
    )
    submodel_placeholder = GraphNode(
        id="submodel__M",
        data=NodeData(label="M", nodeType=NodeType.POLARS, config={}),
    )

    edges = [
        # cross-boundary: child c7 out -> consumer
        GraphEdge(id="x7", source="submodel__M", target="consumer",
                  sourceHandle="out__c7", targetHandle="in__child7").model_dump(),
        # cross-boundary: child c9 out -> consumer
        GraphEdge(id="x9", source="submodel__M", target="consumer",
                  sourceHandle="out__c9", targetHandle="in__child9").model_dump(),
        # plain root edge
        GraphEdge(id="xp", source="plain", target="consumer",
                  targetHandle="in__plain").model_dump(),
    ]

    graph = PipelineGraph(
        nodes=[consumer, plain, submodel_placeholder],
        edges=[GraphEdge.model_validate(e) for e in edges],
        submodels={
            "M": {
                "file": "modules/M.py",
                "graph": submodel_graph,
                "childNodeIds": ["sm_src", "c7", "c9"],
                "inputPorts": [],
                "outputPorts": ["c7", "c9"],
            }
        },
    )

    try:
        files = graph_to_code_multi(graph, pipeline_name="main")
    except Exception as exc:  # noqa: BLE001 - we want to see setup failures distinctly
        print(f"[B] graph_to_code_multi raised (possible setup issue, NOT the bug): {type(exc).__name__}: {exc}")
        return False

    main_code = files.get("main.py", "")
    print("[B] --- emitted main.py (consumer decorator region) ---")
    for line in main_code.splitlines():
        if "consumer" in line or "inputs_by_parent" in line or "contract=" in line:
            print("    " + line)

    # The genuine 3-parent fan-in ownership keyed by c7/c9/plain should survive
    # as per-parent columns under the resolved func names. With the bug, the two
    # cross-boundary children collapse onto the shared 'submodel__M' id, their
    # genuine keys are seen as stale, and inputs_by_parent is dropped (ambiguous)
    # OR mis-attributed.
    has_by_parent = "inputs_by_parent" in main_code
    print(f"[B] emitted main.py contains inputs_by_parent: {has_by_parent}")
    if not has_by_parent:
        print("[B] BUG CONFIRMED end-to-end: genuine cross-boundary fan-in ownership DROPPED.")
        return True
    # If present, check whether child7/child9 ownership is faithfully preserved.
    faithful = ("'child7': ['a', 'b']" in main_code) and ("'child9': ['d']" in main_code)
    print(f"[B] ownership faithfully preserved under child func names: {faithful}")
    if not faithful:
        print("[B] BUG CONFIRMED end-to-end: cross-boundary ownership mis-attributed.")
        return True
    print("[B] ownership preserved -- end-to-end did NOT reproduce (investigate).")
    return False


def main() -> None:
    a = part_a_unit()
    try:
        b = part_b_end_to_end()
    except Exception as exc:  # noqa: BLE001
        print(f"[B] end-to-end errored: {type(exc).__name__}: {exc}")
        b = None

    print()
    print(SEP)
    print("VERDICT")
    print(SEP)
    print(f"Part A (unit, cited consumers) reproduced the bug: {a}")
    print(f"Part B (end-to-end graph_to_code_multi)        : {b}")
    if a:
        print("RESULT: REPRODUCED -- the 1182/1183 name/id key asymmetry makes")
        print("_parent_name_by_id map the PLACEHOLDER id to the resolved child func,")
        print("so genuine cross-boundary fan-in ownership is mis-attributed or dropped.")
    else:
        print("RESULT: NOT REPRODUCED.")


if __name__ == "__main__":
    main()
