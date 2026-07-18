# Tracing — High-Level Specification

## Purpose

When a pricing pipeline produces an output value — a premium, a factor, a model
score — a user needs to be able to answer "why is this cell this value?" without
re-deriving the pipeline by hand. The tracing component answers that question for
a single row: given a graph, a target node, a row index, and (optionally) a
specific column, it re-derives exactly what happened at every upstream node —
what the row's values were before and after each node, which columns each node
added/removed/modified/passed through, what formula or lookup produced a traced
column's value, and (for numeric multiplicative/additive chains) a waterfall
breakdown of how the value accumulated. This is the backend for the pipeline
editor's "click a cell → see its lineage" feature, and the foundation named in
the retired ARCHITECTURE doc (git history) §9.3 for regulatory explainability
(Solvency II, IFRS 17, FCA Consumer Duty) and day-to-day debugging.

The retired EXECUTION_TRACE_DESIGN doc (git history) records the original
design discussion and rationale for why a trace layer is needed at all, the
three levels of lineage it distinguishes (static node lineage, dynamic row
trace, targeted cell trace), and the regulatory drivers.

> NOTE: the design doc's proposed implementation strategy — injecting a
> monotonic `__trace_row_id` column at every source node and threading it through
> joins/group-bys — was not what got built. The shipped implementation performs
> **post-hoc value-based correlation** instead: it reuses the exact DataFrames the
> preview execution already produced and matches each parent row to its resolved
> child row by shared column values, walking backward from the target node. This
> guarantees the trace shows precisely the data the preview table shows, with no
> extra columns threaded through user code. The design doc's `JoinInfo` /
> `AggregationInfo` dataclasses, the `branches` dict on `TraceResult`, and the
> `/api/pipeline/trace/column` and `/api/pipeline/trace/compare` endpoints were
> also never implemented; the current `TraceResult` is a flat, linear list of
> `TraceStep`s plus a `waterfall` summary and `correlation_diagnostics` list —
> the design doc's `haute trace export` regulatory-report CLI command was
> likewise never built (`export_trace()` exists, but only as a Python function
> producing a report-shape dict for the API layer, not a CLI entry point) —
> concepts absent from the original design.

## Scope

In scope:
- `execute_trace()` — the single entry point that orchestrates a one-row trace:
  reusing or materializing per-node DataFrames, correlating rows backward through
  the DAG, assembling `TraceStep`s, enriching them, pruning to column relevance,
  and building a waterfall.
- Post-hoc row correlation, including the edge-join-aware provenance rules needed
  because Polars renames colliding columns on the JOIN-role parent.
- Schema diff computation (added / removed / modified / passed) between a node's
  input and output row.
- Per-node-type enrichment: rating-step table/lookup detail, banding factor/rule
  detail, model-score prediction + explanation, scenario-expansion detail,
  live-switch branch selection, optimiser-apply detail, and row-lineage-type
  classification (created / selected / filtered / aggregated / joined / expanded /
  sorted / passthrough).
- Intra-node and cross-node calculation derivation: parsing/evaluating the
  expression that produced a traced column, walking intra-node dependency chains,
  recursively deriving upstream "input sources," and detecting column renames.
- Waterfall assembly for sequential multiplicative/additive rating chains, with an
  arithmetic-reconciliation invariant against the traced output value.
- Serialising a trace to a JSON-safe API response shape (`trace_result_to_dict`)
  and to a report-shape dict (`export_trace`) for markdown/PDF generation.
- A fingerprint-keyed, byte-bounded cache of materialized per-node DataFrames so
  repeated trace clicks against the same graph structure reuse execution.

Out of scope (owned elsewhere, linked where relevant):
- Parsing and evaluating a Polars expression string, and the intra-node
  dependency chain of assignments within one node's code — the tracing component
  calls into this but does not implement it. See
  [expression-parsing](../expression-parsing/high-level.md).
- Actually executing the pipeline graph (the eager, single-entry-cache
  materialization the trace reuses or falls back to), preview caching, and the
  contract-enforcement machinery that can raise `ContractMismatchError` mid-trace
  — owned by [execution-engine](../execution-engine/high-level.md).
