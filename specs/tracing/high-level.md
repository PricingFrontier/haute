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
editor's "click a cell → see its lineage" feature, and it exists to satisfy
regulatory explainability requirements (Solvency II, IFRS 17, FCA Consumer
Duty) as well as day-to-day debugging.

A trace layer generally distinguishes three levels of lineage: static node
lineage (which nodes could feed a given node, derived from graph structure
alone, with no execution involved), dynamic row trace (which rows and values
actually flowed through a specific execution), and targeted cell trace (the
value-level derivation of one cell for one row). This component implements
the targeted cell trace.

## Scope

In scope:
- `execute_trace()` — the single entry point that orchestrates a one-row trace:
  reusing or materializing per-node DataFrames, correlating rows backward through
  the DAG, assembling `TraceStep`s, enriching them, pruning to column relevance,
  and building a waterfall.
- Post-hoc row correlation, including the edge-join-aware provenance rules needed
  because Polars renames colliding columns on the JOIN-role parent, and the
  per-edge frame selection needed because a multi-frame source (e.g. a ≥2-table
  `apiInput`) stores `dict[label, DataFrame]` rather than a single `DataFrame`.
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
- Serialising a trace to a JSON-safe API response shape (`trace_result_to_dict`),
  including typed omissions and generation provenance. Markdown/CSV/copy/print
  export projects the validated response snapshot in the frontend so the
  artifact cannot diverge from the panel through a second execution.
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
- The generic bounded-LRU and fingerprinting primitives (`LRUCache`,
  `graph_fingerprint()`) the trace cache is built on — owned
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
  match before falling back to a loud `ValueError` if no match exists. If more
  than one row matches the clicked values, the relocation is genuinely ambiguous
  — silently anchoring to the first match could correlate the whole trace
  upstream from the wrong row — so this also raises `ValueError` rather than
  guessing.
- **Row correlation never guesses silently.** Each parent row is matched to its resolved
  child row either by a verified positional alignment (only trusted when the
  child transform provably preserves order, or the shared key uniquely pins one
  row) or by explicit value matching on shared columns. An ambiguous match
  (multiple equally-good candidate rows) or a transform whose behaviour cannot be
  verified leaves that node's step unresolved — it is omitted from the trace
  rather than shown with a wrong row — and is recorded as a non-fatal entry in
  `correlation_diagnostics`.
- **Multi-frame sources correlate per edge, not per node pair.** A multi-frame
  source (e.g. a ≥2-table `apiInput`) stores `dict[label, DataFrame]`; each edge
  out of it carries a `sourceHandle` naming the frame that edge consumes, and the
  same source can feed one child through *several* edges at once (the canonical
  four-port `apiInput` → `OUTPUT` topology, or a join of two data levels straight
  off the input). Row correlation resolves the frame per edge — matching every
  candidate frame against the resolved child row — rather than assuming one frame
  per (source, target) pair, which would silently correlate against whichever
  edge happened to be listed last. When several frames match, the frame carrying
  the traced column wins; a remaining tie stays unresolved, records an
  `ambiguous_source_frame` diagnostic, and becomes a linked omission when it is
  relevant. Tracing the multi-frame node itself (rather than a node downstream
  of a specific frame) raises a `ValueError` naming the problem instead of
  crashing on a bare `dict`.
- **Edge joins resolve multi-frame base columns before JOIN-parent matching.**
  When an `edgeJoin` uses one named frame from a multi-frame source as its BASE
  parent, correlation derives the left-column set only from the frame handle(s)
  wired to that join. It never calls `.columns` on the source bundle. A missing
  or invalid handle raises a message-bearing `ValueError` naming the join/base
  instead of leaking `AttributeError`.
- **Self-referential assignments substitute the pre-assignment value.** When a
  step's expression reassigns a column from itself (`premium = premium * factor`),
  showing the substitution requires the value the RHS actually read — the
  post-assignment output would otherwise appear on the right-hand side too,
  producing an arithmetically false substitution (e.g. `200.0 * 2.0` displayed for
  an output of `200.0`). The same pre-assignment-value discipline applies to
  multi-entry expression chains: each chain entry evaluates in order against
  values fed forward from prior entries, seeded from pre-node input values rather
  than the node's final output values.
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
- **Enrichment observes; it never repairs rows.** Rating, banding, model-score,
  lineage, and schema-diff detail is derived from the complete rows selected by
  correlation and the same immutable runtime contracts the engine consumes.
  Continuous banding enrichment uses the rating runtime's shared rule-eligibility
  parser, so a rule with no usable operator/value pair cannot be credited.
  Enrichment never patches an individual output cell or invents model features.
  A row that cannot be selected atomically remains unresolved.
- **Relevant correlation gaps remain first-class evidence.** A node on the
  retained path whose row cannot be correlated is represented by a typed
  omission carrying its node identity, topological rank, reason, and diagnostic
  reference. It is not represented as a `TraceStep` with fabricated empty rows.
  If the assigning step is itself unresolved and therefore absent from
  `TraceResult.steps`, omission relevance falls back conservatively to every
  attempted unresolved ancestor; it does not use the narrow column filter
  without origin evidence. Ordinary column-relevance pruning produces no
  omission.
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
  retained bytes, evicting least-recently-used entries first. `execute_trace`
  accepts an optional caller-supplied `GraphFingerprintMemo`; the trace route
  passes in the same memo it already used to compute its supersession key, so a
  preamble's utility files are hashed once per request rather than once per call.
  A `None` (tests, CLI, cold requests) creates a fresh memo scoped to that call.
- **Output is fully JSON-safe.** `trace_result_to_dict()` converts every dataclass
  field — including non-finite floats, out-of-JS-safe-integer-range integers, and
  arbitrary Python values captured during enrichment — into a shape safe to
  serialise and send to the frontend.
