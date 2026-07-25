# Frontend and canvas roadmap

## Scope

Owns shared graph-canvas/editor state, cache identity, WebSocket
synchronisation, cross-node browser journeys, visible failure contracts, and
shared visual, keyboard, and accessibility evidence.

## Priorities

| Package | State | Priority | Outcome |
| --- | --- | --- | --- |
| AUD-C16 | Reverify | P0 | Include all result-changing inputs in frontend identity. |
| AUD-C17 | Reverify | P0 | Apply only correctly identified and ordered graph sync messages. |
| ROAD-UI-01 | Active | P1 | Maintain an owned journey/configuration-shape matrix. |
| ROAD-UI-02 | Active | P1 | Add deterministic high-risk cross-node browser journeys. |
| ROAD-UI-03 | Active | P1 | Make meaningful missing upstream choices visible. |
| ROAD-UI-04 | Active | P2 | Add stable visual and keyboard/accessibility assurance. |
| ROAD-UI-05 | Active | P2 | Keep UI CI risk-based and regressions cumulative. |

## Planned improvements

### AUD-C16 — Complete frontend cache identity

**Why:** Source-blind schema state, node-only preview keys, and weak config digests can show stale results as current.

**Plan:** Reverify all cache dimensions; key schema and preview state by source, structural version, and row limit as applicable; invalidate ephemeral schema state on active-source change; use collision-resistant or exact canonical config comparison.

**Acceptance:** Switching any visible-result input cannot reuse stale schema/preview/result state; bounded cache eviction remains deterministic; regression tests cover each identity dimension.

**Dependencies:** Shared API/store interfaces and feature panels consume the identity contract.

**Evidence:** `frontend/src/hooks/usePipelineAPI.ts`; `frontend/src/stores/useNodeResultsStore.ts`; `frontend/src/stores/useSettingsStore.ts`; `frontend/src/__tests__/stores/previewCache.test.ts`; `frontend/src/hooks/__tests__/usePipelineAPI.gaps.test.ts`.

### AUD-C17 — Sound WebSocket graph synchronisation

**Why:** Fail-open source matching, ordering, edge validation, and graph-wide position heuristics can apply an old or wrong graph to the canvas.

**Plan:** Reverify each message path; invalidate in-flight updates after parse errors, compare source identity fail-closed, retain dangling endpoint/handle edges with a visible warning while excluding them from automatic layout, and lay out only nodes lacking real positions.

**Acceptance:** A stale/wrong update cannot clear a newer error or alter another pipeline; valid imported edges reference live nodes/ports, unresolved topology is never silently deleted, and every finite persisted position—including the origin—remains authoritative.

**Dependencies:** Canvas import/export and API sync boundaries.

**Evidence:** `frontend/src/hooks/useWebSocketSync.ts`; `frontend/src/utils/graphHelpers.ts`; `frontend/src/hooks/usePipelineAPI.ts`; `frontend/src/__tests__/App.integration.test.tsx`; `frontend/src/__tests__/adversarial/resilience.test.ts`.

### ROAD-UI-01 — Journey and configuration-shape matrix

**Why:** High-risk persisted shapes need a maintained owner, fixture, contract, and smallest proving test tier.

**Plan:** Publish one reviewed matrix beginning with continuous/categorical/breakpoint Banding-to-Rating shapes, mixed outputs, zero-level factors, malformed/partial config, and persisted tables. Use minimal hand-written, frozen production-shaped, and deterministic browser fixtures.

**Acceptance:** Every initial variant names owner, fixture, and tier; all level types are independently proved through persisted shapes; fixture representativeness is visible without reading a factory.

**Dependencies:** Feature owners retain specialised workflow coverage.

**Evidence:** `frontend/src/types/banding.ts`; `frontend/src/panels/editors/BandingEditor.tsx`; `frontend/src/panels/editors/RatingStepEditor.tsx`; `frontend/src/utils/__tests__/banding.test.ts`; `frontend/src/panels/editors/banding/__tests__/BandingRulesGrid.test.tsx`.