- Rate-table normalisation and the rating-key matching semantics the rating-step
  enricher mirrors (so its "matched"/"default" verdict cannot diverge from what
  the engine's join actually did) — owned by
  [rating](../rating/high-level.md).
- Model-scoring and optimiser-apply explanation internals (feature attribution,
  SHAP-style breakdowns) — the tracing component only calls
  `haute._model_explainability` and `haute._optimiser_apply_explainability` and
  surfaces their structured output; the explanation logic itself belongs to
  [mlflow-model-registry](../mlflow-model-registry/high-level.md) (model-score
  explanation) and [optimiser](../optimiser/high-level.md) (optimiser-apply
  explanation).
- The generic bounded-LRU / fingerprint-cache primitives (`LRUCache`,
  `FingerprintCache`, `graph_fingerprint()`) the trace cache is built on — owned
  by [caching](../caching/high-level.md).
- The HTTP route (`POST /api/pipeline/trace`), its Pydantic request/response
  schemas, timeout handling, request supersession, and concurrency limiting —
  owned by [server-api](../server-api/high-level.md).
- Rendering the trace payload: path highlighting on the graph, the trace side
  panel, waterfall charts, compare mode — owned by
  [frontend-trace-ui](../frontend-trace-ui/high-level.md).

## Behaviour

- **Pure observation layer.** Tracing never modifies pipeline execution or its
  outputs. It either reuses the exact DataFrames a preview execution already
  produced, or (on a cache miss) runs the same eager-execution path the preview
  uses. Either way, the trace shows exactly the data the user sees in the preview
  table.
- **Row identity is verified, not assumed.** When the frontend supplies the
  clicked row's values, the trace checks that the target node's row at
  `row_index` still matches them. A mismatch (e.g. because a Polars join
  reordered rows after a cache eviction) triggers a search for the row that does
  match before falling back to a loud `ValueError` if no match exists.
- **Row correlation never guesses.** Each parent row is matched to its resolved
  child row either by a verified positional alignment (only trusted when the
  child transform provably preserves order, or the shared key uniquely pins one
  row) or by explicit value matching on shared columns. An ambiguous match
  (multiple equally-good candidate rows) or a transform whose behaviour cannot be
  verified leaves that node's step unresolved — it is omitted from the trace
  rather than shown with a wrong row — and is recorded as a non-fatal entry in
  `correlation_diagnostics`.
- **Column-scoped traces prune to relevance.** When a `column` is supplied, the
  trace tags every step by whether it touches that column, then keeps: (a) for a
  pass-through column, only the nodes whose output actually carries it; (b) for a
  calculated/modified column, the node(s) that assign it plus every ancestor that
  contributes a column its formula actually references (falling back to keeping
  all ancestors if no expression info is available).
- **Enrichment is best-effort per step.** Expression parsing/evaluation, chain
  analysis, input-source derivation, rename detection, node-type enrichment, and
  row-lineage classification are each wrapped independently; a failure in one
  never aborts the trace or drops the step — it surfaces as a structured `error`
  / `error_type` field on the relevant enrichment field (`expression`,
  `calculation`, `node_detail`, or `row_lineage_type`) plus a WARNING log.
- **Waterfall values are strictly value-derived.** When a `column` is traced, the
  waterfall walks the traced path and derives each step's contribution from
  *consecutive observed output values* — never from re-applying expression text.
  Expression text is consulted only to pick the multiply-vs-add display label. The
  final cumulative must reconcile with the traced output value; a violation
  raises internally and is converted into a structured `{"error": ...}` payload
  rather than being rendered as a chart that lies.
- **Execution is cached and reused across clicks.** A graph's structure, target
  node, row limit, source, and runtime-input state are fingerprinted; identical
  fingerprints reuse the same materialized per-node DataFrames so switching
  row/column on the same pipeline is near-instant after the first click. The
  cache is invalidated by model retraining (`routes/_train_service.py` calls
  `haute.trace._cache.invalidate()`) and is bounded by both entry count and
  retained bytes, evicting least-recently-used entries first.
- **Output is fully JSON-safe.** `trace_result_to_dict()` converts every dataclass
  field — including non-finite floats, out-of-JS-safe-integer-range integers, and
  arbitrary Python values captured during enrichment — into a shape safe to
  serialise and send to the frontend.

## Design rationale

- **Post-hoc correlation over row-id injection.** The retired
  EXECUTION_TRACE_DESIGN doc (git history) proposed injecting a
  `__trace_row_id` column at every source and threading it through the DAG. The shipped design instead
  matches rows after the fact by value, because it guarantees byte-for-byte
  agreement with what the preview already computed and requires no changes to
  user-authored node code, at the cost of needing careful, node-type-aware
  matching logic (see the edge-join provenance rules below).
- **Preview-cache decoupling via a `PreviewReader` protocol.** Rather than
  reaching into `haute.executor`'s private preview-cache singleton, the trace
  module accepts anything exposing `try_get(fingerprint) -> dict | None` (a
  reader) or a pre-materialised snapshot dict. This keeps the trace module
  testable in isolation and leaves room for a future non-in-process preview
  store, at the cost of the caller (the HTTP route) being responsible for
  wiring the executor's cache in explicitly.
- **Edge-join-aware parent projection.** A generic "keep the child's columns that
  exist in the parent" projection is provably wrong for the JOIN-role (right)
  parent of an edge-join, because Polars renames the right frame's copy of every
  colliding column to `<col><suffix>` while the unsuffixed name in the child
  output carries the BASE (left) frame's value. Projecting by name would silently
  correlate the right parent against left-row values. The fix derives the correct
  match row from the exact kwargs `build_edge_join_kwargs` applied — the same
  single source of truth `execute_edge_join` uses at runtime — so the two paths
  cannot diverge.
- **Waterfall arithmetic contract (C8).** An earlier version fed each step's
  *post-step cumulative* column value in as the multiply/add factor, producing
  nonsensical entries like "×120.0" when the real factor was ×1.2, and classified
  `premium * (1 - discount)` as additive by substring-matching a `-` in the
  expression text. The fix makes every number value-derived (`delta = after -
  before`, implied factor = `after / before`) and classifies the operator by
  parsing the expression's AST top-level `BinOp`, so expression text can only ever
  pick a display label, never corrupt a number.
- **Fail-loud enrichment, never silent fallback.** Per-step enrichment wraps each
  independent concern in its own `try`/`except`, but every except clause writes a
  visible `error` marker into the relevant field and logs a WARNING rather than
  swallowing the exception — consistent with the codebase-wide preference for
  loud failure over an incorrect, hard-to-notice default. The one exception to
  "never swallow silently" that was deliberately removed: an earlier version
  regex-matched `"unable to find column"` in an execution error and silently
  retried with `swallow_errors=True`, which masked genuine column-name typos.
  Cold-execution failures now propagate unchanged.
- **Byte-bounded trace cache mirrors the preview cache.** The trace execution
  cache defaults its byte budget (`HAUTE_TRACE_CACHE_MAX_BYTES`) to the preview
  cache's budget and reuses its frame-size estimator, because both caches retain
  the same class of payload (materialized per-node DataFrames). A single entry
  larger than the whole budget is deterministically rejected at store time
  (logged loudly) rather than silently evicting everything else to make room;
  the trace itself still succeeds, only the cache hit on re-click is lost.

## Interactions

- Depends on [execution-engine](../execution-engine/high-level.md) for
  `PipelineGraph`/`GraphNode` types, topological ordering, the eager execution
  core (`_execute_eager_core`, `_build_node_fn`), preamble compilation, and the
  preview-cache-key construction helpers the trace mirrors so its own
  fingerprints line up with the executor's preview cache.
- Depends on [expression-parsing](../expression-parsing/high-level.md) for
  `parse_expression`, `evaluate_expression`, and `parse_expression_chain` —
  re-exported through `haute.trace`'s module namespace specifically so tests can
  monkeypatch them at `haute.trace.<name>` and have the enrichment dispatch
  (which resolves them via `sys.modules["haute.trace"]`) see the patched
  version.
- Depends on [rating](../rating/high-level.md) for rating-table normalisation
  (`normalise_rating_tables`, `_normalise_combined_outputs`) and the canonical
  rating-key comparison (`normalise_rating_key`) that the rating-step enricher
  uses so its matched/default verdict can never contradict what the engine's own
  join produced.
- Depends on [mlflow-model-registry](../mlflow-model-registry/high-level.md) for
  model-score explanation (`haute._model_explainability`, imported lazily inside
  `enrich_model_score`) and on [optimiser](../optimiser/high-level.md) for
  optimiser-apply explanation (`haute._optimiser_apply_explainability`).
- Depends on [caching](../caching/high-level.md) for `FingerprintCache`,
  `graph_fingerprint()`, and `GraphFingerprintMemo`, which back the trace's own
  execution-result cache.
- Depended on by [server-api](../server-api/high-level.md): `routes/pipeline.py`
  is the sole production caller of `execute_trace()`, wrapping it in a response
  timeout, request-supersession coordinator, and concurrency semaphore, and
  mapping its exceptions to HTTP status codes. `routes/_train_service.py`
  reaches into `haute.trace._cache` to invalidate trace results after a model
  retrain.
- Depended on by [frontend-trace-ui](../frontend-trace-ui/high-level.md), which
  consumes the `TraceResponse` JSON shape (`trace_result_to_dict()`'s output,
  validated against `haute.schemas.TraceResultResponse`) to render the trace
  panel, highlight the graph path, and draw the waterfall chart.

## Failure model

- **Structural preconditions fail loudly as `ValueError`.** An empty graph, a
  `target_node_id` that does not exist in the graph, a `row_index` beyond the
  target node's row count, and a `row_values` mismatch that cannot be resolved by
  relocating the row all raise `ValueError` with a message the HTTP layer pattern
  -matches to choose a specific status code (404 for missing target node, 400 for
  out-of-range row, 409 for a genuine row mismatch).
- **A malformed `preview` argument fails loudly as `TypeError`.** `execute_trace`
  only accepts `None`, a `PreviewReader`-shaped reader, or a snapshot dict; any
  other type, or a reader whose `try_get` returns something other than
  `dict | None`, raises immediately rather than being coerced.
- **Underlying execution errors propagate unchanged.** If a cold execution (no
  usable preview cache) fails — a bad node config, a contract mismatch — the
  original exception (including `ContractMismatchError`) propagates out of
  `execute_trace` unmodified. Nothing catches and reinterprets it.
- **Row correlation failures are non-fatal but visible.** An unresolved node is
  simply absent from `TraceResult.steps`; the fact that it was ambiguous (rather
  than just "not on the path") is recorded in `correlation_diagnostics` with a
  `code`, `severity`, `reason`, human-readable `message`, and the specific
  matched-row indices — enough for a caller to explain the gap to a user rather
  than silently showing an incomplete trace.
- **Per-step enrichment failures are caught, logged, and surfaced as an `error`
  field**, never raised out of `execute_trace`. This applies uniformly across
  expression parsing/evaluation, chain analysis, input-source derivation, rename
  detection, node-type enrichment (rating/banding/model-score/scenario/live-switch
  /optimiser-apply), and row-lineage classification. A step that fails every
  enrichment concern still appears in the trace with its raw input/output values
  intact.
- **Waterfall failures are structured, not silent.** Any exception during
  waterfall assembly — including the two purpose-built
  `WaterfallReconciliationError` (the arithmetic does not add up to the traced
  output) and `WaterfallUnavailableError` (e.g. the traced column is produced on
  two un-orderable joined branches) — is caught and converted to a
  `{"error": ..., "error_type": ...}` payload in the `waterfall` field. A
  precondition failure that means a waterfall simply does not apply (fewer than 3
  contributing steps, no numeric traced output value) returns `None` instead,
  which is distinct from an error.
