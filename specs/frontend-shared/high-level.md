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
  guard the JSON/DOM boundary. The client dynamically loads the modelling
  training parsers from `types/trainGuards.ts` only for train/status/estimate
  responses, keeping that large contract outside the initial bundle. This also
  includes the bundle-split companion
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
  Explore pivot jobs and results use composite Explore-node/pivot identities,
  share terminal polling semantics with other jobs, and retain successful
  matrices across pane unmounts and visibility toggles.
- The design-token layer, which lives in `index.css`: the CSS
  custom-property colour primitives (surface/text/accent hues and the
  success/warning/danger intensity ladders, chart and syntax colours),
  the colour role tokens layered over them (e.g. the success-family
  roles and the `--diff-*` aliases), which name what an element is for
  rather than its shade, and the typography role token `--font-data`,
  which aliases Tailwind's `--font-mono` face for the same
  purpose-not-appearance reason. Components consume these as
  `var(--...)` references directly, in stylesheets and inline styles
  alike. `theme/colors.ts` is a narrower TypeScript-side companion:
  grouped `var(--...)` string constants
  (structure/status/model/chart/syntax) for code that needs a token as a
  JS value, plus the literal `NODE_GROUP_COLORS` palette — it is a
  consumer-facing view of the token layer, not the layer itself.
- Chrome widgets and app-shell surfaces: `ErrorBoundary`, `Toast`,
  `ModalShell`, `Tooltip`, `ContextMenu`, `KeyboardShortcuts`, `Toolbar`,
  `ImportsPanel`, `BackgroundJobPolling`, `NodeSearch`, `BreadcrumbBar`.
  `ImportsPanel` is the sole pipeline-imports editor.
- Small generic hooks with no domain knowledge: `useClickOutside`,
  `useDragResize`, `useJobPolling` (+ its orchestrator
  `useBackgroundJobs`), `useMlflowBrowser`, `useSchemaFetch`,
  `useStaleConfigEstimate`.
- Canonical shared types (`types/node.ts`, `types/trace.ts`,
  `types/banding.ts`) and the generic formatting/naming utilities
  (`utils/formatBytes.ts`, `utils/formatTime.ts`, `utils/formatValue.ts`,
  `utils/color.ts`, `utils/dtypeColors.ts`, `utils/portableKey.ts`,
  `utils/chartHelpers.ts`, `utils/formatTrace.ts`,
  `utils/mlflowOptimiser.ts`).
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
- Optimiser config plumbing (`useConstraintHandlers`, `useDataInputColumns`) and the primary
  contracts for `utils/configField.ts` and `utils/banding.ts` — see
  [frontend-modelling-optimiser-ui](../frontend-modelling-optimiser-ui/high-level.md).
  Node editors are direct consumers of `frontend/src/utils/configField.ts` and
  `frontend/src/utils/banding.ts`; `utils/edgeJoin*.ts` remains owned by
  [frontend-node-editors](../frontend-node-editors/high-level.md).
- Node-type/connection plumbing (`utils/connectionValidation.ts`,
  `utils/apiInputPorts.ts`, `utils/flowElements.ts`,
  `utils/flowHandles.ts`, `utils/nodeTypes.ts`,
  `utils/nodeTypeRegistry.ts`) — these belong to graph-canvas despite the
  node-editor-adjacent naming; see
  [frontend-graph-canvas](../frontend-graph-canvas/high-level.md).
- Trace playback (`useTracing`) — see
  [frontend-trace-ui](../frontend-trace-ui/high-level.md).
- Modelling/optimiser-specific objective logic (`utils/trainingObjective.ts`) — see
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

