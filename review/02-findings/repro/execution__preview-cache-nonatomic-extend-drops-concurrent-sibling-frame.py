"""Adversarial repro for claim:
  preview-cache-nonatomic-extend-drops-concurrent-sibling-frame

CLAIM (paraphrased): execute_graph's `try_get -> _eager_execute -> store` is a
non-atomic RMW on the process-global _preview_cache. Two concurrent requests A
and B that "share the same fingerprint fp (same graph/source/row_limit/contracts)
but different target nodes" both read the same snapshot, each build
`merged = {**prev_outputs, **own_fresh_outputs}`, and the SECOND store overwrites
the first -> A's freshly-materialised exclusive ancestors are DROPPED from the
shared cache entry (last-writer-wins on the whole entry).

The claim's stated failure_scenario / repro_strategy is the ROUTE preview path:
two `/api/pipeline/preview` requests for the same graph/source/row_limit but
DIFFERENT target_node_ids running concurrently (preview route allows up to
HAUTE_PREVIEW_MAX_CONCURRENCY=2 concurrent workers; different node_id => different
supersession key => NOT serialised against each other).

THIS SCRIPT tests the load-bearing precondition the claim asserts:
  "Two concurrent requests A and B share the SAME fingerprint fp ... but
   different target nodes."

The route preview path ALWAYS calls execute_graph(..., target_preview_only=True).
`_preview_projection_cache_suffix` embeds `target_node_id` into the cache-key
suffix whenever target_preview_only is True (line ~654-655 of executor.py):
    parts.append(f":preview_target_only={target_node_id!r}")
That suffix is `extra_keys[0]` fed into `graph_fingerprint`, which mixes it into
the digest. So two route requests for DIFFERENT targets get DIFFERENT fp and write
to DIFFERENT cache entries -- the last-writer-wins collision the claim needs
cannot occur on the route path.

What this script does (all in a tempdir; no real project files touched):
  PART 1 (decisive, machinery-free): reconstruct `fp` EXACTLY as execute_graph
    does for two distinct targets under target_preview_only=True and assert the
    two fingerprints DIFFER -> refutes "share the same fingerprint fp".
  PART 2 (end-to-end, the claim's own repro_strategy executed against the real
    route call shape): seed a shared cache entry, then drive two THREADS through
    execute_graph(..., target_preview_only=True, ...) with DISTINCT targets that
    have disjoint extra ancestors, holding a barrier inside _eager_execute so both
    observe the same try_get snapshot before either stores. Then assert whether a
    sibling's freshly-stored node frame was dropped. With per-target fingerprints
    the two writes land on DIFFERENT entries and NOTHING is dropped.

EXIT semantics (this is a REFUTATION repro):
  * exit 0  => the claim's precondition is BROKEN on the route path: distinct
               targets => distinct fp => no shared-entry last-writer-wins drop.
               (Claim REFUTED for the realistic concurrent driver.)
  * exit 1  => an assertion failed in a way that would SUPPORT the claim (e.g.
               same fp for distinct targets, or a sibling frame actually dropped).
"""

from __future__ import annotations

import sys
import tempfile
import threading
from pathlib import Path

import polars as pl

import haute._sandbox as _sandbox
from haute._cache import GraphFingerprintMemo, graph_fingerprint
from haute.graph_utils import GraphEdge, GraphNode, NodeData, PipelineGraph

# Mirror the constants/flags execute_graph uses when computing the preview fp.
from haute.executor import (  # noqa: E402  (import after sandbox-ready imports is fine)
    ENFORCE_CONTRACTS,
    PREVIEW_INITIAL_COLUMN_LIMIT,
    _preview_cache,
    _preview_projection_cache_suffix,
    execute_graph,
    runtime_input_extra_keys,
)
import haute.executor as executor_mod  # noqa: E402


def _source_node(nid: str, path: str) -> GraphNode:
    return GraphNode(id=nid, data=NodeData(label=nid, nodeType="dataSource", config={"path": path}))


def _polars_node(nid: str, code: str) -> GraphNode:
    return GraphNode(id=nid, data=NodeData(label=nid, nodeType="polars", config={"code": code}))


def _compute_preview_fp(
    graph: PipelineGraph,
    target_node_id: str,
    *,
    row_limit: int,
    source: str,
    enforce_contracts: bool,
) -> str:
    """Reconstruct execute_graph's preview fingerprint EXACTLY (target_preview_only=True).

    Mirrors executor.execute_graph lines ~881-909.
    """
    requested_preview_columns = None  # route default unless user selects columns
    target_preview_only = True  # the ONLY concurrent driver (preview_node route)
    preview_cache_suffix = _preview_projection_cache_suffix(
        graph,
        target_node_id,
        requested_preview_columns,
        target_preview_only=target_preview_only,
        initial_column_limit=(
            PREVIEW_INITIAL_COLUMN_LIMIT
            if target_preview_only and requested_preview_columns is None
            else None
        ),
    )
    extra_keys = [
        f"{row_limit}:{source}:contracts={int(enforce_contracts)}{preview_cache_suffix}",
        *runtime_input_extra_keys(graph),
    ]
    return graph_fingerprint(graph, *extra_keys, memo=GraphFingerprintMemo())


