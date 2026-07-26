# Frontend Preview & Explore — Low-Level Specification

## Module map

| File | Responsibility |
| --- | --- |
| `frontend/src/panels/DataPreview.tsx` | Virtualised preview table, frame selection, search, cell callbacks and value formatting. |
| `frontend/src/panels/PreviewPanelFrame.tsx`, `frontend/src/panels/PreviewPanelTabs.tsx` | Resizable/collapsible frame and generic ARIA tab strip. |
| `frontend/src/panels/previewPanelLayout.ts` | Shared preview-panel dimensions and header/action layout constants. |
| `frontend/src/components/ExecutionDiagnosticsSummary.tsx` | Modelling-owned actionable execution-diagnostic banner consumed by Explore progress and cache reports. |
| `frontend/src/components/ExecutionDiagnosticsIndicator.tsx` | Compact preview-header execution diagnostic indicator. |
| `frontend/src/panels/ExplorePreview.tsx` | Explore run/cancel/store lifecycle and Preview/Overview tab composition. |
| `frontend/src/panels/UtilityPanel.tsx` | Utility-module list/read/create/delete/editor UI with debounced, flushable saves and syntax-error display. |
| `frontend/src/panels/explore/cacheIdentity.ts` | Upstream-lineage/config identity for an Explore cache request. |
| `frontend/src/panels/explore/overviewCardDefinitions.ts`, `frontend/src/panels/explore/overviewConfig.ts` | Ordered overview-card registry and defensive config reader. |
| `frontend/src/panels/explore/ExploreOverviewPane.tsx` | Enabled-card/empty-state dispatcher. |
| `frontend/src/panels/explore/ExploreSummaryCards.tsx`, `frontend/src/panels/explore/SchemaTableCard.tsx` | Dataset, quality, numeric, categorical and schema report cards. |
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
   overview display settings from data-affecting config, and includes submodels/preamble.
2. `frontend/src/panels/ExplorePreview.tsx` canonicalises that identity together with the active
   source. It ignores cached results with a different identity, but keeps the node's active job
   visible and cancellable using the source that job actually started with. It records immediate
   cache hits as completed results and background starts as jobs.
3. Start failures, and cancellation responses without a completed report, call the result-store
   failure path; thrown start/cancel errors also toast. `useBackgroundJobs` in frontend-shared
   polls background Explore jobs and moves terminal responses into the result store. A visible
   report is touched to update cache recency. Preview and Overview are the only tabs and mount
   only for their active tab; a remembered value from a removed pane normalises to Preview.
4. `frontend/src/panels/explore/overviewConfig.ts` drops malformed config values. The overview
   pane renders no-enabled-cards, no-report, or the ordered enabled renderer set.

### Utility editing concurrency

1. `frontend/src/panels/UtilityPanel.tsx` loads files on mount and selects the first file when
   none is active. Switching files first awaits `flushSave()`; a pending/in-flight failure or an
   already-settled rejected draft stops the switch, and the draft plus inline error remain visible
   until a later save succeeds.
2. Edits debounce for 500ms. Unmount flushes any pending write fire-and-forget; post-await
   state updates verify both mounted state and the module still selected, dropping stale replies.
3. Delete explicitly cancels a pending save for the deleted file. Create refreshes the list,
   loads the new module and passes the server-returned import line back to the preamble owner.

## Edge cases and invariants

- A multi-frame preview may have no flat columns: selected-frame columns supply the visible schema
  and header count. Preview-only columns remain visible with an unknown/empty dtype.
- `null`/`undefined` display separately from Haute non-finite-float sentinel objects. Table
  windows clamp if a changed result becomes narrower while horizontally scrolled.
- Explore refuses run while there is no input or a job is active. An instant completed response
  without a report, or a started response without `job_id`, is an explicit failure rather than a
  false success.
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

## Testing

Tests live in `frontend/src/panels/__tests__/DataPreview.test.tsx`,
`frontend/src/panels/__tests__/PreviewPanelFrame.test.tsx`,
`frontend/src/panels/__tests__/PreviewPanelTabs.test.tsx`,
`frontend/src/panels/__tests__/ExplorePreview.test.tsx` and
`frontend/src/panels/__tests__/UtilityPanel.test.tsx`, plus the focused overview suites under
`frontend/src/panels/explore/__tests__/`. They cover virtualisation, frames, search, trace click
delegation, boundary/rejected execution diagnostics, cache identity/result/job lifecycle,
card ordering/config, roving-tab accessibility, utility save-flush/stale-response behaviour and
syntax errors. Shared layout/constants and small visual
helpers are exercised through these component tests rather than owning standalone suites.

Browser preview/smoke coverage is in `frontend/e2e/core-flows.spec.ts`,
`frontend/e2e/data-preview-scroll.benchmark.spec.ts`, and `frontend/e2e/smoke.spec.ts`.

## Execution diagnostics

`DataPreview.tsx` and `ExplorePreview.tsx` consume only the shared guarded version-1
strategy payload through shared diagnostic components. Planning states remain available in
execution metrics for support and observability, but
`DataPreview` does not render a full-width strategy banner. `projected`,
`admitted_eager`, and `not_planned` stay silent. A `boundary` adds a warning icon immediately after
the Preview row/column summary; `rejected` uses an error icon in the same position. Activating the
icon explains in plain language where projection stopped, that result correctness is unaffected,
the possible I/O/memory cost, and an available remediation. Independently actionable memory
pressure uses the warning indicator too. Raw reason codes, bounded-collection wrappers, and
collection JSON are support data and are not user-facing copy.

`ExplorePreview` passes progress or cache-report metrics to
`ExecutionDiagnosticsSummary`, which renders memory pressure and rejected strategies with an
accessible technical-detail disclosure. Missing/unsupported diagnostics stay silent; there is
not yet a distinct diagnostic-unavailable UI state.

Tests prove boundary and rejected indicator placement/explanations, memory-pressure detail,
Explore cache-report metrics, and keyboard access without fabricated values.