**API client.** Every backend call goes through the transport in
`api/client.ts`, which returns typed data or throws `ApiError` (HTTP
non-2xx, carrying `status`/`detail`/`body`) or `ApiTimeoutError` (the request
was aborted by its own timeout guard, distinct from a caller-initiated
abort). Idempotent verbs retry transient failures (network errors, 5xx) with
exponential backoff and jitter; POST is never auto-retried. Concrete JSON
endpoint functions pass successful responses through a runtime parser before
returning, so backend contract drift raises a descriptive `Error` at the call
site instead of `undefined` propagating silently into a component. The
exported generic `request`/`post` helpers, `postRawStream`, and caller-generic
`readJson<T>` are deliberate exceptions: a split module must request
`unknown` and validate its concrete response locally before exporting a typed
value; the contract is not asserted by `types/guards.ts` or by the generic type
parameter. A 403
whose detail matches the "missing/invalid session" reason fires a
`window` `CustomEvent` (`HAUTE_SESSION_EXPIRED_EVENT`) rather than being
handled locally — any part of the app can listen for session expiry without
the client needing to know about auth UI. Before `App` mounts,
`bootstrapHauteSession()` makes one same-origin, no-store POST and lets the
browser retain the returned HttpOnly cookie; concurrent callers share the
same promise. A forced refresh requested while a normal bootstrap is active
is queued behind that request rather than incorrectly joining it. The token
never enters JavaScript, request headers assembled by the client, or a
WebSocket URL. Bundle-split modules such as `api/dispersion.ts` reuse the
generic transport and own local response parsers so they remain outside the
initial chunk. `runDispersionEstimate` fronts a backend job that runs off the
request thread: it polls a `.../status/{job_id}` endpoint on a fixed interval
and resolves only once the job reaches a terminal status, so callers can
still `await` a single promise for what is, on the wire, a start-then-poll
sequence — distinct from `useJobPolling`/`useBackgroundJobs`, which track
jobs the user can navigate away from and revisit.

The shared boundary also owns the versioned execution-strategy diagnostic,
I/O capability, input-cache, and explicit output-write shapes. The diagnostic's
static TypeScript types, constants, and standalone structural validator are
generated from the canonical Pydantic contract; the handwritten parser owns
collection state/count relationships, strategy/status mapping, canonical order,
calibration consistency, compatibility handling, and stable projection into the
UI type. Strategy diagnostics retain known status fields and bounded
unavailable/truncated detail; an unsupported version becomes unavailable, while
a malformed matching version throws rather than being repaired by a feature panel.
Capability order and unsupported legs remain intact, and cache
readiness/freshness/progress are separate typed values.

**Result caching.** `useNodeResultsStore` is the only place preview rows,
optimiser solves, training runs, explore reports, and pivot matrices are kept
once computed. Each result category (`previews`, `solveResults`, `trainResults`,
`exploreResults`, `pivotResults`) is bounded to a fixed entry count and evicted
by least-recently-touched. In all five caches the currently pinned/open node's
entries survive eviction pressure; the pivot trimmer excludes the pinned node's
entries entirely from eviction candidates. A
config-hash (`hashConfig`) lets panels detect
"this result is stale relative to the current node config" without deleting
the old result. Despite its historical name, `hashConfig` is the exact
deterministic canonical JSON identity: root-only transient fields are
removed, object keys are recursively sorted, array order and normal JSON
normalisation are retained, and genuine serialization errors remain visible.
Cache reads compare every result-affecting dimension — canonical config,
source, structural generation and, for previews, row limit — so distinct
configurations cannot alias through a fixed-width digest. Frontier point
selection is a pure re-derivation from cached
frontier data — selecting a different point never re-fetches unless the
caller asks the backend to solve/select explicitly.

Active solve, train, Explore, and Pivot jobs additionally capture the
authoritative pipeline document identity/status fence. A live revision,
status, capability, or graph-synchronisation change drops active polling
state, and a late response rechecks its captured fence before it can enter a
result cache.

**Global settings.** `useSettingsStore` holds row limit, streaming chunk
size, per-section open/closed UI memory, the MLflow connectivity check
(fetched once, shared, retried on a 10s cooldown after failure), the list of
named data sources plus which is active, and a 30-second file-listing cache.
Adding a source runs the label through browser-owned `portableKey()`. Case is
preserved; if distinct labels converge after punctuation handling, the store
detects the occupied key and refuses the second addition. `addSource` returns a discriminated `AddSourceResult`
(`{ok: true, key}` or `{ok: false, reason: "empty" | "duplicate", key?}`),
allowing callers like `Toolbar` to surface *why* the add was rejected —
blank name vs. a label that sanitises onto an already-existing key — rather
than closing with no feedback.

**Toasts.** `useToastStore` deduplicates by exact `(type, text)` match while
an identical toast is still on screen; the toast queue is capped at 10
visible entries. Error toasts do not auto-dismiss; all other types dismiss
after ~3 seconds (enforced by `Toast.tsx`, not the store).

