# Frontend Preview & Explore — Low-Level Specification

## Module map

| File | Responsibility |
| --- | --- |
| `frontend/src/panels/DataPreview.tsx` | Virtualised preview table, frame selection, search, cell callbacks and value formatting. |
| `frontend/src/panels/PreviewPanelFrame.tsx`, `frontend/src/panels/PreviewPanelTabs.tsx` | Resizable/collapsible frame and generic ARIA tab strip with optional visible/assistive per-tab indicators. |
| `frontend/src/panels/previewPanelLayout.ts` | Shared preview-panel dimensions and header/action layout constants. |
| `frontend/src/components/ExecutionDiagnosticsSummary.tsx` | Actionable execution-diagnostic banner owned by [frontend-modelling-optimiser-ui](../frontend-modelling-optimiser-ui/low-level.md) and consumed by Explore progress and cache reports. |
| `frontend/src/components/ExecutionDiagnosticsIndicator.tsx` | Compact preview-header execution diagnostic indicator. |
| `frontend/src/panels/ExplorePreview.tsx` | Explore run/cancel/store lifecycle and Preview/Overview/Charts tab composition. |
| `frontend/src/panels/UtilityPanel.tsx` | Utility-module list/read/create/delete/editor UI with debounced, flushable saves and syntax-error display. `App.tsx` loads the panel through a lazy import only after the user opens Utility, keeping its editor and API path out of startup JavaScript. |
| `frontend/src/panels/explore/cacheIdentity.ts` | Upstream-lineage/config identity for an Explore cache request. |
| `frontend/src/panels/explore/overviewCardDefinitions.ts`, `frontend/src/panels/explore/overviewConfig.ts` | Ordered overview-card registry and defensive config reader. |
| `frontend/src/panels/explore/ExploreOverviewPane.tsx` | Enabled-card/empty-state dispatcher. |
| `frontend/src/panels/explore/chartConfig.ts`, `frontend/src/panels/explore/ExploreChartsPane.tsx` | Strict chart-card config parsing/shared identities and enabled-placeholder visualisation dispatch. |
| `frontend/src/panels/explore/ExploreSummaryCards.tsx`, `frontend/src/panels/explore/SchemaTableCard.tsx` | Dataset, quality, numeric, categorical and schema report cards, including card-specific export grids. |
| `frontend/src/panels/explore/ExploreTableActions.tsx` | Read-only copy-as-TSV and download-as-CSV actions for supported Explore tables, built on the shared table serializers. |
| `frontend/src/panels/explore/DistinctInfoButton.tsx`, `frontend/src/panels/explore/StatValueCell.tsx` | Distinct-count explanation and reusable optional-stat cell. |

## Key types and data structures

- `PreviewData` in `frontend/src/panels/DataPreview.tsx` carries status, schema, preview rows,
  optional frame schema/selection and execution diagnostics. The table combines preview columns
  with selected-frame/flat schema so a returned value is never omitted merely for missing dtype.
- `OverviewConfig` is `Partial<Record<OverviewCardKey, boolean>>`; the fixed
  `OVERVIEW_CARD_DEFINITIONS` order is authoritative regardless of raw-object key order.
- Explore jobs/results in `frontend/src/stores/useNodeResultsStore.ts` are accepted only when
  a cached result's stored `configHash` matches `frontend/src/panels/ExplorePreview.tsx`'s newly
  calculated canonical identity. Active jobs are node-owned and remain actionable across
  identity changes.

## Control flow

### Preview table

1. `frontend/src/panels/DataPreview.tsx` derives a lower-cased column search index when columns
   change and filters it without rebuilding that index per keystroke.
2. A `ResizeObserver` and scroll handler determine the row/column windows. Scroll updates are
   coalesced to animation frames; row virtualisation begins after 50 rows and horizontal windows
   render spacer cells for skipped columns.
3. One delegated tbody click handler reads row/column dataset attributes and calls the supplied
   trace callback. Embedded mode omits outer frame chrome; normal mode uses the shared frame.

### Explore and overview

1. `frontend/src/panels/explore/cacheIdentity.ts` finds all upstream nodes, removes Explore
   overview, pivot, and chart display settings from data-affecting config, and includes
   submodels/preamble.
