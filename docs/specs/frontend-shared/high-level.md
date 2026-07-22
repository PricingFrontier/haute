# Frontend Shared Infrastructure — High-Level Specification

## Purpose

Every other frontend component (graph canvas, node editors, tracing, git UI,
modelling/optimiser UI, explore/EDA, preview) needs the same handful of
cross-cutting services: one way to call the backend, one place to hold
results and settings that must survive a panel unmounting, one visual
language for colours, and a handful of chrome widgets (toasts, modals,
tooltips, search) that every feature reuses rather than reinventing. This
component is that shared substrate. It solves the problem of N features each
growing their own fetch wrapper, their own "is this dropdown open" state
machine, or their own colour constants — which would fragment error handling
and make the app look inconsistent.

It also owns the browser entry point: `main.tsx` mounts the app inside a root
`ErrorBoundary`, so a crash in any single feature degrades to a recoverable
panel instead of a blank white screen.

## Scope

In scope:
- The typed HTTP client (`api/`) and the runtime response parsers that
  guard the JSON/DOM boundary. This includes the bundle-split companion
  `api/dispersion.ts` (GLM dispersion-estimation endpoints), which lives
  outside `api/client.ts` so code reachable only from a lazy-loaded panel
  stays out of the initial JS bundle while sharing the client's exported
  `request`/`post` fetch machinery rather than reimplementing it. The
  assistant-owned split endpoint module consumes the same shared transport
  contract but belongs to
  [frontend-assistant-ui](../frontend-assistant-ui/high-level.md).
- Cross-cutting Zustand stores: node computation results
  (`useNodeResultsStore`), app-wide settings/caches (`useSettingsStore`),
  toast notifications (`useToastStore`), and layout/modal chrome
  (`useUIStore`).
- The design-token layer (`theme/colors.ts`) — CSS-variable-backed colour
  constants used by every visual component.
- Chrome widgets and app-shell surfaces: `ErrorBoundary`, `Toast`,
  `ModalShell`, `Tooltip`, `ContextMenu`, `KeyboardShortcuts`, `Toolbar`,
  `ImportsPanel`, `BackgroundJobPolling`, `NodeSearch`, `BreadcrumbBar`.
  `ImportsPanel` is conditionally rendered; the older `SettingsModal` remains
  a maintained, directly testable component but is not mounted by the
  application.
- Small generic hooks with no domain knowledge: `useClickOutside`,
  `useDragResize`, `useJobPolling` (+ its orchestrator
  `useBackgroundJobs`), `useMlflowBrowser`, `useSchemaFetch`,
  `useStaleConfigEstimate`.
- Canonical shared types (`types/node.ts`, `types/trace.ts`,
  `types/banding.ts`) and the generic formatting/naming utilities
  (`utils/formatBytes.ts`, `utils/formatTime.ts`, `utils/formatValue.ts`,
  `utils/color.ts`, `utils/dtypeColors.ts`, `utils/sanitizeName.ts`).
- The application bootstrap (`main.tsx`).

Explicitly out of scope (owned elsewhere, even though the files live under
the directories this spec covers):
- React Flow canvas state, undo/redo history, node/edge CRUD, drag-drop,
  layout, and the graph-shaped `useGraphStore` — see
  [frontend-graph-canvas](../frontend-graph-canvas/high-level.md). The
  hooks `useGraphCanvasState`, `useEdgeHandlers`, `useNodeHandlers`,
  `usePipelineAPI`, `usePanelGraphContext`, `useSubmodelNavigation`,
  `useWebSocketSync`, and `useKeyboardShortcuts`'s canvas-editing bindings
  all live in `hooks/` but belong conceptually to graph-canvas; this spec
  only covers `KeyboardShortcuts.tsx` as a chrome widget (the *modal*, not
  the App-level key bindings hook).
- Node-editor-specific config plumbing (`useConstraintHandlers`,
  `useDataInputColumns`, `utils/configField.ts`, `utils/edgeJoin*.ts`,
  `utils/banding.ts`) — see
  [frontend-node-editors](../frontend-node-editors/high-level.md).
- Node-type/connection plumbing (`utils/connectionValidation.ts`,
  `utils/apiInputPorts.ts`, `utils/flowElements.ts`,
  `utils/flowHandles.ts`, `utils/nodeTypes.ts`,
  `utils/nodeTypeRegistry.ts`) — these belong to graph-canvas despite the
  node-editor-adjacent naming; see
  [frontend-graph-canvas](../frontend-graph-canvas/high-level.md).
