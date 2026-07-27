# Frontend and canvas roadmap

## Scope

Owns shared graph-canvas/editor state, cache identity, WebSocket
synchronisation, cross-node browser journeys, visible failure contracts, and
shared visual, keyboard, and accessibility evidence.

## Priorities

| Package | State | Priority | Outcome |
| --- | --- | --- | --- |
| ROAD-UI-01 | Active | P1 | Publish explicit per-variant ownership for the journey/configuration-shape matrix. |
| ROAD-UI-04 | Active | P2 | Record the accessibility-automation boundary decision. |
| CANVAS-STATE-01 | Queued | P2 | Make graph seeding a deliberate load transition with truthful undo history. |

## Planned improvements

### ROAD-UI-01 — Journey and configuration-shape matrix

**Why:** High-risk persisted shapes need a maintained owner, fixture, contract, and smallest proving test tier.

**Plan:** Publish one reviewed matrix beginning with continuous/categorical/breakpoint Banding-to-Rating shapes, mixed outputs, zero-level factors, malformed/partial config, and persisted tables. Use minimal hand-written, frozen production-shaped, and deterministic browser fixtures.

**Acceptance:** Every initial variant names owner, fixture, and tier; all level types are independently proved through persisted shapes; fixture representativeness is visible without reading a factory.

**Dependencies:** Feature owners retain specialised workflow coverage.

**Evidence:** `frontend/src/types/banding.ts`; `frontend/src/panels/editors/BandingEditor.tsx`; `frontend/src/panels/editors/RatingStepEditor.tsx`; `frontend/src/__tests__/utils/banding.test.ts`; `frontend/src/panels/editors/banding/__tests__/BandingRulesGrid.test.tsx`.

### ROAD-UI-04 — Targeted visual and interaction assurance

**Why:** Dense editing states need stable visual, focus, label, and keyboard evidence beyond component-local assertions.

**Plan:** Stabilise three selected states—mixed Banding, rebuilt three-factor Rating Step, selected optimiser result—at desktop and one documented supported narrow viewport; add a keyboard journey and decide the automation accessibility boundary before selecting a scanner.

**Acceptance:** Snapshot dimensions and support intent are recorded; protected actions complete by keyboard with visible focus and accessible labels; accessibility automation has a bounded reviewable purpose.

**Dependencies:** Delivered browser journeys (formerly ROAD-UI-02) provide stable fixtures and locators.

**Evidence:** `frontend/e2e`; `frontend/src/panels/editors/BandingEditor.tsx`; `frontend/src/panels/editors/RatingStepEditor.tsx`; `frontend/src/panels/OptimiserPreview.tsx`; `frontend/src/__tests__/traceMotionCss.test.ts`.

### CANVAS-STATE-01 — Deliberate graph seeding lifecycle

**Why:** `useGraphCanvasState` clears history only in its one-time seeding
effect, while production mounts it with empty arrays and later loads through a
different path. A load can therefore retain stale undo/redo state or bypass the
documented seed transition.

**Plan:** Define one graph-load action that installs nodes/edges, resets
fingerprints and history atomically, and is used by both initial and subsequent
pipeline loads.

**Acceptance:** Component tests cover empty mount followed by load, pipeline
switch, remount, undo, and redo; no history entry can refer to the previously
loaded graph.

**Dependencies:** Delivered WebSocket synchronisation (formerly `AUD-C17`) owns
remote message identity and ordering.

**Evidence:** `frontend/src/hooks/useGraphCanvasState.ts`,
`frontend/src/stores/useGraphStore.ts`, and
`frontend/src/hooks/__tests__/useGraphCanvasState.test.ts`.

## Delivered outcomes

- Complete frontend cache identity (`AUD-C16`) and fail-closed, ordered
  WebSocket graph synchronisation with retained dangling-edge warnings
  (`AUD-C17`) are enforced by
  `frontend/src/__tests__/stores/previewCache.test.ts` and
  `frontend/src/__tests__/hooks/useWebSocketSync.test.ts`.
- Deterministic cross-node browser journeys, visible zero-level Banding
  warnings, and the risk-based CI lane structure (`ROAD-UI-02`, `ROAD-UI-03`,
  `ROAD-UI-05`) are delivered through
  `frontend/e2e/canvas-assurance.spec.ts`, `frontend/src/utils/banding.ts`,
  and the measured `@smoke` cross-browser lane in
  `frontend/playwright.config.ts`; the tier narrative in
  [the frontend node-editors specification](../frontend-node-editors/low-level.md)
  records shape-to-tier coverage.
