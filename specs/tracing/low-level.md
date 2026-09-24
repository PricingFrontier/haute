# Tracing — Low-Level Specification

## Module map

| File | Responsibility |
| --- | --- |
| `src/haute/trace.py` | Public facade and orchestrator. `execute_trace()` entry point, `TraceStep`/`TraceOmission`/`TraceResult` dataclasses, the trace execution cache (`_cache`, `TRACE_CACHE_MAX_BYTES`), row/omission assembly, column-relevance pruning, provenance, and JSON serialisation (`trace_result_to_dict`). Re-exports expression-parser and node-type enricher names as public convenience imports; `_trace_enrichment.py` owns its dependencies directly. |
| `src/haute/_trace_correlation.py` | Post-hoc row correlation and schema diff. It imports the shared row JSON converter from `src/haute/_json_safe.py` (owned by [json-shredding](../json-shredding/low-level.md)) imported locally as `_jsonify_row` rather than owning a second converter; the module owns `SchemaDiff` computation, dtype-robust Polars match-expression construction (`_typed_value_match_expr`), exact/relaxed row matching with ambiguity diagnostics, edge-join provenance-aware parent-row projection, per-frame row matching (`_match_parent_row`, shared by the single-frame and multi-frame paths), multi-frame per-edge parent resolution (`_resolve_multi_frame_parent`), and the backward-walk driver `_correlate_rows_posthoc`. |
| `src/haute/_python_syntax.py` | Cross-component dependency owned by [codegen](../codegen/low-level.md): tracing consumes its LibCST-derived exact method-call names and positions; it does not mutate trace source. |
| `src/haute/_trace_enrichment.py` | Node-type enrichers (`enrich_rating_step`, `enrich_banding`, `enrich_model_score`, `enrich_scenario_expansion`, `enrich_live_switch`, `enrich_optimiser_apply`), canonical instance-aware code selection (`_effective_node_code`), row-lineage-type classification (`detect_row_lineage_type`, from node type and operation alone), and the per-step dispatch walk (`enrich_steps`) that drives expression parsing/evaluation (with a pre-assignment-value guard for self-referential columns), intra-node chain analysis, recursive upstream input-source derivation, rename detection, and node-type dispatch for every `TraceStep`. |
| `src/haute/_trace_waterfall.py` | Waterfall assembly for sequential multiplicative/additive rating chains. `WaterfallEntry`/`WaterfallResult` dataclasses, the value-derived `build_waterfall_from_steps()` traced-path driver, and the C8 arithmetic-reconciliation guards. |

## Key types and data structures

