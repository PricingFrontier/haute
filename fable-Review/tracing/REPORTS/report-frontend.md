# Report: frontend

## Verdict (≤10 lines)
The trace frontend is, on the whole, carefully built: the A→B request race is correctly handled by a request-sequence token plus AbortController and is explicitly tested; `row_values` are sent as **raw** parsed values (not the rounded display strings), so backend row-matching is not corrupted by display rounding; the `waterfall` union (`list | {error,error_type} | null`) is handled symmetrically at both ends; `correlation_diagnostics` is surfaced as a visible warning banner; the collapsed-submodel glow works via `resolveTraceId`; guards are well-calibrated (fail loud on missing required scalars, tolerant of benign additions); tsc is clean and the targeted suites pass (165 tests). **The one serious defect is a staleness gap (FE-01, CRITICAL): a trace is NOT invalidated when the graph mutates via the file-watcher/WebSocket sync or via an in-place node-config edit — so the sidebar and node glow keep explaining a pipeline that has since changed underneath them.** This is precisely the deadliest class for a regulator-facing explainability feature, and it is untested. Everything else is Medium/Low (a 409 recovery that discards the trace, a waterfall-error that can be suppressed for rich-detail targets, and dead type/guard surface).

## Findings

### FE-01 [CRITICAL] [correctness/robustness] — Trace + glow are not invalidated when the graph changes (file-watcher sync or in-place node edit)
- Location: `frontend/src/App.tsx:447-503` (`onUpdateNode`, no `clearTrace`); `frontend/src/App.tsx:403-407` (`useWebSocketSync` is not passed `clearTrace`); `frontend/src/hooks/useTracing.ts:296-321,372-442` (glow derived from `traceResult.steps` against current `nodes`).
- Claim: There is no invalidation of `traceResult`/`tracedCell` when the pipeline is mutated by (a) the file watcher pushing a new graph over the WebSocket, or (b) editing the config of the already-selected node that the trace targets. The trace state survives the swap and keeps decorating/explaining the new graph.
- Evidence: A full grep of `clearTrace` usage shows it is wired only to edge handlers, keyboard (Escape), pane click, `handleSwapEdgeJoinInputs`, and the panel close button:
  - `useEdgeHandlers.ts:238,330,343`, `useKeyboardShortcuts.ts:144`, `App.tsx:571` (swap), `App.tsx:789` (pane), `App.tsx:847` (panel close).
  - `onUpdateNode` (the node-config commit path) runs `setNodes(...)`/`setSelectedNode(...)` with **no** `clearTrace` call (read of lines 447-503).
  - `useWebSocketSync` receives `setNodesRaw, setEdgesRaw, setPreamble, ...` but **not** `clearTrace` (grep of `useWebSocketSync.ts` for `clearTrace|traceResult` → *no matches*).
  - No `useEffect` anywhere resets trace on `nodes`/`edges` change (grep for `invalidat|reset.*trace|shallowNodeHash` → only preview-cache invalidation, never trace).
  - `nodesWithStatus` (useTracing.ts:372-442) recomputes `_traceActive`/`_traceDimmed`/`_traceValue` for the **current** `nodes` using the **old** `traceResult.steps` id/value sets, so the same node ids keep glowing with stale `_traceValue`, and `TracePanel` keeps rendering the old `steps`.
