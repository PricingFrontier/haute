# Frontend and canvas improvement backlog

## Scope

Owns shared graph-canvas/editor state, frontend cache identity, WebSocket sync,
cross-node browser journeys, visible failure contracts, and shared
visual/keyboard/accessibility evidence. Feature-specific UI remains with its
component. Current contracts span the
[frontend shared](../../../specs/frontend-shared/high-level.md),
[graph canvas](../../../specs/frontend-graph-canvas/high-level.md), and
[node editor](../../../specs/frontend-node-editors/high-level.md) specs.

## Work queue

| Package | State | Priority | Candidate outcome | Source |
|---|---|---|---|---|
| AUD-C16 | Reverify | P0 | Make frontend preview/schema identity include every source and configuration input that changes visible results. | [Audit cluster C16](../../../review/REMEDIATION-PLAN.md#c16-frontend-source-blind--under-keyed-cache-identity-stale-schema--preview-shown-as-current) |
| AUD-C17 | Reverify | P0 | Give sync/watcher messages identity and ordering so stale or wrong graphs cannot be applied to the canvas. | [Audit cluster C17](../../../review/REMEDIATION-PLAN.md#c17-frontend-websocket-sync-soundness-wrongstale-graph-applied-to-canvas) |
| ROAD-UI-01 | Active | P1 | Maintain a user-journey and production configuration-shape matrix with named ownership. | [Frontend quality milestone 1](../../frontend-ui-quality.md#1-maintain-the-user-journey-and-configuration-shape-matrix) |
| ROAD-UI-02 | Active | P1 | Add deterministic cross-node browser journeys for the highest-risk persisted workflows. | [Frontend quality milestone 2](../../frontend-ui-quality.md#2-add-deterministic-cross-node-browser-journeys) |
| ROAD-UI-03 | Active | P1 | Make important missing upstream choices visible without false warnings during drafts/loading. | [Frontend quality milestone 3](../../frontend-ui-quality.md#3-make-important-missing-data-visible) |
| ROAD-UI-04 | Active | P2 | Add a small, stable visual and keyboard/focus/label assurance set. | [Frontend quality milestone 4](../../frontend-ui-quality.md#4-add-targeted-visual-and-interaction-assurance) |
| ROAD-UI-05 | Active | P2 | Keep UI CI risk-based and make every user-found regression cumulative. | [Frontend quality milestone 5](../../frontend-ui-quality.md#5-keep-ci-risk-based-and-regressions-cumulative) |

## Dependencies

- [Edge Join](../edge-join/README.md) owns Edge Join insertion and feature
  browser acceptance while consuming the shared journey conventions.
- [Engineering quality](../engineering-quality/README.md) owns shared test
  tiers, fixture policy, and CI enforcement.
- Modelling, Optimiser, Explore, Git, I/O, Rating, and Tracing own the
  semantics of their specialised panels.

## Evidence and retirement

The [frontend UI quality roadmap](../../frontend-ui-quality.md) is the active
acceptance source. Audit cache/sync packages require current reproductions.
Retire shared packages only when user-visible state transitions and persistence
are proved at the appropriate unit/browser tier and the ownership matrix names
the feature component responsible for future regressions.
