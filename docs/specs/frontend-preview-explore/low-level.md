# Frontend Preview & Explore — Low-Level Specification

## Module map

| File | Responsibility |
| --- | --- |
| `frontend/src/panels/DataPreview.tsx` | Virtualised preview table, frame selection, search, cell callbacks and value formatting. |
| `frontend/src/panels/PreviewPanelFrame.tsx`, `frontend/src/panels/PreviewPanelTabs.tsx` | Resizable/collapsible frame and generic ARIA tab strip. |
| `frontend/src/panels/previewPanelLayout.ts` | Shared preview-panel dimensions and header/action layout constants. |
| `frontend/src/components/ColumnTable.tsx`, `frontend/src/components/BreakdownDropdown.tsx` | Reusable preview-adjacent column table and sorted visual breakdown dropdown. |
| `frontend/src/components/CacheFetchButton.tsx`, `frontend/src/components/ExecutionDiagnosticsSummary.tsx` | Generic cache fetch/poll/cancel/delete control and execution-diagnostic banner. |
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
  their stored `configHash` and source match `frontend/src/panels/ExplorePreview.tsx`'s newly
  calculated identity.

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
2. `frontend/src/panels/ExplorePreview.tsx` hashes that identity with the active source, ignores
   stored job/result records with a different hash/source, and records immediate cache hits as a
   completed result or background starts as a job.
3. Start failures, and cancellation responses without a completed report, call the result-store
   failure path; thrown start/cancel errors also toast. A visible report is touched to update cache
   recency. Preview and Overview mount only for their active tab; Relationships and Charts are
   currently tab labels with no body.
4. `frontend/src/panels/explore/overviewConfig.ts` drops malformed config values. The overview
   pane renders no-enabled-cards, no-report, or the ordered enabled renderer set.

### Utility editing concurrency

1. `frontend/src/panels/UtilityPanel.tsx` loads files on mount and selects the first file when
   none is active. Switching files first awaits `flushSave()` so the previous debounce cannot
   lose its latest draft.
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

## Error handling

Preview `loading` and `error` statuses are ordinary render branches. Explore records a terminal
error for a failed start or a cancellation that does not return a completed report; thrown
start/cancel exceptions also toast. Utility syntax errors remain inline and other file-operation
failures toast or display action-local text. Cache/report/card shape is assumed to meet the API
contract; optional overview settings alone are parsed defensively.

## Testing

Tests live in `frontend/src/panels/__tests__/DataPreview.test.tsx`,
`frontend/src/panels/__tests__/PreviewPanelFrame.test.tsx`,
`frontend/src/panels/__tests__/ExplorePreview.test.tsx` and
`frontend/src/panels/__tests__/UtilityPanel.test.tsx`, plus the focused overview suites under
`frontend/src/panels/explore/__tests__/`. They cover virtualisation, frames, search, trace click
delegation, cache identity/result lifecycle, card ordering/config, panel accessibility, utility
save-flush/stale-response behaviour and syntax errors. Shared layout/constants and small visual
helpers are exercised through these component tests rather than owning standalone suites.

Browser preview/smoke coverage is in `frontend/e2e/core-flows.spec.ts`,
`frontend/e2e/data-preview-scroll.benchmark.spec.ts`, and `frontend/e2e/smoke.spec.ts`.

## Polars backend contracts (0.6.0)

See [the remediation plan](../../trip/plans/F_0.6.0_polars-backend-remediation.plan.md).
`DataPreview.tsx` and `ExplorePreview.tsx` will consume only the shared guarded version-1
strategy payload. The display distinguishes `projected`, `boundary`, `admitted_eager`,
`rejected`, and `not_planned`, with a separate diagnostic-unavailable state. It uses the shared
authoritative strategy-to-status mapping; components must not reinterpret internal strategies.
A non-success state shows available blocking node/operator/profile, cost, reason, and actionable
remediation. An expandable section shows only the bounded optional metric/provenance detail and
honours `detail_state=available|unavailable|truncated`.

Missing/malformed required fields, unknown version-1 enums, and unsupported higher versions render
diagnostic unavailable. Unknown additive fields are ignored only within version 1. A group-by
boundary is valid only for `strategy=materialisation-boundary`; a rejected group-by surfaces its
HTTP 422 stable code and named fields and blocks execution. No component may recast group-by as
ordinary checked execution or `unprojected-streaming-boundary`.

Tests cover all five statuses, diagnostic unavailable, every version/detail-state path, keyboard
and screen-reader access, deterministic truncated detail, rejected execution gating, the group-by
boundary/rejection distinction, and typed 422 error fields without fabricated values.
