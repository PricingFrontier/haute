# Tracing — Low-Level Specification

## Module map

| File | Responsibility |
| --- | --- |
| `src/haute/trace.py` | Public facade and orchestrator. `execute_trace()` entry point, `PreviewReader` protocol, `TraceStep`/`TraceOmission`/`TraceResult` dataclasses, the trace execution cache (`_cache`, `TRACE_CACHE_MAX_BYTES`), row/omission assembly, column-relevance pruning, provenance, and JSON serialisation (`trace_result_to_dict`). Re-imports and re-exports expression-parser and node-type enricher names so `monkeypatch.setattr("haute.trace.<name>", ...)` in tests reaches the dispatch code in `_trace_enrichment.py`. |
| `src/haute/_trace_correlation.py` | Post-hoc row correlation and schema diff. Value predicates/coercion (`_trace_values_match`, `_jsonify_row`), `SchemaDiff` computation, dtype-robust Polars match-expression construction, exact/relaxed row matching with ambiguity diagnostics, edge-join provenance-aware parent-row projection, per-frame row matching (`_match_parent_row`, shared by the single-frame and multi-frame paths), multi-frame per-edge parent resolution (`_resolve_multi_frame_parent`), and the backward-walk driver `_correlate_rows_posthoc`. |
| `src/haute/_trace_enrichment.py` | Node-type enrichers (`enrich_rating_step`, `enrich_banding`, `enrich_model_score`, `enrich_scenario_expansion`, `enrich_live_switch`, `enrich_optimiser_apply`), row-lineage-type detection (`detect_row_lineage_type`, backed by `_node_output_row_count` for multi-frame-safe row counting), and the per-step dispatch walk (`enrich_steps`) that drives expression parsing/evaluation (with a pre-assignment-value guard for self-referential columns), intra-node chain analysis, recursive upstream input-source derivation, rename detection, and node-type dispatch for every `TraceStep`. |
| `src/haute/_trace_waterfall.py` | Waterfall assembly for sequential multiplicative/additive rating chains. `WaterfallEntry`/`WaterfallResult` dataclasses, the generic `build_waterfall()` API (hand-authored factor lists), the value-derived `build_waterfall_from_steps()` (traced-path driver), and the C8 arithmetic-reconciliation guards. |

## Key types and data structures

- **`PreviewReader`** (`trace.py`, `@runtime_checkable` `Protocol`) — anything
  exposing `try_get(fingerprint: str) -> dict[str, Any] | None`. `FingerprintCache`
  satisfies it by construction.
- **`TraceStep`** (`trace.py`, dataclass) — one node's contribution: `node_id`,
  `node_name`, `node_type`, `schema_diff: SchemaDiff`, `input_values` /
  `output_values` (column → value dicts), `topological_rank`,
  `column_relevant: bool` (default `True`), and enrichment fields populated by
  `_enrich_steps`: `expression`, `calculation`, `node_detail`,
  `row_lineage_type`.
- **`TraceOmission`** (`trace.py`, frozen dataclass) — one relevant graph node
  whose row could not be correlated: `node_id`, `node_name`, `node_type`,
  `topological_rank`, `reason`, and `diagnostic_index`. It never carries
  fabricated row or schema values.
- **`TraceResult`** (`trace.py`, dataclass) — the full per-row trace:
  `target_node_id`, `row_index`, `column`, `output_value`, `steps: list[TraceStep]`,
  `omissions: list[TraceOmission]`,
  `row_id_column`/`row_id_value` (from an `apiInput` node's `row_id_column`
  config, if set), summary counts (`total_nodes_in_pipeline`, `nodes_in_trace`,
  `execution_ms`), `waterfall` (list of entry dicts, a structured error dict, or
  `None`), and `correlation_diagnostics: list[dict[str, Any]]` (never `None`,
  defaults to an empty list). Provenance fields are UTC `generated_at`,
  `pipeline_source`, and `execution_origin` (`fresh_execution`,
  `preview_cache`, or `trace_cache`).
- **`SchemaDiff`** (`_trace_correlation.py`, dataclass) — `columns_added`,
  `columns_removed`, `columns_modified`, `columns_passed`, each a `list[str]`.
- **`WaterfallEntry`** / **`WaterfallResult`** (`_trace_waterfall.py`, dataclasses)
  — `label`, `operation` (`"base"` / `"multiply"` / `"add"`), `value`, `delta`,
  `cumulative`, and `default_used` per entry; `WaterfallResult` wraps `entries`
  plus `final_value`.
- **`WaterfallReconciliationError`** / **`WaterfallUnavailableError`**
  (`_trace_waterfall.py`, both `ValueError` subclasses) — the former means the
  arithmetic contradicts observed values (an invariant violation); the latter
  means the traced values structurally cannot support a truthful waterfall (e.g.
  two un-orderable joined branches both produce the column).
- **Tolerances** — `_TRACE_REL_TOL` / `_TRACE_ABS_TOL` (`_trace_correlation.py`,
  `1e-9` / `1e-12`) for float comparison against frontend-supplied JSON values;
  `_RECONCILE_REL_TOL` (`_trace_waterfall.py`, `1e-9`) for waterfall
  reconciliation.
- **Trace execution cache** (`trace.py`) — `_cache`, a module-level
  `FingerprintCache` with slots `("eager_outputs", "order", "parents_of",
  "node_map", "source_ids")`. `TRACE_CACHE_MAX_BYTES` defaults to
  `PREVIEW_CACHE_MAX_BYTES` (see [caching](../caching/high-level.md)), overridable
  via `HAUTE_TRACE_CACHE_MAX_BYTES`. Both values are parsed into module constants
  at import time by `executor._positive_int_from_env`; a malformed, zero, or
  negative value raises `RuntimeError` during import rather than using `_env.py`'s
  fail-soft request-knob policy. The cache is bounded by both entry count and
  retained bytes; sizing reuses `_estimate_preview_cache_entry_bytes` from
  `haute.executor`, and only the `eager_outputs` slot is treated as
  byte-size-sensitive. `eager_outputs` values are `pl.DataFrame | dict[str,
  pl.DataFrame]` — a multi-frame source (e.g. a ≥2-table `apiInput`) stores its
  per-table frames as a `dict[label, DataFrame]` rather than a single
  `DataFrame`, and every call site that indexes `eager_outputs` (correlation,
  enrichment, row-count/dtype lookups, the target-row-identity check) must
  branch on `isinstance(..., dict)` rather than assume a bare `DataFrame`.