- **`TraceStep`** (`trace.py`, dataclass) — one node's contribution: `node_id`,
  `node_name`, `node_type`, `schema_diff: SchemaDiff`, `input_values` /
  `output_values` (column → value dicts), `topological_rank`,
  `column_relevant: bool` (default `True`), and enrichment fields populated by
  `_enrich_steps`: `expression`, `calculation`, `node_detail`,
  `row_lineage_type`. `input_row`/`output_row` hold the same rows as one-row frames in the
  pipeline's dtypes, for evaluating the step's formulas. They are not serialised. `_TypedRows`
  slices them from the frames correlation read (`_correlate_rows_posthoc` reports each resolved
  row's position through `row_positions`); a multi-frame node's row is the one frame its consumer
  reads. A slice is kept only when its JSON-safe form equals the row the trace shows, and
  otherwise the field is `None`. The input row joins the parents' rows with the same
  `f"{pid}.{k}"` namespacing as `input_values`.
- **Node-detail contracts** — enrichment consumes the same node config shape as
  execution: banding uses `factors`, model score uses `output_column`, and scenario
  expansion uses `column_name`. The emitted detail discriminators are
  `rating_step`, `banding`, `model_score`, `scenario_expander`, `live_switch`, and
  `optimiser_apply`. Rating detail is carried by `tables`/`combined_outputs`;
  each banding factor uses `input_column`, `output_column`, `input_value`, and
  `matched_band` (with match metadata such as bounds/status);
  scenario detail by `scenario_value`/`scenario_column`/`scenario_index`/`parameters`;
  live-switch detail by `active_branch`/`active_scenario`/`pruned_branches`.
  The frontend renders those emitted shapes directly.
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
  `pipeline_source`, and `execution_origin` (`fresh_execution` or
  `trace_cache`).
- **`SchemaDiff`** (`_trace_correlation.py`, dataclass) — `columns_added`,
  `columns_removed`, `columns_modified`, `columns_passed`, each a `list[str]`.
- **`_RowMatchResult`** (`_trace_correlation.py`, frozen dataclass) — one
  vectorised correlation outcome with status `no_match`, `unique_strict`,
  `unique_relaxed`, `ambiguous`, or `unsupported_dtype`; strict/effective key
  columns; relaxation/unsupported reason; aligned dtypes; exact
  `candidate_count`; at most 16 ascending physical `candidate_indices`; and
  `candidate_indices_state` (`available`, `truncated`, or `unavailable`).
  Supported results use `available` exactly through 16 candidates and
  `truncated` above 16. Unsupported results carry null count, no indices, and
  `unavailable`.
- **`CorrelationWork`** (`_trace_correlation.py`, slots dataclass) — one
  request-scoped, constant-space work recorder. `candidate_frames_considered`
  counts parent DataFrames offered to `_match_parent_row`; `match_scans` counts
  vectorised parent-match attempts; `rows_scanned` is the sum of frame heights
  presented to those attempts; `key_columns_scanned` is the sum of key widths;
  `comparison_cells` is the sum of `frame height × key width`; and
  `ambiguity_count` counts ambiguous row outcomes and unresolved source-frame
  ties. It stores no row values, keys, or per-candidate collections.
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
  `LRUCache[str, dict[str, Any]]`; each value contains `eager_outputs`, `order`,
  `parents_of`, `node_map`, and `source_ids`. `TRACE_CACHE_MAX_BYTES` defaults to
  `PREVIEW_CACHE_MAX_BYTES` (see [caching](../caching/high-level.md)), overridable
  via `HAUTE_TRACE_CACHE_MAX_BYTES`. Both values are parsed into module constants
  at import time by canonical fail-fast `_env.int_env`; a malformed, zero, or
  negative explicit value raises `RuntimeError`, while absence uses the documented
  default. The cache is bounded by both entry count and
  retained bytes; sizing reuses `_estimate_preview_cache_entry_bytes` from
  `haute.executor`, and only the `eager_outputs` slot is treated as
  byte-size-sensitive. `eager_outputs` values are `pl.DataFrame | dict[str,
  pl.DataFrame]` — a multi-frame source (e.g. a ≥2-table `apiInput`) stores its
  per-table frames as a `dict[label, DataFrame]` rather than a single
  `DataFrame`, and every call site that indexes `eager_outputs` (correlation,
  enrichment, row-count/dtype lookups, the target-row-identity check) must
  branch on `isinstance(..., dict)` rather than assume a bare `DataFrame`.
- **`edge_metadata`** (`trace.py`, built in `execute_trace` and passed to
  `_correlate_rows_posthoc`) — `dict[tuple[source_id, target_id], list[tuple[str | None, str | None]]]`,
  mapping each source/target node pair to a list of `(sourceHandle, targetHandle)` tuples per edge,
  in edge order. The target handle is load-bearing in correlation for selecting edge-join roles.
- **`source_frames_of`** (`trace.py`, built in `execute_trace` and passed to
  `enrich_steps`) — `dict[tuple[source_id, target_id], list[str | None]]`, a handle-only projection
  of `edge_metadata` retaining each edge's `sourceHandle`. Lets enrichment resolve which frame(s)
  of a multi-frame parent a given child edge actually consumes, mirroring `_pick_source_frame`.
- **`_ROW_REORDERING_METHODS`** (`_trace_correlation.py`) — an exact closed set of
  method-call names (`sort`, `sort_by`, `join`, `group_by`, `gather`, `unique`,
  `pivot`, etc.) compared with LibCST `MethodCallSite` results. Comments, strings,
  longer attribute names, and an attribute that is not called are not operations.
- **`_OPERATION_TYPE_TABLE`** (`_trace_enrichment.py`) — an ordered
  `(substrings, label)` table for sniffing a node's row-lineage operation from
  its code string; `cross_join` is checked before `join` so a cross join is never
  mislabelled.
- **`_effective_node_code(config, node_map)`** (`_trace_enrichment.py`) — the
  single code-selection rule used for the current step, recursive input-source
  enrichment, and upstream-creator enrichment. Ordinary nodes use their own
  code. An `instanceOf` node uses the referenced original's code when the
  reference resolves, except that instance-local code containing
  `.with_columns(` is authoritative. A missing reference leaves the local code
  unchanged; an existing original with no code resolves to the empty string.

## Control flow

### `execute_trace()` (`trace.py`)

**The preview's seed plan.** `execute_trace(..., seed_plan)` takes the `ListedSeed`
entries of the preview it explains. Empty or `None`, the trace runs exactly as below with
no plan. Otherwise `_open_trace_seed_plan` opens `open_listed_seed_plan` for the target —
every listed generation checked against the signature the graph produces at its point and
leased (`SeedPlanExpiredError` otherwise) — prepares only the snapshot-backed inputs the
plan executes, and opens it again when that preparation changed anything. A plan that
seeds nothing (every listed generation lacking columns the trace reads, or dropped with
one) is closed and the trace runs with no plan; otherwise `_execute_trace_core` runs under
it for the whole trace. Under a plan: input preparation is the plan's; strategy
planning admits and estimates only what the plan builds, estimating from its seeds'
generations (`_planned_strategy_scope`), so cached work that could not be admitted if
recomputed never is; the cache key carries the seeded generations
(`_seeded_fingerprint`); `_materialize_eager_outputs` and `_build_trace_plans` execute
under the plan, so nodes above a seed are never built and a seeded point's frame and
uncapped plan are its generation; a seeded point has no parents for the rest of the
trace and is a source (`_trace_source_ids`), so correlation, steps, and relevance stop
there and an ancestor shared with an executed branch is correlated through that branch;
each seeded step carries `snapshot_generation_id` and stays in the step list as the end
of downstream provenance, but it has no input row, so enrichment never reconstructs its
expression or calculation from its own output, an input source traced to it reports the
value it held there with its `snapshot_generation_id` and nothing above it, and a
pass-through target whose value is followed back to it borrows no formula; steps and
omissions are ranked by position in the whole lineage; and every lineage node skipped because of a seed
(`_snapshot_seed_skips`) is an omission with reason `snapshot_seed`, linked to a
correlation diagnostic (`code`/`reason` `snapshot_seed`, severity `info`) whose
`seed_node_ids` names the seeds below it. It never ran, so no schema says whether it
bears on a traced column: it is always reported, never pruned for column relevance.

1. Validate `nodes` non-empty; resolve `target_node_id` (defaults to the last
   node in topological order, computed from the node list in *declared* order —
   not a set — so the tie-break is deterministic across process invocations,
   independent of CPython hash randomisation).
2. Call `execution_facade.preview_lineage_cache_key(...)` with target, source,
   row limit, `requested_columns=None`, `initial_column_limit=None`,
   `port_label=None`, `enforce_contracts=True`, and
   `materialisation_scope="full"`, memoised via a `GraphFingerprintMemo` — the
   caller's, if one was passed in `fingerprint_memo` (the trace route reuses the
   memo it already built for its supersession key), otherwise a fresh one scoped
   to this call.
