# Tracing — Low-Level Specification

## Module map

| File | Responsibility |
| --- | --- |
| `src/haute/trace.py` | Public facade and orchestrator. `execute_trace()` entry point, `PreviewReader` protocol, `TraceStep`/`TraceResult` dataclasses, the trace execution cache (`_cache`, `TRACE_CACHE_MAX_BYTES`), row/step assembly (`_assemble_steps`), column-relevance pruning, and JSON serialisation (`trace_result_to_dict`). Re-imports and re-exports expression-parser and node-type enricher names so `monkeypatch.setattr("haute.trace.<name>", ...)` in tests reaches the dispatch code in `_trace_enrichment.py`. |
| `src/haute/_trace_correlation.py` | Post-hoc row correlation and schema diff. Value predicates/coercion (`_trace_values_match`, `_jsonify_row`), `SchemaDiff` computation, dtype-robust Polars match-expression construction, exact/relaxed row matching with ambiguity diagnostics, edge-join provenance-aware parent-row projection, and the backward-walk driver `_correlate_rows_posthoc`. |
| `src/haute/_trace_enrichment.py` | Node-type enrichers (`enrich_rating_step`, `enrich_banding`, `enrich_model_score`, `enrich_scenario_expansion`, `enrich_live_switch`, `enrich_optimiser_apply`), row-lineage-type detection (`detect_row_lineage_type`), and the per-step dispatch walk (`enrich_steps`) that drives expression parsing/evaluation, intra-node chain analysis, recursive upstream input-source derivation, rename detection, and node-type dispatch for every `TraceStep`. |
| `src/haute/_trace_export.py` | `export_trace()` — converts a `TraceResult` into a report-shape dict (`header`, `formula`, `sources`, `data_flow`, `metadata`) for markdown/PDF report generation. |
| `src/haute/_trace_waterfall.py` | Waterfall assembly for sequential multiplicative/additive rating chains. `WaterfallEntry`/`WaterfallResult` dataclasses, the generic `build_waterfall()` API (hand-authored factor lists), the value-derived `build_waterfall_from_steps()` (traced-path driver), and the C8 arithmetic-reconciliation guards. |

## Key types and data structures

- **`PreviewReader`** (`trace.py`, `@runtime_checkable` `Protocol`) — anything
  exposing `try_get(fingerprint: str) -> dict[str, Any] | None`. `FingerprintCache`
  satisfies it by construction.
- **`TraceStep`** (`trace.py`, dataclass) — one node's contribution: `node_id`,
  `node_name`, `node_type`, `schema_diff: SchemaDiff`, `input_values` /
  `output_values` (column → value dicts), `column_relevant: bool` (default
  `True`), `execution_ms: float`, and enrichment fields populated by
  `_enrich_steps`: `expression`, `calculation`, `node_detail`,
  `row_lineage_type`. `row_data` is a read-only property aliasing
  `output_values`, used by export/display layers.
- **`TraceResult`** (`trace.py`, dataclass) — the full per-row trace:
  `target_node_id`, `row_index`, `column`, `output_value`, `steps: list[TraceStep]`,
  `row_id_column`/`row_id_value` (from an `apiInput` node's `row_id_column`
  config, if set), summary counts (`total_nodes_in_pipeline`, `nodes_in_trace`,
  `execution_ms`), `waterfall` (list of entry dicts, a structured error dict, or
  `None`), and `correlation_diagnostics: list[dict[str, Any]]` (never `None`,
  defaults to an empty list).
- **`SchemaDiff`** (`_trace_correlation.py`, dataclass) — `columns_added`,
  `columns_removed`, `columns_modified`, `columns_passed`, each a `list[str]`.
- **`_RowMatchCandidate`** (`_trace_correlation.py`, frozen dataclass) — an
  internal grouping of `(columns, row_indices)` used only inside relaxed-match
  ambiguity reporting.