- **`source_frames_of`** (`trace.py`, built in `execute_trace`; threaded through
  `_correlate_rows_posthoc` and `enrich_steps`) — `dict[tuple[source_id,
  target_id], list[str | None]]`, one `sourceHandle` entry per edge between that
  pair, in edge order. Lets both correlation and enrichment resolve which
  frame(s) of a multi-frame parent a given child edge actually consumes, mirroring
  the per-edge selection `_pick_source_frame` makes at execution time.
- **`_ROW_REORDERING_TOKENS`** (`_trace_correlation.py`) — a fixed tuple of code
  substrings (`.sort`, `.join(`, `.group_by(`, `.gather`, `.unique(`, `.pivot(`,
  etc.) used to conservatively classify whether a node's code can reorder rows.
- **`_OPERATION_TYPE_TABLE`** (`_trace_enrichment.py`) — an ordered
  `(substrings, label)` table for sniffing a node's row-lineage operation from
  its code string; `cross_join` is checked before `join` so a cross join is never
  mislabelled.

## Control flow

### `execute_trace()` (`trace.py`)

1. Validate `nodes` non-empty; resolve `target_node_id` (defaults to the last
   node in topological order, computed from the node list in *declared* order —
   not a set — so the tie-break is deterministic across process invocations,
   independent of CPython hash randomisation).
2. Compute `runtime_input_extra_keys(graph)` (execution-engine) and a
   `graph_fingerprint()` over the graph, target, `f"{row_limit}:{source}"`, and
   those extra keys, memoised via a `GraphFingerprintMemo` — the caller's, if one
   was passed in `fingerprint_memo` (the trace route reuses the memo it already
   built for its supersession key), otherwise a fresh one scoped to this call.
3. On a trace-cache hit (`_cache.try_get(fp)`), reuse the cached
   `eager_outputs`/`order`/`parents_of`/`node_map`/`source_ids`. On a miss, call
   `_materialize_eager_outputs()` (below) and store the result under `fp`.
4. If `eager_outputs[target_node_id]` is a `dict` (a multi-frame source targeted
   directly, with no downstream node to pick a frame), raise `ValueError` naming
   the node and directing the caller to trace a node downstream of a specific
   frame instead.
5. Build `source_frames_of`: for every edge in the graph, append its
   `sourceHandle` to the list keyed by `(edge.source, edge.target)`, preserving
   edge order. One entry per edge, not per pair — a multi-frame source can feed
   the same child through several edges.
6. If the caller supplied `row_values`, verify the target DataFrame's row at
   `row_index` matches them (`_trace_values_match` per column). On mismatch,
   search the target DataFrame for a row that does match
   (`_find_target_row_index`) and relocate `row_index` to it; if none matches,
   raise `ValueError`. `_find_target_row_index` itself raises `ValueError` if the
   clicked values match *more than one* row (ambiguous relocation — mirrors the
   correlation-layer policy of failing loud over guessing at the first match).
7. If the target node produced output, call `_correlate_rows_posthoc()` (below,
   passed `source_frames_of` and `column` as `traced_column`) to get a JSON-safe
   row dict per node (or `None` for unresolved nodes). If the target node's
   execution failed, build partial rows directly from whatever `eager_outputs`
   are available instead (no correlation), skipping any node whose output is a
   multi-frame `dict`.
8. `_assemble_steps()` builds `TraceStep`s from the correlated rows: source nodes
   get `input_values = {}`; other nodes' `input_values` merge each parent's
   correlated row (namespacing every parent's copy as `f"{pid}.{k}"` whenever
   more than one parent supplies the key). Nodes whose row correlation returned
   `None` are skipped entirely.
9. `_enrich_steps()` (from `_trace_enrichment.py`, passed `source_frames_of`)
   enriches every step in place.
10. If `column` is set, `_prune_to_column_relevance()` tags and filters steps.
11. `_build_trace_omissions()` turns attempted, unresolved correlations on the
    retained value path into diagnostic-linked `TraceOmission` entries; benign
    graph/column pruning remains absent.
12. Resolve `output_value` from the target row (whole row dict if `column` is
    `None`, else the single value). Resolve `row_id_column`/`row_id_value` by
    scanning `nodes` for an `apiInput` node with a `row_id_column` config entry.
13. If `column` is set, compute `integer_output_node_ids` (which steps' output
    column is an integer dtype, via `_is_integer_output_column`; a multi-frame
    `dict` output resolves to `False` rather than raising on the missing
    `.schema` attribute) and call `build_waterfall_from_steps()`.
14. Return the assembled `TraceResult`, logging a single `trace_executed` info
    event with cache-hit status and duration.

### `_materialize_eager_outputs()` (`trace.py`)

1. Normalise the `preview` argument via `_resolve_preview_snapshot()`: `None` →
   no reuse; a reader → consult the exact full-lineage preview fingerprint; a
   raw dict → used verbatim. Trace does not attempt the target-only projected
   key because that cache shape cannot contain the required ancestor evidence.
2. If the preview data has a materialized output for `target_node_id` **and**
   every node in the trace's topological order (built via `_prepare_graph`) has a
   non-`None` output in that preview snapshot, reuse those DataFrames directly —
   no re-execution. A *partial* preview cache (e.g. a target-only projected
   preview) intentionally falls through to a cold execution instead of trace-ing
   with holes.
3. Otherwise, compile the preamble, merge in any caller-supplied `preamble_ns`
   (caller-supplied keys win, for test convenience), and call
   `_execute_eager_core()` (execution-engine) with `swallow_errors=False` — a
   genuine execution failure propagates unmodified rather than being retried or
   masked.