- User impact: The README sells exactly the workflows this breaks — "some members working visually and others working in code, on the same pipeline, at the same time," "Edit the Python file in a text editor and the visual editor updates... in under a second," and "When you need to show a regulator... exactly how a price was derived." A user opens a trace, then edits the pipeline (or a colleague does, or they tweak the currently-selected traced node's config); the graph re-renders but the sidebar still shows `area factor = 1.2 → discount = 0.9 → final = X` and the same nodes glow, now describing values/nodes that no longer exist. No error is shown — silent wrongness.
- Fix sketch: Invalidate trace on structural/config change. Simplest correct option: pass `clearTrace` into `useWebSocketSync` and call it whenever an inbound graph is applied, and call `clearTrace()` at the top of `onUpdateNode`. A more refined option is to record a lightweight graph fingerprint at trace time (e.g., the `resolveGraphFromRefs` hash already available) and clear the trace in a `useEffect` when the live fingerprint diverges, so trivial position-only changes don't nuke the trace.
- First failing test: integration test — render App, drive a cell click to populate `traceResult`, then dispatch a WebSocket graph update (and separately call `onUpdateNode` on the traced target node); assert `traceResult` is cleared / the trace panel unmounts and no node retains `_traceActive`.
- Confidence: high (code + exhaustive grep; behavior currently untested).
- Overlap: new.

### FE-02 [MEDIUM] [robustness] — 409 "does not match the preview row" discards the trace with only a transient toast (no auto-recovery)
- Location: `frontend/src/hooks/useTracing.ts:241-252` (`.catch` → `addToast("error", "Trace error: <detail>")` + `clearTrace()`); backend `src/haute/routes/pipeline.py:483-485`; message `src/haute/trace.py:493-497`.
- Claim: When the backend cannot relocate the clicked row (`_find_target_row_index` fails), it raises 409 with "Trace data does not match the preview row. The preview data may have changed. Please click the node to refresh, then retry." The client turns this into a red toast and calls `clearTrace()`, dropping the panel and glow. There is no auto-refresh-preview-and-retry.
- Evidence: `client.ts:411-428` throws `ApiError` with `.detail` = the 409 string; `useTracing.ts:243-251` reads `err.detail` and toasts it, then `clearTrace()`. The existing test `useTracing.test.ts:253-257` pins exactly this 409 detail path.
- User impact: The message is clear and actionable-by-hand (it tells the user to re-preview), so it is not a silent dead-end — but the user loses their trace and must manually re-preview then re-click. For the marquee "click any price" flow this is a jarring recovery.
- Fix sketch: On a 409 whose detail starts with "Trace data does not match", auto-trigger a preview refresh of `selectedNode` and re-issue the same `handleCellClick` once, surfacing the toast only if the retry also fails.
- First failing test: mock `traceCell` to reject 409 once then resolve; assert the client re-previews and retries and ends with a populated `traceResult`.
- Confidence: high (path fully traced and test-pinned).
- Overlap: new.

### FE-03 [MEDIUM] [informativeness] — Waterfall reconciliation error can be silently dropped when the target step has a rich node_detail
- Location: `frontend/src/trace/StepCard.tsx:102-107` (`showCalculationHero` requires `!richNodeDetail`) and `:228-242` (waterfall is passed only into `CalculationHero`, only inside the `showCalculationHero` block); `frontend/src/panels/TracePanel.tsx:189` (`waterfall` sent only for the target step); `frontend/src/trace/CalculationHero.tsx:537-539` (the only `WaterfallErrorAlert` render site).
- Claim: The `{"error": ...}` waterfall payload only reaches the UI through `CalculationHero`. For target nodes with a "rich" detail (`rating_step`, `banding`, `model_score`, `optimiser_apply`, `scenario_expander`, `live_switch` — `hasPrimaryNodeDetail`), `showCalculationHero` is false, so `CalculationHero` never mounts and the waterfall error is never displayed anywhere.
- Evidence: `hasPrimaryNodeDetail` (`traceStoryView.ts:52-61`) returns true for those detail types; `StepCard.tsx:102-107` gates `showCalculationHero` on `!richNodeDetail`; no other component receives `trace.waterfall`. Backend can emit a waterfall error dict for any target column (`_trace_waterfall.py:601-627`, always `{error, error_type}`).
- User impact: A rating-step/model-score target whose waterfall reconciliation failed shows its rich factor/feature detail but silently omits the "waterfall reconciliation failed" diagnostic — a lost integrity signal in the exact regulator-facing surface. (The successful-entries case is intentionally superseded by the rich detail; it's specifically the *error* banner that goes missing.)
- Fix sketch: Hoist the waterfall-error branch out of `CalculationHero` — render `WaterfallErrorAlert` in `StepCard` for the target step whenever `resolveWaterfallProp(waterfall).error` is set, independent of `showCalculationHero`.
- First failing test: `StepCard` with `isTargetStep`, a `rating_step` `node_detail`, and `waterfall={{error:"…",error_type:"ValueError"}}`; assert a `waterfall-error-alert` renders.
- Confidence: medium (render conditions fully traced; reachability depends on the backend producing a waterfall error for a rich-detail target column).
- Overlap: new.

### FE-04 [LOW] [elegance/informativeness] — Dead step-level fields in `TraceStep` type + guards that the backend never emits
- Location: `frontend/src/types/trace.ts:47-52`; `frontend/src/types/guards.ts:882-908`; backend serializer `src/haute/trace.py:966-985` and Pydantic `src/haute/schemas.py:344-356`.
- Claim: The frontend `TraceStep` type and `parseTraceStep` accept five top-level fields — `taken_branch`, `taken_branch_index`, `null_explanation`, `expression_chain`, `rename_info` — that `trace_result_to_dict` does not emit (its step dict has exactly 12 keys, and `TraceStepResponse` matches those 12). They are parsed as optional and always resolve to `null`.
- Evidence: grep for these names in `trace.py` finds only `expression_chain` in imports, none in the serializer. In the frontend they are unread except `StepCard.tsx:237` `step.calculation?.taken_branch_index ?? step.taken_branch_index` (a dead `??` fallback) and `step.row_lineage_type` (which *is* emitted). `null_explanation`, `rename_info`, top-level `taken_branch`/`expression_chain` are read nowhere but guards.
- User impact: None today (optional → null). Risk: the type advertises step-level branch/null/rename lineage that doesn't exist; a future dev reading `step.null_explanation` would silently get blanks.
- Fix sketch: Delete the unsent fields from `TraceStep` and `parseTraceStep`, or make the backend emit them if they were intended.
- First failing test: n/a (cleanup).
- Confidence: high.
- Overlap: new.

### FE-05 [LOW] [correctness] — Conditional-branch highlight falls back to fragile substring matching when `taken_branch_index` is absent
- Location: `frontend/src/trace/CalculationHero.tsx:457-533` (`isBranchMatched` → `sub.result.includes(resultStr)`).
- Claim: When `calculation.taken_branch_index` is null, the "which branch fired" highlight is derived by checking whether the stringified result value is a substring of a branch's substituted result text. Numeric results or overlapping branch text can highlight the wrong branch (e.g., result `1` substring-matches a branch producing `10`).
- Evidence: lines 469-486; the backend index is preferred (`resolvedTakenBranchIndex`), so this is a fallback only. `guards.contract.test.ts:463-464` shows the backend does populate `calculation.taken_branch_index`, so the fallback is rarely exercised.
- User impact: Rare mis-highlight of the taken conditional branch when the backend omits the index.
- Fix sketch: When `taken_branch_index` is absent, prefer exact equality on typed values rather than `String.includes`, or render no highlight rather than a guessed one.
- First failing test: conditional step with `taken_branch_index: null`, numeric result `1`, branches producing `10`/`1`; assert the `1` branch (not `10`) is `data-matched="true"`.
- Confidence: medium.
- Overlap: new.

### FE-06 [LOW] [performance] — `TracePanel` computes `trace.steps.indexOf(entry)` per rendered row
- Location: `frontend/src/panels/TracePanel.tsx:185`.
- Claim: The per-card step number does an O(n) `indexOf` inside a `.map`, making numbering O(n²) in step count.
- Evidence: line 185 `index={trace.steps.indexOf(entry)}`.
- User impact: Negligible (traces are pipeline-sized); pure cleanliness.
- Fix sketch: Precompute a `node_id → index` map once, or carry the original index on the entry during grouping.
- Confidence: high.
- Overlap: new.

## Contract drift table (backend field → frontend handling → verdict)
| Backend (`trace_result_to_dict` / schemas.py) | Frontend handling (`guards.ts` / consumers) | Verdict |
|---|---|---|
| `status` (always `"ok"`; all errors are HTTP codes) | `parseTraceResponse` requires string; `useTracing` checks `=== "ok"`, else toast | OK — but the non-ok branch (`useTracing.ts:236-239`) is dead in practice; the backend never returns non-ok with 200 (`pipeline.py:460-463`). Harmless defensive code. |
| `trace` (always present) | `parseTraceResult` requires a valid object (throws otherwise) | OK; pinned by `guards.contract.test.ts:258`. Note: any `status:"error"` envelope must still carry a *fully valid* trace or the whole parse throws to `.catch`. |
| step: 12 fields (`node_id/node_name/node_type/schema_diff/input_values/output_values/column_relevant/execution_ms/expression/calculation/node_detail/row_lineage_type`) | Parsed 1:1; required scalars fail loud, optionals default | OK — aligned with `TraceStepResponse`. |
| (no step-level `taken_branch`/`taken_branch_index`/`null_explanation`/`expression_chain`/`rename_info`) | Type + guards accept them as optional → always null | **Drift (benign)** — FE-04. Frontend surface exceeds the wire contract. |
| `waterfall: list \| {error,error_type} \| null` | `parseTraceResult:952-957` splits array→entries, object→`parseWaterfallError` (requires `error`+`error_type`), null→null | OK — backend always includes both keys (`_trace_waterfall.py:601-627`). Union handled correctly. |
| `correlation_diagnostics: list[...]` (required scalar fields per item) | `optionalArray` → `parseTraceCorrelationDiagnostic`; rendered as a warning banner in `TracePanel:124-153` | OK — **surfaced, not dropped**; defaults to `[]` when absent (`guards.contract.test.ts:255`). |
| `output_value` / `row_id_value` (`Any`) | Passed through untyped (`unknown`) and formatted for display | OK. |
| 409 (superseded) / 409 (row mismatch) / 504 / 422 / 400 / 404 / 500 | All arrive as `ApiError`→`.catch`→toast; superseded is normally pre-empted client-side by AbortController | Mostly OK; row-mismatch 409 is a trace-discarding recovery — FE-02. |

## Strengths (verified-good, worth pinning)
- **A→B request race is correct and tested.** `traceRequestSeq` + `AbortController` (`useTracing.ts:195-258`): a late-resolving stale response is discarded by the `requestId` guard. `useTracing.test.ts:178-210` verifies both `traceResult` and `tracedCell` stay on B after A resolves late (suite passes, 165 tests green).
- **`row_values` fidelity preserved.** `DataPreview.tsx:274-276` sends `data.preview[rowIndex]` — the raw parsed backend row (`guards.ts` `optionalPlainObjectArray`) — while `formatValue` rounding is confined to cell rendering (`DataPreview.tsx:176`). The task's "sends rendered strings" hazard is not present.
- **Waterfall union + error surface aligned at both ends** (`guards.ts:952-957/922-928` vs `_trace_waterfall.py:601-627`); `WaterfallErrorAlert` even degrades gracefully to "No details were provided by the backend." on empty text.
- **Coherent 3-tier highlight** driven by the backend's pruned step set: `column_relevant` → glow (`_traceActive`), in-trace carrier (kept by `trace.py:930`) → neutral, not-in-trace → faded (`useTracing.ts:381-385`). Edges are highlighted only when **both** endpoints are in-trace (`:334-343`). Collapsed submodels glow via `resolveTraceId` child→placeholder mapping (`:260-294`), and `SubmodelNode.tsx:15-35` honors the flags.
- **Guards are well-calibrated and type-clean.** Missing required scalars throw (fail loud), benign field additions are ignored (not over-strict), optionals default. `tsc -b --noEmit` exits 0.
- **No per-hover panel re-render.** Hover state lives on graph nodes (`hoveredNodeId` in useTracing), not in `TracePanel`; `CalculationHero` is `React.memo`'d (`:605`) and receives no hover props.
- **Timeouts/cleanup sound.** Trace client timeout is 120s (`client.ts:638`), matching the backend `_TRACE_TIMEOUT`; POST is intentionally not retried (`client.ts:306-315`); `clearTrace` aborts in-flight and bumps the seq so unmount-mid-flight is safe.

## Coverage note
- Ran `npx vitest run src/hooks/__tests__/useTracing.test.ts src/types/__tests__/guards.contract.test.ts src/api/__tests__/client.contract.test.ts` → **3 files, 165 tests, all pass**. `npx tsc -b --noEmit` → **exit 0**.
- **Well-covered:** the A→B race and stale-response discard (`useTracing.test.ts:178-210`); the 409-detail toast path (`:253-257`); trace parse contract incl. waterfall entries + correlation diagnostics + "reject trace missing `trace`" (`guards.contract.test.ts:251-287`).
- **Untested gaps (first-class):** (1) **staleness-on-edit / file-watcher invalidation (FE-01)** — `App.integration.test.tsx` only checks WebSocket *open* timing (`:424`), nothing asserts trace clears on graph mutation; (2) waterfall-error suppression for rich-detail targets (FE-03); (3) conditional-branch fallback matching without `taken_branch_index` (FE-05). I could not spin up a browser to repro FE-01 end-to-end (read-only / no dev-server constraint), so its confidence rests on code + exhaustive grep rather than a live capture — but the absence of any `clearTrace` on the mutation paths is unambiguous in the source.