### ROAD-UI-02 — Deterministic cross-node browser journeys

**Why:** Component tests alone do not prove persisted Banding-to-Rating and optimiser configuration/result/apply workflows.

**Plan:** Use local production-shaped fixtures and semantic locators for Banding-to-Rating discovery, rebuild, edited relativity, save/reload; add focused optimiser configuration, result-identity, and deterministic MLflow-boundary journeys.

**Acceptance:** Browser coverage proves three named Banding factors and Cartesian entries across reload, optimiser constraint/range/frontier persistence, selected-point identity, local apply, and MLflow run/model identity without live services or coordinate guesses.

**Dependencies:** ROAD-UI-01 fixture convention; project isolation.

**Evidence:** `frontend/e2e/core-flows.spec.ts`; `frontend/src/panels/OptimiserConfig.tsx`; `frontend/src/panels/OptimiserPreview.tsx`; `frontend/src/panels/__tests__/OptimiserConfig.test.tsx`; `frontend/src/panels/__tests__/OptimiserPreview.test.tsx`.

### ROAD-UI-03 — Visible missing-data contracts

**Why:** A configured Banding output with no usable levels can disappear from choices without explaining the upstream problem.

**Plan:** Classify typed Banding configuration and show a named accessible warning only for loaded configured outputs with non-blank columns and no valid levels, or confirmed missing selected sources; aggregate affected outputs without hiding healthy choices.

**Acceptance:** Users can distinguish a real upstream defect from no Banding node, a draft, blank output, healthy levels, or loading; every affected output is named and normal choices remain usable.

**Dependencies:** ROAD-UI-01 shape fixtures; ROAD-UI-02 adds browser proof after component stability.

**Evidence:** `frontend/src/types/banding.ts`; `frontend/src/utils/banding.ts`; `frontend/src/panels/editors/RatingStepEditor.tsx`; `frontend/src/utils/__tests__/banding.test.ts`; `frontend/src/panels/__tests__/NodePanel.test.tsx`.

### ROAD-UI-04 — Targeted visual and interaction assurance

**Why:** Dense editing states need stable visual, focus, label, and keyboard evidence beyond component-local assertions.

**Plan:** Stabilise three selected states—mixed Banding, rebuilt three-factor Rating Step, selected optimiser result—at desktop and one documented supported narrow viewport; add a keyboard journey and decide the automation accessibility boundary before selecting a scanner.

**Acceptance:** Snapshot dimensions and support intent are recorded; protected actions complete by keyboard with visible focus and accessible labels; accessibility automation has a bounded reviewable purpose.

**Dependencies:** ROAD-UI-02 stable fixtures and locators.

**Evidence:** `frontend/e2e`; `frontend/src/panels/editors/BandingEditor.tsx`; `frontend/src/panels/editors/RatingStepEditor.tsx`; `frontend/src/panels/OptimiserPreview.tsx`; `frontend/src/__tests__/traceMotionCss.test.ts`.

### ROAD-UI-05 — Risk-based CI and cumulative UI regressions

**Why:** Additional lanes only help when their risk reduction, failure owner, artifact path, and overlap with the normal E2E gate are explicit.

**Plan:** Keep full PR browser E2E authoritative; measure candidate smoke/visual/cross-browser lanes before adding them; require user-found regressions to begin with a failing smallest test and update fixture/matrix policy.

**Acceptance:** Every UI test has an owner and tier rationale; any additional lane has reproducible artifacts and failure ownership; new UI bugs leave durable user-level evidence where appropriate.

**Dependencies:** ROAD-UI-01 matrix; existing CI/browser infrastructure.

**Evidence:** `frontend/package.json`; `frontend/playwright.config.ts`; `frontend/e2e`; `.github/workflows`; `frontend/src/__tests__/App.integration.test.tsx`.