### `_match_parent_row()` (`_trace_correlation.py`)

Correlates ONE candidate frame (a bare parent `DataFrame`, or one frame of a
multi-frame parent) against the resolved child row. Factored out of
`_correlate_rows_posthoc` so the single-frame and multi-frame paths share
identical matching logic:

1. Project the child's row onto the parent's columns (`_build_parent_match_row`)
   — a generic name-based projection, except for the JOIN-role parent of an
   `edgeJoin` child, which is routed through `_edge_join_right_match_row` (see
   Edge cases below).
2. **Fast path**: if the parent and child DataFrames have equal row counts and
   the child's row index is in range, try the parent row at that same
   positional index. Trust it only if (a) there are no shared columns and
   either the parent has exactly one row or the child transform provably
   cannot reorder (`_child_transform_may_reorder` returns `False`); or (b)
   there are shared columns, they all value-match, and either the child cannot
   reorder or the shared key uniquely identifies one row in the parent
   (`_shared_key_is_unique`).
3. **Value-matching fallback**: `_find_matching_row()` tries an exact match on
   all shared columns first; on no exact match (and `allow_relaxed=True`), it
   scores every row by how many shared columns match and picks the widest
   relaxed subset — but only if exactly one row achieves that width. Any tie
   (multiple exact or multiple best-relaxed matches) is recorded via
   `_record_ambiguous_row_match` / `_record_relaxed_candidate_ambiguity` into
   `diagnostics` and returns `(None, -1)`. `allow_relaxed` is forced to `False`
   for an edge-join's JOIN-role parent (`_allows_relaxed_parent_match`) — a
   relaxed miss there must not manufacture false lineage.
4. Returns `(row_dict, positional_index, match_width)`, where `match_width` is
   the number of child columns projected onto this frame — the specificity a
   multi-frame resolution uses to rank competing candidate frames.

### `_resolve_multi_frame_parent()` (`_trace_correlation.py`)

Correlates a multi-frame parent (`dict[label, DataFrame]`) row for one child.
Called once per (parent, child) pair from `_correlate_rows_posthoc`, with
`handles` set to `source_frames_of[(parent_id, child_id)]` — one `sourceHandle`
per edge between that pair, in edge order:

1. Deduplicate `handles` into an ordered set of non-`None` handles, then keep
   only the frames that exist in the parent's `dict` and are non-empty as
   `candidates`. No candidates → record an `unresolved_source_frame` diagnostic
   and return `(None, -1)`.
2. Exactly one candidate → delegate straight to `_match_parent_row` on that
   frame (this is the common case: a single edge, or several edges all naming
   the same frame).
3. Several candidates → call `_match_parent_row` on *each* independently
   (diagnostics suppressed per-candidate to avoid noise), keeping only frames
   that produced a confident row match. No frame matches → record
   `unresolved_source_frame` and return `(None, -1)`.
4. Disambiguate the surviving matches in order: (a) if `traced_column` is set
   and any matched frame's `DataFrame` has that column, narrow to those frames;
   (b) narrow further to the frame(s) with the widest `match_width`; (c) if more
   than one frame still survives, record an `ambiguous_source_frame` diagnostic
   (`severity: "warning"`) naming the candidates and return `(None, -1)`.
5. Return the winning frame's `(row_dict, positional_index)`.

### `_correlate_rows_posthoc()` (`_trace_correlation.py`)

1. Extract and jsonify the target node's row at `row_index`; seed `result` and
   `row_indices` with it.
2. Build `children_of` as the reverse of `parents_of`.
3. Walk `order` in reverse. For each unresolved node with a materialized output:
   - Find a child of that node already resolved with a non-empty row
     (`resolved_child_id`); if none exists, the node is not on the path to the
     target and is marked unresolved (`None`, index `-1`).
   - If the parent's output (`eager_outputs[nid]`) is a `dict` (a multi-frame
     source), delegate to `_resolve_multi_frame_parent()` (above) with
     `handles=source_frames_of.get((nid, resolved_child_id))` and the caller's
     `traced_column`, and continue to the next node.
   - Otherwise, delegate to `_match_parent_row()` (above) on the bare
     `DataFrame`.
4. Return the per-node row dict (or `None`) map.

### `enrich_steps()` (`_trace_enrichment.py`)

