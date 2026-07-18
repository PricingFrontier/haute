# T09 — The error story: loading, recovery, and failure visibility

**Severity:** HIGH (UX-03) + MEDIUM ×4 · **Effort:** M
**Pairing:** dev/reviewer pair for T09.1/T09.2 (state-machine changes); batch review for the copy items.
**Files:** `frontend/src/hooks/useTracing.ts`, `frontend/src/App.tsx`,
`frontend/src/panels/TracePanel.tsx`, `frontend/src/trace/StepCard.tsx`,
`frontend/src/trace/WaterfallErrorAlert.tsx`, `src/haute/trace.py` (T09.4 placeholder emission)
**Origin:** UX-03/06/08, FE-02, FE-03 (informativeness + frontend reviews)

The failure modes are mapped end-to-end (see the error-story table in `REPORTS/report-ux.md`); the
machinery is sound — codes are right, messages are accurate — but the *presentation* loses them.

## T09.1 (UX-03, HIGH) — no loading state; failures vanish in a 3-second toast

`App.tsx:846` mounts `TracePanel` only when `traceResult` exists; between click and response (up to
the 120 s timeout) nothing indicates a trace is running — a slow cold trace is indistinguishable
from a dead click. On failure, `clearTrace()` + a 3 s toast (`Toast.tsx:32`) is the entire signal.

**Fix:** give `useTracing` an explicit request state (`idle | loading | error | ready`), and mount
`TracePanel` in all non-idle states: loading = header skeleton + "Tracing `<column>`…" + cancel
(wired to the existing AbortController); error = persistent in-panel banner with the backend detail
+ a Retry button (re-issues the identical request); ready = today's panel. Keep the clicked cell
highlighted during loading. The generic-500 copy ("Check the server logs") gets a friendlier line
("Something failed while tracing this cell — the server logged the details.") with the raw detail
behind a disclosure.
**Failing tests:** (1) pending fetch → panel shows the loading state (mock a never-resolving
`traceCell`); (2) rejected fetch → persistent in-panel error with Retry, no auto-dismiss; (3) Retry
re-calls `traceCell` with identical args; (4) cancel during loading aborts and returns to idle.

## T09.2 (FE-02) — auto-recover the row-mismatch 409

The backend's 409 "Trace data does not match the preview row … click the node to refresh, then
retry" (raised more often once T02 makes ambiguity fail-loud) currently discards the trace and asks
the user to do the recovery by hand. **Fix:** on a 409 with that detail prefix, automatically
refresh the selected node's preview and re-issue the trace once; only surface the (persistent,
T09.1) error if the retry also fails. Guard against loops with a single-retry flag on the request.
**Failing test:** `traceCell` rejects 409-mismatch once then resolves; assert one preview refresh +
one retry + populated trace; a double-409 ends in the persistent error, not a loop.

## T09.3 (FE-03 + UX-06) — waterfall errors: never suppressed, never raw

Two halves:
- **Suppression (FE-03):** the `{"error", "error_type"}` waterfall payload renders only inside
  `CalculationHero`, which is not mounted when the target step has a rich `node_detail`
  (rating/banding/model/optimiser/scenario/live-switch — `StepCard.tsx:102-107`). A reconciliation
  failure on those targets is silently dropped. **Fix:** hoist — render `WaterfallErrorAlert` from
  `StepCard` for the target step whenever the waterfall prop resolves to an error, independent of
  `showCalculationHero`.
- **Raw copy (UX-06):** `WaterfallErrorAlert` prints the backend diagnostic verbatim. **Fix:** map
  `error_type` → plain-language copy (reconciliation → "We couldn't show a factor-by-factor
  breakdown because the numbers don't reconcile — the per-step values below are still exact.";
  unavailable → "A breakdown isn't available here (the value comes from more than one branch).")
  with the raw text behind a "Details" disclosure.
**Failing tests:** StepCard with `isTargetStep` + `rating_step` detail + error waterfall → alert
rendered (currently absent); alert shows mapped copy with raw detail collapsed.

## T09.4 (UX-08) — dropped steps leave a visible placeholder, not a gap

`_assemble_steps` skips correlation-failed nodes (`trace.py:802-803`); the only signals are the
`N of M` header count (also reduced by benign pruning) and the diagnostics banner (only when a
diagnostic was recorded). **Fix (backend+frontend):** when a node on the kept path has
`output_row is None`, emit a minimal placeholder step (`node_id/name/type`,
`"omitted": "row_not_correlated"`, no values) instead of dropping it; `TracePanel` renders a muted
"⚠ `<name>` — step omitted (its row could not be matched)" card linking to the matching diagnostic.
Column-relevance pruning (expected, benign) must NOT produce placeholders — only correlation
failures. Guards: add the optional `omitted` field to `types/trace.ts` + `guards.ts` + `schemas.py`.
**Failing test:** duplicate-key parent that fails correlation → payload contains the placeholder
step with reason; panel renders the omitted card in topo position.

## Acceptance for the package

- Every row of the error-story table lands somewhere persistent and actionable: no failure mode's
  only trace is a 3-second toast.
- The full failure matrix has a frontend test each: mismatch-409 (auto-recovered), timeout-504,
  superseded (silent by design — pin that), waterfall-error (rich + plain targets), generic-500.
- `npx tsc -b --noEmit` clean; guards contract tests updated alongside any payload addition.