3. Prepare the target's lineage (`prepare_graph`), classify every physical lineage
   edge with `_trace_lineage_alignments` (which passes the graph preamble's
   `polars.selectors` aliases to `trace_edge_alignment`), and compute each head-framed
   node's prefix length with `trace_head_prefixes` (see
   [Limited-preview lineage](#limited-preview-lineage-tracecorrelationpy) below).
   On a trace-cache hit (`_cache.get(fp)`), reuse the cached head frames
   (`eager_outputs`) and `order`/`parents_of`/`node_map`/`source_ids`. On a miss, call
   `_materialize_eager_outputs()` (below) and store the result under `fp`. Uncapped
   lineage plans are never cached: they hold Python scans bound to this request's
   execution context, so `_build_trace_plans` builds them at most once per request, and
   only when a row-scoped lookup or an enrichment needs them.
4. Read the target output with `.get()`. If it is a `dict` (a multi-frame source
   targeted directly, with no downstream node to pick a frame), raise
   `ValueError` naming the node and directing the caller to trace a node
   downstream of a specific frame instead. If it is absent because execution
   produced only a partial result, retain the documented partial-trace path
   below rather than indexing the missing key.
5. Build `source_frames_of`: for every edge in the graph, append its
   `sourceHandle` to the list keyed by `(edge.source, edge.target)`, preserving
   edge order. One entry per edge, not per pair — a multi-frame source can feed
   the same child through several edges.
6. Build a `RowScopeResolver` over a click-local copy of the head frames (row-scoped
   lookups are written only there, never to cached head frames). If the caller supplied
   `row_values` and the target DataFrame exists, verify the row at `row_index` with
   `_match_rows_vectorized`. On mismatch, search via `_find_target_row_index` in the
   limited target frame; when the clicked values are absent from it, look the row up in
   the target's uncapped plan (`_lookup_clicked_row`), use that lookup as the target frame,
   and clear the resolver's head-resolved set so no ancestor is proven by a head frame. No
   match or ambiguity raises `ValueError`, while an unsupported target dtype raises
   `TraceCorrelationUnsupportedError`. A missing target skips verification and continues
   through the partial-result path.
7. If the target node produced output, call `_correlate_rows_posthoc()` (below,
   passed the click-local frames, `source_frames_of`, `column` as `traced_column`, and the
   resolver as `row_scope`) to get a JSON-safe row dict per node (or `None` for unresolved
   nodes). If the target node's
   execution failed, build partial rows directly from whatever `eager_outputs`
   are available instead (no correlation), skipping any node whose output is a
   multi-frame `dict`.
8. `_assemble_steps()` builds `TraceStep`s from the correlated rows: source nodes
   get `input_values = {}`; other nodes' `input_values` merge each parent's
   correlated row (namespacing every parent's copy as `f"{pid}.{k}"` whenever
   more than one parent supplies the key). Nodes whose row correlation returned
   `None` are skipped entirely.
9. `enrich_steps()` (from `src/haute/_trace_enrichment.py`, imported into `trace.py` as
   `_enrich_steps`, passed `source_frames_of`, `incoming_edges_of`, and the request's
   `lineage_plans` provider) enriches every step in place. An online optimiser apply's
   explanation reads its inputs from the uncapped plans — a quote's full scenario set and a
   ratio constraint's whole-frame baseline lie outside the head rows — while a
   ratebook-mode apply, which is row-local, explains from the frames that produced the
   clicked row.
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

It runs only on a trace-cache miss; trace never reads the preview cache.

1. Compile the preamble, merge in any caller-supplied `preamble_ns`
   (caller-supplied keys win, for test convenience), and call
   a display walk (`walk_graph`, execution-engine) that raises node failures, collects
   the head-framed nodes (`CollectPolicy.display(collect=..., row_limits_by_node=...)`)
   each to its prefix, so each head frame is its node's plan limited to its own prefix;
   the walk's uncapped plans (`WalkResult.frames`) are returned for this request. A genuine execution failure
   propagates unmodified rather than being retried or masked.

### Limited-preview lineage (`_trace_correlation.py`)

A preview limits the previewed node's output, not its sources, so trace follows the rows
the limited preview shows rather than independent source samples.

1. **Edge classification.** `trace_edge_alignment` classifies each physical edge —
   parent, child, source handle, and target role — separately into a `TraceEdgeAlignment`.
   An edge is order-aligned when its child emits at least one row per input row in input
   order: the base edge of a left edge join whose `maintainOrder` is `left` or
   `left_right`; model scoring, banding, rating steps, and scenario expansion without
   post-code; the selected live-switch input; Explore and external-file nodes without
   code, data output, and modelling; the selected data input of an optimiser; the selected
   ratebook input of a ratebook-mode optimiser apply; and single-input Polars or Explore
   code that the chunk-locality classifier (`classify_chunk_local_polars_code`, given the
   preamble's selector aliases) admits and that calls no row-dropping frame method
   (`filter`, `drop_nulls`), or whose whole program is one `head(k)` or `limit(k)` call on
   its input (`prefix_cap = k`). Chunk-local code evaluates every row from that row alone,
   so an order-dependent expression such as `pl.col("x").reverse()` makes the edge
   unaligned. Every other edge is not aligned. A port the child never reads is not row
   lineage (`read` is false) and is not correlated: an optimiser apply reads its
   `ratebook_input` when its resolved `optimiser_mode` is `ratebook`, its first input when
   it is `online`, and both while the mode is unknown. Input names are keyed by physical
   edge, so two ports of one source keep their own executable names. A Polars transform's
   `inputMapping` (`logical name -> current edge name`) lets its code address an input by
   a stable logical name; the executor binds both names, so `_trace_lineage_alignments`
   returns each child's logical-name aliases and both edge alignment and the carried-column
   proof read the code under both names.
2. **Head frames.** `trace_head_prefixes` gives the target prefix length `row_limit`. A
   parent is head-framed when an aligned edge reaches it from a head-framed child; its
   prefix length is the largest requirement among those children, each reduced to
   `min(prefix, k)` through a `head(k)`/`limit(k)` program, so no head frame reads rows the
   preview did not compute.
3. **Resolution (`RowScopeResolver.resolve`).** Every port a child reads is resolved
   separately. A port is matched in the parent's head frame, with `_match_parent_row`'s
   positional and value matching, when its edge is aligned, the parent is head-framed, and
   the child's row came from the child's own head frame; the clicked target row starts
   head-resolved. Every other port correlates in a row-scoped frame (`lookup`): the
   parent's uncapped plan filtered by typed equality on the resolved child row's carried
   columns and limited to two rows, matched strictly with relaxed matching disabled; two
   surviving rows are ambiguous. Lookups are memoised per request by node, port, and
   carried values; the resolver also reads each lineage plan's schema at most once per
   request. A lookup probes by key first, because filtering an uncapped plan on every
   carried column makes Polars decode each of those columns across the whole input
   (measured: 108 equalities over a 10-million-row input take seconds, a key equality
   milliseconds). The probe columns are the carried columns that key an Edge Join on the
   traced lineage — the child of an edge the trace correlates across — in either role
   (`edge_join_key_columns_by_role`), and whose carried value is not null. Joins outside
   that lineage are never read, so an unfinished join elsewhere in the graph cannot affect
   a trace. With at least one, the lookup first collects the plan filtered on
   those typed equalities, limited to `_ROW_SCOPE_PROBE_LIMIT + 1` (1,001) rows, then
   applies every carried equality to those candidates in memory and keeps two rows. A
   probe returning more than `_ROW_SCOPE_PROBE_LIMIT` rows cannot show it saw every
   candidate, so the lookup then filters the plan on every carried equality, as does a
   lookup with no probe column. Both paths use the same typed equality expressions and
   return the same rows, so matching, ambiguity, and memoisation are unchanged. Lookup
   filters use the bare comparisons, never null-filled ones: a filter drops a row whose
   comparison is null either way, and a null-filled comparison stops Polars pruning Parquet
   row groups by their statistics (a key probe on a 10-million-row file: 1.8 s null-filled,
   under 0.01 s bare).
   A child that already proves its parent's row skips the parent's lookup (row transfer).
   The child's row must be unique in the child's uncapped plan — a lookup, or the clicked
   row's lookup (`_lookup_clicked_row`), that returned exactly one row, or itself a transfer
   — and the child must derive its rows from each parent row's values alone: a pass-through
   node type whose only traced lineage input is this parent (a pass-through node with several
   inputs returns just one of them, so its row proves nothing about the others), or an Edge
   Join's `base` port with `how` of `left`, `inner`, `semi`, `anti`, or `cross`. The child must configure no `column_renames`, the parent must have a
   single-frame plan, and every parent plan column must appear in the child's one-row frame
   with the same dtype. Two identical parent rows would then yield two identical matching
   child rows, so a unique child row proves exactly one parent row, and that row is the
   child's frame restricted to the parent's columns — the frame the lookup would return. A
   transferred parent is itself unique and not head-resolved. Every other edge still looks
   up: Polars code, whose slices, deduplication, aggregation, and order-dependent
   expressions break the argument; builder nodes; an Edge Join's `join` port or `right`
   and `full` strategies, whose rows need not come from a base row; multi-frame ports; and
   a child row resolved from a head frame or not proven unique. On the measured pipeline
   this removes the lookup above a user `.limit(100_000)` (about 2 s). A parent resolved through any lookup is not head-resolved, so its own
   parents are looked up. Among several matching ports, a frame carrying the traced column
   wins, then the widest carried match, and a tie records `ambiguous_source_frame`. An
   unresolved port records an empty frame with its schema so column relevance keeps its
   omission; with no carried column the diagnostic reason is `row_scope_unproven`.
   `_correlate_rows_posthoc` does not correlate a parent through a child that reads none of
   its ports.
4. **Carried columns (`_carried_values`).** Join-role parents use the edge-join
   right-provenance mapping; base-role parents use the child columns present in the base;
   pass-through node types (including OUTPUT documents and the optimiser) use every shared
   column; node types with column contracts use the shared columns minus the builder's
   produced columns. Code (Polars, Explore, external file, and builder post-code) must pass
   `carried_column_proof` in `src/haute/_column_lineage.py`, given each port plan's column
   names and dtypes (builder post-code, which runs on the builder's own output, gets none),
   the preamble's selector aliases, and the child's `inputMapping` aliases, which the proof
   resolves to the edge input names it reports (`input_aliases`). The proof is closed and syntactic over
   `df = <chain>` statements rooted at one input:
   - `filter`, `sort`, `head`, `tail`, `limit`, `slice`, `unique`, `drop`, `drop_nulls`,
     and `lazy` keep values;
   - `with_columns` and `select` assign every keyword output and every output that is not a
     bare column; a pure column selection (a literal selector or a literal name list)
     assigns nothing; an expression rooted at a literal selector whose only naming step is
     its outermost call assigns that call's `alias`, or the selector's expansion (renamed
     by a trailing `.name.suffix`/`.name.prefix`) over the program's proven column superset
     — every input's columns, every column assigned so far, and for each join every
     right-side column with and without the join's literal `suffix` (default `_right`).
     Because a name-based selector decides each column by its own name, expanding over that
     superset assigns every column the expression can rewrite. A dtype-dependent rooted
     selector expands only against the root input's dtypes before any assigning operation
     or join; a positional rooted selector, a naming step deeper in the chain, a
     non-literal join suffix, or missing input schemas fail the proof;
   - `with_row_index` assigns its name; a literal `rename` assigns both names;
   - `join` or `cross_join` of another input or an inline literal frame (whose columns are
     assigned) with `how` of `inner`, `left`, `semi`, `anti`, or `cross` records its literal
     `on` keys as the same-name keys of that joined input; a non-root input joined twice
     fails the proof;
   - a literal `group_by(...).agg(...)` carries only its keys;
   - an output whose name the syntax cannot fix — a regex or wildcard column outside a
     selector computation, a non-literal selector, `.name` or `.struct` rewrites, `pipe`,
     or an unaliased `when`/`then` — and every other method or statement fail the proof.

   The proven assignments are removed from the carried columns. A column several inputs
   share identifies only the program's root input, unless the join that brought in this
   port's input proves it a same-name key of that input, and a non-root input's null value
   is not carried because it may be an outer fill. Every remaining shared column is a
   strict equality constraint, so a rewrite the proof cannot see matches no parent row
   rather than a wrong one.

### `_match_parent_row()` (`_trace_correlation.py`)

Correlates ONE candidate frame (a bare parent `DataFrame`, or one frame of a
multi-frame parent) against the resolved child row. Factored out of
`_correlate_rows_posthoc` so the single-frame and multi-frame paths share
identical matching logic:

1. Project the child's row onto the parent's columns (`_build_parent_match_row`)
   — a generic name-based projection, except for the JOIN-role parent of an
   `edgeJoin` child, which is routed through `_edge_join_right_match_row` (see
   Edge cases below). If that join's BASE parent is a multi-frame bundle,
   `_build_parent_match_row` resolves the frame(s) named by
   `source_frames_of[(base_id, child_id)]` before deriving the left-column set;
   no bare source `dict` is treated as a DataFrame.
2. **Fast path**: if the parent and child DataFrames have equal row counts and
   the child's row index is in range, try the parent row at that same
   positional index. Trust it only if (a) there are no shared columns and
   either the parent has exactly one row or the child's parsed structured call
   sites contain no known reorder operation (`_child_transform_may_reorder`
   returns `False`); or (b)
   there are shared columns, they all value-match, and either the child cannot
   reorder or `_match_rows_vectorized` over the full parent identifies exactly
   the expected physical row. There is no second uniqueness comparator or
   private fast-path truth table.
3. **Value-matching fallback**: `_find_matching_row()` tries an exact match on
   all shared columns first; on no exact match (and `allow_relaxed=True`), it
   scores every row by how many shared columns match and picks the widest
   relaxed subset — but only if exactly one row achieves that width. Any tie
   (multiple exact or multiple best-relaxed matches) is recorded via
   `_record_ambiguous_row_match` (with reason `"duplicate_exact_match"` or
   `"relaxed_match_ambiguous"`) into `diagnostics` and returns `(None, -1)`.
   `allow_relaxed` is forced to `False` for an edge-join's JOIN-role parent
   (`_allows_relaxed_parent_match`) — a
   relaxed miss there must not manufacture false lineage.
4. Returns `(row_dict, positional_index, match_width)`, where `match_width` is
   the number of child columns projected onto this frame — the specificity a
   multi-frame resolution uses to rank competing candidate frames.

### `_resolve_multi_frame_parent()` (`_trace_correlation.py`)

Correlates a multi-frame parent (`dict[label, DataFrame]`) row for one child.
Called once per (parent, child) pair from `_correlate_rows_posthoc`, with
`edges` set to `(edge_metadata or {}).get((parent_id, child_id))` — a sequence
of `(sourceHandle, targetHandle)` pairs per edge between that pair, in edge order:

1. Appends one candidate per physical edge (duplicates allowed, without deduplicating
   handles) for non-`None` handles whose frame exists in the parent's `dict` and is
   non-empty as `candidates`. No candidates → record an `unresolved_source_frame`
   diagnostic and return `(None, -1)`.
2. Exactly one candidate → delegate straight to `_match_parent_row` on that
   frame.
3. Several candidates → call `_match_parent_row` on *each* independently
   (diagnostics suppressed per-candidate to avoid noise), keeping only frames
   that produced a confident row match. Duplicate-handle edges are compared
   independently. No frame matches → record `unresolved_source_frame` and
   return `(None, -1)`.
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
3. With a `row_scope` resolver (every `execute_trace` call), walk `order` in reverse and
   resolve each node through `RowScopeResolver.resolve` from a resolved child that reads
   one of its ports, preferring a child whose edge can use head frames; a node with no such
   child is not on the path to the target. Without a resolver (direct callers of the
   function), walk `order` in reverse. For each unresolved node with a materialized output:
   - Find a child of that node already resolved with a non-empty row
     (`resolved_child_id`); if none exists, the node is not on the path to the
     target and is marked unresolved (`None`, index `-1`).
   - If the parent's output (`eager_outputs[nid]`) is a `dict` (a multi-frame
     source), delegate to `_resolve_multi_frame_parent()` (above) with
     `edges=(edge_metadata or {}).get((nid, resolved_child_id))` and the caller's
     `traced_column`, and continue to the next node.
   - Otherwise, delegate to `_match_parent_row()` (above) on the bare
     `DataFrame`.
4. Return the per-node row dict (or `None`) map.

The caller creates one `CorrelationWork` recorder and passes it through the
walk. `execute_trace()` measures only this correlation phase and emits one
`trace_correlation_completed` structured log containing the recorder fields,
`execution_origin`, and `duration_ms`. The counters are operational telemetry;
they are deliberately not added to the trace response contract.

The reproducible performance corpus covers single-frame linear, multi-frame,
join, and reordered/ambiguous typed-value workloads at 10,000 rows, alongside
the existing cold and trace-cache paths. On the repository's ordinary
CPU performance runner, each representative correlation must remain below
500 ms and within all of these deterministic work ceilings: at most 8 candidate
frames, 16 vectorised match scans, 160,000 scanned rows, 128 scanned key-column
slots, and 1,280,000 comparison cells. These are regression ceilings, not a
target to consume. Null and non-finite keys retain their typed semantics and
ambiguity remains unresolved. Evidence below every ceiling retires the proposed
sidecar/index: an index is introduced only after a representative workload
crosses a ceiling and demonstrates parity plus bounded eviction with its owning
execution cache.

### `enrich_steps()` (`_trace_enrichment.py`)

Iterates every `TraceStep` (each step wrapped in its own outer `try`/`except` so
one step's unforeseen failure cannot abort the whole enrichment pass) and:

Expression parser and node-type enricher dependencies are direct module
references. Tests patch `_trace_enrichment` itself; no dispatcher reads
`sys.modules["haute.trace"]`, so enrichment has no import-order dependency on
its public facade.

1. Resolves the node's code through `_effective_node_code`; the same helper is
   used when deriving recursive input sources and when borrowing an upstream
   creator's expression, so all three enrichment paths follow the identical
   `instanceOf`/`.with_columns(` rule.
2. Parses/evaluates the expression for `column` when the column is
   added/modified at this step, or (for the *target* step only) borrows the
   expression/calculation of the step a pass-through value came from.
   `_pass_through_origin` follows the target's value back one step at a time
   through the single parent that holds `column` with that same value, to the
   step that added or last modified it. When no parent or more than one does —
   a join whose sides both hold the column — or a parent missing from the steps
   might (its materialised frame, when there is one, has the column), or the
   value reached a seeded step, the origin is unproven and nothing is borrowed:
   another branch's formula would explain a value the target never had.
   **Call-phase rule** (`_assignment_values`/`_assignment_row`, for the step's
   own assignment, a borrowed one, an input source's derivation and each chain
   entry): `assignment_phases(code, column)` splits the columns the node's
   `with_columns` calls assign into those assigned before the call that last
   assigns `column` and those assigned by it or a later call. A column in the
   second set is read at its value from before that call: its `input_values`
   entry when no earlier call assigned it, and otherwise left out as unknown.
   When the phases are `unresolved`, a write with no static name may have
   rewritten any column, even one whose value it left equal while changing its
   dtype, so every column is treated as part of that second set: only input
   columns no earlier call assigned remain. When an earlier call holds such a
   write (`unresolved_before`), no input value is provably the one the call reads,
   so every column in that second set is left out; columns neither the call nor a
   later one touches keep their output value, which nothing after changes.
   **Self-referential guard** (the rule's special case for the target column):
   if `column` is both
   `columns_modified` (per the step's `SchemaDiff`) and one of the parsed
   expression's own `referenced_columns` (e.g. `premium = premium * factor`),
   evaluating against `{**input_values, **output_values}` unmodified would seed
   the RHS's `premium` with the *post-assignment* output — producing an
   arithmetically false substitution (`200.0 * 2.0` displayed for an output of
   `200.0`). When a pre-assignment `input_values[column]` exists, it overrides
   the output value in the evaluation namespace before calling
   `evaluate_expression`, and `_assignment_row` makes the same substitution in the typed row
   passed as `row=` (a frame holding no row when the typed row is unavailable, which the
   evaluator reports as `traced_row_unavailable`). When it doesn't (the column was newly created this
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
   sees the correct fed-forward intermediate; the typed chain row is fed forward
   the same way, with a not-computed entry's column dropped so a later entry reading it reports
   the column unavailable. A failing entry's fallback
   `result_value` prefers the fed-forward `combined_values` entry and falls back
   to `step.output_values` only if that is also absent.
   **Execution values** (`_with_execution_value`): when a formula that assigns a step's column is
   `not_row_local`, the calculation shows that column's `output_values` entry with
   `result_source: "trace_execution"` and keeps `not_computable_reason`. This applies to the
   step's own assignment, a borrowed one, an input source's derivation, and a chain entry whose
   target the chain assigns once. `execute_trace` passes enrichment the compiled preamble
   namespace (cached per process) over any caller-supplied `preamble_ns`, so formulas resolve the
   names the node code ran with.
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
   using the matched factor. Building `factor_input_dtypes` for
   `enrich_rating_step` and `enrich_banding` walks each parent's materialized
   output; when a parent is a multi-frame `dict`,
   it is scoped to the frame(s) named by `source_frames_of.get((pid,
   step.node_id))` (falling back to every frame if no handle is recorded) rather
   than merged across every frame the source emits — a column name recurring
   across frames with a different dtype must resolve in the *consumed* frame's
   dtype, not by dict-iteration order over frames the node never sees. Rating
   entry and input-row keys are both canonicalised through that exact dtype via
   `normalise_rating_key(value, dtype)`. A real rating table with entries cannot
   fall back to Python-scalar dtype inference: missing factor dtype resolution
   raises inside enrichment and is surfaced through the existing structured
   node-enrichment error boundary. Continuous banding rules are filtered through
   rating's shared usable-condition parser before comparison, so a rule the
   runtime skipped for having no usable operator/value pair cannot be selected
   by enrichment.
7. Classifies `step.row_lineage_type` via `detect_row_lineage_type()`, using the
   node type first (source nodes and constants → `"created"`, `liveSwitch` →
   `"selected"`, `edgeJoin` → `"joined"`, `scenarioExpander` → `"expanded"`,
   `optimiserApply` → `"aggregated"` — checked before any code-sniffing because
   config-driven nodes carry no literal operation), then a code-sniffed
   `operation_type` (`_sniff_operation_type`), falling back to `"passthrough"`.
   Trace frames are limited or row-scoped, so frame heights never decide a label.

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
   (`_step_targets_column` parses `with_columns(...)` assignments with the
   Python AST; comments and `==` comparisons are not assignments — an identity
   factor like `×1.0` would otherwise vanish as a `"passed"` schema-diff entry),
   contributes an entry via
   `_classify_contribution()`.
5. `_classify_contribution()` computes `delta = value_after - value_before` and,
   when `value_before` is positive, `value_after` is non-negative, and the implied
   factor is finite, uses `value_after / value_before` for
   multiplicative display; otherwise it falls back to additive (delta-only)
   display and logs a WARNING (`waterfall_implied_factor_undefined` /
   `waterfall_sign_change`). `_operation_hint()` (AST-based, not substring
   matching) can still force additive display when the node's own expression's
   top-level operator is `+`/`-`.
6. `build_waterfall()` is called with the internally assembled step dicts, each carrying an observed
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

- **One vectorised value contract serves every correlation call site.**
  `_match_rows_vectorized` is used for target relocation, ordinary parents,
  edge-join parents, and multi-frame candidates; shared keys never fall back to
  a Python full-frame scan. Null matches only null, Boolean only the same
  Boolean, integer/integer is exact, and finite float comparison uses
  `abs(a-b) <= max(1e-12, 1e-9 * max(abs(a), abs(b)))`. Integer/float comparison
  is allowed only through the JavaScript-safe boundary; an unsafe integer may
  match only its exact canonical decimal string.
- **Temporal, decimal, categorical, and nested matching stays typed.** Date,
  Time, Datetime, and Duration compare through checked integer temporal
  representations with timezone-awareness preserved; Decimal uses exact
  lossless rescaling and never floats; Categorical/Enum compares normalised
  string values; compatible List/Array/Struct uses a typed one-row Polars
  literal. Object and incompatible nested schemas are unsupported.
- **Ambiguity and relaxation are explicit.** Multiple candidates never select
  the first row. Relaxation is attempted only after zero strict candidates,
  may omit keys but cannot weaken a retained key's value contract, and emits a
  low-confidence diagnostic naming the strict/effective keys and exact bounded
  candidate evidence.
- **Non-finite and oversized values never silently coerce.**
  `_typed_value_match_expr` treats `nan`/`inf`/`-inf` as first-class tokens
  (via `non_finite_float_token`/`_value_non_finite_token`) so a NaN can match a
  NaN from JSON without a bare `==` false negative. `_as_trace_waterfall_float`
  explicitly raises `WaterfallUnavailableError` for an integer (or a
  JSON-safe-integer-marked string) outside JavaScript's exact integer range,
  rather than silently truncating precision into a rendered number.
- **`_typed_value_match_expr` is dtype-robust.** Comparing a numeric trace value
  against a non-numeric column, or a non-finite float against a non-`Float`
  column, would otherwise raise `ComputeError`/`InvalidOperationError` at collect
  time inside Polars and crash the whole correlation walk. The typed matcher
  returns either the exact predicate or an explicit unsupported reason; the
  correlation result records that state rather than coercing across dtype
  families.
- **String/numeric dtype mismatches do not fall back to stringwise equality.**
  Except for the exact canonical unsafe-integer/string rule, incompatible
  value/dtype families return an unsupported reason or no match. Correlation
  never broadens them by casting both sides to strings.
- **Positional alignment is gated, never assumed from row-count equality alone.**
  Equal row counts between parent and child is necessary but not sufficient — a
  row-reordering transform (sort/sort_by/join/group_by/gather/sample/shuffle/
  unique/top_k/bottom_k/explode/pivot/cross_join, per
  `_ROW_REORDERING_METHODS`) can
  produce the same row count as its input while permuting rows. When the code
  is absent or cannot be parsed by the structured syntax boundary,
  `_child_transform_may_reorder` conservatively assumes it *can* reorder —
  failing loud (unresolved step) over guessing. It never falls back to a
  substring classification after a parse failure.
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
  A multi-frame BASE parent is narrowed through the edge's named source
  handle(s) before these rules inspect its columns. No selectable frame raises a
  message-bearing `ValueError` naming the edge join and base parent.
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
- **The trace cache fingerprint includes the canonical dataframe graph input fingerprint** so
  an out-of-band re-export of a file-backed `dataInput`/`externalFile`, a
  model-artifact signature change, or a rebuilt `apiInput` table snapshot
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
| `RuntimeError` | Module import — malformed/non-positive `HAUTE_PREVIEW_CACHE_MAX_BYTES` or `HAUTE_TRACE_CACHE_MAX_BYTES` | Importing caller; cache construction does not start |
| `ContractMismatchError` | Propagated unchanged from the display walk (execution-engine) on a cold-execution contract violation | HTTP route, mapped to 422 |
| `TraceCorrelationUnsupportedError` (`ExecutionError`) | `_find_target_row_index` — selected target keys use an unsupported dtype/value comparison | HTTP 422 / background `contract_error`; stable code and node/key/dtype/reason fields |
| Any exception from cold execution (a display walk that raises node failures) | Propagated unchanged — no regex-based masking/retry | HTTP route, mapped to 500 (or a specific status if it happens to be one of the recognised `ValueError` shapes) |
| `WaterfallReconciliationError` (`ValueError` subclass) | `_check_display_consistency`, the final-cumulative reconciliation check in `build_waterfall_from_steps` | Caught inside `build_waterfall_from_steps`; converted to `{"error": ..., "error_type": "WaterfallReconciliationError"}` in `TraceResult.waterfall` |
| `WaterfallUnavailableError` (`ValueError` subclass) | `_ensure_single_column_lineage`, `_reject_renamed_join_branch_origins`, `_as_trace_waterfall_float` (unsafe integer) | Same as above — converted to a structured error dict, not raised |
| Any other exception during waterfall assembly | — | Caught, logged (`waterfall_build_failed`, WARNING, `exc_info=True`), converted to `{"error": ..., "error_type": ...}` |
| Any exception during a single enrichment concern (expression parse/eval, chain, input sources, rename detection, node-type enrichment, row-lineage detection) | `_trace_enrichment.py`, per-concern `try`/`except` | Caught, logged at WARNING with `exc_info=True`, surfaced as an `error`/`error_type` key on the relevant field (`expression`, `calculation`, `node_detail`, or the `row_lineage_type` string itself as `"error: ..."`) — never re-raised |
| Any exception escaping an entire step's enrichment (outer catch-all in `enrich_steps`) | `_trace_enrichment.py` | Caught, logged (`trace_enrichment_step_failed`), an `error` marker is set on `step.node_detail` if not already present, and the loop continues to the next step |
| `ValueError` (edge-join misconfiguration) | `_build_parent_match_row` — an `edgeJoin` parent edge has no valid `base`/`join` target role, or the role topology is ambiguous | Propagates unchanged out of `_correlate_rows_posthoc` |
| `ValueError` (missing/unselectable base frame) | `_build_parent_match_row` — the edge-join's base parent has no materialized output, is a multi-frame bundle with no wired/selectable frame handle, or names a missing frame while correlating the join parent | Propagates unchanged with join/base context; never leaks `AttributeError` |

## Testing

Tests live in `tests/`, one focused file per concern plus several broad
integration/regression suites:

- **`tests/test_trace_snapshot_seeding.py`** — traces over the generations their preview
  read: a trace of a seeded preview stopping at the join step read from the snapshot and
  reporting both sources as `snapshot_seed` omissions naming it; a first preview's
  capture traced back to the identical row over a shuffled source, building nothing
  above it; a diamond with one cached branch keeping the shared ancestor traceable
  through the other; a snapshot published after an unseeded preview leaving its trace
  unseeded; a refresh while another job leases the preview's generation still tracing
  it; a clear with and without another lease; a graph edit answering 409
  `preview_seed_plan_expired`; a column-projected capture recomputed; a trace reusing
  the preview entry stored under its plan; a worker's expired plan mapped to 409; a
  column trace never calculating a seeded step from its own output; downstream provenance
  ending at the seeded step with the value it held there; a pass-through target explained
  from the seed on its path rather than an executed creator on another, and never from
  the other side of a join whose sides both hold its column — explained, without seeds,
  by the join side that supplied it and by the last assignment rather than the first,
  and given no formula when both join sides hold its value; an ancestor
  correlated through the uncached branch when the cached one is ambiguous; and cached
  work that could not be admitted if recomputed traced from its snapshot.

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
  and trace calls. `TestLimitedPreviewTrace` traces rows of a target-only limited
  preview: a joined value to its lookup row with and without `maintainOrder`, a filtered
  row past the source prefix, a grouped row's source rows reported ambiguous, order-
  preserving lineage from head frames with no lookup, code below an unordered join through
  its carried columns (and `row_scope_unproven` when the carried key is rewritten), a later
  join key never identifying an earlier joined input, an order-dependent expression never
  attributed positionally, a limited rating step validating only the rows it read,
  computed and positional selectors below an unordered join, a row-local `polars.selectors`
  program traced from head frames, a selector renamed mid-expression reported
  `row_scope_unproven` instead of attributed to an unrelated row, and code that addresses
  its input through an `inputMapping` alias traced through both upstream Edge Joins.
  `tests/test_trace_multi_frame.py::test_row_scope_names_each_port_of_one_source_by_its_own_frame`
  pins per-edge input names for both edge orders.
- **`tests/test_trace_row_scope_lookup.py`** — `RowScopeResolver.lookup` probes by Edge
  Join key (either role, `on` or `leftOn`/`rightOn`) with bare comparisons and matches the
  other carried values in memory; keeps two rows sharing every value; falls back to the full
  filter when the probe reaches its limit or no non-null join key is carried (no Edge Join,
  a cross join, the key not carried, a null key); and returns exactly the full filter's rows
  across probe limits and null data.
- **`tests/test_trace_enrichment.py`** — focused enrichment coverage:
  node-type-specific enrichment (rating step, banding, model score, scenario
  expansion, live switch, data-source metadata) and row-lineage-type detection,
  exercised both via TDD stubs and through `execute_trace` on real data-flow
  patterns.
- **`tests/test_trace_calculation_hero.py`** and
  **`tests/test_trace_hero_tdd.py`** — the expression/calculation
  ("Calculation Hero") feature: conditional-branch indication, waterfall data
  generation, preamble constant resolution, full-context values for window formulas, intra-node
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
  traced; and a banding node fed by one
  frame of a multi-frame source resolves factor dtypes from that frame alone,
  not a dict-iteration-order merge across every frame the source emits; and an
  `edgeJoin` whose BASE is one named frame of a multi-frame source correlates
  its JOIN parent without treating the base bundle as a DataFrame.
- **`tests/test_trace_correlation_remediation.py`** — the typed vectorised
  matcher matrix, candidate bounds, ambiguity/unsupported diagnostics, and
  public unsupported target error contract.
- **`tests/test_trace_evidence_contract.py`** — diagnostic-linked omissions,
  conservative relevance fallback when the assigning step is unresolved, and
  evidence retention.
- **`tests/test_trace_fidelity_contract.py`** — runtime/trace agreement for
  banding and rating rules, fail-loud enrichment, and preview-value fidelity.
- **`tests/test_lineage_preview_cache.py`** — shared lineage key construction,
  runtime input invalidation, and the deliberate full-versus-target-only cache
  scope boundary.
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
  coverage of the cold trace path and the trace execution cache
  (`haute.trace._cache`) under load, plus the 10,000-row linear, join,
  multi-frame, reordered typed-value, and ambiguous correlation corpus. It
  records the production `CorrelationWork` counters and enforces the work
  ceilings above without introducing a test-only cost model.
  Enforces latency budgets: cached target preview `< 0.5s`, trace-cache hit
  `< 0.3s`. Excluded from the
  default test run by the `perf` marker (`addopts = "-m 'not perf'"` in
  `pyproject.toml`); run explicitly via
  `uv run python scripts/run_perf_suite.py --pytest-target tests/performance/test_preview_trace_perf.py`.

Correlation, waterfall, enrichment fail-loud, evidence, fidelity, and
lineage-key paths have dedicated regression files indexed above. This spec does not
itself execute the suite; treat file/class presence as an index, not a
substitute for running
`pytest tests/test_trace*.py tests/test_optimiser_apply_trace_enrichment.py`
when changing this component.