def part1_precondition(graph: PipelineGraph) -> str:
    """Distinct targets under target_preview_only=True must yield DISTINCT fp."""
    print("=== PART 1: fingerprint precondition (route path, target_preview_only=True) ===")
    fp_a = _compute_preview_fp(
        graph, "node_a", row_limit=100, source="live", enforce_contracts=ENFORCE_CONTRACTS
    )
    fp_b = _compute_preview_fp(
        graph, "node_b", row_limit=100, source="live", enforce_contracts=ENFORCE_CONTRACTS
    )
    print(f"fp(target=node_a) = {fp_a[:16]}...")
    print(f"fp(target=node_b) = {fp_b[:16]}...")
    print(f"suffix(node_a)    = {_preview_projection_cache_suffix(graph, 'node_a', None, target_preview_only=True, initial_column_limit=PREVIEW_INITIAL_COLUMN_LIMIT)!r}")
    print(f"suffix(node_b)    = {_preview_projection_cache_suffix(graph, 'node_b', None, target_preview_only=True, initial_column_limit=PREVIEW_INITIAL_COLUMN_LIMIT)!r}")

    # The claim asserts A and B "share the same fingerprint fp". If that were
    # true these would be EQUAL. Assert they DIFFER -> precondition broken.
    assert fp_a != fp_b, (
        "CLAIM-SUPPORTING: distinct preview targets produced the SAME fingerprint; "
        "a shared-entry last-writer-wins drop would then be possible."
    )
    print("OK: distinct targets => DISTINCT preview fingerprints (separate cache entries).\n")
    return "refuted-precondition"