- **`WaterfallEntry`** / **`WaterfallResult`** (`_trace_waterfall.py`, dataclasses)
  — `label`, `operation` (`"base"` / `"multiply"` / `"add"`), `value`, `delta`,
  `cumulative` per entry; `WaterfallResult` wraps `entries` plus `final_value`.
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
  via `HAUTE_TRACE_CACHE_MAX_BYTES`. Bounded by both entry count and retained
  bytes; sizing reuses `_estimate_preview_cache_entry_bytes` from
  `haute.executor`, and only the `eager_outputs` slot is treated as
  byte-size-sensitive.
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
   those extra keys, memoised via a request-scoped `GraphFingerprintMemo`.
3. On a trace-cache hit (`_cache.try_get(fp)`), reuse the cached
   `eager_outputs`/`order`/`parents_of`/`node_map`/`source_ids`. On a miss, call
   `_materialize_eager_outputs()` (below) and store the result under `fp`.
4. If the caller supplied `row_values`, verify the target DataFrame's row at
   `row_index` matches them (`_trace_values_match` per column). On mismatch,
   search the target DataFrame for a row that does match
   (`_find_target_row_index`) and relocate `row_index` to it; if none matches,
   raise `ValueError`.
5. If the target node produced output, call `_correlate_rows_posthoc()` (below)
   to get a JSON-safe row dict per node (or `None` for unresolved nodes). If the
   target node's execution failed, build partial rows directly from whatever
   `eager_outputs` are available instead (no correlation).
6. `_assemble_steps()` builds `TraceStep`s from the correlated rows: source nodes
   get `input_values = {}`; other nodes' `input_values` merge each parent's
   correlated row (namespacing a colliding key as `f"{pid}.{k}"` on collision).
   Nodes whose row correlation returned `None` are skipped entirely.
7. `_enrich_steps()` (from `_trace_enrichment.py`) enriches every step in place.
8. If `column` is set, `_prune_to_column_relevance()` tags and filters steps.
9. Resolve `output_value` from the target row (whole row dict if `column` is
   `None`, else the single value). Resolve `row_id_column`/`row_id_value` by
   scanning `nodes` for an `apiInput` node with a `row_id_column` config entry.
10. If `column` is set, compute `integer_output_node_ids` (which steps' output
    column is an integer dtype) and call `build_waterfall_from_steps()`.
11. Return the assembled `TraceResult`, logging a single `trace_executed` info
    event with cache-hit status and duration.

### `_materialize_eager_outputs()` (`trace.py`)

1. Normalise the `preview` argument via `_resolve_preview_snapshot()`: `None` →
   no reuse; a reader → try each candidate preview fingerprint (a
   column-projected key first, then the unsuffixed full-preview key) and use the
   first hit; a raw dict → used verbatim.
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

### `_correlate_rows_posthoc()` (`_trace_correlation.py`)

1. Extract and jsonify the target node's row at `row_index`; seed `result` and
   `row_indices` with it.
2. Build `children_of` as the reverse of `parents_of`.
3. Walk `order` in reverse. For each unresolved node with a materialized output:
   - Find a child of that node already resolved with a non-empty row
     (`resolved_child_id`); if none exists, the node is not on the path to the
     target and is marked unresolved (`None`, index `-1`).
   - Project the child's row onto the parent's columns
     (`_build_parent_match_row`) — a generic name-based projection, except for
     the JOIN-role parent of an `edgeJoin` child, which is routed through
     `_edge_join_right_match_row` (see Edge cases below).
   - **Fast path**: if the parent and child DataFrames have equal row counts and
     the child's row index is in range, try the parent row at that same
     positional index. Trust it only if (a) there are no shared columns and
     either the parent has exactly one row or the child transform provably
     cannot reorder (`_child_transform_may_reorder` returns `False`); or (b)
     there are shared columns, they all value-match, and either the child cannot
     reorder or the shared key uniquely identifies one row in the parent
     (`_shared_key_is_unique`).
   - **Value-matching fallback**: `_find_matching_row()` tries an exact match on
     all shared columns first; on no exact match (and `allow_relaxed=True`), it
     scores every row by how many shared columns match and picks the widest
     relaxed subset — but only if exactly one row achieves that width. Any tie
     (multiple exact or multiple best-relaxed matches) is recorded via
     `_record_ambiguous_row_match` / `_record_relaxed_candidate_ambiguity` into
     `diagnostics` and returns `(None, -1)`. `allow_relaxed` is forced to `False`
     for an edge-join's JOIN-role parent (`_allows_relaxed_parent_match`) — a
     relaxed miss there must not manufacture false lineage.
