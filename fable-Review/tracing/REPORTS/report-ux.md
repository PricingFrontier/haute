# Report: informativeness

## Verdict (≤10 lines)
The trace is genuinely strong at *structure*: the graph glow/fade is real, rating/banding/model-score/optimiser nodes each get a purpose-built, well-labelled detail view, defaults are warning-flagged, correlation diagnostics are surfaced, and progressive disclosure (collapse pass-throughs) keeps big pipelines readable. But it fails the actuary's core test — "value in, operation, value out" — for the single most common rating pattern. **For any self-referential factor step (`premium = premium * factor`), the CalculationHero and waterfall total display an arithmetically wrong number** that contradicts the panel header (reproduced: header says premium = 120, hero says 144). This is a HIGH silent-wrongness bug in the headline story. Beyond it: there is **no loading state and failures vanish in a 3-second toast** (a 120 s cold trace is indistinguishable from a hung/failed one); the **regulator-pack promise is unbacked** (`_trace_export.py` is dead code, no export/print/copy, no what-if or two-trace compare); every step card prints a **dead "0.0ms"**; and number formatting is fragmented across three formatters (locale-dependent vs hard en-US, 1dp vs 2dp vs 4dp) so rounded chains can visually disagree. Fix the self-referential substitution first — it's a small backend change mirroring a fix that already exists 400 lines away.

## README-promise audit

| README claim (quoted, shortened) | Verdict | Evidence |
|---|---|---|
| "Click any cell… traces the path through every node that contributed… showing the value at each step" (l.65) | **PARTIAL** | Cell click → `useTracing.ts:212` → StepCard per node with value chips (`StepCard.tsx:64-78`). "Value at each step" is shown, but the *calculation* of that value is garbled for self-referential steps (see UX-01). |
| "base rate → area factor → discount → loading → final price" (l.68, inline arrow chain) | **PARTIAL** | No literal breadcrumb arrow-chain is rendered. The story is a vertical `StepCard` list (`TracePanel.tsx:164-192`) plus a waterfall. `buildFlowChain` exists (`traceGrouping.ts:180`) but is unused by the panel. |
| "The graph highlights the path… Nodes that contributed glow. The rest fade." (l.71) | **TRUE** | `PipelineNode.tsx:288` `_traceActive` → glow shadow (`:415`), `:296` `dimmed` → `opacity 0.25` fade (`:329,:390,:425`); traced value shown on-node (`:554`). |
| "A sidebar shows what happened at each step — which values were used, what changed, and what the result was." (l.71) | **PARTIAL** | Values used + what changed: yes (schema-diff colouring, `StepCard.tsx:283-296`). "What the result was": wrong for self-referential steps (UX-01). |
| "Rating steps show the table factors used, the selected value from each table, default usage, and combined-output inputs." (l.71) | **TRUE** | `RatingStepDetail.tsx:77-85` (factors), `:67-69` (selected), `:73-75` (default-used warning), `:92-128` (combined outputs). Best-in-class here. |
| "Banding traces show the source value that selected the band and continue the upstream lineage." (l.71) | **TRUE** | `BandingDetail.tsx:60-72` (source value → band + range); upstream lineage via `_attach_banding_lineage` → `input_sources` (`_trace_enrichment.py:1237-1271`). |
| "First click runs the full pipeline and caches… every click after pulls from cache — updates instantly." (l.73) | **PARTIAL** | Backend cache real (`trace.py:389-463`). But there is **no loading state** for the first (potentially 120 s) click and **no cache/staleness indicator** in the UI (UX-05, UX-11). |
| "…show a regulator… exactly how a price was derived… in a click." (l.75) | **FALSE (as a shareable artifact)** | `_trace_export.py:8` `export_trace` is **dead/test-only**; the route serialises via `trace_result_to_dict` instead (`pipeline.py:462`). No export/print/PDF/CSV/copy anywhere in the trace UI. You can show the screen; you cannot produce a regulator pack. |
| "timing breakdown… how long each step took, colour-coded" (l.95) — as applied to the trace | **FALSE for the trace** | That is the *preview* Pipeline-Timing dropdown (`Toolbar.tsx:253-260`, fed by `timing_ms`). The trace's per-step `execution_ms` is always 0.0 yet still printed (`StepCard.tsx:186`) — see UX-04. |
| "What-if analysis — change inputs and watch the price move through every step" (l.169) — via the trace | **FALSE (not integrated)** | Zero `whatIf`/`what-if` in `frontend/src`; `TracePanel.tsx:20-25` is read-only `{trace, onClose}`; `useTracing.ts` exposes one immutable `traceResult`. No input-edit-and-re-run, no two-trace compare. |

