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
| EDA-R03 | Planned | P3 | An empty pivot result charts as empty rather than as a configuration error, the chart environment observer exists once, nulls use the glyph the specification names, a pivot start failure is recorded without a fabricated job id, and the Explore panels' dead exports are gone. |
| EDA-R04 | Planned | P2 | `PivotTableGrid` resolves header formats by `result.column_fields` / `result.row_fields`, async confirmation callbacks in `ExplorePivotsConfig` and `ExploreChartsConfig` read the latest node config, and `ExplorePreview` renders `AskAssistantButton` on execution errors. |

`EDA-R03` comes from the
[25 October 2026 codebase review](codebase-review-2026-10-25.md).

## Planned improvements

### EDA-R03 — The Explore panels' empty state, observer, glyph and sentinel

**Why:** A pivot result with no row paths (a filter matching no rows with row
grand totals off) makes the chart card show a danger alert saying the result
lacks the coordinates a chart requires and the configuration should be reviewed,
although nothing is misconfigured, while the Pivots pane shows a neutral "No rows
match" for the same result. The theme and reduced-motion observer effect is
duplicated line for line between Explore's combo chart and the workbench's chart.
Null cells render a hyphen where the specification says an em dash and the
Segments tab uses one. A pivot start failure is recorded by fabricating a job
entry with a time-based id and then failing it. Five Explore exports are
referenced only at their definitions and several more only inside their module.

**Plan:** Return an empty dataset for an empty result and keep the error for a
result with cells but no coordinates; one `useChartEnvironmentRevision` hook
beside the shared chart theme; one glyph; a store action that records a start
failure without a job id; delete and un-export the symbols.

**Acceptance:** An empty filtered pivot shows the pane's empty wording on its
chart; jscpd reports no observer pair; the glyph matches the specification;
knip reports none of the exports.

**Dependencies:** None.

**Evidence:** `frontend/src/panels/explore/chartData.ts::adaptPivotChartData`;
`frontend/src/panels/explore/ExploreChartsPane.tsx`;
`frontend/src/panels/explore/ComboChart.tsx`; `frontend/src/workbench/ChartBody.tsx`;
`frontend/src/panels/explore/PivotTableGrid.tsx`;
`frontend/src/panels/explore/useExplorePivotActions.ts`;
`frontend/src/panels/explore/pivotConfig.ts`;
`frontend/src/panels/explore/chartConfig.ts`.

### EDA-R04 — Pivot header format lookup by field name, fresh config reads after confirmation dialogs, and Explore error assistant action
**Why:** Three defects affect the Explore pivot grid, card editors, and preview panel:
1. `PivotTableGrid` looks up header number formats by positional index
   (`pivot.columns[level]?.format` and `pivot.rows[level]?.format`) instead of
   matching `result.column_fields[level]` and `result.row_fields[level]` against
   `pivot.columns` / `pivot.rows` by `field`. When row or column fields are
   reordered or removed in the draft while a retained `ExplorePivotResult` is still
   displayed, headers are formatted with the wrong field's `PivotNumberFormat`.
2. In `ExplorePivotsConfig` (`deleteSelectedPivot`) and `ExploreChartsConfig`
   (`deleteSelectedChart`, `handleSourcePivotSelect`), the `confirmed(...).then(...)`
   callbacks write closure-captured `pivots` / `charts` / `chart` arrays from before
   the confirmation dialog opened, overwriting any edits that arrived while the
   dialog was open.
3. `ActiveNodePreviewBody` in `App.tsx` passes `errorAction` (`AskAssistantButton`)
   to `DataPreview` for standard nodes, but omits it when rendering `ExplorePreview`
   for an `EXPLORE` node.

**Plan:** Resolve each level's format in `PivotTableGrid` by matching
`result.column_fields[level]` and `result.row_fields[level]` by `field` name against
`pivot.columns` and `pivot.rows`; read the latest `pivots` / `charts` from the
current node config inside the `confirmed(...).then(...)` callbacks (fenced by
document identity); and pass `errorAction` through `ActiveNodePreviewBody` to
`ExplorePreview`.

**Acceptance:** Reordering two column fields with different number formats in the
draft while a retained result is shown keeps each header formatted according to its
own `result.column_fields[level]`; an edit landing while a delete or source-switch
confirmation is open is not clobbered when the dialog resolves; and an execution
error on an Explore node renders the `AskAssistantButton` in `ExplorePreview`.

**Dependencies:** None.

**Evidence:** `frontend/src/panels/explore/PivotTableGrid.tsx::PivotTableGrid`;
`frontend/src/panels/editors/ExplorePivotsConfig.tsx::ExplorePivotsConfig`;
`frontend/src/panels/editors/ExploreChartsConfig.tsx::ExploreChartsConfig`;
`frontend/src/App.tsx`;
`frontend/src/panels/ExplorePreview.tsx::ExplorePreview`.
