# Frontend Preview & Explore — High-Level Specification

## Purpose

While a user builds a pipeline graph, they need to see what a node's output actually
looks like — rows, columns, dtypes, and why an execution degraded — without leaving
the canvas. This component is the data-preview surface: a virtualized table that can
render either inline (embedded in another panel) or as its own resizable/collapsible
panel, plus the generic panel chrome (frame, tab strip, layout constants) other
preview panels in the app are built from.

It also owns the Explore node's specific preview workflow. Explore is the pipeline's
EDA node: instead of showing a capped row preview, it lets the user materialize and
cache full-dataset summary statistics (row/column counts, per-field null%/distinct/
min/max, numeric distributions, categorical value counts, data-quality issues) on
demand, because computing those over the full dataset is too expensive to do on every
keystroke the way the row preview is.

Finally, this component supplies a handful of small, deliberately generic building
blocks — a column-name/dtype table, a build/poll/delete cache button, a hoverable
metric-breakdown dropdown, and an execution-diagnostics banner — that both the
preview panels here and unrelated config editors elsewhere in the app reuse rather
than re-implement.

## Scope

In scope:
- The generic virtualized data-preview table and its two render modes (`DataPreview`).
- The resizable/collapsible panel frame and generic tab strip that preview panels are
  built from (`PreviewPanelFrame`, `PreviewPanelTabs`, `previewPanelLayout`).
- The Explore node's compound preview widget: run/cancel a cache-materialisation job,
  the tab set (Preview / Overview / Relationships / Charts), and the cache-identity
  rule that decides when a previously cached report is still valid (`ExplorePreview`,
  `explore/cacheIdentity.ts`).
- The Explore Overview pane and its card registry: dataset snapshot, schema table,
  numeric summary, categorical summary, data quality (`explore/` directory).
- Small shared widgets this component owns but that are also consumed by editors
  outside it: `ColumnTable`, `FramesTable`, `BreakdownDropdown`, `CacheFetchButton`,
  `ExecutionDiagnosticsSummary`.

Out of scope (owned elsewhere, linked where relevant):
- Fetching/deriving the `PreviewData` this component renders — the pipeline-run
  request, error normalisation, and store caching live in `useNodeResultsStore` and
  the `usePipelineAPI`/`useNodeHandlers`/`useEdgeHandlers` hooks
  ([frontend-shared](../frontend-shared/high-level.md)).
- The Explore backend's actual stats computation (row scan, percentile/distinct
  computation, data-quality rule evaluation) — [explore-eda](../explore-eda/high-level.md).
- Canvas node rendering and per-node-type iconography (`NodeTypeIcon`) —
  [frontend-graph-canvas](../frontend-graph-canvas/high-level.md); this component
  only consumes the icon lookup, it doesn't own node-type→icon mapping.
- The sibling preview panels for other node types (`OptimiserDataPreview`,
  `ModellingPreview`) — [frontend-modelling-optimiser-ui](../frontend-modelling-optimiser-ui/high-level.md).
  They build on `PreviewPanelFrame`/`PreviewPanelTabs`/`previewPanelLayout` from this
  component but their body content and data shape are theirs.
- `FramesTable`'s only caller, `ApiInputEditor`, and the cascade/inherit/add-keys
  operations it wires up — [json-shredding](../json-shredding/high-level.md);
  `FramesTable` here is presentation-only, the editor owns what the buttons do.
- `ColumnTable`'s other call sites (`OutputEditor`, `ColumnsTab`) and
  `CacheFetchButton`'s call sites (`ApiInputEditor`, `_DatabricksSelector`'s cache
  workflow) — [frontend-node-editors](../frontend-node-editors/high-level.md) and
  [databricks-io](../databricks-io/high-level.md) respectively own how those callers
  use the shared widgets.

## Behaviour

- **The row preview never hides a previewed column.** `DataPreview` joins the
  server-reported `preview_columns` (which columns actually came back) against two
  possible schema sources — the node's flat `columns` and, for a multi-frame
  producer, that frame's `frame_columns` entry — and falls back to an unknown dtype
  rather than dropping a column neither source recognises.
- **A multi-frame producer gets a frame-select dropdown**, shown only when the node
  reports 2+ frames *and* the caller wired a frame-select handler; selecting a frame
  re-requests the preview scoped to that frame's port label. A single-frame node, an
  ordinary node, or a caller without the handler renders the unchanged single-table
  UI.
- **Large previews stay responsive.** Rows beyond a 50-row threshold and columns
  beyond the visible width are windowed (virtualized) rather than rendered in full;
  scrolling reveals more of either axis without remounting the table or losing scroll
  position, and a column search can surface a column outside the current window.
- **The panel frame is resizable, collapsible, and can expand to fill available
  height**, independent of what it's showing; state (collapsed / expanded-to-top /
  restored height) is local to the frame instance.
- **Explore's cache identity ignores its own display-only config.** Toggling which
  overview cards are enabled does not change the Explore cache key — the underlying
  dataset didn't change — so switching a toggle on shows the already-cached report
  immediately rather than prompting a re-cache. Any other config, graph structure, or
  upstream node change (including something as indirect as an upstream node's own
  config) does invalidate the cache key, because it can affect the data.
- **Explore's Overview pane has three states**, driven independently by (a) which
  card toggles are on and (b) whether a cached report exists for the current cache
  identity: no toggles on → point at the config panel; toggles on but no report →
  point at the "Process & cache full data" action; toggles on with a report → render
  each enabled card, in the fixed order the card registry defines.
- **A cache-fetch resource is fully reset on identity change**, not just re-fetched:
  switching `resourceKey` immediately clears the previously shown cache details,
  errors, and in-flight build/poll state before the new key's status load resolves,
  so a slow status check for the new key can never render stale data borrowed from
  the old key.

