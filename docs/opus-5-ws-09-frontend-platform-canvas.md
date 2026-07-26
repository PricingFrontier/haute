# WS-09 — Frontend platform & graph canvas

Part of the Opus 5 review split (`opus-5-workstreams.md`). Evidence and fix guidance:
`opus-5-review.md`. Owner: unassigned · Status: not started.

**Branch:** `opus5/ws-09-frontend-platform-canvas`

## Mission

The frontend's shared foundation — the API client and parse guards, the result/graph stores,
job polling, modal shell — plus the graph canvas and its WebSocket live-sync. This stream
carries the review's only frontend critical (`frontend-graph-canvas-1`, a Wave-1 data-loss
bug that silently reverts externally-edited submodels) and owns the two files every other
frontend stream wants to touch (`api/client.ts`, `types/guards.ts`).

## Scope

| Component | C | H | M | L |
|---|---:|---:|---:|---:|
| frontend-shared | 0 | 4 | 8 | 7 |
| frontend-graph-canvas | 1 | 2 | 7 | 7 |
| **Total** | **1** | **6** | **15** | **14** |

## Priorities

**P1 — data loss (review Wave 1, critical):**

- `frontend-graph-canvas-1` (C): `useWebSocketSync` applies nodes/edges/preamble from a
  `graph_update` but drops the incoming `submodels` map, then calls `markSaved()`. The next
  save posts the stale map back and silently reverts externally-authored submodel state on
  disk. Apply `g.submodels` through `setSubmodelsRaw` in the same guarded block, include it
  in the rollback path, fail loudly if a frame legitimately omits it, and add the regression.
- `frontend-graph-canvas-5` (M): `submodels` is excluded from the persisted fingerprint and
  `dirty` on a rationale the code now contradicts — so nothing flags the divergence above.
  Fix together with the critical.

**P1 — trust boundary and polling load:**

- `frontend-shared-1` (H): twelve exported client functions bypass `types/guards.ts` while
  both specs claim every one is guarded — add guards or enumerate the exceptions with a
  stated reason each.
- `frontend-shared-3` (H): `useJobPolling` polls every 500 ms for a job's entire 24-hour
  lifetime because backoff resets on every successful poll; the spec claims a 5 s steady
  state. Decide the intended ramp, spec it, then change the code.
- `over-complication-3` (M): four independent background-job polling loops with four
  cancellation/backoff/timeout policies; the one in `api/client.ts` has no production caller
  — delete it and consolidate the rest.
- `frontend-shared-8` (L): `parse*` guards with a bare `catch { return null }` contradict the
  fail-loud parse-guard contract.

**P2 — bugs:** `frontend-shared-4` (ModalShell rebuilds its focus trap every parent render,
stealing keyboard focus), `frontend-shared-5` (aborted dispersion run leaves an orphaned
backend job), `frontend-shared-11` (`bootstrapHauteSession(true)` joins a non-forced
bootstrap), `frontend-shared-6` (fabricated 500 instead of the documented plain `Error`),
`frontend-graph-canvas-7` (claimed unknown-node-type guard does not exist),
`frontend-graph-canvas-11` (`MAX_HISTORY` eviction only on `undoStack`).

**P3 — spec truth:** fold both canvas contracts and the frontend-shared contracts, and
delete the "Current limitation" paragraph that asserts fixed defects as live
(`contracts-c-2` — dangerous, it invites reintroducing the permissive source match),
`contracts-c-4` / `frontend-shared-2` (djb2 vs canonical JSON), `contracts-c-7`,
`contracts-c-9`, `contracts-c-10`, `contracts-c-5`, `frontend-graph-canvas-2/-3/-4/-8`,
`frontend-shared-7`, `frontend-shared-12`, `testing-credibility-3`,
`testing-credibility-4`, plus the module-map and scope hygiene items
(`frontend-graph-canvas-6/-9/-10/-12/-13`, `frontend-shared-9/-10/-13/-14`).
Also apply `failure-model-2`'s frontend correction for WS-04: the documented 404 soft
fallback at `frontend-shared/high-level.md:270-273` is unreachable against the real backend.

## Finding inventory

