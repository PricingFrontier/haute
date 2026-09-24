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
| FMO-R01 | Planned | P3 | The banding histogram and the two-chart result layouts use the shared chart pieces. |

## Planned improvements

### FMO-R01 — The last charts onto the shared pieces
**Delivered so far:** the chart approach is the shared hand-drawn SVG (the
`ChartScaffold` primitives and the `utils/chartHelpers.ts` axis and scale
helpers, which absorbed the modelling `chartGeometry` module); ECharts stays
in the Explore combo chart. Every modelling validation chart draws its value
axis through `ChartValueGrid`; AvE and PDP share `FeatureDiagnosticTab`; the
GLM and tree-family target configurations share `modelColumnChoices` and
`WeightOffsetFields`; and the frontier, data-preview and convergence charts
scale through the shared helpers.

**Why:** The banding histogram still computes its own extent and positions
bars in percentages, and the Lift and Residuals tabs each lay their two charts
out side by side with their own width breakpoints and gap arithmetic.

**Plan:** Move the banding histogram onto `ResponsiveChart`/`ChartSvg` and the
shared scale helpers (its unpadded, edge-to-edge extent is deliberate and must
survive), and extract one two-chart result layout for Lift and Residuals.

**Acceptance:** Every chart uses the shared axis and scale helpers; the
banding, modelling and optimiser panel tests pass.

**Dependencies:** `MOD-T08` (modelling) also removes duplicated GLM pane code;
take them together if both are open.

**Evidence:** `frontend/src/panels/editors/banding/BandingHistogram.tsx`;
`frontend/src/panels/modelling/LiftTab.tsx`;
`frontend/src/panels/modelling/ResidualsTab.tsx`;
`frontend/src/utils/chartHelpers.ts`.