- **Generation provenance is explicit and narrow.** Every response carries a UTC
  `generated_at`, the pipeline/source identity available to the server, and an
  `execution_origin` of `fresh_execution`, `preview_cache`, or `trace_cache`.
  These fields describe how the trace snapshot was assembled; they do not claim
  that an external data source is fresh.
  Provider group, safe source identity, selected snapshot generation, and
  external freshness are outside the current
  `TraceResult`; the response does not infer or display provenance it does not
  carry.

## Design rationale

- **Post-hoc correlation over row-id injection.** An alternative design would
  inject a `__trace_row_id` column at every source node and thread it through
  the DAG. The shipped design instead matches rows after the fact by value,
  because it guarantees byte-for-byte agreement with what the preview already
  computed and requires no changes to user-authored node code, at the cost of
  needing careful, node-type-aware matching logic (see the edge-join
  provenance rules below).
- **Preview-cache decoupling via a `PreviewReader` protocol.** Rather than
  reaching into `haute.executor`'s private preview-cache singleton, the trace
  module accepts anything exposing `get(fingerprint) -> dict | None` (a
  reader) or a pre-materialised snapshot dict. This keeps the trace module
  testable in isolation and leaves room for a future non-in-process preview
  store, at the cost of the caller (the HTTP route) being responsible for
  wiring the executor's cache in explicitly.
  The current HTTP preview route publishes target-only cache entries, while a
  truthful trace requires full ancestor materialisation. Those shapes
  deliberately do not share a key, so the first trace after an ordinary HTTP
  preview executes cold; the trace layer never accepts a partial snapshot to
  manufacture the appearance of reuse.
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
- **Per-edge, not per-node-pair, multi-frame resolution.** A naive design would
  cache one resolved `sourceHandle` per (source, target) node pair. That is
  provably wrong for the four-port `apiInput` → `OUTPUT` topology and for a node
  joining two data levels straight off the same multi-frame source: both wire
  several edges between the same pair, each naming a different frame, so a
  per-pair cache would collapse them to whichever edge was recorded last and
  correlate every consumer against an arbitrary frame. Resolution instead keeps
  one `sourceHandle` per *edge* and matches every distinct candidate frame
  against the resolved child row independently, at the cost of a small
  per-child fan-out over candidate frames instead of an O(1) lookup.
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
  shared preview-lineage cache-key factory the trace calls so its fingerprints
  use the executor's canonical identity contract.
- Depends on [expression-parsing](../expression-parsing/high-level.md) for
  `parse_expression`, `evaluate_expression`, and `parse_expression_chain`.
  `_trace_enrichment` imports those dependencies directly; it does not recover
  its own public facade from `sys.modules`, so importing the enrichment module
  independently cannot fail on a hidden import-order cycle.
- Depends on [rating](../rating/high-level.md) for rating-table normalisation
  (`normalise_rating_tables`, `_normalise_combined_outputs`) and the canonical
  rating-key comparison (`normalise_rating_key(value, dtype)`). The rating-step
  dispatch resolves each factor's originating dtype from the exact consumed
  parent frame and supplies it to the enricher, so JSON/Python scalar widening
  cannot make its matched/default verdict contradict the engine's own join.
- Depends on [mlflow-model-registry](../mlflow-model-registry/high-level.md) for
  model-score explanation (`haute._model_explainability`, imported lazily inside
  `enrich_model_score`) and on [optimiser](../optimiser/high-level.md) for
  optimiser-apply explanation (`haute._optimiser_apply_explainability`).
- Depends on [caching](../caching/high-level.md) for `LRUCache`,
  `GraphFingerprintMemo`, and the shared `preview_lineage_cache_key(...)`
  factory, which back the trace's own execution-result cache.
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
  target node's row count, a `row_values` mismatch that cannot be resolved by
  relocating the row (including a relocation that matches more than one row —
  ambiguous, not just missing), and a `target_node_id` that resolves to a
  multi-frame source's `dict` output (tracing must target a node downstream of a
  specific frame) all raise `ValueError` with a message the HTTP layer pattern
  -matches to choose a specific status code (404 for missing target node, 400 for
  out-of-range row or a multi-frame target, 409 for a genuine or ambiguous row
  mismatch).
- **A malformed `preview` argument fails loudly as `TypeError`.** `execute_trace`
  only accepts `None`, a `PreviewReader`-shaped reader, or a snapshot dict; any
  other type, or a reader whose `get` returns something other than
  `dict | None`, raises immediately rather than being coerced.
- **Underlying execution errors propagate unchanged.** If a cold execution (no
  usable preview cache) fails — a bad node config, a contract mismatch — the
  original exception (including `ContractMismatchError`) propagates out of
  `execute_trace` unmodified. Nothing catches and reinterprets it.
- **Unsupported target correlation dtypes fail as a public typed error.**
  `TraceCorrelationUnsupportedError(ExecutionError)` carries stable
  `node_id`, `key_columns`, `dtypes`, and `reason_code` fields with code
  `trace_correlation_unsupported`. The HTTP contract maps it to 422 and
  background execution records `contract_error`; upstream-parent unsupported
  comparisons remain diagnostic-linked omissions.
- **Row correlation failures are non-fatal but visible.** An unresolved relevant
  node is absent from `TraceResult.steps` and present in `TraceResult.omissions`,
  linked to the corresponding `correlation_diagnostics` entry by a stable
  diagnostic index. Multi-frame source resolution adds
  two diagnostic codes to the same list: `unresolved_source_frame` when no
  candidate frame yields any match, and `ambiguous_source_frame` (`severity:
  "warning"`) when more than one frame matches equally well and no frame can be
  selected safely.
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