4. Return the per-node row dict (or `None`) map.

### `enrich_steps()` (`_trace_enrichment.py`)

Iterates every `TraceStep` (each step wrapped in its own outer `try`/`except` so
one step's unforeseen failure cannot abort the whole enrichment pass) and:

1. Resolves the node's code, following `instanceOf` config to the original
   node's code when the instance's own code has no `.with_columns(`.
2. Parses/evaluates the expression for `column` when the column is
   added/modified at this step, or (for the *target* step only) walks upstream
   to find the step that created a pass-through column and borrows its
   expression/calculation.
3. Parses the intra-node expression chain (`parse_expression_chain`) when the
   node assigns multiple dependent columns in one `.with_columns(...)` call, and
   evaluates each chain entry against the combined input+output values.
4. Recursively derives `input_sources` for every referenced column
   (`_build_input_sources`, depth-limited to 3, cycle-guarded via a
   `(node_id, column)` visited set) — for each reference, finds the nearest
   upstream step that created/modified it, parses/evaluates its formula (with a
   banding-specific branch that reuses `enrich_banding`'s factor detail instead
   of generic expression parsing), and recurses into *its* references.
5. Runs `_fix_upstream_values()`: when an input-source derivation found a
   known-good value that disagrees with a `None` in an upstream step's
   `output_values` (typically because post-hoc correlation matched the wrong row
   for that node), it re-filters that node's DataFrame for a uniquely-matching
   row and patches `output_values` in place — but only when the match is unique;
   an ambiguous match is logged and left untouched rather than guessed.
6. Detects renames (`.rename({...})` or a pure `.with_columns(new=pl.col(old))`)
   and builds a rename chain by walking backward through prior steps.
7. Dispatches node-type enrichment by `node_type` (`ratingStep`, `banding`,
   `modelScore`, `scenarioExpander`, `liveSwitch`, `optimiserApply`) into
   `step.node_detail`; for `banding`, additionally attaches lineage
   (`_attach_banding_lineage`) directly into `step.expression`/`step.calculation`
   using the matched factor.
8. Classifies `step.row_lineage_type` via `detect_row_lineage_type()`, using the
   node type first (source nodes → `"created"`, `liveSwitch` → `"selected"`,
   `edgeJoin` → `"joined"` — checked before any code-sniffing because a join's
   config-driven code carries no literal `.join(` token), then a code-sniffed
   `operation_type` (`_sniff_operation_type`), then a row-count-delta fallback.

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
   when `value_before != 0` and the implied factor is finite and non-negative
   (no sign flip), the implied factor `value_after / value_before` for
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
   the observed chain (e.g. a ×1.0 requirement from a zero prior cumulative, or a
   reapplied value outside float tolerance of the observation).
7. The final `wf_result.final_value` must match the coerced `final_value` within
   `_RECONCILE_REL_TOL`; a mismatch raises `WaterfallReconciliationError`.
8. All three failure modes (`WaterfallReconciliationError`,
   `WaterfallUnavailableError`, any other exception) are caught at the top level
   and converted into a `{"error": ..., "error_type": ...}` dict instead of
   propagating.

### Serialisation

`trace_result_to_dict()` (`trace.py`) builds a plain dict mirroring
`TraceResult`/`TraceStep` field-for-field (unpacking `schema_diff` into its four
list fields) and runs it through `haute._json_safe.to_json_safe()` for a final
JSON-safety pass. `export_trace()` (`_trace_export.py`) instead reduces a
`TraceResult` to a report-oriented shape: it finds the step that created/modified
the traced column (`formula`), derives each referenced column's true upstream
origin — the *first* step whose schema diff records it as added/modified, falling
back to the first step that merely carries it — for the `sources` list, and
produces an ordered `data_flow` summary plus `metadata` counts.

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

## Error handling

| Exception | Raised by | Propagates to |
| --- | --- | --- |
| `ValueError` | `execute_trace` — empty graph, unknown `target_node_id`, `row_index` out of range (also raised inside `_correlate_rows_posthoc`), unresolved row-value mismatch after relocation attempt | HTTP route (`routes/pipeline.py`), pattern-matched on message prefix to 404 / 400 / 409 |
| `TypeError` | `_resolve_preview_snapshot` — `preview` is not `None`/reader/dict, or a reader's `try_get` returns a non-`dict` non-`None` value | Caller of `execute_trace` |
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
  against `haute._trace_correlation`.
- **`tests/test_trace_api.py`** — the `POST /api/pipeline/trace` HTTP layer via
  FastAPI `TestClient`: request validation, response shape, serialisation, and
  error-status mapping. Explicitly deferred to `test_trace_integration.py` for
  core trace-logic correctness.
- **`tests/test_trace_integration.py`** — the largest suite (71 tests):
  end-to-end cell-click → backend computation → enriched result flow across every
  pipeline topology, node type, data shape, caching behaviour, and error path;
  described in its own docstring as "the complete specification for the trace
  enhancement."
- **`tests/test_trace_matches_preview.py`** — pins the contract that a trace's
  target-node values exactly match `preview[row_index]` for the same graph and
  `row_limit`, including the shared-cache-fingerprint requirement between preview
  and trace calls.
- **`tests/test_trace_enrichment.py`** — the largest single file (184 tests):
  node-type-specific enrichment (rating step, banding, model score, scenario
  expansion, live switch, data-source metadata) and row-lineage-type detection,
  exercised both via TDD stubs and through `execute_trace` on real data-flow
  patterns.
- **`tests/test_trace_calculation_hero.py`** (76 tests) and
  **`tests/test_trace_hero_tdd.py`** (36 tests) — the expression/calculation
  ("Calculation Hero") feature: conditional-branch indication, waterfall data
  generation, preamble constant resolution, window-function fallback, intra-node
  dependency chains, column-rename tracking, null explanation, and copy/export
  data-structure shape.
- **`tests/test_trace_waterfall.py`** (25 tests) — the C8 arithmetic-contract
  regression suite, driving flagship multiplicative/additive scenarios through
  `execute_trace` end-to-end (not hand-fed factors) so a regression in
  value-derivation cannot hide behind a synthetic unit test.
- **`tests/test_trace_edge_join.py`** (12 tests) — the W1.7 edge-join provenance
  remediation: correlation path and per-step row values for both the base and
  JOIN-role parents of an `edgeJoin` node, including suffix collision cases.
- **`tests/test_trace_banding_lineage.py`** (6 tests) — lineage tests specific to
  banding-created fields.
- **`tests/test_optimiser_apply_trace_enrichment.py`** (20 tests) — optimiser-apply
  explainability enrichment.
- **`tests/test_trace_w4_fixes.py`** (29 tests) — W4-audit correlation-soundness
  regressions: fail-loud/unresolved behaviour over wrong-row attribution, and
  numeric-comparison agreement with actual engine behaviour.
- **`tests/test_trace_fail_loudly.py`** (10 tests) — the fail-loud enrichment
  sweep: pins that specific `except Exception` sites inside `_enrich_steps` and
  its helpers surface a visible `error` field (or raise) rather than silently
  returning `None`.
- **`tests/test_trace_display_edge_cases.py`** (66 tests) — unusual value types
  (None/NaN/Inf/bool/date), column-identity edge cases (alias/overwrite/join
  suffix/special names), pipeline-structure edge cases (single node, long chains,
  diamonds, fan-out), row-correlation edge cases (filter/sort/join/positional
  fallback), expression-parser integration, calculation accuracy, serialisation,
  and cache/concurrency edge cases.
- **`tests/test_trace_coverage.py`** (80 tests) — coverage-directed tests
  targeting paths in `trace.py`, `_trace_waterfall.py`, and `_trace_export.py`
  identified by coverage analysis rather than by feature.
- **`tests/test_trace_cache_byte_awareness.py`** (14 tests) — the byte-aware
  trace-cache eviction remediation: bounded by bytes AND entry count, LRU
  eviction, deterministic reject-at-store for an oversized single entry. Twin
  module to `tests/test_preview_cache_byte_awareness.py`.
- **`tests/test_trace_golden.py`** (2 tests) — golden-snapshot serialisation
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