2. `frontend/src/panels/ExplorePreview.tsx` canonicalises that identity together with the active
   source. It ignores cached results with a different identity, but keeps the node's active job
   visible and cancellable using the source that job actually started with. It records immediate
   cache hits as completed results and background starts as jobs.
3. Start failures, and cancellation responses without a completed report, call the result-store
   failure path; thrown start/cancel errors also toast. `useBackgroundJobs` in frontend-shared
   polls background Explore jobs and moves terminal responses into the result store. A visible
   report is touched to update cache recency. Preview, Overview, and Charts mount only for their
   active tab; a remembered value from a still-unsupported pane normalises to Preview.
   `ExploreOverviewPane` is a `React.lazy` boundary, so its report-card and export code stays out
   of startup JavaScript. Suspense renders a labelled Overview loading state inside the existing
   tabpanel until that module is ready.
4. `frontend/src/panels/explore/overviewConfig.ts` drops malformed config values. The overview
   pane renders no-enabled-cards, no-report, or the ordered enabled renderer set.
   `frontend/src/panels/explore/chartConfig.ts` instead returns an explicit parse failure for a
   malformed `charts` block. The Charts pane renders that diagnostic, or renders enabled chart
   placeholders in persisted order without requiring an Explore cache report.
5. Schema, numeric-summary, and categorical-summary cards derive a `TableGrid` from the exact
   display fields they render and pass it to `ExploreTableActions`. Copy serialises the header
   and every exported row as TSV; download uses the shared CSV escaping helper. Schema supplies
   every column matching its current search query, not only the current 50-row page. Numeric and
   categorical summaries export their complete profile lists. The actions do not offer JSON
   sharing or paste-in because Explore reports are read-only analysis artifacts. The shared
   serializers remain click-loaded from the already-lazy Overview code.
6. `SchemaTableCard` derives one factual Profile cell per column from the report's additive
   quality fields: ID candidate, high cardinality, text length min/mean/max, and temporal span.
   The same text participates in schema search and full filtered TSV/CSV export; an unflagged
   column renders an em dash.

`DataPreview` consumes guarded version-1 execution metrics through
`ExecutionDiagnosticsIndicator`: projected/admitted/not-planned states stay
silent; a boundary or rejection places a warning/error icon immediately after
the row/column summary, and memory pressure uses the warning path. Activating
the icon explains projection limits, correctness, possible I/O/memory cost,
and remediation without exposing raw bounded-collection JSON.
`ExplorePreview` passes progress or cache-report metrics to
`ExecutionDiagnosticsSummary`, whose technical detail is disclosed on demand.

### Utility editing concurrency

1. `frontend/src/App.tsx` mounts `UtilityPanel` behind a local `Suspense` boundary only while
   `utilityOpen`; the bundle checker treats its chunk as lazy-only and rejects startup preload.
2. `frontend/src/panels/UtilityPanel.tsx` loads files on mount and selects the first file when
   none is active. Switching files first awaits `flushSave()`; a pending/in-flight failure or an
   already-settled rejected draft stops the switch, and the draft plus inline error remain visible
   until a later save succeeds.
3. Edits debounce for 500ms. Unmount flushes any pending write fire-and-forget; post-await
   state updates verify both mounted state and the module still selected, dropping stale replies.
4. Delete explicitly cancels a pending save for the deleted file. Create refreshes the list,
   loads the new module and passes the server-returned import line back to the preamble owner.

## Edge cases and invariants

- A multi-frame preview may have no flat columns: selected-frame columns supply the visible schema
  and header count. Preview-only columns remain visible with an unknown/empty dtype.
- `null`/`undefined` display separately from Haute non-finite-float sentinel objects. Table
  windows clamp if a changed result becomes narrower while horizontally scrolled.
- Explore refuses run while there is no input or a job is active. An instant completed response
  without a report, or a started response without `job_id`, is an explicit failure rather than a
  false success.
- A valid empty/missing chart array prompts the user to add a chart. A non-empty array with every
  card disabled reports that no charts are shown. Duplicate/blank ids and wrong-typed fields are
  invalid configuration, not empty state.