## Design rationale

- **Two render modes for one table (`embedded` vs framed).** `DataPreview` is used
  both standalone on the canvas (wrapped in its own `PreviewPanelFrame`) and nested
  inside `ExplorePreview`'s already-framed panel. Rather than duplicate the table,
  header, and virtualization logic, the component takes an `embedded` flag that
  suppresses only the redundant "Preview" title/icon and relocates the frame-select
  control, keeping one virtualization implementation for both call sites.
- **Column search builds its lowercase index once**, not per keystroke or per scroll
  tick, specifically because the multi-thousand-column case is a real workload here
  (wide API-input frames) and re-lowercasing every column name on every render was a
  measured cost worth avoiding.
- **Row/column virtualization is threshold-gated, not always-on**, so small previews
  (the common case) skip the windowing math entirely and render the full table
  directly.
- **The frame-select dropdown requires both data and a handler to appear** — data
  alone (2+ frames) isn't sufficient, because a caller that hasn't wired
  `onSelectFrame` has nowhere to send the selection, and showing a dropdown that does
  nothing would be worse than showing no dropdown.
- **`cacheIdentity` deliberately strips the `overview` config key before hashing**,
  because folding it in would invalidate a perfectly good cached report every time
  the user only changes which summary cards they want to look at — the toggle is a
  display preference, not an input to what gets computed upstream.
- **`CacheFetchButton` is generic over its status shape** (`TStatus extends
  BaseCacheStatus`) so unrelated cache workflows (JSON-cache builds, Databricks table
  caching) can reuse the same build/poll/cancel/delete state machine and stale-response
  guarding instead of re-implementing generation-counter tracking per caller.
- **Every async operation in `CacheFetchButton` is generation-stamped per
  `resourceKey`.** A response is only applied if the resource key hasn't changed and
  no newer request of the same kind has started since — this is what prevents a slow
  request for an old key (or a superseded request for the same key) from clobbering
  what's on screen. See [Failure model](#failure-model) for how this interacts with
  errors specifically.

## Interactions

- Depends on [frontend-shared](../frontend-shared/high-level.md) for the
  `PreviewData` shape, the `useNodeResultsStore`/`useGraphStore`/`useSettingsStore`/
  `useUIStore` state Explore's preview reads and writes, the `useDragResize` and
  `useClickOutside` hooks, and the `runExplore`/`cancelExplore` API client calls.
- Depends on [frontend-graph-canvas](../frontend-graph-canvas/high-level.md) for
  `NodeTypeIcon` (the icon shown in a preview panel's header) and the `SimpleNode`/
  `SimpleEdge` graph types `ExplorePreview` and `cacheIdentity` consume.
- Depends on [explore-eda](../explore-eda/high-level.md) for the shape of
  `ExploreCacheReport`/`ExploreColumnStat` the overview cards render, and for the
  actual cache-build/status/cancel HTTP endpoints `ExplorePreview` calls.
- Depended on by [frontend-modelling-optimiser-ui](../frontend-modelling-optimiser-ui/high-level.md):
  `OptimiserDataPreview` and `ModellingPreview` build on `PreviewPanelFrame`,
  `PreviewPanelTabs`, and `previewPanelLayout` from this component.
- Depended on by [frontend-node-editors](../frontend-node-editors/high-level.md):
  `OutputEditor` and `ColumnsTab` reuse `ColumnTable`; `App.tsx`'s node inspector
  renders the framed `DataPreview` for the active canvas node.
- Depended on by [json-shredding](../json-shredding/high-level.md)/
  [databricks-io](../databricks-io/high-level.md): `ApiInputEditor` reuses
  `FramesTable` and `CacheFetchButton`; `_DatabricksSelector` reuses
  `CacheFetchButton`.
- Depended on by the app toolbar (frontend-shared/chrome): `BreakdownDropdown`
  renders the pipeline timing/memory breakdowns there.

## Failure model

- **An execution error surfaces as data, not a thrown error.** `DataPreview` treats
  `status: "error"` as a normal render state — the error message is shown both in the
  panel header and the body — rather than throwing; the panel never crashes on a
  failed node.
- **Memory-pressure diagnostics only render for genuinely inconclusive failures.**
  `ExecutionDiagnosticsSummary`/`buildExecutionDiagnostic` suppress the memory-pressure
  banner whenever a more specific terminal reason (`contract_error`, `timed_out`,
  `cancelled`, `superseded`, plain `error`) is already known, so the user isn't shown
  a vague "memory pressure" message for a failure that has a precise cause.
- **A cache-status check failing is shown as its own distinct state**
  (`CacheFetchButton`'s "Cache status unavailable"), not folded into the generic
  build-error message, and it deliberately suppresses the normal not-cached hint so
  the user isn't told to build a cache the app couldn't even confirm the absence of.
- **Stale async responses are dropped, not surfaced.** `CacheFetchButton` compares
  both the active `resourceKey` and a per-operation generation counter before
  applying any status/fetch/progress/delete response or error; a response that fails
  either check is discarded silently — this is intentional (the UI already reset for
  the new resource) rather than a suppressed error.
- **Explore job startup failures still register as a completed job cycle.** If
  `runExplore` throws before returning a job id, `ExplorePreview` still calls
  `startExploreJob` immediately followed by `failExploreJob`, so the failure is
  visible through the same status/toast path a mid-run failure would use, rather than
  being swallowed at the call site.
- **A completed-without-a-result response is a hard error, not a silent no-op.**
  `ExplorePreview.handleRun` throws (`"Explore completed without a cache report"`) if
  the backend reports `status: "completed"` but omits `result`; this is caught by the
  same failure path as any other run error and surfaces a toast.