Iterates every `TraceStep` (each step wrapped in its own outer `try`/`except` so
one step's unforeseen failure cannot abort the whole enrichment pass) and:

1. Resolves the node's code, following `instanceOf` config to the original
   node's code when the instance's own code has no `.with_columns(`.
2. Parses/evaluates the expression for `column` when the column is
   added/modified at this step, or (for the *target* step only) walks upstream
   to find the step that created a pass-through column and borrows its
   expression/calculation. **Self-referential guard**: if `column` is both
   `columns_modified` (per the step's `SchemaDiff`) and one of the parsed
   expression's own `referenced_columns` (e.g. `premium = premium * factor`),
   evaluating against `{**input_values, **output_values}` unmodified would seed
   the RHS's `premium` with the *post-assignment* output — producing an
   arithmetically false substitution (`200.0 * 2.0` displayed for an output of
   `200.0`). When a pre-assignment `input_values[column]` exists, it overrides
   the output value in the evaluation namespace before calling
   `evaluate_expression`. When it doesn't (the column was newly created this
   step, so there is no pre-assignment value to show), evaluation is skipped
   entirely and `step.calculation` is set directly from the output value
   (`{"target_column", "substituted_text": f"{column} = {value!r}",
   "result_value": value}`) rather than risk a false substitution.
3. Parses the intra-node expression chain (`parse_expression_chain`) when the
   node assigns multiple dependent columns in one `.with_columns(...)` call.
   Chain entries evaluate **in order**, feeding each entry's result forward into
   the next: the evaluation namespace (`combined_values`) starts from
   `{**input_values, **output_values}`, but every column that is itself a chain
   target is first reset to its *pre-node* `input_values` entry (or removed if
   the column is newly created this step, i.e. absent from `input_values`) —
   otherwise the entry that reassigns that column would see its own
   post-assignment output on the RHS, the same self-referential problem as step
   2. As each chain entry evaluates successfully, its `result_value` is written
   back into `combined_values` under its `target_column` so the *next* entry
   sees the correct fed-forward intermediate. A failing entry's fallback
   `result_value` prefers the fed-forward `combined_values` entry and falls back
   to `step.output_values` only if that is also absent.
4. Recursively derives `input_sources` for every referenced column
   (`_build_input_sources`, depth-limited to 3, cycle-guarded via a
   `(node_id, column)` visited set) — for each reference, finds the nearest
   upstream step that created/modified it, parses/evaluates its formula (with a
   banding-specific branch that reuses `enrich_banding`'s factor detail instead
   of generic expression parsing), and recurses into *its* references.
5. Detects renames (`.rename({...})` or a pure `.with_columns(new=pl.col(old))`)
   and builds a rename chain by walking backward through prior steps.
6. Dispatches node-type enrichment by `node_type` (`ratingStep`, `banding`,
   `modelScore`, `scenarioExpander`, `liveSwitch`, `optimiserApply`) into
   `step.node_detail`; for `banding`, additionally attaches lineage
   (`_attach_banding_lineage`) directly into `step.expression`/`step.calculation`
   using the matched factor. Building `factor_input_dtypes` for `enrich_banding`
   walks each parent's materialized output; when a parent is a multi-frame `dict`,
   it is scoped to the frame(s) named by `source_frames_of.get((pid,
   step.node_id))` (falling back to every frame if no handle is recorded) rather
   than merged across every frame the source emits — a column name recurring
   across frames with a different dtype must resolve in the *consumed* frame's
   dtype, not by dict-iteration order over frames the node never sees.
7. Classifies `step.row_lineage_type` via `detect_row_lineage_type()`, using the
   node type first (source nodes → `"created"`, `liveSwitch` → `"selected"`,
   `edgeJoin` → `"joined"` — checked before any code-sniffing because a join's
   config-driven code carries no literal `.join(` token), then a code-sniffed
   `operation_type` (`_sniff_operation_type`), then a row-count-delta fallback.
   Parent and child row counts for the fallback go through
   `_node_output_row_count()`, which counts the widest frame's rows for a
   multi-frame `dict` output rather than `len(dict)` (which would count frames,
   not rows).

### `build_waterfall_from_steps()` (`_trace_waterfall.py`)

1. Bail out (`None`) if there is no `column` or fewer than 3 steps.
2. Coerce `final_output_value` to a finite float via `_as_trace_waterfall_float`
   — an out-of-JS-safe-integer-range int or numeric string raises
   `WaterfallUnavailableError`; a non-numeric value returns `None` (waterfall
   does not apply, not an error).
3. `_ensure_single_column_lineage()` rejects (raises `WaterfallUnavailableError`)
   if the column is produced by origins on two branches with no lineage path
   between them, or if an `edgeJoin` node emits the column's JOIN-branch origin
   under a suffixed name while the base branch keeps the unsuffixed name (a
   renamed-join-branch origin cannot be used as unsuffixed-column lineage).
4. Walk `steps` in order. The first step where the column is added/modified
   opens the chain as `"base"` (`value_before = observed`). Each subsequent step
   where the column is modified, *or* where the node's code structurally
   assigns the column even though this row's value did not change
   (`_step_targets_column` — an identity factor like `×1.0` would otherwise
   vanish as a `"passed"` schema-diff entry), contributes an entry via
   `_classify_contribution()`.
5. `_classify_contribution()` computes `delta = value_after - value_before` and,
   when `value_before` is positive, `value_after` is non-negative, and the implied
   factor is finite, uses `value_after / value_before` for
   multiplicative display; otherwise it falls back to additive (delta-only)
   display and logs a WARNING (`waterfall_implied_factor_undefined` /
   `waterfall_sign_change`). `_operation_hint()` (AST-based, not substring
   matching) can still force additive display when the node's own expression's
   top-level operator is `+`/`-`.
6. `build_waterfall()` (the generic, hand-authored-list-compatible core) is
   called with the assembled step dicts, each carrying an observed
   `"cumulative"` — this snaps each entry's cumulative to the OBSERVED value
   (never re-applying `value` arithmetically) and runs
   `_check_display_consistency()` per step, which raises
   `WaterfallReconciliationError` if a display number cannot be reconciled with
   the observed chain (e.g. a non-identity multiply factor from a zero prior
   cumulative, or a reapplied value outside float tolerance of the observation).
7. The final `wf_result.final_value` must match the coerced `final_value` within
   `_RECONCILE_REL_TOL`; a mismatch raises `WaterfallReconciliationError`.
8. All three failure modes (`WaterfallReconciliationError`,
   `WaterfallUnavailableError`, any other exception) are caught at the top level
   and converted into a `{"error": ..., "error_type": ...}` dict instead of
   propagating.

### Serialisation

`trace_result_to_dict()` (`trace.py`) builds a plain dict mirroring
`TraceResult`/`TraceStep`/`TraceOmission` field-for-field (unpacking
`schema_diff` into its four list fields) and runs it through
`haute._json_safe.to_json_safe()` for a final JSON-safety pass. Report export is
not a second backend interpretation; the frontend projects this validated
snapshot deterministically.

## Edge cases and invariants

- **Non-finite and oversized values never silently coerce.** `_trace_values_match`
  treats `nan`/`inf`/`-inf` as first-class tokens (via
  `non_finite_float_token`/`_value_non_finite_token`) so a NaN can match a NaN
  from JSON without a bare `==` false negative. `_as_trace_waterfall_float`
  explicitly raises `WaterfallUnavailableError` for an integer (or a
  JSON-safe-integer-marked string) outside JavaScript's exact integer range,
  rather than silently truncating precision into a rendered number.
- **`_build_value_match_expr` is dtype-robust.** Comparing a numeric trace value
  against a non-numeric column, or a non-finite float against a non-`Float`
  column, would otherwise raise `ComputeError`/`InvalidOperationError` at collect
  time inside Polars and crash the whole correlation walk. When the column's
  dtype is known, such a mismatch degrades to an always-false predicate
  (preserving the documented `(None, -1)` fail-soft contract) instead of
  crashing; a genuine cross-type coercion (an int-like key matching a string
  column) still goes through a stringwise compare.
- **Positional alignment is gated, never assumed from row-count equality alone.**
  Equal row counts between parent and child is necessary but not sufficient — a
  row-reordering transform (sort/join/group_by/gather/sample/shuffle/unique/
  top_k/bottom_k/explode/pivot/cross_join, per `_ROW_REORDERING_TOKENS`) can
  produce the same row count as its input while permuting rows. When the code
  cannot even be inspected, `_child_transform_may_reorder` conservatively assumes
  it *can* reorder — failing loud (unresolved step) over guessing.
- **Edge-join provenance is derived, not assumed by name (three rules, all
  sourced from `build_edge_join_kwargs`, the same kwargs `execute_edge_join`
  applies at runtime):** (1) a suffixed child column whose unsuffixed name exists
  in *both* parents is the right frame's copy of a collision — match the parent's
  unsuffixed name against it; (2) an unsuffixed child column present in the right
  parent only is right-provenance — match it directly; if present in both
  parents it carries the *left* row's value and must not be matched against the
  right frame; (3) join-key columns are mapped onto the parent's key name for
  every row where the right side participated (rows where it did not — left-join
  misses, full-join left-only rows — correlate to nothing and the step is left
  unresolved rather than inventing lineage).
- **Column relevance pruning has two distinct cases**, both anchored on
  `_tag_column_relevance` tagging every step first: a pass-through column keeps
  only nodes whose *output* actually carries it (pruning unrelated source
  branches); a calculated/modified column keeps its origin node(s) plus every
  ancestor that produces a column its formula's `referenced_columns` actually
  names (falling back to keeping *all* ancestors when no expression info is
  available, e.g. an opaque node).
- **`instanceOf` code resolution appears in three independent places**
  (`enrich_steps`, `_build_input_sources`, `_build_rename_chain`) — a cloned
  node instance whose own code lacks `.with_columns(` borrows the *original*
  node's code so the step that structurally created a column gets the correct
  expression, not a blank one.
- **The trace cache fingerprint includes `runtime_input_extra_keys(graph)`** so
  an out-of-band re-export of a flat-file `dataSource`/`externalFile`, a
  model-artifact signature change, or an `apiInput` JSON-cache rebuild
  invalidates cached trace frames even though the graph structure itself did not
  change.
- **A single trace-cache entry larger than the whole byte budget is rejected at
  store time** (same admit-or-reject-at-store policy as the dataframe-execution
  cache) rather than silently evicting every other entry to fit it; the trace
  result returned to the caller is unaffected, only its cache hit is lost.
- **Multi-frame source resolution is per edge, never per (source, target) pair.**
  A multi-frame source's edges each carry a `sourceHandle` naming the frame that
  edge consumes; the same source can feed the same child through several edges
  at once (the canonical four-port `apiInput` → `OUTPUT` topology, or a node
  joining two data levels straight off one multi-frame source). `source_frames_of`
  therefore stores a `list[str | None]` per pair — one entry per edge, in edge
  order — and `_resolve_multi_frame_parent` matches every distinct candidate
  frame against the resolved child row independently rather than assuming a
  single frame per pair.
- **Multi-frame disambiguation never guesses.** When several frames of one
  source all confidently match the child row, the traced column (if any) wins
  first, then the widest `match_width` (most specific match). A surviving tie
  remains unresolved and emits an `ambiguous_source_frame` diagnostic linked to
  the resulting omission.
- **A duplicate row match during target-row relocation fails loud.**
  `_find_target_row_index` (`trace.py`) collects *every* row index matching the
  clicked values on the shared columns; more than one match raises `ValueError`
  naming the match count and the shared columns, rather than silently returning
  the first index — mirroring the row-correlation layer's ambiguous-match policy
  (`_record_ambiguous_row_match`) at the row-identity-verification layer.
- **Self-referential column assignments never let the output value leak into
  its own substitution's input side.** Both the single-expression path
  (`enrich_steps` step 2) and the intra-node chain path (step 3) detect when a
  step's target column is also referenced on its own right-hand side and
  substitute the pre-assignment value instead of the final output value —
  otherwise a step like `premium = premium * factor` would display an
  arithmetically false substitution built from its own result.

## Error handling

| Exception | Raised by | Propagates to |
| --- | --- | --- |
| `ValueError` | `execute_trace` — empty graph, unknown `target_node_id`, `row_index` out of range (also raised inside `_correlate_rows_posthoc`), unresolved row-value mismatch after relocation attempt, `target_node_id` resolving to a multi-frame source's `dict` output | The HTTP route rejects an empty graph itself with 400; recognised remaining message shapes map to 404 / 400 / 409, while an unrecognised `ValueError` is sanitised to 500 |
| `ValueError` (ambiguous duplicate match) | `_find_target_row_index` (`trace.py`) — the clicked `row_values` match more than one row on the shared columns during target-row relocation | Propagates unchanged out of `execute_trace`; HTTP route maps to 409 |
| `TypeError` | `_resolve_preview_snapshot` — `preview` is not `None`/reader/dict, or a reader's `try_get` returns a non-`dict` non-`None` value | Caller of `execute_trace` |
| `RuntimeError` | Module import — malformed/non-positive `HAUTE_PREVIEW_CACHE_MAX_BYTES` or `HAUTE_TRACE_CACHE_MAX_BYTES` | Importing caller; cache construction does not start |
| `ContractMismatchError` | Propagated unchanged from `_execute_eager_core` (execution-engine) on a cold-execution contract violation | HTTP route, mapped to 422 |
| Any exception from cold execution (`_execute_eager_core`, `swallow_errors=False`) | Propagated unchanged — no regex-based masking/retry | HTTP route, mapped to 500 (or a specific status if it happens to be one of the recognised `ValueError` shapes) |
| `WaterfallReconciliationError` (`ValueError` subclass) | `_check_display_consistency`, the final-cumulative reconciliation check in `build_waterfall_from_steps` | Caught inside `build_waterfall_from_steps`; converted to `{"error": ..., "error_type": "WaterfallReconciliationError"}` in `TraceResult.waterfall` |
| `WaterfallUnavailableError` (`ValueError` subclass) | `_ensure_single_column_lineage`, `_reject_renamed_join_branch_origins`, `_as_trace_waterfall_float` (unsafe integer) | Same as above — converted to a structured error dict, not raised |
| Any other exception during waterfall assembly | — | Caught, logged (`waterfall_build_failed`, WARNING, `exc_info=True`), converted to `{"error": ..., "error_type": ...}` |
| Any exception during a single enrichment concern (expression parse/eval, chain, input sources, rename detection, node-type enrichment, row-lineage detection) | `_trace_enrichment.py`, per-concern `try`/`except` | Caught, logged at WARNING with `exc_info=True`, surfaced as an `error`/`error_type` key on the relevant field (`expression`, `calculation`, `node_detail`, or the `row_lineage_type` string itself as `"error: ..."`) — never re-raised |
| Any exception escaping an entire step's enrichment (outer catch-all in `enrich_steps`) | `_trace_enrichment.py` | Caught, logged (`trace_enrichment_step_failed`), an `error` marker is set on `step.node_detail` if not already present, and the loop continues to the next step |
| `ValueError` (edge-join misconfiguration) | `_build_parent_match_row` — a node wired as a parent of an `edgeJoin` that matches neither its `baseInput` nor `joinInput` | Propagates unchanged out of `_correlate_rows_posthoc` |
| `ValueError` (missing base frame) | `_build_parent_match_row` — the edge-join's base parent has no materialized output when correlating the join parent | Propagates unchanged |

## Testing

Tests live in `tests/`, one focused file per concern plus several broad
integration/regression suites:

- **`tests/test_trace.py`** — core unit coverage of `execute_trace`,
  `SchemaDiff`/`TraceResult`/`TraceStep`, and `_find_matching_row` directly
  against `haute._trace_correlation`; includes the fail-loud duplicate-match
  regression for `_find_target_row_index` (ambiguous relocation raises
  `ValueError` rather than returning the first matching index).
- **`tests/test_trace_api.py`** — the `POST /api/pipeline/trace` HTTP layer via
  FastAPI `TestClient`: request validation, response shape, serialisation, and
  error-status mapping. Explicitly deferred to `test_trace_integration.py` for
  core trace-logic correctness. Includes the end-to-end 409 case for a
  duplicate-row relocation conflict.
- **`tests/test_trace_integration.py`** — the broad end-to-end suite:
  end-to-end cell-click → backend computation → enriched result flow across every
  pipeline topology, node type, data shape, caching behaviour, and error path;
  described in its own docstring as "the complete specification for the trace
  enhancement."
- **`tests/test_trace_matches_preview.py`** — pins the contract that a trace's
  target-node values exactly match `preview[row_index]` for the same graph and
  `row_limit`, including the shared-cache-fingerprint requirement between preview
  and trace calls.
- **`tests/test_trace_enrichment.py`** — focused enrichment coverage:
  node-type-specific enrichment (rating step, banding, model score, scenario
  expansion, live switch, data-source metadata) and row-lineage-type detection,
  exercised both via TDD stubs and through `execute_trace` on real data-flow
  patterns.
- **`tests/test_trace_calculation_hero.py`** and
  **`tests/test_trace_hero_tdd.py`** — the expression/calculation
  ("Calculation Hero") feature: conditional-branch indication, waterfall data
  generation, preamble constant resolution, window-function fallback, intra-node
  dependency chains, column-rename tracking, null explanation, copy/export
  data-structure shape, and (`TestSelfReferentialCalculation`) the
  pre-assignment-value substitution fix for self-referential assignments
  (`premium = premium * factor`), both as a single expression and as an
  intra-node chain that feeds a self-referential intermediate forward into a
  dependent entry.
- **`tests/test_trace_waterfall.py`** — the C8 arithmetic-contract
  regression suite, driving flagship multiplicative/additive scenarios through
  `execute_trace` end-to-end (not hand-fed factors) so a regression in
  value-derivation cannot hide behind a synthetic unit test.
- **`tests/test_trace_edge_join.py`** — the W1.7 edge-join provenance
  remediation: correlation path and per-step row values for both the base and
  JOIN-role parents of an `edgeJoin` node, including suffix collision cases.
- **`tests/test_trace_multi_frame.py`** — multi-frame (≥2-table `apiInput`) row
  correlation: tracing a node downstream of a multi-frame source correlates
  through the edge's named frame instead of crashing on the bare `dict`;
  targeting the multi-frame node itself (directly, or through the HTTP route)
  raises a clear `ValueError`/400 rather than an opaque 500; the four-port
  `apiInput` → `OUTPUT` topology (one source, one target, four edges) correlates
  the source step to the frame that actually identifies the traced row rather
  than whichever edge's `sourceHandle` came last; a single polars node joining
  two frames of the same source via two edges resolves the correlated source row
  to the drivers frame or the vehicles frame depending on which column is
  traced; `_node_output_row_count` counts a multi-frame bundle's rows (max
  across frames) rather than its frame count; and a banding node fed by one
  frame of a multi-frame source resolves factor dtypes from that frame alone,
  not a dict-iteration-order merge across every frame the source emits.
