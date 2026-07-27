# Frontend and canvas roadmap

## Scope

Owns shared graph-canvas/editor state, cache identity, WebSocket
synchronisation, cross-node browser journeys, visible failure contracts, and
shared visual, keyboard, and accessibility evidence.

## Priorities

| Package | State | Priority | Outcome |
| --- | --- | --- | --- |
| — | — | — | No active frontend/canvas roadmap package remains. |

## Planned improvements

There are no active frontend/canvas roadmap packages.

## Delivered outcomes

- `ROAD-UI-01` is the explicit eight-row Banding-to-Rating
  configuration-shape matrix in the frontend node-editor low-level
  specification. Each variant names its primary component owner,
  representative fixture, smallest proving tier, and browser-escalation rule.
- `ROAD-UI-04` records 1440×900 and 1024×768 as the reviewed assurance
  viewports for mixed Banding, rebuilt three-factor Rating, and selected
  optimiser states. The existing Playwright journey proves focus, Enter/Tab,
  and keyboard-save behaviour. Accessibility automation is deliberately
  bounded to semantic/ARIA/focus component assertions plus that stable
  keyboard journey; a blanket scanner is rejected until it has named routes,
  rules, browser, exception owners, and expiries.
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
- `CANVAS-STATE-01` makes both initial seeding and pipeline loads use one
  atomic full-snapshot transition. It preserves live runtime metadata, records
  an independently cloned persisted baseline, clears both histories, derives a
  clean state, and advances structural/panel cache identities monotonically so
  remounts and submodel-only switches cannot resurrect or reuse prior-document
  state. Store, hook, pipeline-load, and App integration regressions enforce
  the boundary.
