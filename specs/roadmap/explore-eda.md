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
| EDA-R04 | Planned | P2 | `PivotTableGrid` resolves header formats by `result.column_fields` / `result.row_fields`, async confirmation callbacks in `ExplorePivotsConfig` and `ExploreChartsConfig` read the latest node config, and `ExplorePreview` renders `AskAssistantButton` on execution errors. |

## Planned improvements

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
