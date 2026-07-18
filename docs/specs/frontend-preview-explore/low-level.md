# Frontend Preview & Explore — Low-Level Specification

## Module map

| File | Responsibility |
| --- | --- |
| `panels/DataPreview.tsx` | Virtualized row/column data table; frame-select dropdown for multi-frame producers; column search; embedded vs framed render modes. |
| `panels/PreviewPanelFrame.tsx` | Resizable/collapsible chrome shared by every preview panel: header (icon, label, subtitle, actions), collapse/expand-to-top, drag-to-resize. |
| `panels/PreviewPanelTabs.tsx` | Generic ARIA tablist renderer, parameterised over a tab-key union type. |
| `panels/previewPanelLayout.ts` | Shared layout constants: default/min/max panel height, header height class, action-button class. |
| `panels/ExplorePreview.tsx` | Explore node's compound preview: run/cancel cache job, tab switching, wires `DataPreview` (Preview tab) and `ExploreOverviewPane` (Overview tab). |
| `panels/explore/cacheIdentity.ts` | Builds the object hashed into Explore's cache key: upstream-lineage node/edge subset plus submodels/preamble, with `overview` config stripped. |
| `panels/explore/ExploreOverviewPane.tsx` | Card registry dispatcher: picks which overview cards to render based on `config.overview` toggles and report presence. |
| `panels/explore/ExploreSummaryCards.tsx` | Four of the five overview cards: dataset snapshot, data quality, numeric summary (incl. a NaN-count column, float-only), categorical summary (with per-field expandable value lists). |
| `panels/explore/SchemaTableCard.tsx` | The fifth overview card: paginated, searchable per-column schema table (dtype, null%, NaN% (float-only), distinct, min/max). |
| `panels/explore/DistinctInfoButton.tsx` | Shared "?" tooltip affordance placed next to every "Distinct" column header (Numeric Summary, Schema table) clarifying that the count excludes null and NaN. |
| `panels/explore/overviewCardDefinitions.ts` | The ordered card registry (key, label, description) and the `isOverviewCardEnabled` gate. |
| `panels/explore/overviewConfig.ts` | `readOverview`: defensively parses `node.data.config.overview` into a typed, boolean-only record. |
| `panels/explore/StatValueCell.tsx` | Shared `<td>` for an optional monospaced stat value (used by schema + numeric summary tables). |
| `components/ColumnTable.tsx` | Generic column-name/dtype table with optional checkbox column and row interactivity. |
| `components/FramesTable.tsx` | Collapsible per-frame row list for the API Input editor (label, path, emit, column count, cascade/inherit/add-keys entry points). |
| `components/BreakdownDropdown.tsx` | Hover/click-to-open dropdown showing a sorted, bar-charted breakdown of items (used for pipeline timing/memory in the toolbar). |
| `components/CacheFetchButton.tsx` | Generic build/poll/cancel/delete cache workflow button, generic over a `TStatus extends BaseCacheStatus`. |
| `components/ExecutionDiagnosticsSummary.tsx` | Renders a memory-pressure diagnostic banner when `buildExecutionDiagnostic` determines one applies. |

## Key types and data structures

- **`PreviewData`** (`DataPreview.tsx`) — the full shape a row preview renders:
  `status: "ok" | "error" | "loading"`, `row_count`/`column_count` (full-dataset
  counts), `columns: ColumnInfo[]` (flat schema, empty for a multi-frame producer by
  design), `preview: Record<string, unknown>[]` (the actual returned rows),
  `preview_columns?`/`preview_row_count?`/`preview_row_limit?`/`preview_truncated?`
  (what was actually returned vs. the full dataset), `frame_columns?:
  Record<string, ColumnInfo[]>` (per-frame schema for a multi-table producer, 2+
  keys drive the frame dropdown), `selected_frame?` (drives the dropdown's value;
  `undefined` means "first frame"), plus `execution_metrics`/`schema_warnings`/
  `timings`/`memory` diagnostic fields.