**Chrome widgets.** `ErrorBoundary` wraps the root and each independently
recoverable panel in `App.tsx`, so one feature's crash doesn't take down the
whole editor. `ModalShell` is the shared dialog primitive — backdrop click,
Escape-to-close, and a full focus trap (Tab wraps inside the dialog, focus
returns to the triggering element on close) — used by `KeyboardShortcuts`
and by every git dialog. The focus trap is installed for the modal lifetime;
parent re-renders update callback refs without refocusing the dialog or
rebuilding its listener. `Tooltip` is a zero-delay, self-clamping hover
label that repositions to avoid clipping the viewport edge. `ContextMenu` is
the right-click node menu with roving-tabindex arrow-key navigation.
`Toolbar` is the app's top chrome: it displays the package-derived browser
version alongside the source selector, row-limit/chunk-size inputs, undo/redo,
timing/memory breakdowns, the Submodel/Instance selection actions, and Save
and Commit as two sibling filled buttons.
`NodeSearch` is the Ctrl+K command palette, windowed to
render only visible rows for large graphs; the application loads its module
only when the palette is opened, so this user-triggered surface is not part
of the initial browser bundle. `BreadcrumbBar` shows the pipeline →
submodel navigation stack and is hidden entirely at depth 1.

**Reusable controls and style.** The global stylesheet establishes the initial dark canvas and
semantic tokens that all panels consume; it also owns native-control, scrollbar, and React
Flow interaction defaults. `NodeTypeIcon` displays the canonical icon/colour and maps an
unknown node type to Polars. `ToggleButtonGroup` is a real single-choice radio
group: only the selected option is in the tab order and Arrow keys/Home/End change both
selection and focus. The shared form primitives associate labels with controls, honour
disabled state, and buffer text locally until an explicit commit boundary, so typing into a
graph-backed configuration cannot create one undo entry per character.

**Pipeline imports.** The active imports UI is the right-side `ImportsPanel`, opened from the
toolbar and rendered by the app's mutually-exclusive right-panel cascade. It delegates editing
to `CodeEditor` and calls its parent for every editor change; the app applies those changes with
the graph store's raw preamble setter, so importing text immediately affects derived dirty state
without making an undo entry per keystroke.

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
  Theme-sensitive colour is declared once as custom properties in
  `index.css`, with role tokens aliasing the raw intensity-ladder
  primitives so call sites reference purpose, not shade; components use
  `var(--...)` rather than literal colours (the role-layer boundary is
  guarded by the tokenization gate in
  `frontend/src/__tests__/cssColorTokenization.test.ts`). `theme/colors.ts` follows
  the same rule where TypeScript needs a colour value, re-exporting
  `var(--...)` strings rather than hex — except for the small
  `NODE_GROUP_COLORS`, `PIVOT_CHART_COLORS`, and
  `PIVOT_CONDITIONAL_FORMAT_COLORS` palettes, which are fixed branding or
  visualisation semantics rather than theme roles. The semantic-colour
  tokenization test rejects fixed colour literals everywhere else.
- **Endpoint modules split out of `api/client.ts` when their only
  consumer is lazy-loaded.** `api/dispersion.ts` exists as a separate file
  — not more exports on `client.ts` — specifically so its code isn't
  reachable from the initial bundle graph; `frontend/scripts/check-bundle-size.mjs`
  gates the initial-gzip budget this layout exists to respect. The
  pattern generalises — `api/assistant.ts` follows the same split for the
  assistant panel's endpoints (including its status/session/history parsers and
  fully validating SSE reader, the same feature-owned local-parsing rule
  `api/dispersion.ts` follows) rather than growing `client.ts`
  unconditionally. Large response parsers can preserve the same boundary
  independently: `client.ts` dynamically imports `types/trainGuards.ts` from
  its modelling train/status/estimate methods, and the bundle checker treats
  that parser chunk as lazy-only and rejects a startup modulepreload.
- **A cached result's staleness key is `configHash` + `source` +
  `structuralVersion`, never `configHash` alone.** Cache entries (`CachedExploreResult`,
  `CachedSolveResult`, `CachedTrainResult`) and `useStaleConfigEstimate` compare all three
  dimensions because a matching config hash under a different data source or graph
  revision would otherwise falsely read as current.

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
  `loadPipeline` follows the same fail-loud rule as other endpoints and does
  not invent an empty graph for a 404.