- Explore progress is a determinate ARIA progressbar only while the run is busy. Its value is
  the displayed fraction clamped to 0-100; native buttons give export actions keyboard semantics,
  and both are disabled when their grid has no body rows.
- Utility save replies cannot clear/show errors for a different active module. A 400 API detail
  matching `line N` highlights that line; list/load errors are toast-visible, not interpreted as
  a missing utility directory.
- The frame restores its saved height after expand-to-top and uses parent height, own bottom edge,
  then viewport height when measuring available space.
- `PreviewPanelTabs` gives exactly one enabled tab `tabIndex=0`; Left/Right wrap across enabled
  tabs, Home/End select the boundary tab, and disabled tabs are skipped.

## Error handling

Preview `loading` and `error` statuses are ordinary render branches. Explore records a terminal
error for a failed start or a cancellation that does not return a completed report; thrown
start/cancel exceptions also toast. Actionable cache-report execution metrics render in the
shared diagnostics banner. Utility syntax errors remain inline and block a requested file switch;
other file-operation failures toast or display action-local text. Cache/report/card shape is
assumed to meet the API contract; optional overview settings alone are parsed defensively.
Missing or unsupported execution diagnostics stay silent; the primary preview
or Explore failure remains authoritative and no diagnostic-unavailable success
state is fabricated.

## Testing

Tests live in `frontend/src/panels/__tests__/DataPreview.test.tsx`,
`frontend/src/panels/__tests__/PreviewPanelFrame.test.tsx`,
`frontend/src/panels/__tests__/PreviewPanelTabs.test.tsx`,
`frontend/src/panels/__tests__/ExplorePreview.test.tsx` and
`frontend/src/panels/__tests__/UtilityPanel.test.tsx`, plus the focused overview suites under
`frontend/src/panels/explore/__tests__/` and
`frontend/src/__tests__/editors/ExploreChartsConfig.test.tsx`. They cover virtualisation, frames, search, trace click
delegation, boundary/rejected execution diagnostics, cache identity/result/job lifecycle,
overview/chart card ordering and config, chart list/configure/back/toggle behavior, chart
visualisation empty/error states, roving-tab accessibility, utility save-flush/stale-response behaviour and
syntax errors. The Explore suites also pin progressbar name/value semantics, TSV headers and
contents, RFC-4180 CSV quoting through the download blob, full filtered-schema export across
pagination, disabled empty-table actions, and native-button accessibility.
`frontend/src/__tests__/App.utilityPanelLazy.test.ts` and the bundle-budget tests
guard the Utility panel's on-demand chunk boundary. Shared layout/constants and small visual
helpers are exercised through these component tests rather than owning standalone suites.

Browser preview/smoke coverage is in `frontend/e2e/core-flows.spec.ts`,
`frontend/e2e/data-preview-scroll.benchmark.spec.ts`, and `frontend/e2e/smoke.spec.ts`.

## Modelling config panes

The modelling pane behavior is defined by
[frontend-modelling-optimiser-ui](../frontend-modelling-optimiser-ui/high-level.md#modelling-config-panes).
`frontend/src/panels/PreviewPanelTabs.tsx` is the generic tab-strip owner and accepts one
optional per-tab indicator descriptor with a semantic kind and accessible label. It renders a
compact visible mark/text without relying on colour alone and includes the indicator meaning in
the tab's accessible name or description. Callers that omit indicators render exactly as before.

Indicators do not change the active key, enabled-tab list, click behavior, `aria-controls`,
equal-width sizing, or the existing roving-focus contract: exactly one enabled tab is tabbable;
Left/Right wrap, Home/End choose the boundaries, and disabled tabs are skipped. Updating only an
indicator must not move focus or select a different tab.

`frontend/src/panels/__tests__/PreviewPanelTabs.test.tsx` proves warning and active indicators,
visible/non-colour-only output, assistive text, indicator-only rerenders, and unchanged mouse,
disabled-tab, ARIA and Arrow/Home/End behavior. The modelling and node-editor components are
recorded consumers in `specs/ownership.toml`.