- **`tests/test_trace_banding_lineage.py`** — lineage tests specific to
  banding-created fields.
- **`tests/test_optimiser_apply_trace_enrichment.py`** — optimiser-apply
  explainability enrichment.
- **`tests/test_trace_w4_fixes.py`** — W4-audit correlation-soundness
  regressions: fail-loud/unresolved behaviour over wrong-row attribution, and
  numeric-comparison agreement with actual engine behaviour.
- **`tests/test_trace_fail_loudly.py`** — the fail-loud enrichment
  sweep: pins that specific `except Exception` sites inside `_enrich_steps` and
  its helpers surface a visible `error` field (or raise) rather than silently
  returning `None`.
- **`tests/test_trace_display_edge_cases.py`** — unusual value types
  (None/NaN/Inf/bool/date), column-identity edge cases (alias/overwrite/join
  suffix/special names), pipeline-structure edge cases (single node, long chains,
  diamonds, fan-out), row-correlation edge cases (filter/sort/join/positional
  fallback), expression-parser integration, calculation accuracy, serialisation,
  and cache/concurrency edge cases.
- **`tests/test_trace_coverage.py`** — coverage-directed tests
  targeting paths in `trace.py` and `_trace_waterfall.py` identified by coverage
  analysis rather than by feature.
- **`tests/test_trace_cache_byte_awareness.py`** — the byte-aware
  trace-cache eviction remediation: bounded by bytes AND entry count, LRU
  eviction, deterministic reject-at-store for an oversized single entry. Twin
  module to `tests/test_preview_cache_byte_awareness.py`.
