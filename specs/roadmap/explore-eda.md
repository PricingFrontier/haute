# Explore / EDA roadmap

## Scope

Explore reports and their attached pivot/chart presentations provide correct,
bounded, cached analysis and clear analyst workflows. Current behaviour is
specified in [Explore / EDA](../explore-eda/high-level.md),
[frontend node editors](../frontend-node-editors/high-level.md), and
[frontend preview/explore](../frontend-preview-explore/high-level.md).

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| EDA-E09 | Planned | P2 | Add bounded server-binned distributions. |
| EDA-E10 | Planned | P2 | Add one cached on-demand relationship/key-analysis service. |
| EDA-E18 | Deferred | P3 | Evaluate advanced Excel-parity pivot operations after representative use. |
| EDA-E23 | Deferred | P3 | Add shared chart filter and hierarchy interactions after representative use. |
| EDA-E24 | Deferred | P3 | Evaluate the remaining PivotChart parity surface from evidence. |

## Planned improvements

### EDA-E09 — Distribution charts

**Why:** Analysts need distributions without client-side raw-data processing.

**Plan:** Emit capped server-binned numeric histograms from the bounded report
path and render them with explicit empty/skipped states.

**Acceptance:** Tests cover null, constant, negative, and wide-schema guardrail
cases plus chart rendering.

**Dependencies:** The current bounded Explore collection, cache, and tab/panel
contracts.

**Evidence:** `src/haute/routes/_explore_service.py`;
`src/haute/schemas.py`; `frontend/src/panels/explore`;
`tests/test_explore_routes.py`.

### EDA-E10 — Target relationships

**Why:** A report needs bounded, target-aware signals for feature investigation.

**Plan:** After EDA-E09 establishes the bounded distribution primitives, add
one on-demand analysis job/cache surface with explicit cache-miss and
cancellation behaviour. It owns bounded numeric/categorical target
aggregations, target/weight configuration, and exact user-selected
multi-column key uniqueness checks. Key analysis is not a second synchronous
scan path or a base-report estimate.

**Acceptance:** Tests cover cache miss, cancellation/supersession, target and
weight validation, numeric and categorical results, bounded levels, ranked UI
rendering, exact single-/multi-column key counts, unhashable key rejection, and
cache identity for the selected analysis and columns.

**Dependencies:** EDA-E09 plus the current bounded collection, dataframe-cache,
job-lifecycle, and tab/panel contracts.

**Evidence:** `src/haute/routes/explore.py`;
`src/haute/routes/_explore_service.py`; `frontend/src/panels/explore`;
`tests/test_explore_routes.py`.

### EDA-E18 — Advanced Excel pivot parity

**Why:** Excel includes valuable higher-order pivot operations, but adding them
without representative use would multiply schema, execution, and UI complexity
without evidence that the additional surface is warranted.

**Plan:** Use real pivot layouts and cardinality metrics to prioritise date
grouping, per-level subtotals, Show Values As, calculated fields/items, slicers,
conditional formatting, and richer number formats. Each selected feature
receives its own spec amendment and bounded-execution contract; unselected
features remain explicitly unsupported rather than partially emulated.

**Acceptance:** A decision record ranks the advanced operations using usage,
scale, accessibility, and execution-cost evidence, and creates bounded child
packages only for selected work.

**Dependencies:** The current bounded pivot configuration/result contract plus
representative production evidence.

**Evidence:** `src/haute/routes/_pivot_service.py`; `frontend/src/panels/explore/pivotConfig.ts`; `frontend/src/panels/explore/ExplorePivotsPane.tsx`; `tests/test_explore_pivots.py`; pivot configuration/result telemetry and support feedback.

### EDA-E23 — Shared PivotChart interactions

**Why:** Excel-style chart filtering and hierarchy navigation are useful only
when their effect on the associated pivot and every dependent chart is clear.
Private chart filters would create a second, divergent analysis definition.

**Plan:** After the linked ComboChart workflow has representative use, add
field/filter controls that edit the source pivot's persisted filters rather
than creating chart-local copies. Before committing, show the pivot name and
count/list of dependent charts that will become stale. Commit the pivot change
once, refresh it once, and update every dependent pivot table and chart from
the one new result. When the pivot contract supports hierarchy state, add
shared expand/collapse; if EDA-E18 admits slicers, allow one slicer to target an
explicit pivot and therefore all linked charts. Legend visibility remains
local ephemeral view state and is not confused with persisted pivot filtering.

**Acceptance:** Tests cover shared filter impact disclosure, cancel/confirm,
one graph history edit, one pivot job for many dependents, stale/fresh
transitions, failed refresh retention, hierarchy propagation, slicer target
isolation, keyboard interaction, and no private aggregation or filter cache.

**Dependencies:** The current source-linked pivot/ComboChart contract plus
representative interaction evidence; slicer work also depends on EDA-E18.

**Evidence:** `src/haute/routes/_pivot_service.py`; `frontend/src/panels/explore/ExploreChartsPane.tsx`; `frontend/src/panels/explore/ComboChart.tsx`; `tests/test_explore_charts.py`; `frontend/src/panels/explore/__tests__/ExploreChartsPane.test.tsx`; pivot/chart interaction telemetry and the relevant pivot editor, result-store, Charts pane, and end-to-end workflow tests.

### EDA-E24 — Broader PivotChart parity

**Why:** Excel exposes many chart and formatting operations, but supporting
them without evidence would expand the renderer, schema, and validation surface
speculatively.

**Plan:** Use representative layouts and interaction metrics to prioritise
pie/doughnut, additional area presets, reusable chart templates,
drill-through, and SVG/data export. Keep scatter, bubble, stock, and any chart
that requires row-level observations explicitly unsupported while the source
contract is a categorical pivot matrix. Each selected capability receives a
spec amendment, typed compatibility rules, accessibility requirements, and a
bundle/performance budget before implementation.

**Acceptance:** A decision record ranks candidates by analyst use, pivot-shape
compatibility, accessibility, bundle cost, and rendering scale, and creates
bounded child packages only for selected work. Unsupported kinds fail clearly
and never reinterpret pivot categories as row-level observations.

**Dependencies:** The current source-linked ComboChart contract, EDA-E23 where
the candidate crosses shared interactions, and representative use evidence.

**Evidence:** `src/haute/routes/_pivot_service.py`; `frontend/src/panels/explore/chartConfig.ts`; `frontend/src/panels/explore/ExploreChartsPane.tsx`; `tests/test_explore_charts.py`; chart configuration/render telemetry, export requests, support feedback, and measured browser/bundle performance.