Critical (1): `frontend-graph-canvas-1`.
High (6): `contracts-c-2`, `contracts-c-4`, `frontend-shared-1`, `frontend-shared-3`,
`testing-credibility-3`, `testing-credibility-4`.
Medium (15): `contracts-c-5`, `contracts-c-7`, `contracts-c-9`, `contracts-c-10`,
`frontend-graph-canvas-2`, `frontend-graph-canvas-3`, `frontend-graph-canvas-4`,
`frontend-graph-canvas-5`, `frontend-graph-canvas-8`, `frontend-shared-2`,
`frontend-shared-4`, `frontend-shared-5`, `frontend-shared-7`, `frontend-shared-12`,
`over-complication-3`.
Low (14): `frontend-graph-canvas-6`, `frontend-graph-canvas-7`, `frontend-graph-canvas-9`,
`frontend-graph-canvas-10`, `frontend-graph-canvas-11`, `frontend-graph-canvas-12`,
`frontend-graph-canvas-13`, `frontend-shared-6`, `frontend-shared-8`, `frontend-shared-9`,
`frontend-shared-10`, `frontend-shared-11`, `frontend-shared-13`, `frontend-shared-14`.

## File ownership (exclusive)

- `frontend/src/api/client.ts`, `api/dispersion.ts`, `types/guards.ts`, `types/node.ts`
- `frontend/src/stores/**` (`useGraphStore.ts`, `useNodeResultsStore.ts`, `useUIStore.ts`)
- `frontend/src/hooks/useWebSocketSync.ts`, `useJobPolling.ts`, `useBackgroundJobs.ts`,
  `usePipelineAPI.ts`, `useGraphCanvasState.ts`, `useEdgeHandlers.ts`,
  `useSubmodelNavigation.ts`
- `frontend/src/utils/graphHelpers.ts`, `layout.ts`, `graphSnapshot.ts`, `nodeTypes.ts`,
  `sanitizeName.ts`, `apiInputPorts.ts`
- `frontend/src/components/ModalShell.tsx`, `SettingsModal.tsx`, `KeyboardShortcuts.tsx`
- `docs/specs/frontend-shared/**`, `docs/specs/frontend-graph-canvas/**`
- Their tests under `frontend/src/__tests__/`, `frontend/src/hooks/__tests__/`,
  `frontend/src/utils/__tests__/`, `frontend/src/api/__tests__/`

## Cross-stream touchpoints

- **`api/client.ts` and `types/guards.ts` are the frontend's shared hubs.** WS-10, WS-11,
  WS-12 and WS-13 all have findings that reference them. Rule: those streams request the
  guard/endpoint change here (or send a reviewed patch) rather than editing directly.
  Land `frontend-shared-1`'s guard work early so the others can build on it.
- `frontend/src/App.tsx` and `panels/NodePanel.tsx` belong to WS-10 — the canvas fixes here
  must stay inside hooks/stores/utils.
- `frontend-graph-canvas-1` is the stale-mirror vector behind WS-05's `submodels-2` (dissolve
  trusting the client copy) — tell WS-05 when it lands; both fixes are needed.
- `frontend-node-editors-1` (WS-10) depends on this stream's retention behaviour for
  null-handle apiInput edges — do not change `filterIncomingEdges` semantics without telling
  WS-10.
- `over-complication-10` (Python keyword list copied four times) is WS-10's finding but two
  copies live in this stream's `utils/` — coordinate the de-duplication.

## Definition of done

- The critical is fixed with a `useWebSocketSync` regression proving an incoming submodel
  change reaches the store and survives a subsequent save; `submodels` participates in the
  dirty fingerprint.
- The client trust boundary is either enforced or accurately documented; polling has one
  policy and no dead loop.
- Canvas and frontend-shared contract sections folded and deleted, including the "Current
  limitation" paragraph; Testing sections index the real parallel test trees.
- Baseline entries for both components deleted; findings fixed or deferred with reasons.

## Verification

- `npm --prefix frontend test -- src/hooks/__tests__/useWebSocketSync` (all four suites)
- `npm --prefix frontend test -- src/__tests__/stores src/api/__tests__`
- `npm --prefix frontend run typecheck` and `npm --prefix frontend run lint`
- `uv run pytest tests/test_docs_accuracy.py -q` for the spec edits.