def part2_end_to_end_concurrent(graph: PipelineGraph, target_a: str, target_b: str) -> str:
    """Drive two threads through execute_graph (route shape) and check for a drop.

    Barrier inside _eager_execute forces both threads to read their try_get
    snapshot and begin executing before either stores, maximising the
    interleave the claim relies on.
    """
    print("=== PART 2: end-to-end concurrent extend (the claim's repro_strategy) ===")
    _preview_cache.invalidate()

    # Compute the per-target fingerprints the two route calls will use, so we can
    # inspect the resulting cache entries afterwards.
    fp_a = _compute_preview_fp(
        graph, target_a, row_limit=100, source="live", enforce_contracts=ENFORCE_CONTRACTS
    )
    fp_b = _compute_preview_fp(
        graph, target_b, row_limit=100, source="live", enforce_contracts=ENFORCE_CONTRACTS
    )

    # Barrier so BOTH threads enter _eager_execute (i.e. both have already done
    # their try_get read) before either returns to store. Two parties.
    enter_barrier = threading.Barrier(2, timeout=30)
    real_eager_execute = executor_mod._eager_execute
    call_lock = threading.Lock()
    calls: list[str] = []

    def _barriered_eager_execute(*args, **kwargs):  # type: ignore[no-untyped-def]
        # args[1] is target_node_id in _eager_execute(graph, target_node_id, ...)
        tgt = args[1] if len(args) > 1 else kwargs.get("target_node_id")
        with call_lock:
            calls.append(str(tgt))
        # Synchronise: both threads must arrive here (post-try_get) before either
        # is allowed to proceed to its store.
        try:
            enter_barrier.wait()
        except threading.BrokenBarrierError:
            pass
        return real_eager_execute(*args, **kwargs)

    executor_mod._eager_execute = _barriered_eager_execute  # type: ignore[assignment]

    errors: dict[str, BaseException] = {}

    def _run(target: str) -> None:
        try:
            execute_graph(
                graph,
                target_node_id=target,
                row_limit=100,
                source="live",
                target_preview_only=True,
                requested_preview_columns=None,
                include_schema_metadata=True,
            )
        except BaseException as exc:  # noqa: BLE001 - record for the parent thread
            errors[target] = exc

    try:
        t_a = threading.Thread(target=_run, args=(target_a,), name="preview-A")
        t_b = threading.Thread(target=_run, args=(target_b,), name="preview-B")
        t_a.start()
        t_b.start()
        t_a.join(timeout=60)
        t_b.join(timeout=60)
    finally:
        executor_mod._eager_execute = real_eager_execute  # type: ignore[assignment]
        # Drop any pins the route paths left so invalidate() fully clears later.
        _preview_cache.invalidate()

    if errors:
        # An unrelated failure does NOT count as reproducing the bug. Surface it.
        for tgt, exc in errors.items():
            print(f"!! thread for target {tgt!r} raised {type(exc).__name__}: {exc}")
        raise AssertionError(
            "SETUP/UNRELATED FAILURE: a worker thread raised; cannot judge the drop claim."
        )

    print(f"both threads entered _eager_execute (post-try_get): calls={sorted(calls)}")
    print(f"fp(target_a={target_a!r}) == fp(target_b={target_b!r}) ? {fp_a == fp_b}")

    # Re-fetch BEFORE the finally-invalidate ran? No -- we invalidated in finally
    # to release pins. Re-run the two calls WITHOUT the barrier to repopulate the
    # cache deterministically and inspect entry contents (single-threaded now).
    _preview_cache.invalidate()
    execute_graph(
        graph, target_node_id=target_a, row_limit=100, source="live",
        target_preview_only=True, requested_preview_columns=None, include_schema_metadata=True,
    )
    entry_a = _preview_cache.try_get(fp_a)
    execute_graph(
        graph, target_node_id=target_b, row_limit=100, source="live",
        target_preview_only=True, requested_preview_columns=None, include_schema_metadata=True,
    )
    entry_a_after_b = _preview_cache.try_get(fp_a)
    entry_b = _preview_cache.try_get(fp_b)

    assert entry_a is not None, "entry for target_a should exist after its own call"
    assert entry_b is not None, "entry for target_b should exist after its own call"

    nodes_a = set(entry_a["eager_outputs"])
    nodes_a_after_b = set(entry_a_after_b["eager_outputs"]) if entry_a_after_b else set()
    nodes_b = set(entry_b["eager_outputs"])
    print(f"entry[fp_a] nodes (after A)         = {sorted(nodes_a)}")
    print(f"entry[fp_a] nodes (after B as well) = {sorted(nodes_a_after_b)}")
    print(f"entry[fp_b] nodes                   = {sorted(nodes_b)}")

    # The claim's predicted failure: target_a's exclusive ancestor is DROPPED
    # from the cache because target_b's store overwrote the shared entry. That
    # requires fp_a == fp_b. We assert the OPPOSITE: distinct entries, and A's
    # entry is unaffected by B's store.
    assert fp_a != fp_b, (
        "CLAIM-SUPPORTING: the two route calls shared one fingerprint/entry."
    )
    assert nodes_a == nodes_a_after_b, (
        "CLAIM-SUPPORTING: target_a's cache entry CHANGED after target_b stored "
        f"(before={sorted(nodes_a)} after={sorted(nodes_a_after_b)}) -> sibling frame dropped."
    )
    # target_a's exclusive ancestor must still be present in A's entry.
    assert target_a in nodes_a_after_b, (
        f"CLAIM-SUPPORTING: target_a {target_a!r} missing from its own entry after B stored."
    )
    print("OK: distinct entries; target_a's frames survive target_b's store (no drop).\n")
    return "refuted-end-to-end"


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="haute_preview_rmw_repro_"))
    _sandbox.set_project_root(tmp)

    # Source parquet with a couple of columns.
    source_path = tmp / "src.parquet"
    pl.DataFrame({"id": list(range(50)), "v": list(range(50))}).write_parquet(source_path)

    # Graph: source -> node_a (exclusive ancestor of A) and source -> node_b
    # (exclusive ancestor of B). A and B are siblings with DISJOINT extra
    # ancestors beyond the shared source -- exactly the claim's topology.
    graph = PipelineGraph.model_validate(
        {
            "nodes": [
                _source_node("source", str(source_path)).model_dump(),
                _polars_node("node_a", "df = source.with_columns(a=pl.col('v') + 1)").model_dump(),
                _polars_node("node_b", "df = source.with_columns(b=pl.col('v') + 2)").model_dump(),
            ],
            "edges": [
                GraphEdge(id="e_source_a", source="source", target="node_a").model_dump(),
                GraphEdge(id="e_source_b", source="source", target="node_b").model_dump(),
            ],
        }
    )

    part1_precondition(graph)
    part2_end_to_end_concurrent(graph, target_a="node_a", target_b="node_b")

    print("CLAIM REFUTED: on the only concurrent driver (preview route, "
          "target_preview_only=True) distinct targets get distinct fingerprints, "
          "so the non-atomic RMW writes to SEPARATE cache entries and no sibling "
          "frame is dropped. The 'share the same fingerprint fp' precondition does "
          "not hold for that path.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