## The story as rendered (per node type: what the user sees)

**Panel frame.** A header — `Trace: <column> = <output_value>`, then `<row_id_col> = <value> · N of M nodes · created by <node>` with a `show full trace` toggle (`TracePanel.tsx:63-108`). Below it: an optional amber **correlation-warnings** alert (`:124-153`), then the story: a vertical stack of `StepCard`s with pass-through runs collapsed into a `N pass-through nodes hidden` button (`:164-192`, `traceGrouping.collapsePassthroughs`).

**Source / dataSource / apiInput.** Collapsed card: node name + type badge + `creates`/`created` badge + `0.0ms`. Expanded: "Source node <name>" or a plain column-values table (`StepCard.tsx:258-265,299-360`). No calculation — correct for a source.

**Polars transform (the workhorse).** Collapsed: the traced column chip `~ premium: 120` plus, for non-target steps, a substituted-text chip (`StepCard.tsx:209-216`). Expanded target step: the **CalculationHero** — formula line (`premium × area_factor`), substituted line (`120.0 × 1.2`), and `= premium <result>` (`CalculationHero.tsx:283-312`), with an upstream **InputSourceTree** showing where each operand came from (`:256-269`). For 3+ multiplicative factors, a **WaterfallChart** replaces the box (animated bars + running values + bold total). *This is where UX-01 bites: the substituted line and the result/total are wrong for self-modifying columns.*

**Rating step.** `RatingStepDetail`: one block per table with a status chip (`matched`/`default`/`no_match`, colour-coded), `selected:` and `default:` chips, a `default used` amber chip, and factor chips (`col: value`); then a Combined Outputs section (`<col> = <value>`, `<operation> from base <base_value>`, input chips). Clear and audit-friendly (`RatingStepDetail.tsx:33-129`).

**Banding.** `BandingDetail`: a summary chip row (output column, `source=value -> band`, range `[lo, hi)`, `default` chip) and, for multi-factor nodes, an Output/Source/Band/Rule table (`BandingDetail.tsx:31-77`). The band-selecting source value and the matched interval are both visible.

**Model score.** `ModelScoreDetail`: a prediction chip, a Feature Values grid, and — when SHAP is available — a **Contribution Ladder** (Base → +/− per-feature contributions with running total → Prediction), missing features labelled `not provided`, truncation noted (`ModelScoreDetail.tsx:99-162`). Genuinely explainable.

**Optimiser apply.** Online: selected-scenario callout with the `objective + λ·term = score` decomposition, an SVG candidate curve, and a candidates table (`OptimiserApplyDetail.tsx:60-188`). Ratebook: base/final callout + a Factor/Input/Value/Total ladder with `default used` flags (`:191-244`). Error variant renders `Trace failed: <error> (<type>)` (`:246-257`).

**Scenario expander / live switch.** Compact chip panels: scenario column/value/index + grid min/max/steps (`ScenarioExpanderDetail.tsx`); active branch + pruned branches list (`LiveSwitchDetail.tsx`). Adequate.

## Findings

### UX-01 [HIGH] [informativeness] — Self-referential factor steps display a wrong substituted value and result, contradicting the price
- **Location:** `src/haute/_trace_enrichment.py:1578-1584` (root cause); rendered by `frontend/src/trace/CalculationHero.tsx:310` & `:547`→`WaterfallChart.tsx:126`, `frontend/src/trace/StepCard.tsx:97-99,209-216,268-274`.
- **What the user experiences:** Tracing `premium` through a standard sequential ratebook (`premium = premium * area_factor`, then `* age_factor`). **Reproduced payload:** panel header `premium = 120` but the target-step hero reads **`premium × area_factor` → `120.0 × 1.2` → `= premium 144.00`**. In the 3-factor case the waterfall *bars* are correct (100 → 120 → 108) but the bold **total shows 97.20** while the header shows 108, and the intermediate "Area Loading" card shows a chip `120.0 × 1.2` next to `~premium: 120`. The InputSourceTree even shows `premium = 100 (Base Rate)` on the line directly above a formula that substitutes `120` for premium — self-contradictory within one box.
- **Evidence:** `evaluate_expression(code, column, {**step.input_values, **step.output_values}, …)` merges output over input, so a self-modified column resolves to its *post* value. The correct fix already exists for the input-sources path (`_trace_enrichment.py:1170-1184`, `self_referential_modification` → use `input_values[ref_col]`) but is **not** applied to the primary calculation. Reproduced live (target=area): `substituted_text='120.0 * 1.2', result_value=144.0` with `out_premium=120.0`; the observed-value backend waterfall is correct, proving only the per-step `calculation` is wrong.
- **Proposal (concrete design):** In `enrich_steps`, before the primary `evaluate_expression`, build an eval map that substitutes the *input* value for any column that is both referenced and in `columns_modified` (mirror the `self_referential_modification` block already at `:1170-1184`). Then `substituted_text`/`result_value` become `100.0 × 1.2 = 120.0`, matching the waterfall and header. No frontend change needed.
- **Effort:** S
- **Confidence:** High (reproduced end-to-end).