- **`tests/test_trace_golden.py`** — golden-snapshot serialisation
  tests against `tests/fixtures/ui_contracts/trace_response.json`, validated
  through `haute.schemas.TraceResponse` — guards the wire shape the frontend
  depends on against accidental drift.
- **`tests/performance/test_preview_trace_perf.py`** — performance/benchmark
  coverage of the preview-cache-reuse path and the trace execution cache
  (`haute.trace._cache`) under load, on a representative multi-branch graph.
  Enforces latency budgets: cached target preview `< 0.5s`, first trace backed
  by a full preview cache `< 0.8s`, trace-cache hit `< 0.3s`. Excluded from the
  default test run by the `perf` marker (`addopts = "-m 'not perf'"` in
  `pyproject.toml`); run explicitly via
  `uv run python scripts/run_perf_suite.py --pytest-target tests/performance/test_preview_trace_perf.py`.

Known coverage gaps: none identified from a read of the test file list and
docstrings above — the correlation, waterfall, and enrichment fail-loud paths in
particular have dedicated regression files (`test_trace_w4_fixes.py`,
`test_trace_fail_loudly.py`, `test_trace_waterfall.py`) beyond ordinary feature
tests. This spec does not itself execute the suite; treat file/class presence as
an index, not a substitute for running `pytest tests/test_trace*.py
tests/test_optimiser_apply_trace_enrichment.py` when changing this component.