- **`ColumnWindow`** (`DataPreview.tsx`) — `{ startIdx, endIdx, leftPad, rightPad,
  totalWidth }`, the result of `getColumnWindow()`; describes which slice of columns
  is actually rendered and how much blank spacer width stands in for the rest.
- **`ColumnSearchEntry`** (`DataPreview.tsx`) — `{ column, normalizedName }`, the
  prebuilt lowercase search index built once per `columns` array via
  `buildColumnSearchIndex()`.
- **`FramesTableRow`** (`FramesTable.tsx`) — one row's worth of caller-supplied facts:
  `label`, `path`, `emit`, `columnCount`, `invalidColumnCount`, `pathError: string |
  null` (the reason this frame can't take part in inherit/cascade), `canInherit`.
  Presentational only — the component never decides the row's content, only how it's
  displayed and which buttons are enabled.
- **`BreakdownItem`** (`BreakdownDropdown.tsx`) — `{ node_id, label, value }`; the
  dropdown derives `sorted` (descending by value), `maxValue`, and `total` from the
  raw item list via a single `useMemo`.
- **`BaseCacheStatus`** (`CacheFetchButton.tsx`) — the minimal shape every cache
  status response must satisfy: `cached`, `row_count`, `column_count`, `size_bytes`.
  `CacheFetchButtonProps<TStatus extends BaseCacheStatus>` additionally requires a
  `timestampField: keyof TStatus` so the button can read a caller-specific "cached
  at" field without hardcoding its name.
- **`OverviewCardDefinition`/`OverviewCardKey`/`OverviewConfig`**
  (`overviewCardDefinitions.ts`) — `OVERVIEW_CARD_DEFINITIONS` is the fixed, ordered
  list of five cards; `OverviewConfig = Partial<Record<OverviewCardKey, boolean>>` is
  the parsed toggle state. Card render order in `ExploreOverviewPane` always follows
  this array's order, independent of the order keys appear in `config.overview`.
- **`ExploreCacheReport`/`ExploreColumnStat`** (`api/types.ts`, consumed not owned) —
  the materialised Explore result: `row_count`, `column_count`, `generated_at`,
  `columns: ExploreColumnStat[]` (per-column `kind`, `null_count`, `nan_count`
  (float-NaN count; `null`/`undefined` for non-float dtypes — the invalid-numeric
  bucket, distinct from `null_count`), `distinct_count`,
  min/p25/median/mean/p75/max/std/zero/negative), `overview_summary` (data-quality
  issues, categorical profiles).

## Control flow

### DataPreview render path
1. `columns` is computed by joining `preview_columns` (if present) against
   `schemaColumns` (flat) and `frameSchemaColumns` (selected frame's), preferring the
   flat schema and falling back to the frame schema, then to an unknown dtype —
   never dropping a previewed column (`DataPreview.tsx:213-227`).
2. `columnSearchIndex` is rebuilt only when `columns` changes (`useMemo`); filtering
   against `normalizedColumnSearch` is a second, cheaper `useMemo` layer so typing in
   the search box never re-lowercases every column name.
3. On scroll, `handleTableScroll` reads `scrollTop`/`scrollLeft` and defers the state
   update to the next animation frame (`requestAnimationFrame`), coalescing rapid
   scroll events into at most one re-render per frame; the in-flight RAF is cancelled
   on unmount.
4. A `ResizeObserver` on the scroll container tracks `viewHeight`/`viewWidth`, which
   feed both row virtualization (`getColumnWindow`'s row analogue, inline in the
   render body) and `responsiveColumnWidth()` (column width shrinks below 900px/720px
   breakpoints). Falls back to `FALLBACK_VIEW_WIDTH`/`FALLBACK_VIEW_HEIGHT` before the
   observer's first callback fires.
5. Row virtualization activates only when `data.preview.length > VIRTUALIZE_THRESHOLD`
   (50); column virtualization activates only when the column count exceeds the
   visible width's capacity plus overscan (`getColumnWindow`). Both produce a
   start/end index range plus leading/trailing spacer cells rather than rendering
   every row/column and hiding the rest with CSS.
6. Cell clicks are handled via one delegated listener on `<tbody>`
   (`handleCellClick`), reading `data-row-index`/`data-column` off the clicked `<td>`
   via `closest()` rather than per-cell handlers — this is why virtualized/windowed
   cells still click correctly (`clicks a horizontally virtualized cell...` test).
7. `embedded` mode renders only the header bar + diagnostics + table (no outer
   `PreviewPanelFrame`); non-embedded mode wraps the same content in
   `PreviewPanelFrame`, passing the frame-select dropdown into the frame's `actions`
   slot instead of the header bar.

### ExplorePreview run/cache flow
1. `cacheIdentity` = `buildExploreCacheIdentity()` over the node's upstream lineage
   (`upstreamNodeIds` does a fixed-point BFS/DFS over edges), each node's
   data-affecting config (`overview` key stripped for Explore nodes only), plus
   `submodels`/`preamble`.
2. `configHash = hashConfig({ graph: cacheIdentity, source: activeSource })`.
3. `currentExploreJob`/`currentCachedResult` are only considered "current" if their
   stored `configHash` *and* `source` both match the freshly computed values — a
   stale job/result for a different graph or source is treated as absent, not shown.
4. `handleRun`: calls `runExplore()`. A `status: "completed"` response with a result
   goes straight to `startExploreJob` + `completeExploreJob` (a synthetic job id
   `cached:${nodeId}` is used when the backend didn't return one — this represents an
   instant cache hit, not a background job). A `status: "started"` response requires
   a `job_id`; its absence throws. Any thrown error (including the "completed without
   a report" and missing-job-id cases) is caught by one handler that still calls
   `startExploreJob` then `failExploreJob`, so a startup failure is visible through
   the same status UI as a mid-run failure.
5. `handleCancel`: calls `cancelExplore(jobId)`; a `"completed"` cancellation result
   with a report resolves through `completeExploreJob` (the job finished before the
   cancel reached it), everything else resolves through `failExploreJob`.
6. `touchExplorePreview(nodeId)` runs in a `useEffect` keyed on `report` — every time
   a report becomes visible, it bumps the result's LRU recency in
   `useNodeResultsStore`, independent of whether the report came from a fresh run or
   was already cached.
7. Tab body: only `"preview"` renders `DataPreview` (embedded) and only
   `"overview"` renders `ExploreOverviewPane`; `"relationships"`/`"charts"` render
   nothing (`activePane === "overview" ? ... : null`) — those tabs exist in the tab
   strip but have no implemented body yet.

### ExploreOverviewPane card gating
1. `readOverview(node.data.config)` parses `config.overview`, discarding non-object,
   array, or non-boolean-per-key values rather than throwing.
2. `enabledCards = OVERVIEW_CARD_DEFINITIONS.filter(isOverviewCardEnabled)` — this is
   what fixes render order regardless of the raw config's key order.
3. Empty-state branching: `enabledCards.length === 0` → "No cards enabled" (points at
   the config panel); else `report === null` → "No cached data yet" (points at the
   run button); else render every enabled card via `CARD_RENDERERS[key](report)`.

### CacheFetchButton lifecycle
1. `useLayoutEffect` keyed on `resourceKey`: on every key change, bumps all four
   generation refs (`statusGenerationRef`, `fetchGenerationRef`,
   `progressGenerationRef`, `deleteGenerationRef`), clears `startPendingRef`, and
   resets `cache`/`building`/`progress`/`error`/`statusError` to their empty values —
   synchronously, before paint, so no stale UI is ever briefly visible for the new
   key.
2. Initial status load (`useEffect` on `resourceKey`): captures the generation at
   call time; the `.then`/`.catch` both re-check `activeResourceKeyRef.current ===
   requestKey && statusGenerationRef.current === generation` before applying the
   result — either check failing means the key changed or a newer status request
   superseded this one, and the response is dropped.
3. Progress polling (`useEffect` on `[building, resourceKey]`): only runs while
   `building` is true; sets up a 1s `setInterval` calling `getProgress`, applying the
   same active-key + generation guard per tick. If a tick reports `active: false` but
   `startPendingRef.current` is still true (the initial `startFetch` call hasn't
   resolved yet), `building` is deliberately *not* cleared — this is what the "keeps
   building while start fetch is pending" test locks in.
4. `doFetch`: bumps `fetchGenerationRef` and also invalidates any in-flight status
   check (`statusGenerationRef.current += 1`) since a fresh fetch supersedes a stale
   status read; sets `startPendingRef.current = true` until the fetch resolves.
5. `doDelete`: same generation-invalidation pattern against
   `deleteGenerationRef`/`statusGenerationRef`.
6. Render precedence for the button label/color: cancel-while-building (if
   `cancelFetchFn` provided) > building-without-cancel (spinner) > cached (refresh
   label) > status-error (only when not cached and not building) > not-cached (fetch
   label).

## Edge cases and invariants

- **Multi-frame producer with an empty flat schema.** A multi-frame node reports
  `column_count: 0, columns: []` by design (it has no single representative schema).
  `DataPreview` must resolve columns from `frame_columns[selectedFrame]` in this
  case, and the header's column count falls back to `columns.length` (the resolved
  list), not the reported `column_count`, so the header never shows "0 cols" for a
  populated multi-frame preview.
- **A previewed column absent from every schema source still renders**, with an
  empty-string dtype, rather than being dropped — regression-guarded explicitly by a
  DataPreview test (`"still renders a previewed column whose dtype is absent..."`).
- **Column virtualization window clamps on shrink.** If the previewed data changes to
  fewer columns while scrolled past the new column count, `getColumnWindow`'s
  `maxStartIdx = max(0, columnCount - windowSize)` clamp prevents the window from
  pointing past the end of the new (shorter) column list.
- **`null` vs `undefined` vs non-finite floats** are all distinct in `formatValue`:
  `null`/`undefined` render the literal string `"null"` with italic muted styling;
  the backend's `__haute_type__: "non_finite_float"` sentinel objects render as
  `"NaN"`/`"Infinity"`/`"-Infinity"` (including when nested inside a struct/list cell,
  via a JSON.stringify replacer); everything else falls through to
  `toLocaleString()`/`String()`.
- **Schema/numeric-summary null-percentage buckets are three-way**: exactly 0% is
  muted ("uninteresting"), >50% is `--warning-strong` styled, everything in between is
  primary-styled; `rowCount === 0` short-circuits to muted regardless of
  `null_count` (division-by-zero guard, not a real percentage).
- **NaN% reuses the same three-way severity buckets and colour ramp as Null%**
  (`nullSeverity`/`nullPctStyle` in `SchemaTableCard.tsx` are called with `nan_count`
  as well as `null_count`), but only when `nan_count` is present. A `null`/`undefined`
  `nan_count` (any non-float dtype) renders a plain muted `"-"` in both
  `SchemaTableCard` (`data-testid="explore-schema-nan-pct"`, `data-nan-severity="none"`)
  and `ExploreSummaryCards`' `NumericSummaryCard` (`data-testid=
  "explore-numeric-nan-count"`) — distinct from an actual `0`, which is styled muted
  but still renders the digit. `NumericSummaryCard` only ever shows numeric-kind
  columns, so its NaN column is populated far more often than `SchemaTableCard`'s
  (which lists every column, numeric or not).
- **`SchemaTableCard` search runs before the 50-row page limit is applied** — the
  search filters `report.columns` first, then pagination slices the filtered result,
  so a match can be on any page of the unfiltered set and will still be found (test:
  "searches across all schema columns before applying the row limit").
- **`CategoricalSummaryCard`'s per-field expand state persists across value-list
  re-renders** via a `Set<string>` of expanded field names, keyed by field name, not
  by row index — reordering `profiles` doesn't lose expand state for a still-present
  field.
- **`FramesTable` renders an invalid frame's row at reduced opacity with its
  inherit/add-keys buttons disabled**, rather than omitting the row — a persisted
  frame with a broken path must still be visible (so the user can see and fix it),
  it just can't participate in inherit/cascade until the path is valid.
- **`PreviewPanelFrame`'s expand-to-top computes available height defensively**:
  tries the container's parent's bounding-rect height first, falls back to the
  container's own bottom edge, falls back to `window.innerHeight` — this handles the
  frame being measured before layout has settled.
- **`BreakdownDropdown` disables interaction entirely for an empty item list**
  (`hasData = data !== null`, where `data` is `null` when `items.length === 0`): the
  button renders at reduced opacity and clicking it does not open the (empty)
  dropdown panel.

## Error handling

- `DataPreview` never throws for a failed node; `status: "error"` is a normal render
  branch, not a caught exception.
- `ExplorePreview.handleRun`/`handleCancel` catch synchronously-thrown and rejected
  promise errors from the API client with `err instanceof Error ? err.message :
  String(err)`, route them through `failExploreJob` (updating store state) and
  `addToast("error", ...)` (user-visible toast) — never left unhandled.
- `CacheFetchButton` distinguishes an `ApiError` (has `.detail`) from a generic
  `Error` when extracting a display message (`e instanceof ApiError ? e.detail ||
  e.message : e.message`), and special-cases the string `"Cache build cancelled"` to
  suppress it as an error entirely (a user-initiated cancel is not a failure).
- `CacheFetchButton`'s status-check failures are logged via `console.warn` (not
  thrown) and surfaced as `statusError`, a state distinct from `error` (fetch/delete
  failures) — the two are combined for display (`visibleError = error ||
  statusError`) but tracked separately so a stale-status-load guard doesn't also
  need to reason about fetch state.
- `ExecutionDiagnosticsSummary` renders `null` (no DOM output) when
  `buildExecutionDiagnostic` returns `null` — there is no error path here, just an
  absence of anything to show.

## Testing

- `frontend/src/panels/__tests__/DataPreview.test.tsx` — unit/behavioural, React
  Testing Library. Covers: null-data no-op render, header content, struct/list JSON
  formatting, error/loading states, embedded vs framed chrome, memory-pressure
  diagnostic passthrough, cell click delegation (including on a horizontally
  virtualized cell), null-value styling, truncation footer text (including the
  same-row-count-but-capped case), row and column virtualization bounds (500 rows,
  1000 and 10k×1000 columns), horizontal-scroll column reveal, column-window
  clamping on data shrink, column search (including verifying the lowercase index is
  built once via a `String.prototype.toLowerCase` spy), collapse/expand, traced-cell
  highlighting, and the full frame-select dropdown matrix (2+ frames + handler shows
  it; 1 frame, no `frame_columns`, or no handler each suppress it; selection calls
  back; `selected_frame` reflects in the control) plus the multi-frame empty-flat-
  `columns` regression suite.
- `frontend/src/panels/__tests__/PreviewPanelFrame.test.tsx` — mocks `useDragResize`
  to assert default sizing is passed through, collapsed-state control layout,
  expand-to-top height computation via a mocked `getBoundingClientRect`, and that
  collapsing from full-height resets to the "expand to top" icon rather than "restore
  height".
- `frontend/src/panels/__tests__/ExplorePreview.test.tsx` — integration-style against
  real Zustand stores (`useGraphStore`, `useNodeResultsStore`, `useSettingsStore`,
  `useUIStore`), API client mocked. Covers: run → completed-with-result flow and the
  exact `runExplore` payload shape, embedded row preview rendering, tab switching
  without leaking preview content into other panes, started-job registration for
  background polling, each overview-card-toggle combination rendering the right
  cards, cache reuse when only `overview` toggles change (including through a live
  `useGraphStore` update, asserting `structuralVersion` doesn't bump), cache
  invalidation on code/source/upstream-config changes (three separate scenarios),
  the no-report empty state, keeping an active job cancellable across an overview-
  only config change, and job cancellation end-to-end.
- `frontend/src/panels/explore/__tests__/ExploreOverviewPane.test.tsx` — the three
  empty/populated states, all-cards-stacked-in-order, single-card-only rendering for
  each of the five cards, and that the no-data empty-state body text doesn't name a
  specific card.
- `frontend/src/panels/explore/__tests__/SchemaTableCard.test.tsx` — row-per-column
  rendering, absence of any auto-grouping/inventory UI (explicitly asserted not
  present), pagination (50/page, disabled next at the last page), search-before-
  pagination, null% formatting and severity buckets, NaN% formatting and severity
  (an all-NaN float column reading `100.0%`/`data-nan-severity="high"` despite 0%
  null, and a non-float column rendering the `"-"`/`"none"` placeholder pair), the
  `colSpan={7}` empty-state cells (bumped from 6 to account for the new NaN% column),
  the Distinct-header info button (`getByRole("button", { name: /Null and NaN are
  not values/i })` resolving to `data-testid="explore-distinct-info"`), min/max
  rendering (not "examples"), sanitised `data-testid`s for special-character column
  names, empty-column-list state, and dtype colour-class delegation.
- `frontend/src/panels/explore/__tests__/ExploreSummaryCards.test.tsx` and
  `overviewConfig.test.ts` — card-level rendering (not read in full for this spec;
  see file for detail), the Data Quality card's empty-state copy (now naming NaN
  alongside missing/constant/negative/mostly-zero fields), `NumericSummaryCard`'s
  NaN column (populated `nan_count` for a float column vs. the `"-"` placeholder for
  an integer column via `explore-numeric-nan-count`), the same Distinct-header info
  button assertion as `SchemaTableCard.test.tsx`, and `readOverview`'s defensive
  parsing (missing/null/wrong-type `overview`, non-boolean per-key values, unknown
  extra keys ignored).
- `frontend/src/components/__tests__/ColumnTable.test.tsx` — column/dtype rendering,
  checkbox presence/absence/toggle (including click-through on interactive rows with
  `stopPropagation` verified via call-count), custom `nameColor`/accent class/
  className, and the empty-columns header-only case.
- `frontend/src/components/__tests__/CacheFetchButton.test.tsx` — almost entirely
  stale-response-guarding scenarios: status failure display, stale status
  success/rejection from a superseded resource key, stale same-resource status
  arriving after a fetch or delete already completed, error-clearing on resource-key
  change, and the "keeps building while start fetch is pending" progress-vs-startup
  race (using fake timers).
- `frontend/src/components/__tests__/ExecutionDiagnosticsSummary.test.tsx` — renders
  memory-pressure banner for a running job and for a `memory_limited` terminal
  failure; parameterised `it.each` over the five non-memory terminal reasons
  asserting the banner does *not* render for any of them.
- `frontend/src/__tests__/components/BreakdownDropdown.test.tsx` — empty-state
  (faint, non-interactive), total display, open/close toggle, descending sort order,
  per-item and total formatted-value display.
- No dedicated unit test exists for `FramesTable` (`components/FramesTable.tsx`),
  `explore/StatValueCell.tsx`, or `explore/DistinctInfoButton.tsx`; `FramesTable` is
  exercised indirectly through `__tests__/editors/ApiInputEditor.test.tsx` (owned by
  [json-shredding](../json-shredding/high-level.md)), and `StatValueCell` and
  `DistinctInfoButton` only through the schema/numeric-summary card tests that
  render them.
- **`frontend/e2e/data-preview-scroll.benchmark.spec.ts`** — a Playwright
  benchmark (tagged `@benchmark`, excluded from the default `npm run test:e2e`
  lane, run via `npm run test:e2e:benchmark`) that scrolls a 10k-row ×
  1000-column preview and asserts frame-time and scroll-step p95 budgets plus
  a bound on the number of virtualized header/body cells actually rendered.
  This is the only performance coverage of `DataPreview`'s virtualization at
  the real-browser layer; the unit tests above assert virtualization bounds
  but not frame timing.
