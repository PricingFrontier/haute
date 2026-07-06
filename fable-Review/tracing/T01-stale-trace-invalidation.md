# T01 — Stale trace survives pipeline edits (frontend invalidation gap)

**Severity:** CRITICAL · **Effort:** M · **Dev/reviewer pair: REQUIRED** (silent-wrongness class)
**Files:** `frontend/src/App.tsx`, `frontend/src/hooks/useWebSocketSync.ts`, `frontend/src/hooks/useTracing.ts`
**Origin:** FE-01 (frontend review) · verified by lead session (exhaustive `clearTrace` grep)

## The defect

A displayed trace (`traceResult` + node glow + step values) is **not invalidated when the pipeline
changes underneath it**. `clearTrace` is wired to edge handlers, Escape, pane click, edge-join input
swap, and the panel close button — and to nothing else:

- `App.tsx:447-503` — `onUpdateNode` (the node-config commit path) calls `setNodes(...)` /
  `setSelectedNode(...)` with **no** `clearTrace()`.
- `App.tsx:403-407` — `useWebSocketSync` (the file-watcher → browser graph push) receives
  `setNodesRaw, setEdgesRaw, setPreamble, …` but **not** `clearTrace`; a grep of
  `useWebSocketSync.ts` for `clearTrace|traceResult` has zero matches.
- No `useEffect` anywhere resets trace state on `nodes`/`edges` change.

`nodesWithStatus` (`useTracing.ts:372-442`) then recomputes `_traceActive`/`_traceDimmed`/
`_traceValue` for the **current** nodes from the **old** `traceResult.steps`, so the same node ids
keep glowing with stale values, and `TracePanel` keeps rendering the old steps.

## Why this is CRITICAL

The README sells exactly the workflows this breaks: visual + code editing "on the same pipeline, at
the same time" with sub-second file-watcher sync, and "show a regulator exactly how a price was
derived". Sequence: user traces a premium → they (or a colleague, via the watched `.py` file) change
a factor → the canvas updates but the sidebar still narrates `area factor = 1.2 → … → final = X`
against the new graph. No error, no staleness marker — the story looks authoritative and is wrong.
This is the UI twin of T02: both are "the trace confidently explains the wrong thing".

## Fix design

Invalidate on any semantic graph change; tolerate cosmetic ones.

1. **Wire the two mutation paths** (minimum correct fix):
   - Pass `clearTrace` into `useWebSocketSync`; call it whenever an inbound graph payload is
     *applied* (not on every WS message — only when nodes/edges/preamble state is actually replaced).
   - Call `clearTrace()` at the top of `onUpdateNode` before `setNodes`.
2. **Preferred refinement** (keeps traces alive across position-only drags): capture a semantic
   fingerprint at trace time — reuse `shallowNodeHash` (`frontend/src/utils/shallowNodeHash.ts`,
   already excludes position) over the resolved graph from `resolveGraphFromRefs`, plus the edge
   list and preamble. Store it next to `tracedCell` inside `useTracing`. A `useEffect` on
   `[nodes, edges, preambleRef.current]` recomputes and calls `clearTrace()` on divergence. With
   this in place the two explicit wire-ups in (1) become redundant but harmless; keep the
   `onUpdateNode` one for immediacy.
3. Do **not** silently re-fetch the trace after invalidation — the row identity may no longer exist.
   Clearing back to the NodePanel is the honest state; pair with T09's persistent panel states if
   both land.

## Failing tests first (vitest)

1. `useTracing` unit: populate `traceResult` via a mocked `traceCell`; re-render the hook with a
   `nodes` array where one traced node's `data.config` changed; assert `traceResult === null`.
   (With approach 2 this is the core regression test; with approach 1 alone, test via App below.)
2. App integration (`App.integration.test.tsx` pattern): drive a cell click → trace shown; dispatch
   a WebSocket graph-update message; assert the trace panel unmounts and no node carries
   `_traceActive`.
3. App integration: same, but through `onUpdateNode` on the traced target node.
4. Negative: a position-only node move does **not** clear the trace (approach 2 only).

## Acceptance

- Editing any node's config, adding/removing nodes/edges, or receiving a file-watcher graph update
  clears trace state (panel + glow + `tracedCell`) synchronously in the same render pass.
- Position-only changes preserve the trace (if approach 2).
- No regression in the existing `clearTrace` call sites (Escape/pane/edge tests still green).