## Polars backend contracts (0.6.0)

This implementation slice is governed by the
[Polars backend remediation plan](../../trip/plans/F_0.6.0_polars-backend-remediation.plan.md)
and is delivered as P9a (correlation/enrichment) followed by P9b (cache
integration). P9a does not wait for or introduce lineage-key code. P9b starts
only after the shared caching/lineage-preview-key slice is complete.

### Correlation primitive

Introduce one internal vectorized matching primitive and typed result used by
`_find_target_row_index`, ordinary post-hoc parent correlation, edge-join parent
matching, and multi-frame source-frame selection. The result distinguishes
`no_match`, `unique_strict`, `unique_relaxed`, `ambiguous`, and
`unsupported_dtype`, and includes at least strict/effective key columns,
relaxation reason, exact `candidate_count`, up to 16 candidate indices, and an
explicit candidate availability/truncation state. It builds Polars expressions
and never materialises shared keys into Python or iterates a frame row-by-row in
Python. Counting candidates can inspect the full result inside Polars, but no
more than 16 indices cross into Python.

The V1 strict-comparison matrix is bounded and exhaustive. It is also the
per-value comparator used after any key relaxation; relaxation can omit keys but
cannot weaken a retained key's comparison:

| Dtype family | Exact V1 comparison contract |
| --- | --- |
| Null | Null matches only null. It does not match any finite/non-finite value or sentinel. |
| Boolean | Exact boolean equality. Boolean is never numeric: `true` does not match `1`/`1.0`, and `false` does not match `0`/`0.0`. |
| Integer/integer | Exact mathematical integer equality after lossless signed/unsigned coercion. Overflow or an unrepresentable cross-signed comparison is `unsupported_dtype`, not a float cast. |
| Integer/float | Allowed only when `abs(integer) <= 2**53` and the float is finite. Compare with the finite-numeric formula below. Above that boundary integer/float is `unsupported_dtype`. |
| Unsafe integer/string | An integer represented at the JSON boundary outside the JavaScript-safe range matches only its exact canonical base-10 decimal string. No whitespace, `+`, exponent, decimal point, or redundant leading zero is accepted; `0` is the only zero spelling. No other numeric/string coercion is supported. |
| Finite float/numeric | For finite values `a` and `b`, match iff `abs(a - b) <= max(1e-12, 1e-9 * max(abs(a), abs(b)))`. This one formula is used for float/float and the permitted integer/float case. |
| Non-finite float/sentinel | NaN matches only NaN or `{"__haute_type__": "non_finite_float", "value": "nan"}`; positive infinity only positive infinity or the same canonical sentinel with value `"inf"`; negative infinity only negative infinity or the sentinel with value `"-inf"`. There is no cross-sign, finite, null, malformed-sentinel, or other-sentinel match. |
| String/binary | Exact equality within a compatible string or binary family. Apart from the unsafe-integer rule above, string is not numeric. String/binary cross-family comparison is unsupported. |
| Date | Date matches only Date representing the same calendar day. Date-versus-Datetime is unsupported. |
| Time | Time matches only Time at the exact same integer nanosecond-of-day. Unit conversion is checked. |
| Datetime | Normalise compatible units to a checked integer UTC nanosecond value. Timezone-aware matches only timezone-aware at the same instant; naive matches only naive. Aware-versus-naive, Date-versus-Datetime, or conversion overflow is `unsupported_dtype`. |
| Duration | Exact equality after checked conversion to signed integer nanoseconds. A lossy or overflowing conversion is `unsupported_dtype`. |
| Decimal | Exact equality after losslessly rescaling both integer coefficients to the larger scale. Decimal never compares through float; rescaling overflow or incompatible precision is `unsupported_dtype`. |
| Categorical/enum | Compare normalised string values, not physical category codes. |
| List/array/struct | Polars 1.39.3 native equality against a typed one-row literal, only for the same compatible schema. |

