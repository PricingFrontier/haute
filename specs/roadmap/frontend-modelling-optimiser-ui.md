# Frontend modelling and optimiser UI roadmap

## Scope

The modelling and optimiser configuration and result panels. Current
behaviour is specified in
[the frontend modelling and optimiser UI specification](../frontend-modelling-optimiser-ui/high-level.md).
These packages come from the
[23 September 2026 codebase review](codebase-review-2026-09-23.md).

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| FMO-R01 | Planned | P3 | Modelling result tabs, target configuration and result charts share one implementation. |

## Planned improvements

### FMO-R01 — Shared result tabs and charts
**Why:** The lift, residuals, actual-versus-expected, partial-dependence and
loss tabs share 60- to 90-line copied blocks. The GLM target configuration and
the target-and-task configuration share a 75-line block. Only the Explore
combo chart uses ECharts; the modelling, optimiser and banding charts are
hand-drawn SVG, and the convergence chart re-implements the axis scaling that
`chartHelpers` provides for the frontier chart.

**Plan:** Extract a shared result-tab scaffold and one target-configuration
component. Decide one chart approach (the shared SVG scaffold and helpers, or
ECharts) and move the hand-drawn charts onto it.

**Acceptance:** The listed copied blocks are single components; every chart
uses the chosen approach's shared axis and scale helpers; the modelling and
optimiser panel tests pass.

**Dependencies:** `MOD-T08` (modelling) also removes duplicated GLM pane code;
take them together if both are open.

**Evidence:** `frontend/src/panels/modelling/LiftTab.tsx`;
`frontend/src/panels/modelling/ResidualsTab.tsx`;
`frontend/src/panels/modelling/AveTab.tsx`;
`frontend/src/panels/modelling/PdpTab.tsx`;
`frontend/src/panels/modelling/LossTab.tsx`;
`frontend/src/panels/modelling/GLMTargetConfig.tsx`;
`frontend/src/panels/modelling/TargetAndTaskConfig.tsx`;
`frontend/src/panels/optimiser/ConvergenceChart.tsx`;
`frontend/src/utils/chartHelpers.ts`;
`frontend/src/panels/explore/chartRuntime.ts`.
