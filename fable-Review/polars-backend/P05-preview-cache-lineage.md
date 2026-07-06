# P05 — Preview cache: lineage-scoped keys (make the README's caching story true)

**Severity:** HIGH (architecture) · **Effort:** L · **Dev/reviewer pair: REQUIRED** · Do this package **after** the mechanical ones — it changes cache identity.

Files: `src/haute/executor.py` (primary), `src/haute/trace.py` (reconstructs the same key), `src/haute/_cache.py` (fingerprint helpers), tests across `tests/test_preview_*` / `tests/test_trace_*`.

## FR-15 [HIGH] — the preview cache key over-scopes to the whole graph and embeds the target

### Current behaviour (verified at 4fcaa8f0)
1. The GUI preview route **always** calls `execute_graph(..., target_preview_only=True)`
   (`routes/pipeline.py:539`; also `routes/output_assemble.py:114`). Only `cli/_run.py:55` and
   `_scaffold.py:1221` use the default full-materialisation mode.
2. With `target_preview_only=True`, `_preview_projection_cache_suffix` (`executor.py:645-675`) appends
   `:preview_target_only='<node_id>'` (plus `:initial_col_limit=…` / requested columns / port label)
   to the fingerprint extra keys, and `fp = graph_fingerprint(graph, *extra_keys)`
   (`executor.py:933-941`) hashes the **entire graph**.

### Consequences
- **Per-click miss:** clicking node A then node B produces different fingerprints → `try_get` misses →
  the full upstream chain re-executes for every node click (one lazy collect at the target, bounded by
  `row_limit`). The "partial hit — extend" path (`executor.py:996-1071`) can never fire across targets
  and is effectively dead in production.
- **Over-invalidation:** because the fp covers the whole graph, editing ANY node — including a node
  strictly *downstream* of the click — invalidates every preview entry. The README promises "change a
  rating factor and only the downstream nodes recalculate — everything upstream stays cached"; the
  engine does not do this today.
- The in-file comments still describe the pre-target-suffix design ("Cache the materialized DataFrames
  so clicking different nodes is instant", `executor.py:467-472`) — see P10/FR-38.

The lineage-scoping machinery already exists and is proven: `_upstream_subgraph`
(`_dataframe_execution_cache.py:215-232`) + `dataframe_execution_cache_key`'s documented contract
("Downstream edits therefore do not churn upstream cache entries").

### Fix design (recommended: option B)
**Option A (minimal, not recommended):** drop the target from the fp and resurrect the extend path.
Rejected: the byte-budget then has to hold *all* targets' frames in one entry, which is what
`target_preview_only` was introduced to avoid.

**Option B (recommended): scope the fingerprint to the target's upstream lineage.**
- In `execute_graph`, when `target_node_id` is set, compute the fp over the target's pruned upstream
  subgraph instead of the whole graph:
  - Reuse `prepare_graph(graph, target_node_id, source=source)` (already called downstream) or
    `_upstream_subgraph`; build a subgraph `PipelineGraph` of ancestors ∪ {target} with the
    live-switch-pruned edges for the active `source`.
  - `fp = graph_fingerprint(lineage_graph, *extra_keys)` with the SAME extra keys as today
    (row_limit, source, contracts flag, projection suffix, `runtime_input_extra_keys`).
  - `runtime_input_extra_keys(graph)` must also be computed on the **lineage graph**, not the whole
    graph — otherwise editing an unrelated file-backed source elsewhere still invalidates this entry
    (the helper already takes a graph argument; pass the subgraph).
- Behaviour after the change: editing node X invalidates exactly the entries whose lineage contains X.
  Clicking upstream node U after editing downstream node D is a **hit**. Clicking a different node is
  still a miss on first click (each target has its own entry) but its entry now survives unrelated
  edits — combined with the 8-entry LRU this gives real cross-click warmth.
- **Trace must move in lockstep.** `trace.py` reconstructs the exact preview key shape to reuse preview
  entries (the comment at `executor.py:924-932` documents the coupling, and both sides share
  `runtime_input_extra_keys`). Extract ONE shared helper — e.g. `preview_cache_fingerprint(graph,
  target_node_id, row_limit, source, enforce_contracts, target_preview_only, requested_preview_columns,
  initial_column_limit, port_label)` — in `executor.py`, and make `trace.py` call it instead of
  rebuilding the string by hand. This removes the drift risk the current comment merely warns about.
- **Keep** the target/columns/port suffix components — they are correct per-entry identity. The change
  is ONLY which graph the base fingerprint covers.
- The extend path (`executor.py:996-1071`): after lineage scoping it remains reachable only for
  same-fp partial entries (e.g. earlier transient failure). Keep it, but delete the cross-target
  aspiration from its comments; or simplify it in a follow-up once tests pin the surviving semantics.

### Correctness cautions (write tests for each)
1. **Two nodes, same lineage-set, different node id** must still produce different fps: the target id
   is part of `preview_cache_suffix` when `target_preview_only=True` — pin with a test (A→B chain:
   clicking A vs B differ).
2. **Live-switch scenarios:** the lineage subgraph must be built from `source`-pruned edges (use the
   same `prune_live_switch_edges` the executor uses), otherwise switching scenario would serve the
   wrong branch's cache. Test: graph with a live_switch, preview under source="live" vs the batch
   scenario → distinct fps.
3. **Preamble:** `graph_fingerprint` mixes in `preamble_execution_fingerprint`; the subgraph must
   carry `graph.preamble`, `graph.source_file`, `preserved_blocks`, `sources`, `active_source` — copy
   the construction pattern from `_upstream_subgraph` verbatim (it already carries all of these).
4. **Node-id sensitivity of `graph_fingerprint` vs node ORDER:** the lineage graph's node list must be
   deterministically ordered (sort by id, as `_upstream_subgraph` inherits from `graph.nodes` order —
   the base fingerprint is computed from the graph payload; ensure two requests building the same
   subgraph produce identical fps). Add a test constructing the subgraph twice from differently-ordered
   inputs.
5. **Eviction/pinning:** unchanged — entries are still pinned during serialisation and unpinned in the
   `finally` (`executor.py:1328-1333`).

### TDD plan (failing tests first)
1. **The headline behaviour:** build A→B→C; preview C (populates cache); edit C's config (or add node D
   downstream of C); preview B → assert `_eager_execute` is NOT called (spy/monkeypatch) — cache hit.
   Today: called (fails).
2. Preview C; edit B (upstream); preview C → assert `_eager_execute` IS called (invalidation still
   works).
3. Same-lineage different-target distinct entries (caution 1).
4. Live-switch scenario separation (caution 2).
5. Trace reuse: run preview then trace on the same target; assert trace reuses the preview entry
   (existing trace-reuse tests should keep passing — they are the guard rail for the shared-helper
   extraction).
6. Byte-budget behaviour unchanged: fill >8 targets, assert LRU eviction still bounded.

### Acceptance
- Tests 1–6 green; full preview/trace suites green.
- `trace.py` and `executor.py` share one key-construction helper (no duplicated suffix logic).
- Comments updated (P10/FR-38 items in `executor.py` get fixed as part of this package).
- README's caching paragraph is now literally true; if any gap remains, amend the README in the same
  commit — the product must not document behaviour the engine doesn't have.