Object values and incompatible nested schemas return `unsupported_dtype`.
Neither case can invoke a Python scalar loop or full-frame fallback. The
canonical preview/trace serializer remains the display-value authority only;
its support for arbitrary objects is not a correlation-key contract.

Relaxation runs only after zero strict candidates, follows an explicit ordered
rule set, and returns `unique_relaxed` only for one candidate. Ambiguous relaxed
results remain ambiguous. Callers emit a `low_confidence_relaxed_match`
diagnostic naming omitted/relaxed keys, reason, exact candidate count, available
candidate indices, and whether those indices were truncated; they cannot promote
it to an unmarked strict result.

Every match result has these exact candidate fields:

- `candidate_count: int|null`
- `candidate_indices: list[int]`
- `candidate_indices_state: available|truncated|unavailable`

For every supported path, the primitive adds `with_row_index` before filtering,
counts the full candidate set exactly inside Polars, orders the original physical
row indices ascending, and returns the first 16. `candidate_indices_state` is
`available` if and only if `candidate_count <= 16` (including zero), and is
`truncated` if and only if `candidate_count > 16`. For `unsupported_dtype`, the
only valid payload is `candidate_count = null`, `candidate_indices = []`, and
`candidate_indices_state = unavailable`. All arrays returned by the primitive
or copied into diagnostics are deterministic and capped at 16 entries.

Target relocation maps ambiguity to the existing loud `ValueError`; upstream
correlation maps it to the unresolved-step diagnostic. Target relocation maps
`unsupported_dtype` to the public
`TraceCorrelationUnsupportedError(ExecutionError)`, exported from
`haute.errors`. That exception has stable code
`trace_correlation_unsupported` and exactly the stable diagnostic fields
`node_id`, `key_columns`, `dtypes`, and `reason_code`; the HTTP route maps it to
422 and a background execution records `contract_error`. Array-valued fields
are deterministic and capped at 16 entries. `key_columns` and `dtypes` are
aligned, retain deterministic match-key order, and are capped together. Upstream
correlation remains unresolved and records a typed `unsupported_dtype`
diagnostic. Existing edge-join provenance and multi-frame deterministic
selection rules supply match rows and frames to the common primitive rather
than reimplementing comparison.

### Serialisation, caching, and enrichment

`trace_result_to_dict()` delegates value conversion to the canonical preview
JSON-safe serializer, including recursive temporal/nested values, arbitrary
objects, non-finite values, and unsafe integers. Parity tests compare every
trace input/output cell's JSON value with its preview equivalent. This serializer
is display-only for arbitrary objects and must not be called as a way to turn an
unsupported object or incompatible nested value into a correlation key.

P9b trace-key construction calls the shared lineage preview-key factory from the
caching remediation slice, after that API lands. It supplies every mandatory
factory input and may add only trace-specific dimensions allowed by that factory;
it must not recompute whole-graph identity or maintain a parallel lineage
fingerprint. P9a makes no temporary cache-key abstraction.

Create two distinct request-local structures owned by one `execute_trace()` call:

- A completed-result enrichment memo, passed to enrichment helpers. Its key
  includes node identity, concern, column, row identity, frame/port identity, and
  every other input read by that concern. A sibling diamond may reuse an already
  completed result with the identical key. Exceptions are never inserted.
- An active recursion stack scoped to each call path. A helper pushes before
  descent and pops in `finally`; encountering an active key produces the existing
  structured cycle/error marker. Active entries are not completed memo values,
  so a sibling branch is not mistaken for a cycle.

Neither structure is module-level, embedded in the response, or retained after
the request finishes.

### Delivery and verification

Implement P9a in this order: (1) typed primitive and every V1 matrix-cell test;
(2) strict/JSON-parity tests; (3) all correlation call-site migration with
duplicate and unsupported-dtype regressions; (4) relaxed diagnostics and bounded
candidate reporting; (5) completed-result memo and per-call-path stack tests.
Implement P9b only after the lineage key factory exists: integrate it and delete
duplicate trace key construction. Do not implement row-id injection, persistent
enrichment caching, new API/UI surfaces, arbitrary-object key coercion, or
heuristic ambiguity resolution in either slice.

Tests prove no Python shared-key full-frame scan remains (source/AST or
instrumented structural assertion), vectorized matching is used by every call
site, and every matrix cell has supported and unsupported coverage. Boundary
tests pin finite comparisons just below, exactly on, and just above the absolute
and relative tolerance branches; negative and zero-centred values; boolean
versus 0/1; exact integer/integer comparison; integer/float at `abs(integer) ==
2**53` and rejection above it; exact canonical unsafe-integer strings plus
leading-zero, plus-sign, whitespace, exponent, and decimal-point rejections; and
the complete NaN/`nan`, positive-infinity/`inf`, negative-infinity/`-inf`,
cross-sign, finite, and null truth table.

Temporal and Decimal boundary tests cover Date only with Date; Date-versus-
Datetime rejection; Time nanosecond-of-day equality; Datetime unit normalisation,
aware same-instant equality, aware-versus-naive rejection, and checked-nanosecond
overflow; negative and positive Duration equality plus lossy/overflow rejection;
and Decimal equal values at different scales, unequal values, precision/rescale
overflow, and Decimal-versus-float rejection. Candidate tests assert the exact
payload at counts 0, 1, 16, and 17, including ascending original physical row
indices from `with_row_index` and the only valid unsupported payload. Duplicate
target/parent matches remain loud or unresolved; unsupported target values raise
`TraceCorrelationUnsupportedError`; its code, named fields, 422/background
mapping, deterministic order, and 16-entry caps are pinned; unsupported parent
values stay unresolved with the typed diagnostic; and trace/preview JSON parity
holds without broadening key support. Add large-frame correlation performance
coverage plus memo tests for same-key reuse, differing concern inputs, sibling
diamonds, cycle push/pop, exceptions, and cross-request isolation.