### UX-02 [MEDIUM] [capability-gap] — No regulator/stakeholder export; the export code that exists is dead
- **Location:** `src/haute/_trace_export.py:8` (`export_trace`, 111 lines, unreachable); route serialises via `trace_result_to_dict` (`routes/pipeline.py:462`).
- **What the user experiences:** README sells "show a regulator… in a click," but there is no Download/Print/Export/Copy control anywhere in the trace UI (verified: no `export_trace`/clipboard/print in `TracePanel.tsx` or `frontend/src/trace/*`; `export_trace`'s only callers are tests). The actuary can screenshot but cannot hand over a structured artifact.
- **Evidence:** whole-repo grep: every `export_trace` caller is a test; no route/CLI/button.
- **Proposal:** Wire `export_trace` to reality: add `GET /pipeline/trace/export` (or extend the trace response with an `export` block) and a "Download trace" split-button in the panel header offering **Markdown** (already the shape `export_trace` returns), **CSV** (per-step: node, operation, value-in, value-out, factor, default?), and **Print-to-PDF** (a print stylesheet on the story). One-line copy: *"Download this price derivation (Markdown / CSV / PDF)."* Include row identity, column, output, per-step formula+substituted+result, and default-usage flags.
- **Effort:** M
- **Confidence:** High.

### UX-03 [HIGH] [error-story] — No loading state; failures disappear in a 3-second toast
- **Location:** `frontend/src/App.tsx:846` (`traceResult ? <TracePanel/> : <NodePanel/>`), `frontend/src/hooks/useTracing.ts:204-252`, `components/Toast.tsx:32` (3 s auto-dismiss).
- **What the user experiences:** Between click and result (up to the 120 s trace timeout, `pipeline.py:93`) there is **no "tracing…" affordance** — the old NodePanel just stays, so a slow cold trace is indistinguishable from a hung one. On failure, `clearTrace()` reverts to NodePanel and the *only* signal is a toast that vanishes in 3 s (`Trace error: <detail>`). A user who glances away misses it entirely.
- **Evidence:** No loading branch mounts TracePanel; `.catch` at `useTracing.ts:241-252` does `addToast("error", …)` + `clearTrace()`.
- **Proposal:** Mount `TracePanel` in three explicit states — **loading** (skeleton + "Tracing <column>…", cancellable), **error** (persistent in-panel banner with the message + a Retry button, not a transient toast), **empty** ("This column is a pass-through / nothing computed it here"). Keep the clicked-cell highlight during loading.
- **Effort:** M
- **Confidence:** High.

### UX-04 [MEDIUM] [legibility] — Every step card advertises a dead "0.0ms"
- **Location:** `frontend/src/trace/StepCard.tsx:186` renders `{step.execution_ms.toFixed(1)}ms`; `src/haute/trace.py:166` field default is never assigned (only `TraceStep(...)` site at `trace.py:826-835` omits it).
- **What the user experiences:** Right-aligned on *every* step card, `0.0ms` — implying each step is instantaneous. It's a shipped-but-unpopulated field.
- **Evidence:** grep `\.execution_ms\s*=` in `src/haute` → no per-step assignment; only the whole-trace `TraceResult.execution_ms` is set (`trace.py:606`).
- **Proposal:** Either (a) populate per-step timing in `execute_trace` (wrap each node's eager materialisation with `perf_counter`) and colour-code it like the preview timing dropdown, delivering the README's per-step timing promise inside the trace; or (b) if per-step timing isn't meaningful for a cached observation layer, drop the field from `StepCard` and the payload. Prefer (a).
- **Effort:** S (remove) / M (populate)
- **Confidence:** High.

### UX-05 [MEDIUM] [legibility] — Three number formatters disagree (locale + precision); rounded chains can visually contradict the total
- **Location:** `frontend/src/utils/formatValue.ts:28` (`toLocaleString(undefined, …)` — browser locale), vs `frontend/src/trace/traceFormatting.ts:19,24` (`toLocaleString("en-US", …)` — hard US), vs `WaterfallChart.tsx:100-101` (`toFixed(1)` running values) vs `:26` (2dp total).
- **What the user experiences:** In one panel, key-value chips / rating / banding / model detail use `formatValue` (browser locale, 2dp), while the CalculationHero result and chain use `formatSmartValue` (en-US, 2-4dp). A German/French user sees mixed decimal/thousands separators in the same panel. Rate factors render at **2dp with no full-precision tooltip** in `RatingStepDetail.tsx:68` and `BandingDetail`, so a `1.0525` factor shows as `1.05`; the waterfall shows running values at **1dp** (`132.2`) but the total at **2dp** (`132.25`).
- **Evidence:** `RatingStepDetail.tsx:67-69` `selected: {formatValue(table.selected_value)}` has no `title`; `StepCard.tsx:27-33` *does* attach a full-precision tooltip, so the inconsistency is only in the detail sub-components.
- **Proposal:** One shared `formatTraceNumber(value, {dp, full})` with a single locale policy (pick en-US or the app locale, consistently), used everywhere, and attach the full-precision `title` tooltip on every rounded numeric cell (rating/banding/model). Standardise waterfall running values to the same dp as the total.
- **Effort:** M
- **Confidence:** High.

### UX-06 [MEDIUM] [error-story] — Waterfall errors show the raw backend diagnostic verbatim
- **Location:** `frontend/src/trace/CalculationHero.tsx:537-538` → `WaterfallErrorAlert.tsx:24-27`.
- **What the user experiences:** Under a bare "Waterfall error" heading, the raw string, e.g. *"waterfall reconciliation failed: waterfall for column 'premium' does not reconcile: final cumulative 123.4 != traced output value 125.0."* Internal diagnostics, no plain-language cause, no next step.
- **Evidence:** `WaterfallErrorAlert` prints `error` verbatim with `white-space: pre-wrap`; strings originate in `_trace_waterfall.py:601-628`.
- **Proposal:** Map `error_type` → friendly copy: `WaterfallReconciliationError` → *"We couldn't show a factor-by-factor breakdown for this step because the numbers don't reconcile — the per-node values below are still exact."*; `WaterfallUnavailableError` → *"A breakdown isn't available here (the value comes from more than one branch)."* Keep the raw text behind a "Details" disclosure.
- **Effort:** S
- **Confidence:** High.

### UX-07 [MEDIUM] [informativeness] — Default usage isn't flagged in the waterfall or the collapsed card
- **Location:** flag present only in expanded detail — `RatingStepDetail.tsx:73-75`, `OptimiserApplyDetail.tsx:229-233`, `NodeDetailBlock.tsx:55-57`; absent from `WaterfallChart.tsx` and the collapsed `StepCard` chip.
- **What the user experiences:** A factor sourced from a table **default** (a top audit concern) looks identical to a matched factor in the headline waterfall and the collapsed card; the amber "default used" chip is only visible after expanding the rating detail.
- **Evidence:** `WaterfallEntry` carries no default flag (`_trace_waterfall.py:55-64`); `WaterfallChart` bars have no default styling.
- **Proposal:** Thread a `default_used` boolean into waterfall entries and the collapsed key chip; render a small amber "default" tag on that bar/chip. Backend already knows this per table (`_enrich_single_table` `default_used`).
- **Effort:** S
- **Confidence:** Med.

### UX-08 [MEDIUM] [error-story] — Dropped (uncorrelated) steps vanish with only a coarse count as signal
- **Location:** `src/haute/trace.py:802-803` (`if output_row is None: continue` silently omits a step); user-facing signals are the `N of M nodes` count (`TracePanel.tsx:87`) and the correlation alert (`:124-153`, only if the backend appended a diagnostic).
- **What the user experiences:** If a node's row can't be correlated, its StepCard simply isn't there. The `N of M` count also drops for benign column-relevance pruning, so a lower count doesn't localise the omission; only a matching `correlation_diagnostics` entry explains it, and nothing marks the *position* in the story where a step is missing.
- **Evidence:** correlation diagnostics *are* rendered (a genuine strength) but not tied to an inline placeholder.
- **Proposal:** When a node in topo order is skipped for correlation failure, emit a lightweight placeholder entry the panel renders inline: a muted "⚠ <node name> — step omitted (row could not be matched)" card linking to the diagnostic. Distinguish it from pruning (which is expected).
- **Effort:** M
- **Confidence:** Med.

### UX-09 [MEDIUM] [capability-gap] — No copy-to-clipboard of a step or the whole story
- **Location:** verified absent across `frontend/src/trace/*` and `TracePanel.tsx` (all clipboard code lives in graph/table editors, not the trace).
- **What the user experiences:** To quote a derivation in an email or ticket, the actuary must retype or screenshot.
- **Proposal:** A "Copy" affordance per StepCard (copies `node · formula · substituted · = result`) and a panel-level "Copy full derivation" (the Markdown from UX-02). One-line copy on hover: *"Copy this step."*
- **Effort:** S
- **Confidence:** High.

### UX-10 [LOW] [legibility] — "null" and "passthrough" are developer jargon in a regulator-facing view
- **Location:** `formatValue.ts:26`, `traceFormatting.ts:15,30` render null/undefined as literal `null`; `detect_row_lineage_type` returns `passthrough` for `with_columns` nodes, shown as a badge (`StepCard.tsx:174`, when no column is traced).
- **What the user experiences:** A missing rate factor reads `null`; a node that *computes premium* is badged `passthrough` (technically "rows unchanged," but reads as "did nothing").
- **Evidence:** Reproduced: base/area/age nodes all `row_lineage_type = "passthrough"` despite modifying premium.
- **Proposal:** Render null as `—` with a `title="no value"`; relabel row-lineage badges for humans (`passthrough` → `unchanged rows` or suppress it when the node clearly adds/modifies columns).
- **Effort:** S
- **Confidence:** Med.

### UX-11 [LOW] [informativeness] — No cache/staleness indicator
- **Location:** `trace_result_to_dict` (`trace.py:959-996`) omits `cache_hit`; the backend computes it (`trace.py:391`) but doesn't ship it.
- **What the user experiences:** No way to tell a freshly-computed trace from a cached one. (Correctness is fine — cache is fingerprint-keyed — this is confidence/transparency only.)
- **Proposal:** Add `cache_hit` + a compute timestamp to the payload; show a subtle "cached · <time>" chip in the header.
- **Effort:** S
- **Confidence:** Med.

## Error-story table (failure mode → user-visible result → grade)

| Failure mode | Backend origin | Status / payload | Frontend | Exact user-visible text | Grade |
|---|---|---|---|---|---|
| Row-mismatch | `trace.py:493-497` | **409**, detail=full string | `useTracing.ts:243-250` toast | "Trace error: Trace data does not match the preview row. The preview data may have changed. Please click the node to refresh, then retry." | **Good** (actionable) but 3 s only |
| Timeout | `pipeline.py:466-470` (120 s) | **504** | toast | "Trace error: Trace execution timed out (120s limit)" | **Partial** (what, not what-to-do); no loading state beforehand |
| Superseded | `_supersession.py` → `pipeline.py:458` | **409** | swallowed by seq-guard `useTracing.ts:242` | *(nothing)* | **OK by design** |
| Waterfall error | `_trace_waterfall.py:601-628` | **200**, `waterfall={error,…}` | `CalculationHero.tsx:537`→`WaterfallErrorAlert` | "Waterfall error" + raw diagnostic verbatim | **No** (UX-06) |
| Empty graph | route `pipeline.py:411` | **400** | toast | "Trace error: Empty graph" | **Partial** (terse; ~unreachable) |
| Target not found | `trace.py:366` | **404** | toast | "Trace error: Target node '<id>' not found in graph" | **Partial** (verbatim; ~unreachable) |
| Generic 500 | `pipeline.py:494` / `_helpers.py:183` | **500** | toast | "Trace error: Operation failed. Check the server logs for details." | **No** (user has no logs) |
| Contract mismatch | `pipeline.py:473-480` | **422** | toast | "Trace error: <node/column diff>" | **Partial** |

Weakest: waterfall raw-error verbatim (UX-06) — the one failure rendered *inside* the panel; the no-loading + 3 s-toast combination (UX-03) that can hide any failure; and the generic-500 dead-end.

## Enhancement proposals ranked (regulator-value ÷ effort)
1. **Fix self-referential substitution** (UX-01) — HIGH ÷ S. The headline story is wrong today; one small backend change.
2. **Loading + persistent error/empty states** (UX-03) — HIGH ÷ M. Makes the feature trustworthy for slow/failed traces.
3. **Regulator export (Markdown/CSV/PDF) reusing `export_trace`** (UX-02) — HIGH ÷ M. Turns "show a regulator" from screenshot to artifact.
4. **Kill or populate per-step `0.0ms`** (UX-04) — MED ÷ S. Removes a misleading number.
5. **Unify formatters + full-precision tooltips** (UX-05) — MED ÷ M. Stops rounded chains from visually disagreeing.
6. **Copy-to-clipboard** (UX-09) — MED ÷ S. Cheap sharing.
7. **Default flag in waterfall + collapsed card** (UX-07) — MED ÷ S. Audit-critical signal in the headline.
8. **Friendly waterfall-error copy** (UX-06) — MED ÷ S.
9. **Inline dropped-step placeholder** (UX-08) — MED ÷ M.
10. **What-if: editable input re-trace, and two-trace before/after compare** — HIGH ÷ L. The biggest regulator/underwriter capability, but a substantial build (new endpoints + UI); parked behind the above.

## Strengths (what already tells the story well)
- **Graph glow/fade is real and calm** — contributing nodes glow, others fade to 0.25, traced value shown on-node (`PipelineNode.tsx:288-554`); motion is a single 0.2 s opacity ease with a `trace-motion-lite` perf path (`index.css:241,308`).
- **Node-type detail views are purpose-built and honest** — rating (factors/selected/default/combined), banding (source→band + interval), model SHAP contribution ladder with `not provided`, optimiser candidate curve + score decomposition. Defaults are amber-flagged in the detail views.
- **Correlation diagnostics are surfaced**, not swallowed — amber alert with per-row reasons (`TracePanel.tsx:124-153`); `N of M nodes` count in the header.
- **Progressive disclosure works** — consecutive pass-throughs collapse into a "N pass-through nodes hidden" button with a "show full trace" toggle (`traceGrouping.collapsePassthroughs`, `TracePanel.tsx:93-104,175`), so a 50-node pipeline reduces to the relevant spine.
- **The waterfall is arithmetically defended** — every contribution is derived from consecutive *observed* values and must reconcile with the traced output or fail loudly (`_trace_waterfall.py` C8 contract). (Its *bars* are correct even where UX-01 corrupts the per-step calc; the fix in UX-01 makes the whole story consistent.)
- **Enrichment fails loudly, per-step** — every enricher catches and annotates with a visible `error` marker rather than dropping data silently (`_trace_enrichment.py` throughout).

## Coverage note
Grounded in first-hand reads of the full backend (`trace.py`, `_trace_enrichment.py`, `_trace_waterfall.py`, `_trace_export.py`) and the full frontend render path (`TracePanel`, `traceStoryView`, `traceGrouping`, `StepCard`, `CalculationHero`, `TraceDetail`, all six per-node Detail components + helpers, `WaterfallChart/ErrorAlert`, `ExpressionChain`, `InputSourceTree`, `formatValue`, `traceFormatting`, `formatTrace`, `PipelineNode`, `index.css`), plus three delegated fan-out investigations (export/what-if/clipboard reachability; `execution_ms`/`correlation_diagnostics` usage; end-to-end error-string mapping). UX-01 is reproduced against a real `execute_trace` payload; scratch scripts in the session scratchpad (`gen_trace.py`, `analyze.py`). **Not exercised live:** a real rating-step/banding node payload (their configs are complex to hand-build) — those node-type renderings are assessed from component code, not a live payload, so treat their fidelity claims as code-grounded rather than run-verified. Categorical-banding and edge-join lineage paths were read but not deeply walked.

---
Headline: **UX-01 (self-referential factor substitution shows the wrong number, contradicting the price)** is the one HIGH silent-wrongness finding — reproduced end-to-end — and it's a small backend fix. UX-03 (no loading/persistent-error state) is the other HIGH. Everything else is MEDIUM/LOW friction or capability gaps, ranked by value/effort above.