- Trace playback (`useTracing`, `utils/formatTrace.ts`) — see
  [frontend-trace-ui](../frontend-trace-ui/high-level.md).
- Modelling/optimiser-specific helpers (`utils/chartHelpers.ts`,
  `utils/mlflowOptimiser.ts`, `utils/trainingObjective.ts`) — see
  [frontend-modelling-optimiser-ui](../frontend-modelling-optimiser-ui/high-level.md).
- Git-specific undo entries (`utils/vcHistory.ts`) and all git chrome
  (`BranchIndicator`, `BranchManager`, commit/push/divergence modals) — see
  [frontend-git-ui](../frontend-git-ui/high-level.md). This spec covers only
  the generic `ModalShell` that those dialogs are built on.
- Graph diffing/execution-metrics/preview-shape utilities
  (`utils/buildGraph.ts`, `utils/graphDiff.ts`, `utils/graphHelpers.ts`,
  `utils/graphPerformance.ts`, `utils/graphSnapshot.ts`,
  `utils/executionDiagnostics.ts`, `utils/makePreviewData.ts`,
  `utils/columnFingerprint.ts`, `utils/activePreview.ts`,
  `utils/shallowNodeHash.ts`, `utils/validateConfigRefs.ts`,
  `utils/layout.ts`) — these are graph-canvas internals despite living in
  the shared `utils/` folder.

The global styling contract (`index.css`), reusable compact-choice and
node-icon controls, and the `components/form/` public form primitives are
also shared infrastructure. `chartHelpers`, `formatTrace`, and
`mlflowOptimiser` are side-effect-free shared leaf utilities: their domain
panels own the workflows that consume them, while this component owns their
consistent formatting and classification contracts.

## Behaviour

**API client.** Every backend call goes through `request()` in
`api/client.ts`, which returns typed data or throws `ApiError` (HTTP
non-2xx, carrying `status`/`detail`/`body`) or `ApiTimeoutError` (the request
was aborted by its own timeout guard, distinct from a caller-initiated
abort). Idempotent verbs retry transient failures (network errors, 5xx) with
exponential backoff and jitter; POST is never auto-retried. Every response
passes through a `parse*` guard in `types/guards.ts` before reaching a
caller, so a backend contract drift raises a descriptive `Error` at the call
site instead of `undefined` propagating silently into a component. A 403
whose detail matches the "missing/invalid session" reason fires a
`window` `CustomEvent` (`HAUTE_SESSION_EXPIRED_EVENT`) rather than being
handled locally — any part of the app can listen for session expiry without
the client needing to know about auth UI. `request`/`post` are exported so
bundle-split modules (`api/dispersion.ts`) can build endpoint functions on
the same machinery without re-implementing fetch/retry. `api/dispersion.ts`
does own its response parsing locally rather than adding to
`types/guards.ts` — a deliberate exception to the "every response goes
through `types/guards.ts`" rule below, made for the same bundle-size reason
the module is split out in the first place. Some
mutation endpoints (`runFrontier`, `runDispersionEstimate`) front a
backend job that runs off the request thread: the client function itself
polls a `.../status/{job_id}` endpoint on a fixed interval and resolves
only once the job reaches a terminal status, so callers can still `await`
a single promise for what is, on the wire, a start-then-poll sequence —
distinct from `useJobPolling`/`useBackgroundJobs`, which track jobs the
user can navigate away from and revisit.

**Result caching.** `useNodeResultsStore` is the only place preview rows,
optimiser solves, training runs, and explore reports are kept once computed.
Each result category (`previews`, `solveResults`, `trainResults`,
`exploreResults`) is bounded to a fixed entry count and evicted by
least-recently-touched, except the currently pinned/open node's entry, which
survives eviction pressure. A config-hash (`hashConfig`) lets panels detect
"this result is stale relative to the current node config" without deleting
the old result. Frontier point selection is a pure re-derivation from cached
frontier data — selecting a different point never re-fetches unless the
caller asks the backend to solve/select explicitly.

**Global settings.** `useSettingsStore` holds row limit, streaming chunk
size, per-section open/closed UI memory, the MLflow connectivity check
(fetched once, shared, retried on a 10s cooldown after failure), the list of
named data sources plus which is active, and a 30-second file-listing cache.
Adding a source runs the label through the shared `sanitizeName()` so two
labels that only differ by case or punctuation don't collide on the
persisted key. `addSource` returns a discriminated `AddSourceResult`
(`{ok: true, key}` or `{ok: false, reason: "empty" | "duplicate", key?}`)
rather than a bare `string | null`, so a caller like `Toolbar` can tell
the user *why* the add was rejected — blank name vs. a label that
sanitises onto an already-existing key — instead of the form silently
closing with no feedback.