- A request that exceeds its timeout becomes `ApiTimeoutError`, distinguished
  from a caller-cancelled `AbortError` so UI can show "timed out" instead of
  silently doing nothing.
- A response that parses as JSON but doesn't match the expected shape throws
  a descriptive `Error` from the relevant `parse*` guard (e.g. "expected
  object, got string") — this is a contract violation between frontend and
  backend, not a user-facing condition, and is expected to surface during
  development/CI rather than in production traffic.
- A split-module response is subject to the same rule: the generic transport
  returns `unknown`, the feature parser validates required fields before
  returning, and a malformed payload throws an ordinary descriptive `Error`.
  HTTP failures remain `ApiError`; a type parameter alone is never treated as
  validation.
- Parsers whose purpose is to discriminate an optional payload return
  `null` only when their discriminator does not match (for example an
  unsupported execution-diagnostic schema version or a non-divergence Git
  response). Once the discriminator matches, malformed required fields
  throw; parse exceptions are never converted to `null`.
- `useNodeResultsStore.updateFrontierAfterSelect` throws if the backend's
  echoed `point_index` doesn't match what was requested — treated as a
  contract violation, never silently corrected.
- Cache-limit misconfiguration (`MAX_CACHED_*` set to a non-positive
  integer) throws immediately from `assertValidCacheLimit` rather than
  silently disabling eviction.
- `runDispersionEstimate`'s embedded poll loop rejects with
  an `ApiError` carrying the job's message on any non-`running`/non-`completed` terminal
  status, and with a plain `Error` if a `"completed"` status arrives
  without a result/value payload — a job that finishes without the data
  it promised is a contract violation, not treated as success. A caller
  `AbortSignal` fired mid-poll rejects with a `DOMException`
  (`"AbortError"`); `runDispersionEstimate` awaits `cancelDispersion` before
  rejecting so an abandoned poll cannot silently leave an orphaned job
  running server-side. A cancellation failure is visible rather than
  swallowed.
- `ErrorBoundary` is the last line of defence for render-time exceptions:
  it logs via `console.error` and shows a "Try again" fallback scoped to
  the boundary it wraps, so one panel's crash is visible and recoverable
  without reloading the whole app.

## Pipeline editor document trust boundary

The initial pipeline endpoint returns `unknown` through the generic transport. A dedicated strict
parser validates the complete versioned editor-document shape, including exact nested keys,
finite positions and spans, recovery endpoint references, diagnostics, raw revision, and
server-derived capabilities. Only then does one adapter create React Flow presentation nodes.
Recovery wire objects are never passed to canonical graph request builders.

`useDocumentStatusStore` atomically owns the authoritative load state, capabilities, diagnostics,
raw revision, current readable source, source-selection trust, graph-synchronisation state, and any
current system load failure. The request-facing revision ref is updated before graph publication.
Save requests send that revision as `base_revision`. A `409` `stale_document_revision`
response keeps the local graph dirty, marks the document unsynchronised, and shows the sync
banner until the user reloads or discards local edits; the client never retries with an
overwrite.
Authored `degraded` and `source_only` responses are successful load states and do not emit the generic
load-failure toast; transport, permission, unreadable-file, and malformed-response failures retain an
explicit read-only failure surface and cannot leave a retained canvas looking current.

The same strict parser validates `pipeline_document_update` frames before any store mutation. Status
and capability state is applied before a graph snapshot and remains applied when a dirty-graph guard
rejects graph replacement. A `parse_error` is a sanitized system failure for the current editor
document: it marks the graph unsynchronised and activates the load-failure surface until the next
valid document update. Authored recovery states never arrive through that frame.
Degraded preview calls use the recovery-preview transport with source, revision, and target identity
only. A current source-only state and an optional in-memory last-renderable snapshot retain separate
revisions and are never merged.

Minimal repair responses cross a separate strict parser boundary. Dry-run
validation requires the remove-only discriminator, source/target identities,
64-hex plan hash, bounded artifact patches, retained artifacts, warnings, and
predicted load state. Apply validation requires the same plan identity plus a
complete valid editor document. The browser never accepts replacement bytes,
source spans, migration instructions, or a recovery graph as an apply payload;
it sends only server identities, revision, explicit config-deletion choice,
and the confirmed plan hash.
