# Cleared — adversarially checked and found CORRECT. Do not "fix" these.

Each item below was explicitly investigated during this review (several with executed experiments)
and found to behave correctly. They are recorded so the implementing agent does not mistake them for
bugs, and because some are load-bearing context for the fix packages.

## Engine core
- **Polars null-join semantics** (reference behaviour for P04/FR-13): a Polars join does NOT match
  null keys (verified: full join of 2+2 null-keyed rows → 3 rows). The OUTPUT assembler's Polars side
  is correct; it is the Python nester that diverges.
- **`_execute_lazy` checkpointing rationale** (parquet round-trips at joins/fan-outs to break
  pola-rs/polars#24206 plan duplication) is sound; P10/FR-34 is only about the phantom third strategy.
- **The dataframe execution cache** (`_dataframe_execution_cache.py`): scan-refcount pinning,
  store-window eviction exemption, materialization-lock ordering (`materialize lock → _lock`
  everywhere), same-key write serialisation, and the single-unlink rule were traced and hold. The
  documented caller contract ("keep the source scan alive while derived frames may collect") is a real
  footgun but is documented and enforced by existing callers (`cache_backed_node_ids` retention in
  `_execute_lazy._release_consumed_parents`).
- **`_lru_cache.LRUCache`**: locking, TTL, pin/unpin, byte accounting (Python bigints — no overflow),
  and `evict_where` returning values so cascade callbacks run outside the lock are all correct.
- **`_topo.py`**: graphlib migration correct; duplicate edges counted symmetrically; self-loops
  detected; `_find_cycle_nodes` reports the cycle ∪ downstream union as intended.
- **Admission control** (`_execution_admission.py`): admission-by-rejection (no blocking) —
  structurally cannot deadlock or starve; release is idempotent with a `weakref.finalize` backstop.
  (P09/FR-32 is a narrow leak window during construction, not a design flaw.)
- **`_stat_gated_cache.py`**: double-checked per-key single-flight; stat-before/after torn-read guard
  with one retry then hard `RuntimeError` — textbook fail-loud. (P11/FR-46 note is cosmetic.)
- **Projection planner** (`projection.py`): the ordered/unordered AST demand walks, the rename
  namespace translation, select's execute-all-outputs semantics, and the "return None → full width →
  let execution raise the real error" policy were reviewed closely and are correct. The fan-in rules
  (declared `inputs_by_parent`, edge-join ownership routing via `narrow_join_parent_demand`,
  suffix-mapped duplicates) hold.

## Rating / scoring
- **Rating-key canonicalisation** (`normalise_rating_key` / `_rating_key_expr`): eager expression and
  Python mirror verified equal across `25.0`, `-0.0`, `2^63`, NaN, inf; Float32 widening and
  int64-range edges checked. The F084 dedup-before-canonicalise comment is accurate (mixed-entry
  columns genuinely collapse `25.0` → `'25'`).
- **Rating join shape**: entries deduplicated before the join; `maintain_order="left"` — lookups can
  neither fan out rows nor scramble order. Misses without defaults fail loud (that guard's COST is
  P07/FR-22; its correctness is fine).
- **Model scoring row alignment** is structural (predictions attached to the exact materialised
  frame); eager and batch prediction flattening agree (verified `ScoringModel.predict` flattens
  internally); categorical value-domain validation is applied consistently across all four scoring
  paths; multiclass proba and feature-order/dtype mismatches fail loud.
- **Edge-join node** (`_edge_join.py`): role/key validation thorough; single native join;
  `narrow_join_parent_demand` conservatively full-width on ambiguity. (One LOW note: `collect_eager`
  silently no-ops when either input is lazy — only act if a caller depends on it; none found.)

## Trace
- **Correlation ambiguity discipline**: `_find_matching_row` records `duplicate_exact_match` and
  returns nothing rather than guessing — correct; P03 extends that discipline to the entry point.
- **`_child_transform_may_reorder`** returns True (safe) for empty/config-driven code, and edge-join
  is config-driven — the no-shared-columns positional fallback is NOT reachable for a join child via a
  missed reorder token.
- **Trace memory bounds**: 8-entry byte-bounded LRU; frames stored by reference so preview reuse does
  not duplicate Arrow buffers.
- **Waterfall reconciliation** refuses to render when steps don't reconcile arithmetically —
  fail-loud as intended; enrichment errors annotate the step instead of poisoning the trace.

## Batch / shred
- **Chunking hot loop** (`chunking.py`): `prepare_graph`/`_build_funcs` hoisted out of the chunk loop;
  `_project_frame` caches its per-node projection to avoid O(nodes×chunks) `collect_schema`; streaming
  collect with one chunk in flight; AST whitelist fail-loud. Only the P11/FR-42 nits remain.
- **JSON-shred cache coherency**: atomic build/swap, per-parquet self-describing schema, conservation
  assertion, skip accounting — high quality. The always-verify-content-hash stance is deliberate and
  must survive P06/FR-17 (only the double-hash within one call is waste).
- **`_hashing.py`**: streamed xxh64, OS errors propagate. 64-bit collision bound is astronomically
  safe at current key cardinality.

## Infra
- **`_execution_context.py`** (aside from P01): stage/checkpoint instrumentation, once-per-threshold
  memory-pressure dedup (callback fired outside the lock), per-thread stage stacks — correct and
  thread-safe.
- **`_polars_utils.atomic_write`**: temp-then-rename with cleanup-on-error; unique artifact names
  (uuid) in the dfexec cache mean no replace-over-open-file on Windows.
- **`temporary_streaming_chunk_size`**: correctly serialises mutation of Polars' process-global config
  behind an RLock (inherent global-config limitation is documented).