**Toasts.** `useToastStore` deduplicates by exact `(type, text)` match while
an identical toast is still on screen; the toast queue is capped at 10
visible entries. Error toasts do not auto-dismiss; all other types dismiss
after ~3 seconds (enforced by `Toast.tsx`, not the store).

**Chrome widgets.** `ErrorBoundary` wraps the root and each independently
recoverable panel in `App.tsx`, so one feature's crash doesn't take down the
whole editor. `ModalShell` is the shared dialog primitive — backdrop click,
Escape-to-close, and a full focus trap (Tab wraps inside the dialog, focus
returns to the triggering element on close) — used by `KeyboardShortcuts`
and by every git dialog. `Tooltip` is a zero-delay, self-clamping hover
label that repositions to avoid clipping the viewport edge. `ContextMenu` is
the right-click node menu with roving-tabindex arrow-key navigation.
`Toolbar` is the app's top chrome: source selector, row-limit/chunk-size
inputs, undo/redo, timing/memory breakdowns, save (with a "save & commit"
split-button). `NodeSearch` is the Ctrl+K command palette, windowed to
render only visible rows for large graphs. `BreadcrumbBar` shows the
pipeline → submodel navigation stack and is hidden entirely at depth 1.

**Reusable controls and style.** The global stylesheet establishes the initial dark canvas and
semantic tokens that all panels consume; it also owns native-control, scrollbar, and React
Flow interaction defaults. `NodeTypeIcon` displays the canonical icon/colour and maps an
unknown historical node type to Polars. `ToggleButtonGroup` is a real single-choice radio
group: only the selected option is in the tab order and Arrow keys/Home/End change both
selection and focus. The shared form primitives associate labels with controls, honour
disabled state, and buffer text locally until an explicit commit boundary, so typing into a
graph-backed configuration cannot create one undo entry per character.

**Pipeline imports.** The active imports UI is the right-side `ImportsPanel`, opened from the
toolbar and rendered by the app's mutually-exclusive right-panel cascade. It delegates editing
to `CodeEditor` and calls its parent for every editor change; the app applies those changes with
the graph store's raw preamble setter, so importing text immediately affects derived dirty state
without making an undo entry per keystroke. `SettingsModal` has the older dialog implementation
of the same callback contract, but no production import or render site.

**Leaf helpers.** Chart ticks and optimiser-mode inference are deterministic and side-effect
free. Trace formatting makes special values and calculation substitution visible rather than
normalising them away; malformed/circular object values are not caught by the formatter and
therefore fail at the caller, consistent with the application's fail-loud policy.

## Design rationale

- **Fail loud, not soft.** The API layer and the result-cache parsers
  (`hashConfig`, frontier-point derivation) throw on malformed input rather
  than defaulting — per the project's error-handling policy, a wrong
  fallback is worse than a visible crash, because a silent fallback hides
  the bug that produced it.
- **Retry only what's safe to retry.** GET/HEAD/PUT/DELETE/OPTIONS retry
  transient failures; POST does not, because retrying a non-idempotent
  mutation without server-side dedup risks duplicate side effects (e.g.
  double-triggering a training job). 4xx responses never retry — they mean
  the request itself is wrong, not that the server was briefly unavailable.
- **Entry-count LRU, not byte-accurate.** `useNodeResultsStore`'s caches
  evict by count rather than measured memory, because preview payloads are
  already bounded by backend row/column limits and byte-accurate browser
  accounting would add cost and noise for a case that isn't causing
  problems yet.
- **Separate stores by concern, not by feature.** Toasts, settings, and
  layout chrome are three different stores (not one monolith) specifically
  so that a component depending on "is the palette open" doesn't
  re-render on every MLflow poll tick.
- **CSS variables, not hard-coded hex, for anything theme-sensitive.**
  `theme/colors.ts` re-exports `var(--...)` tokens (light/dark themes swap
  the underlying CSS custom properties) rather than literal colours,
  except for the small `NODE_GROUP_COLORS` palette, which is
  fixed-per-node-type branding rather than a theme concern.
- **Endpoint modules split out of `api/client.ts` when their only
  consumer is lazy-loaded.** `api/dispersion.ts` exists as a separate file
  — not more exports on `client.ts` — specifically so its code isn't
  reachable from the initial bundle graph; `scripts/check-bundle-size.mjs`
  gates the initial-gzip budget this layout exists to respect. The
  pattern generalises — `api/assistant.ts` follows the same split for the
  assistant panel's endpoints (including its SSE stream reader and local
  event parsing, the same local-parsing exception `api/dispersion.ts`
  makes) rather than growing `client.ts` unconditionally.
