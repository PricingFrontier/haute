# Verified strengths — protect these with regression tests, do not "fix" them

Every item below was verified against the current tree (code + empirical probe or passing suite).
When implementing the T-docs, these behaviours are the regression surface: if a fix would change
one, stop and re-read the relevant T-doc.

## Correlation & core
- **`_find_matching_row` is genuinely fail-loud** (W4): duplicate exact matches and ambiguous
  relaxed matches record diagnostics and return `(None, -1)` — never row 0
  (`_trace_correlation.py:401-443`; verified by repro). T02 extends this contract to the
  relocation entry point; do not weaken it here.
- **`_build_value_match_expr` is dtype-robust**: numeric/NaN/Inf values against incompatible
  column dtypes degrade to a broadcast-safe non-match instead of raising at collect time
  (`:196-232`). The T03 vectorisation must reuse it, not reimplement it.
- **The positional fast path is correctly gated**: trusted only when shared keys match AND
  uniquely identify the row, or the child transform provably preserves order
  (`_child_transform_may_reorder`, `:478-496`); the no-shared-columns `elif` (`:777`) is the one
  capability FR-04's deletion must preserve.
- **Edge-join right-parent provenance is right**: `_edge_join_right_match_row` (`:562-616`) routes
  suffixed/colliding columns and join keys per the same `build_edge_join_kwargs` SSOT the runtime
  uses, and raises loudly on a missing base frame.
- **Fingerprint composition is injective** (`canonical_json` framing, `_cache.py:501-513`) and the
  per-graph base fingerprint memo makes no-preamble fingerprints ~4 µs.
- **Concurrency is safe**: `FingerprintCache`→`LRUCache` is RLock-guarded; supersession +
  worker-thread offload cannot corrupt slots; shared frames between trace and preview caches are
  references (no 2× resident copy). (T08.3 fixes only the *accounting*.)
- **Route error mapping is accurate** for every reachable error: 409 mismatch / 409 superseded /
  400 / 404 / 422 contract / 504 timeout (`routes/pipeline.py:464-496`).

## Enrichment & waterfall
- **Waterfall C8 reconciliation holds** (verified end-to-end): contributions derive from
  consecutive observed values, identity steps are explicit ×1.0 entries, the cumulative reconciles
  exactly with the traced output, and failures produce the structured `{"error", "error_type"}`
  payload instead of silent absence.
- **Rating key matching shares `normalise_rating_key` with the engine** and mirrors
  `unique(keep="last")` ordering — key/default decisions cannot drift (only the *value* field can:
  T04.1).
- **Banding continuous matching is Float32-faithful** when the parent dtype is available
  (`_coerce_pair_through_dtype` mirrors Polars literal-downcast semantics; `repros/probe_f32.py`).
- **Optimiser-apply enrichment reconciles or raises** (`OptimiserApplyTraceError` on
  selection/factor-product mismatch) — clamped outputs cannot mislead silently.
- **`_detect_rename` is code-based, not value-equality** — equal-valued distinct columns do not
  produce false rename chains (`repros/probe_rename_waterfall.py`).
- **Enrichers fail loud per-step** — every enricher annotates a visible `error` marker rather than
  dropping data.

## Frontend
- **The A→B click race is correct and pinned**: request-sequence token + AbortController; a
  late-resolving stale response cannot clobber the newer trace (`useTracing.ts:195-258`;
  `useTracing.test.ts:178-210`).
- **`row_values` are raw, not display-rounded**: `DataPreview.tsx:274-276` sends the parsed backend
  row; formatting stays in the cell renderer — backend matching is not corrupted by display
  rounding.
- **The waterfall union type is handled symmetrically at both ends** (list / error dict / null),
  and **`correlation_diagnostics` are surfaced** as an amber banner (`TracePanel.tsx:124-153`) —
  not dropped.
- **Three-tier highlight semantics are coherent**: relevant→glow, in-trace carrier→neutral,
  rest→fade; edges highlight only when both endpoints are in-trace; collapsed submodels glow via
  the `resolveTraceId` child→placeholder mapping, honoured by `SubmodelNode`.
- **Guards are calibrated**: missing required scalars throw; benign additions pass; optionals
  default. `tsc -b --noEmit` clean; 165 targeted tests green during this review.
- **Node/edge projection caching** gives React Flow reference-stable objects (no spurious
  re-renders); `CalculationHero` is memoised; hover state does not re-render the panel.
- **Progressive disclosure works**: consecutive pass-through steps collapse behind a counter with
  a "show full trace" toggle — a 50-node pipeline reads as its relevant spine.
- **Rating/banding/model/optimiser detail views are purpose-built and honest** (status chips,
  amber default flags, SHAP contribution ladder with "not provided" labels, candidate curves).

## Suggested pins (cheap tests worth adding during the waves)
1. Duplicate-key parent → `(None,-1)` + `duplicate_exact_match` diagnostic (already exists? verify,
   else add).
2. Numeric-value-vs-Utf8-column predicate → non-match, no raise.
3. No-shared-columns positional fallback survives FR-04.
4. Waterfall reconciliation exactness on the golden 3-factor chain.
5. A→B race test kept green through T09's state-machine refactor (it is the regression net).
6. Submodel placeholder glow mapping (`resolveTraceId`).
