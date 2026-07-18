# T10 — Regulator pack, dead surface, and presentation polish

**Severity:** MEDIUM (capability) + LOW ×7 · **Effort:** M for T10.1; S each for the rest
**Pairing:** batch-review class throughout (no silent-wrongness; one reviewer over the wave).
**Files:** `src/haute/_trace_export.py`, `src/haute/routes/pipeline.py`, `src/haute/trace.py`,
`frontend/src/panels/TracePanel.tsx`, `frontend/src/trace/*`, `frontend/src/types/trace.ts`,
`frontend/src/types/guards.ts`, `frontend/src/utils/formatValue.ts`
**Origin:** UX-02/04/05/07/09/10/11, FE-04/05/06, ENR-06, FR-11-comments (UX + frontend + enrichment reviews)

## T10.1 (UX-02 + ENR-06 + UX-09) — ship the regulator pack; resurrect or delete `export_trace`

The README's core sell — "show a regulator exactly how a price was derived" — has no artifact
behind it: `_trace_export.export_trace` (111 lines) is dead production code (only test callers),
and the UI has no export/print/copy anywhere in the trace surface.

**Fix (one coherent feature):**
1. Backend: repoint `export_trace` at the *enriched* steps (its `formula`/`sources` currently
   re-derive from raw rows and can disagree with what the panel showed — unacceptable for a
   handed-over artifact; source them from `step.expression`/`calculation`/`node_detail`). Add
   `POST /pipeline/trace/export` accepting the same `TraceRequest` + `format: "markdown"|"csv"` —
   it re-runs `execute_trace` (cache makes this cheap) and returns the document. Include: row
   identity, traced column + output, per-step operation/substituted/result, rating table +
   default-usage flags, waterfall table, correlation warnings, timestamp + pipeline file name.
2. Frontend: "Download derivation" split-button in the `TracePanel` header (Markdown / CSV /
   Print) + a per-step "Copy" affordance and a panel-level "Copy full derivation" (the Markdown).
   Print = a print stylesheet over the story view (cheapest PDF path).
3. If the maintainer prefers not to ship export now: **delete** `_trace_export.py` + its tests
   (per the no-dead-code rule) — but given the README promise, shipping it is the recommendation.

**Failing tests:** route test — export of the golden rating pipeline contains the same
selected-values/defaults the trace payload carries (byte-compare the numbers, not the prose);
frontend — copy button writes the step summary to the clipboard mock.

## T10.2 (UX-04) — per-step `0.0ms` is a dead field: populate it

`TraceStep.execution_ms` is serialized on every step and rendered on every card
(`StepCard.tsx:186`) but never assigned (only the whole-trace `TraceResult.execution_ms` is,
`trace.py:606`). Every card says `0.0ms`. Populate it: time each node inside
`_execute_eager_core`'s per-node loop when tracing cold (the executor already produces `timing_ms`
for previews — reuse that plumbing), and carry cached-trace steps' last-known timings with the
cache entry. If populating is deemed not worth it, delete the field end-to-end (payload, types,
guards, StepCard) — do not keep shipping a fake number. Recommendation: populate on cold, hide on
cache-hit ("cached" chip instead — pairs with T10.6).
**Failing test:** cold trace of a 3-node chain → every step's `execution_ms > 0`; or (deletion) the
field is absent everywhere.

## T10.3 (UX-05) — one number-formatting policy for the whole panel

Three formatters disagree: `formatValue.ts:28` (browser locale, 2dp), `traceFormatting.ts:19,24`
(hard en-US, 2–4dp), `WaterfallChart.tsx:100-101` (1dp running vs 2dp total). Mixed separators in
one panel for non-US locales; `1.0525` factors display as `1.05` with no tooltip in
`RatingStepDetail.tsx:68`/`BandingDetail` (StepCard already does the full-precision `title` —
copy that pattern). **Fix:** a single `formatTraceNumber(value, {dp})` in `trace/traceFormatting.ts`
with one locale policy (recommend: app-wide `formatValue` locale), full-precision `title` on every
rounded numeric, waterfall running values at the total's dp.
**Failing test:** snapshot a rating detail with `1.0525` → rendered `1.05` carries
`title="1.0525"`; all-components format audit test iterating the golden payload.

## T10.4 (UX-07) — default-usage flag in the waterfall and collapsed cards

Default hits (top audit concern) are amber-flagged only inside the expanded rating detail. Thread
`default_used` into `WaterfallEntry` (`_trace_waterfall.py:55-64`; the enricher already knows it
per table) and onto the collapsed StepCard chip; render a small amber "default" tag.
**Failing test:** golden rating pipeline with a default hit → waterfall entry carries
`default_used: true` and the bar renders the tag.

## T10.5 (UX-10) — de-jargon `null` and `passthrough`

Nulls render as literal `null` (`formatValue.ts:26`, `traceFormatting.ts:15,30`) → render `—` with
`title="no value"`. `row_lineage_type="passthrough"` badges nodes that *compute the traced value*
(rows unchanged ≠ did nothing) → display copy "rows unchanged", and suppress the badge when the
step adds/modifies columns. (Backend semantics unchanged; T04.4 fixes the *wrong* labels, this
fixes the *misleading* one.)

## T10.6 (UX-11) — cache/staleness chip

Ship `cache_hit` (already computed, `trace.py:391`) + an ISO `computed_at` in the payload; render a
subtle "cached · HH:MM" chip in the panel header. Complements T01 (which guarantees the cached
trace is never *stale against the graph*). Guards/types updated.

## T10.7 (FE-04/FE-05/FE-06 + comment fixes) — dead surface and micro-cleanups

- Delete the five never-emitted step fields from `types/trace.ts:47-52` + `parseTraceStep`
  (`taken_branch`, `taken_branch_index`, `null_explanation`, `expression_chain`, `rename_info`)
  and the dead `?? step.taken_branch_index` fallback at `StepCard.tsx:237`.
- `CalculationHero.tsx:457-533`: when `calculation.taken_branch_index` is null, replace the
  `String.includes` branch-highlight fallback with typed equality — or no highlight; a guessed
  highlight is worse than none.
- `TracePanel.tsx:185`: precompute a `node_id → index` map instead of per-row
  `trace.steps.indexOf(entry)`.
- Comment rot: `trace.py:368` still says "single-entry cache" (it is an 8-entry byte-bounded LRU);
  fix alongside FR-11's `{node_id: index}` map in `_trace_enrichment.py:1092`.

## Acceptance for the package

- A regulator can leave the app with a Markdown/CSV/printed derivation whose numbers byte-match
  the panel.
- No fake or dead fields cross the wire; `tsc` + guards contract suites green.
- One formatter, one locale policy, full-precision recoverable everywhere.
