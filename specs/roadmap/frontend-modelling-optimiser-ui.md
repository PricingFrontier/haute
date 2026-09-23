# Frontend modelling and optimiser UI roadmap

## Scope

The modelling and optimiser configuration and result panels. Current
behaviour is specified in
[the frontend modelling and optimiser UI specification](../frontend-modelling-optimiser-ui/high-level.md).
GLM pane work is planned in the [modelling roadmap](modelling.md). These
packages come from the
[23 September 2026 codebase review](codebase-review-2026-09-23.md).

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| FMO-R01 | Planned | P3 | Modelling result tabs, target configuration and result charts share one implementation. |
| FMO-R02 | Planned | P2 | A training estimate that cannot size its input says why, and never reports that the data fits in 0 MB. |

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

### FMO-R02 — An unavailable training estimate says why
**Why:** The training estimate has two specified unavailable outcomes, and
`POST /api/modelling/estimate` carries no reason for either. When the
target's cardinality cannot be proven, the estimate has no row total and the
training panel hides the estimate entirely, so the user cannot tell that one
was attempted or what would make one possible. When the cardinality is known
but the schema cannot be resolved, the estimate keeps the row total with zero
bytes per row, and the panel reports "Dataset fits in memory" with 0 MB of
estimated training RAM: a reassurance the estimator never gave. The optimiser
estimate's null `total_rows` has no reason either, but the optimiser panel
does not display the total, so it needs none until it does.

**Plan:** Specify a closed set of unavailable reasons in the modelling
specification: cardinality not provable (naming the blocking node when
known) and schema not resolvable. Carry the reason on `RamEstimate` and
`TrainEstimateResponse`. In the training panel, show the reason in place of
the missing figure, and never show a memory verdict for an estimate without
a memory figure.

**Acceptance:** Each unavailable case returns its reason under test; the
training panel shows the reason instead of hiding the estimate or reporting
0 MB; an estimator exception is still an error response, never a reason.

**Dependencies:** None. `API-R03` (server API) would generate the response
type; this package does not wait for it.

**Evidence:** `src/haute/_ram_estimate.py::estimate_safe_training_rows`;
`src/haute/_ram_estimate.py::RamEstimate`;
`src/haute/routes/modelling.py::estimate_training`;
`src/haute/schemas.py::TrainEstimateResponse`;
`frontend/src/types/trainGuards.ts`;
`frontend/src/panels/modelling/TrainingActionsAndResults.tsx`.