- **A cached result's staleness key is `configHash` + `source` +
  `structuralVersion`, never `configHash` alone.** `CachedExploreResult`
  already tracked all three; solve/train results and
  `useStaleConfigEstimate`'s cached-result contract used to compare
  `configHash` alone, which let a cached solve/train/estimate result from
  one data source silently read as current after switching to another —
  same config hash, wrong source's data. Both were widened to the same
  three-field key in one change rather than leaving two different
  staleness definitions in the codebase.

## Interactions

- Consumed by every other frontend component: graph canvas
  ([frontend-graph-canvas](../frontend-graph-canvas/high-level.md)), node
  editors ([frontend-node-editors](../frontend-node-editors/high-level.md)),
  tracing ([frontend-trace-ui](../frontend-trace-ui/high-level.md)), git UI
  ([frontend-git-ui](../frontend-git-ui/high-level.md)), the assistant panel
  ([frontend-assistant-ui](../frontend-assistant-ui/high-level.md)), and the
  modelling/optimiser, explore/EDA, and preview panels — all of them call
  through `api/client.ts`, read/write `useNodeResultsStore` /
  `useSettingsStore` / `useToastStore` / `useUIStore`, and render inside the
  chrome this component provides.
- Talks to [server-api](../server-api/high-level.md) exclusively through the
  typed functions in `api/client.ts` — no other module in the frontend is
  expected to call `fetch()` directly against `/api/*`; split endpoint
  modules (dispersion, assistant) consume `client.ts`'s exported fetch/stream
  helpers rather than hand-rolling transport, which is how the rule holds
  even for the assistant's non-JSON SSE stream.
- `useBackgroundJobs` (mounted once via `BackgroundJobPolling`) polls solve,
  train, and explore jobs regardless of which panel is currently open, so
  those results reach `useNodeResultsStore` even after the user navigates
  away from the node that started them; this depends on the backend's job
  endpoints described in [background-jobs](../background-jobs/high-level.md).

## Failure model

- A non-2xx HTTP response becomes an `ApiError` with `status`/`detail`;
  callers branch on `status` (e.g. `loadPipeline` treats 404 as "no pipeline
  yet" and returns an empty graph — the one deliberate soft-fallback in the
  client, scoped to a single known-benign case).
- A request that exceeds its timeout becomes `ApiTimeoutError`, distinguished
  from a caller-cancelled `AbortError` so UI can show "timed out" instead of
  silently doing nothing.
- A response that parses as JSON but doesn't match the expected shape throws
  a descriptive `Error` from the relevant `parse*` guard (e.g. "expected
  object, got string") — this is a contract violation between frontend and
  backend, not a user-facing condition, and is expected to surface during
  development/CI rather than in production traffic.
- `useNodeResultsStore.updateFrontierAfterSelect` throws if the backend's
  echoed `point_index` doesn't match what was requested — treated as a
  contract violation, never silently corrected.
- Cache-limit misconfiguration (`MAX_CACHED_*` set to a non-positive
  integer) throws immediately from `assertValidCacheLimit` rather than
  silently disabling eviction.
- `runFrontier`/`runDispersionEstimate`'s embedded poll loop rejects with
  an `ApiError` (carrying the job's message and, for frontier, its
  `http_status_code`) on any non-`running`/non-`completed` terminal
  status, and with a plain `Error` if a `"completed"` status arrives
  without a result/value payload — a job that finishes without the data
  it promised is a contract violation, not treated as success. A caller
  `AbortSignal` fired mid-poll rejects with a `DOMException`
  (`"AbortError"`); `runDispersionEstimate` additionally best-effort
  cancels the backend job on abort (`cancelDispersion`, failure swallowed)
  so an abandoned poll doesn't leave an orphaned job running server-side.
- `ErrorBoundary` is the last line of defence for render-time exceptions:
  it logs via `console.error` and shows a "Try again" fallback scoped to
  the boundary it wraps, so one panel's crash is visible and recoverable
  without reloading the whole app.

## Polars backend contracts (0.6.0)

See [the remediation plan](../../trip/plans/F_0.6.0_polars-backend-remediation.plan.md).
Shared frontend infrastructure owns the typed API boundary for execution-strategy results. Its
runtime guard accepts known status fields while retaining safe representations of unknown future
fields, truncation and unavailable values. Feature-specific panels consume that single guarded
contract; they must not independently parse or normalise backend strategy payloads.
