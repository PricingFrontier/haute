# Frontend Shared Infrastructure — Low-Level Specification

## Module map

| File | Responsibility |
|---|---|
| `frontend/src/utils/editorIdentities.ts` | Builds bounded identity requests, applies exact-order server responses, and attaches authoritative node/edge metadata without mutating the candidate graph. |
| `frontend/src/main.tsx` | Local-session bootstrap: establishes the browser-managed HttpOnly cookie before mounting `App` inside `StrictMode` + a root `ErrorBoundary`; renders an actionable reload state if the local backend is unavailable. |
| `frontend/src/api/client.ts` | Typed `fetch()` wrapper: same-origin cookie credentials, single-flight `bootstrapHauteSession`, retry/backoff, timeout, abort handling, session-expiry event, and one function per backend endpoint. Exports `request`/`post` so split-chunk endpoint modules can reuse the same fetch machinery, and a raw-stream helper (cookie credentials + `ApiError` mapping, no JSON parse) for split modules with non-JSON transports — the assistant SSE stream (see [frontend-assistant-ui](../frontend-assistant-ui/low-level.md)). Modelling train/status/estimate methods dynamically import `types/trainGuards.ts` only after their response arrives so the large training contract stays out of the initial bundle. |
| `frontend/src/api/dispersion.ts` | GLM dispersion-estimation endpoints (NB `theta` / Tweedie `var_power`): `estimateGlmDispersion`, `getDispersionStatus`, `cancelDispersion`, and `runDispersionEstimate` (starts + polls to completion, resolving with the estimated number). Split out of `client.ts` so its code — reachable only from the lazy-loaded modelling config panel — stays out of the initial JS bundle; built on `client.ts`'s exported `request`/`post` and owns its own runtime parsers (`parseDispersionEstimateResponse`, `parseDispersionStatusResponse`) rather than routing through `types/guards.ts`. |
| `frontend/src/api/types.ts` | Request/response TypeScript interfaces mirrored from backend contracts, including nullable directory sizes and canonical evaluation/tuning reports and previews; its execution-strategy pilot aliases the generated declarations while normalising nullable reason fields for the stable UI shape. It re-exports canonical node/trace types and owns the runtime `JOB_STATUS_VALUES`, `FAILED_JOB_STATUSES`, and `TERMINAL_JOB_STATUSES` shared by guards and pollers. |
| `frontend/src/types/node.ts` | Canonical persisted `PIPELINE_NODE_TYPES` vocabulary and `NodeTypeValue`; `HauteNodeData`/`PipelineFlowNode`/`SubmodelNodeData` shapes, `ColumnInfo`, `BackendNodeStatus`/`NodeStatus`, and the `nodeData()`/`effectiveNodeType()` accessors used everywhere a React Flow `Node.data` needs typed access. |
| `frontend/src/types/trace.ts` | Trace playback shapes (`TraceStep`, `TraceResult`, per-node-type `TraceNodeDetail` variants) mirroring backend trace output. |
| `frontend/src/types/banding.ts` | Banding-factor rule shapes shared between the banding node editor and its trace rendering. |
| `frontend/src/types/guards.ts` | Shared runtime parser primitives plus parsers (`parse*`) and type guards for eagerly used concrete JSON API response shapes; part of the JSON/DOM trust boundary. Execution-strategy parsing delegates matching-version structural assertions to its generated standalone validator, then applies explicit compatibility, relationship, ordering, and calibration semantics. `parseFileListResponse` accepts absent, numeric, or null `size` while retaining strict validation of every other field. Generic transport helpers, the caller-generic `readJson<T>`, and split-module local parsers are explicit exceptions. |
| `frontend/src/types/generatedContractValidation.ts` | Lazy Explore adapter for generated-validator errors: constructs stable instance paths (including missing required properties), formats contract failures, and locates matching keyword/path errors without coupling the chart parser to Ajv internals. The eager execution parser keeps its small fail-fast formatter local so this module does not hoist Explore-only contract data into the initial chunk. |
| `frontend/src/generated/api-contracts.schema.json`, `frontend/src/generated/api-contracts.generated.ts`, `frontend/src/generated/api-contracts.constants.generated.ts`, `frontend/src/generated/api-contracts.execution-strategy-diagnostic.validators.mjs`, `frontend/src/generated/api-contracts.execution-strategy-diagnostic.validators.d.mts`, `frontend/src/generated/api-contracts.explore-charts.validators.mjs`, and `frontend/src/generated/api-contracts.explore-charts.validators.d.mts` | Committed contract source, static declarations, lazy Explore constants, and split self-contained validators owned by [engineering-quality](../engineering-quality/low-level.md) and consumed by frontend trust boundaries. The execution validator co-exports its generated schema version and is eager; the Explore chart validator and option constants stay behind its lazy panel chunk. |
| `frontend/src/types/trainGuards.ts` | Dynamically imported runtime parsers for modelling train/status/estimate responses. Training parsing strictly retains authoritative live-history/truncation and validates complete evaluation/tuning reports, weighted fit evidence, deterministic winner/count links, and bounded evaluation previews while remaining outside the initial JavaScript graph. |
| `frontend/src/types/pipelineRepair.ts` | Exact-key minimal repair dry-run/apply wire types and parsers. Apply delegates its nested document to `parsePipelineEditorDocument`; no response or request type contains replacement source bytes or migration operations. |
| `frontend/src/stores/useNodeResultsStore.ts` | Zustand store: preview/solve/train/explore result caches, authoritative training history plus bounded ETA samples, column cache, derived-getter memoization, LRU eviction, and the atomic per-pivot start claim (one current claim per Explore node + pivot id holding the owning node id, the requested dataframe cache key, calculation identity, and a unique generation token; taking a claim before submission serialises concurrent consumers, an identical automatic target no-ops, every manual Retry and every newer automatic target atomically replaces the generation, only the current token may promote it to a job or release it — superseded outcomes are discarded — and clearing a node's results drops exactly the claims whose stored node id matches). |
| `frontend/src/stores/useSettingsStore.ts` | Zustand store: row limit, streaming chunk size, section open/closed state, MLflow status cache, data sources, file-listing cache. |
| `frontend/src/stores/useToastStore.ts` | Zustand store: toast queue with dedup, capped at 10 entries. |
| `frontend/src/stores/useUIStore.ts` | Zustand store: modal/panel open flags (git/utility/imports/assistant, mutually exclusive by construction — each setter clears the others), sync banner, node panel width, per-node Explore/modelling selection memory (editor pane, preview pane, and the configured chart/pivot Configure-subview ids), hover highlight, node search open flag. |
| `frontend/src/theme/colors.ts` | CSS-variable-backed colour token constants (`STRUCTURE_COLORS`, `STATUS_COLORS`, `MODEL_COLORS`, `CHART_COLORS`, `SYNTAX_COLORS`) plus the fixed `NODE_GROUP_COLORS`, `PIVOT_CHART_COLORS`, and `PIVOT_CONDITIONAL_FORMAT_COLORS` visualisation palettes. |
| `frontend/src/utils/formatBytes.ts` | Byte count → `B`/`KB`/`MB` string. |
| `frontend/src/utils/formatTime.ts` | Unix timestamp → `HH:MM` / coarse relative-time label. |
| `frontend/src/utils/formatValue.ts` | Renders backend's non-finite-float sentinel (`{__haute_type__: "non_finite_float", ...}`) as `NaN`/`Infinity`/`-Infinity`. |
| `frontend/src/utils/color.ts` | Hex → `rgba(...)` string with alpha, for CSS-var-driven accent colours. |
| `frontend/src/utils/dtypeColors.ts` | Dtype string → Tailwind text-colour class for column-type badges. |
| `frontend/src/utils/portableKey.ts` | Browser-owned persistence key; intentionally not Python-compatible or reversible. Executable identity comes only from server metadata. |
| `frontend/src/components/ErrorBoundary.tsx` | Class-component error boundary with a "Try again" fallback UI. |
| `frontend/src/components/Toast.tsx` | `ToastMessage` type + `ToastContainer`, rendering `useToastStore`'s queue with per-type icon/colour and auto-dismiss. |
| `frontend/src/components/ModalShell.tsx` | Shared dialog chrome: backdrop, Escape-close, full Tab focus trap, focus restore on unmount. |
| `frontend/src/components/Tooltip.tsx` | Zero-delay CSS-hover tooltip with edge-clamped horizontal position and top/bottom auto-flip. |
| `frontend/src/components/ContextMenu.tsx` | Node right-click menu: rename/duplicate/create-instance/dissolve-submodel/delete, arrow-key roving focus. |
| `frontend/src/components/KeyboardShortcuts.tsx` | `?`-triggered modal listing keyboard shortcuts, built on `ModalShell`. |
| `frontend/src/components/Toolbar.tsx` | App top chrome: package-derived browser version, source selector, row-limit/chunk-size inputs, undo/redo, timing/memory breakdowns, Submodel/Instance selection actions, utility/imports/assistant buttons, zoom, centre/layout, and Save + Commit. Actions share the `.toolbar-btn` surface; the selection actions carry `aria-disabled` rather than `disabled` so an unavailable action stays focusable and its handler can explain the refusal. Numeric fields suppress native spinners without clipping either the configured row-limit value or the chunk-size backend maximum. Composes `BreakdownDropdown` and `BranchIndicator` (git-ui). |
| `frontend/src/components/BreakdownDropdown.tsx` | Sorted, accessible timing/memory breakdown disclosure used by the shared toolbar. |
| `frontend/src/panels/ImportsPanel.tsx` | Active pipeline-imports right panel: `PanelShell` plus `CodeEditor`, explanatory always-included imports, and callback-only preamble mutation/close handling. `App.tsx` supplies the graph-store-backed preamble and selects it through `importsOpen`. |
| `frontend/src/components/BackgroundJobPolling.tsx` | Zero-render mount point (`memo`) that only invokes `useBackgroundJobs()`. |
| `frontend/src/components/NodeSearch.tsx` | Ctrl+K command palette: dynamically imported by `App.tsx` only while open, filters/windows the current React Flow node list, supports arrow-key navigation, and jumps the canvas viewport to the selected node. |
| `frontend/src/components/BreadcrumbBar.tsx` | Pipeline → submodel navigation trail; renders nothing at stack depth ≤ 1. |
| `frontend/src/hooks/useClickOutside.ts` | Attaches/detaches a `mousedown` listener that fires `onClose` when the click lands outside `ref`, only while `active`. |
| `frontend/src/hooks/useDragResize.ts` | Bottom-panel drag-to-resize: DOM-direct mutation while dragging, commits to React state on mouseup. |
| `frontend/src/hooks/useJobPolling.ts` | Thin React adapter that keeps one `JobPollingController` configured, reconciles the current job record after commit, and disposes it on unmount. |
| `frontend/src/hooks/jobPollingController.ts` | The single state authority for generic background polling: active poller identities, timers, abort controllers, interval ramp, progress throttling, replacement, terminal completion/error, and disposal. |
| `frontend/src/hooks/useBackgroundJobs.ts` | Wires `useJobPolling` to the optimiser/train/explore endpoints and `useNodeResultsStore` actions; mounted once in `App.tsx`. |
| `frontend/src/hooks/useMlflowBrowser.ts` | Lazy-loads MLflow experiments/runs/models/versions for dropdown UIs; shared by `ModelScoreEditor` and `OptimiserApplyEditor` (node-editors). |
| `frontend/src/hooks/useSchemaFetch.ts` | Fetch-schema-on-mount-and-on-path-change pattern used by `frontend/src/panels/editors/ApiInputEditor.tsx` and `frontend/src/panels/editors/DataInputEditor.tsx` (node-editors). |
| `frontend/src/hooks/useStaleConfigEstimate.ts` | Generic "estimate endpoint keyed by config hash + source + structural version, refetch when any of the three changes" pattern, built on `hashConfig`. Takes a required `context: {source, structuralVersion}` argument alongside the cached result. |
| `frontend/src/index.css` | Global Tailwind import and dark-theme CSS-variable contract: root sizing/type, native-control and scrollbar defaults, React Flow interaction overrides, canonical semantic surface/status/chart/git-node tokens consumed directly by the theme module and components, and typography role tokens (`--font-data`) that alias Tailwind theme tokens rather than redeclaring them (no Tailwind theme token may be redeclared in the file's plain blocks — they are unlayered and would shadow `@layer theme`; a deliberate override belongs in an `@theme` block, which the gate exempts automatically; and components conventionally reference the role token rather than the raw Tailwind name — adoption and emission both pinned by `__tests__/cssColorTokenization.test.ts`). Also owns the `.toolbar-btn` action-button surface (resting/hover/pressed fills, engaged `aria-pressed` toggle, and a flat unavailable state that keeps its label readable) and the `.toolbar-number-input` spinner suppression. |
| `frontend/src/utils/chartHelpers.ts` | Small pure chart leaf helpers: compact K/M/scientific axis labels and inclusive evenly spaced Y ticks (a degenerate range yields one tick). |
| `frontend/src/utils/formatTrace.ts` | Cross-surface trace-value/expression/calculation/schema-summary presentation formatting: retains date-shaped strings, represents non-finite numbers explicitly, quotes ordinary strings, escapes column names before substitution, and uses longest names first to avoid partial replacement. |
| `frontend/src/utils/mlflowOptimiser.ts` | Pure MLflow run/model metadata classifier: the canonical `params.mode` value selects ratebook versus online; absent or invalid values yield the empty mode. |
| `frontend/src/components/NodeTypeIcon.tsx` | Shared node-type icon wrapper: looks up canonical metadata and deliberately renders the Polars icon for an absent or unknown type, so compact lists never crash on incomplete historical data. |
| `frontend/src/components/ToggleButtonGroup.tsx` | Generic controlled segmented single-choice group with radio semantics, roving `tabIndex`, Arrow/Home/End selection and focus movement, optional accessible name, and token-derived active styling. |
| `frontend/src/components/form/CommittedTextField.tsx` | Controlled-looking input/textarea with a local draft: commits once on blur (and Enter for the input), skips no-op commits, and discards a stale draft when the external value changes, preserving one edit/one undo snapshot. |
| `frontend/src/components/form/ConfigCheckbox.tsx` | Labelled controlled checkbox using a caller id or React `useId`, disabled semantics, and shared accent/text tokens. |
| `frontend/src/components/form/EditorLabel.tsx` | Consistent micro-label primitive; can be a correctly associated `<label>` or non-form span/div for display-only content. |
| `frontend/src/components/form/index.ts` | Public barrel for the committed text field/area, checkbox, and editor-label primitives; editor callers import the shared contract rather than deep paths. |

## Key types and data structures

- **`ApiError`** (`api/client.ts`): `status: number`, `detail?: string`,
  `body?: unknown` (parsed JSON error body), `rawDetail?: unknown`
  (`body.detail` or the whole body, pre-stringify — consumed by
  `executionDiagnostics` in node-editors to read structured failure fields
  without re-parsing `detail`).
- **`ApiTimeoutError`**: `timeoutMs: number`, `url: string`; thrown only
  when the *timeout guard's own* `AbortController` fired (tracked via an
  internal `abortSource` flag in `attemptFetch`), never for a
  caller-supplied signal.
- **`RetryPolicy`** / **`ApiClientOptions`**: `{ maxRetries?, baseDelayMs? }`
  and `{ signal?, timeout?, retry? }`. `DEFAULT_RETRY_POLICY` is
  `{ maxRetries: 3, baseDelayMs: 100 }`; `resolveRetryPolicy` throws if a
  caller passes a non-integer/negative `maxRetries` or a non-positive
  `baseDelayMs`.
- **`HauteNodeData`** (`types/node.ts`): the typed view of a pipeline
  node's `Record<string, unknown>` data — `label`, `nodeType`, `config`,
  transient `_columns`/`_availableColumns`/`_schemaWarnings`/`_columnsSource`/
  `_status` fields set by `usePipelineAPI` (graph-canvas) and `useTracing`
  ([frontend-trace-ui](../frontend-trace-ui/low-level.md)), and
  `_diffStatus` used only by the read-only git comparison view.
  `_columnsSource` tags which active data source the `_columns`/
  `_availableColumns`/`_schemaWarnings` stash was captured under — a
  graph-canvas concern (see
  [frontend-graph-canvas](../frontend-graph-canvas/low-level.md)) but the
  field itself lives on this shared type. `nodeData()` is the single cast
  boundary — callers should never write `node.data as HauteNodeData`
  directly.
- **`CachedPreview` / `CachedSolveResult` / `CachedTrainResult` /
  `CachedExploreResult`** (`stores/useNodeResultsStore.ts`): one struct per
  result category, each carrying enough to redraw its panel plus a
  `configHash`/`source`/`structuralVersion` staleness key.
  Every active solve/train/Explore/Pivot job also carries a captured
  `DocumentExecutionFence` (source identity, raw revision, load status, and
  execute capability). `useBackgroundJobs` drops all active jobs when that
  live fence changes, while every progress/completion/failure store action
  independently rejects a late response whose captured fence is no longer
  current or whose renderable graph is no longer synchronised.
  `CachedSolveResult` additionally carries both `result` (current,
  possibly frontier-point-derived) and `originalResult` (the as-solved
  baseline), so switching frontier points never loses the original. A
  direct `complete*Job` call with no active job recorded (no in-flight
  `ActiveSolveJob`/`ActiveTrainJob` to read `source`/`structuralVersion`
  from) falls back to `source: ""` and `structuralVersion: -1` — sentinels
  that can never equal a real value, so the record reads as stale rather
  than silently matching whatever the caller happens to be viewing.
- **`CachedPivotResult` / `ActivePivotJob`** (`stores/useNodeResultsStore.ts`): records keyed by
  `${exploreNodeId}:${pivotId}`. A cached matrix stores its backend calculation identity and
  dataframe-cache key; an active job stores the same composite ownership plus job id/source.
  Completion replaces only that pivot's result, failure stays local, and disabling a card does
  not delete either record. `useBackgroundJobs` polls every active pivot job through the guarded
  pivot status parser and applies the standard completed/failed terminal split.
- **`AddSourceResult`** (`stores/useSettingsStore.ts`): `addSource`'s
  return type — `{ok: true, key: string}` on success, or
  `{ok: false, reason: "empty"}` / `{ok: false, reason: "duplicate", key}`
  on rejection. Replaces a bare `string | null` so a caller can surface
  *why* the add failed rather than treating `null` as an unexplained
  no-op.
- **`DispersionParam`** (`"theta" | "var_power"`), **`DispersionEstimateStart`**
  (`{status: "started", job_id}`), **`DispersionEstimateStatus`**
  (`{status: JobStatus, progress, message, elapsed_seconds, param, value,
  llf, n_fits, error, terminal_reason}`) (`api/types.ts`): the GLM
  dispersion-estimation job shapes consumed by `api/dispersion.ts`.
- **`JOB_STATUS_VALUES` / `JobStatus` / `FAILED_JOB_STATUSES` /
  `TERMINAL_JOB_STATUSES`** (`api/types.ts`) — the single runtime/type
  vocabulary for all job parsers and polling failure/terminal checks.
- **`FrontierStatusResponse`** (`api/types.ts`): `{status: JobStatus,
  progress, message, elapsed_seconds, result: FrontierResponse | null,
  terminal_reason?, error_code?, http_status_code?, error_detail?,
  execution_metrics?}` — the response from the frontier background-job
  status endpoint. `FrontierResponse` has an optional `job_id` for the
  `status === "started"` case.
- **`NodeResultsState`**: the store's full shape — six job/result record
  pairs (`{previews, solveResults+solveJobs, trainResults+trainJobs,
  exploreResults+exploreJobs}`), a `columnCache` keyed
  `"nodeId"` or `"nodeId:source"`, and a `pinnedPreviewNodeId` that is
  exempted from LRU eviction across all four result caches.
- **`SettingsState.mlflow`**: `{status: "pending"|"connected"|"error",
  backend, host, installed, importable, trackingConfigured, detail}` —
  `useMlflowStatus()` (exported alongside the store) maps `"pending"` to
  `"loading"` for display purposes only; the store itself never uses the
  word "loading".
- **`ToastMessage`** (`components/Toast.tsx`): `{id, type: "success"|
  "error"|"info"|"warning", text}`. `id` is a monotonically increasing
  string counter, not a UUID.
- **`GraphPayload`** (`api/types.ts`): `{nodes, edges, submodels?,
  preamble?}` — the minimal canonical shape every pipeline-mutating endpoint
  accepts. Before transport, the browser recursively removes React Flow UI
  fields and all underscore-prefixed editor metadata from root and embedded
  graphs without mutating the live graph. It is distinct from the richer
  `PipelineGraph` (adds pipeline metadata) that `loadPipeline`/
  `getCommitPipeline` return.

## Control flow

**Request lifecycle (`api/client.ts`)**: `request()` resolves the retry
policy, then loops `attempt = 0..maxRetries`. Each attempt calls
`attemptFetch`, which owns a fresh `AbortController` per attempt — a
`setTimeout` aborts it on timeout, and an external caller signal (if any)
is bridged to the same controller with its listener removed in a `finally`
so listeners don't accumulate across retries. On failure, `shouldRetry`
gates continuation: non-idempotent method → no; `AbortError` → no (user
cancelled, propagate immediately); `TypeError` (network layer) or 5xx → yes,
provided attempts remain, in which case `backoffSleep` waits
`backoffDelayMs(attempt)` (equal-jitter exponential: `[base·2ⁿ/2,
base·2ⁿ]`) before the next attempt, itself abortable by the external
signal. A non-timeout `AbortError` from `backoffSleep`/`attemptFetch`
propagates as-is.

`inferJsonCacheSchema` is an endpoint-specific timeout exception: ordinary
Infer Tables calls omit `sample_size` and use a 30-minute timeout, matching the
cache-build budget, because schema discovery is complete by default and can
legitimately exceed the shared 30-second interactive timeout on multi-GB
inputs. Supplying `sample_size` remains an explicit caller choice; the client
never inserts one silently.

**Local session bootstrap (`main.tsx` + `api/client.ts`)**:
`bootstrapHauteSession()` deduplicates concurrent calls, POSTs
`/api/session/bootstrap` with `credentials:"same-origin"` and `cache:"no-store"`,
and never reads a response token. The browser stores the HttpOnly cookie and
ordinary API/raw-stream requests explicitly use same-origin credentials.
`main.tsx` mounts `App` only after success; failure renders a local-server
diagnostic with Reload. A forced bootstrap refresh is used by WebSocket
reconnection after a backend restart. If that forced refresh arrives while a
normal bootstrap is active, it queues a second request behind the in-flight
one; concurrent forced callers join that queued forced request.

**Response parsing**: concrete JSON endpoint functions pipe
`request<unknown>()` results through the matching runtime parser before
returning. `loadPipeline` follows the same fail-loud HTTP and payload rules as
the rest; a 404 is an `ApiError`, not an invented empty graph. The exported
generic `request`/`post` helpers, `postRawStream`, and caller-generic
`readJson<T>` cannot assert an endpoint-specific shape and are documented
exceptions. Split modules such as `api/dispersion.ts` validate with their own
local parsers (`parseDispersion*`) to preserve the lazy bundle boundary.
`api/assistant.ts` likewise requests status/session JSON as `unknown`, validates
every history row locally, and parses every required field of each SSE variant
before invoking its callback. These feature parsers tolerate unrelated additive
fields but reject missing or mistyped required fields with ordinary `Error`
values; only the shared transport manufactures `ApiError`.

**Client-embedded job polling** (`runDispersionEstimate`): a third polling
shape alongside `useJobPolling` and `useNodeResultsStore`'s result caches —
the poll loop lives directly inside the `async` client function rather than
in a hook or store action. It starts the job, polls
`getDispersionStatus(jobId)` at the configured interval (500ms by default),
and resolves or rejects the single outer promise. Because there is no store
entry for this job, a caller that unmounts mid-poll relies on its own
`AbortSignal` to stop the loop. An abort after job creation awaits
`cancelDispersion(jobId)` before the outer promise rejects; cancellation
failure remains visible.

**Result-cache write path** (`useNodeResultsStore`): each `complete*Job`
action first validates the active job's `DocumentExecutionFence`; a stale
response removes only the obsolete in-flight entry and writes no result.
A current response (1) removes the corresponding entry from the `*Jobs` in-flight map,
(2) builds the next cached record, (3) calls `trimCacheByRecency` to bound
the record count (recency tracked in a module-level `Map`, not store state,
so touching a cache entry for read purposes doesn't trigger a re-render),
(4) evicts the module-level derived-getter cache
(`_optimiserPreviewCache`/`_modellingPreviewCache`) for anything
`trimCacheByRecency` dropped, and (5) recomputes the derived-getter cache
entry for the just-written node. `getOptimiserPreview`/`getModellingPreview`
are safe to call during render because they only ever read the memoized
derived cache or recompute it inline — they never call `set()`.

**Frontier point selection** (`selectFrontierPoint`,
`updateFrontierAfterSelect`): selecting a point is a pure local
re-derivation (`deriveSolveResultForFrontierPoint`) from the cached
frontier's `points` array — no network call. `updateFrontierAfterSelect` is
the network-driven counterpart used after an explicit backend
`/optimiser/frontier/select`; it validates the echoed `point_index` matches
the request, merges the richer per-point fields the backend returned back
into the cached frontier's `points` array (so later re-selecting that point
doesn't need another round trip), and — critically — if the user has since
selected a *different* point while the request was in flight, it keeps the
frontier-array enrichment but does not regress the displayed
`result`/`selectedPointIndex` to the stale response's point (the
"stale-response guard").

**Background job polling** (`JobPollingController` + `useJobPolling` +
`useBackgroundJobs`): `useBackgroundJobs` mounts four `useJobPolling`
instances (solve/train/explore/pivot), each driven by the store's `*Jobs`
record. The hook owns one controller instance, updates its callbacks, and
reconciles the committed job record into it. The controller is the single
authority for the running-poller map: a job not yet present gets a
`setTimeout`-driven loop starting
at `BASE_INTERVAL_MS` (500ms). After each non-terminal response or retryable
poll error, the next interval doubles (`500ms → 1s → 2s → 4s → 5s`) and then
holds at `MAX_INTERVAL_MS` (5s) for the rest of that job. Each request is
capped by `POLL_TIMEOUT_MS` (30s) and the poller by
`MAX_LIFETIME_MS` (24h) total. `CONSECUTIVE_FAILURES_FOR_TOAST` consecutive
poll errors trigger a toast (poll errors are tolerated silently up to that
point — the network hiccup case is expected). A 404/410 from the poll
endpoint (`TERMINAL_MISSING_JOB_STATUSES`, checked via
`getMissingJobPollErrorMessage`) is treated as "job is gone, stop polling"
rather than a retryable transient error. Reconciliation aborts and retires a
poller when its node disappears or the same node is replaced by a different
job id; completions from retired identities cannot publish progress or
terminal state. Controller disposal performs the same retirement for every
job and leaves no timer or request alive after unmount.

**Modal focus trap** (`ModalShell`): on mount, stashes
`document.activeElement`, focuses the dialog container, and installs a
`keydown` listener that (a) closes on Escape or any `extraCloseKeys` match,
and (b) on Tab, redirects focus back inside the container if it has somehow
landed outside, otherwise wraps first↔last focusable element. On unmount,
focus is restored to the element that was focused before the modal opened.
The listener is installed once per mount and reads `onClose`/
`extraCloseKeys` through current refs, so a parent re-render neither steals
focus nor leaves stale callbacks.

## Edge cases and invariants

- **Retry budget is `maxRetries + 1` attempts total**, not `maxRetries`
  retries after the first try counted separately from it — the loop is
  `attempt <= maxRetries`.
- **External abort takes priority over timeout**: `attemptFetch` tracks
  which source (`"timeout"` vs `"external"`) fired first via
  `abortSource ??= source`; only a timeout-sourced abort becomes
  `ApiTimeoutError`, so a caller-cancelled request never gets misreported
  as a timeout even if both fire near-simultaneously.
- **No browser-readable session token exists.** `api/client.ts` never reads a
  window global or Vite token variable; browser authentication is cookie-managed.
- **Column cache freshness** (`useNodeResultsStore.getColumns`) is
  `structuralVersion === useGraphStore.getState().structuralVersion` — a
  direct cross-store read at call time, not a subscription, so a stale
  read only happens if the caller doesn't re-invoke `getColumns` after a
  structural change.
- **`hashConfig`** strips `_nodeId`/`_columns`/`_schemaWarnings`/
  `_availableColumns` only from the root config, applies ordinary
  `JSON.stringify`/`JSON.parse` normalisation, recursively sorts object keys,
  preserves array order, and returns the canonical JSON string itself.
  Nested fields with the same names remain semantic. This exact identity is
  intentionally collision-free; cycles, `BigInt`, and other genuine
  serialization failures throw rather than receiving a fallback identity.
- **`trimCacheByRecency`** first prunes any recency-map entries whose key
  no longer exists in `records` (handles external deletion, e.g.
  `clearNode`), then evicts the least-recently-touched entries beyond
  `maxEntries`, always excluding `pinnedKey` — if pinning would leave more
  entries than `maxEntries`, the pinned entry is still never evicted (the
  bound is soft in that one case).
- **`addSource`** performs no state change for a blank/whitespace-only name
  (`{ok: false, reason: "empty"}`) or for a name whose sanitized key
  already exists in `sources` (`{ok: false, reason: "duplicate", key}`) —
  callers must check `result.ok` before reading `result.key` and setting
  `activeSource`. `Toolbar`'s add-source form keeps itself open and shows
  the reason as inline error text on rejection, rather than closing
  silently as it did when the return type was a bare `string | null`.
- **`useStaleConfigEstimate` compares the complete result identity.**
  `cachedResult` carries `configHash`, `source`, and `structuralVersion`;
  any mismatch marks the estimate stale.
- **Toast dedup** compares only `(type, text)`; it does not advance the
  toast id counter on a suppressed duplicate, so the counter's absence of
  increment is itself the observable "nothing was added" signal used by
  tests.
- **`NodeSearch`** windows rendering to `NODE_SEARCH_VISIBLE_ROWS +
  2 × NODE_SEARCH_OVERSCAN_ROWS` rows regardless of result-set size, but
  keeps the currently-active result in the accessibility tree (visually
  hidden, off-screen-clipped) even when scrolled out of the rendered
  window, so `aria-activedescendant` always resolves to a real DOM node.
  `App.tsx` dynamically imports the component and wraps only its conditional
  render site in `Suspense`; a closed palette therefore contributes no
  `NodeSearch` code to the initial chunk.
- **`ModalShell`** guards the zero-focusable-elements case: if
  `querySelectorAll(FOCUSABLE_SELECTOR)` returns nothing, Tab is
  `preventDefault`ed and focus is forced back onto the container itself
  rather than escaping.

**Process-wide MLflow fetch guard.** `useSettingsStore.fetchMlflow` guards
re-entrancy with a module-level `let _mlflowFetchingGuard` rather than store
state. The guard is shared across every store instance in the process, so tests
that create fresh instances must retain process-wide concurrency semantics
rather than assume per-instance isolation.

## Error handling

- `ApiError` and `ApiTimeoutError` are the only two error types the API
  layer manufactures; both extend `Error` and set `name` accordingly, so
  `instanceof` checks work standardly. All other thrown values (e.g. raw
  `TypeError` from `fetch()` on a network failure) pass through unwrapped.
- A matching but malformed `parse*` payload throws a plain `Error` with a
  message naming the parser and expected shape. Optional discriminators
  return `null` only before their contract matches: unsupported
  execution-diagnostic schema versions and non-divergence/non-fork Git
  responses are not those payload types. Once the version/status
  discriminator matches, required-field errors propagate and are never
  converted to `null`. There is no dedicated parse-error type; callers
  distinguish contract violations from HTTP failures with
  `instanceof ApiError` (parse errors are never `ApiError`).
- `useNodeResultsStore.updateFrontierAfterSelect` and the frontier-point
  numeric/array coercion helpers (`numericFrontierValue`, `recordValue`,
  `numericArrayValue`, etc.) throw plain `Error`s naming the offending
  field — these are expected to be caught by the calling panel's own error
  handling (typically surfaced as a toast), not by the store.
- `assertValidCacheLimit` throws synchronously if misconfigured — this is a
  programmer error (wrong constant), not a runtime condition, so it is
  expected to fail fast in development/tests rather than be caught.
- `ErrorBoundary.componentDidCatch` logs to `console.error` and never
  rethrows; the boundary's `name` prop (e.g. `"Canvas"`, `"NodePanel"`,
  `"Toast"` — see `App.tsx`'s per-region wrapping) is included in the log
  prefix so a crash's origin is identifiable from the console alone.

## Testing

- `tests/test_frontend_backend_contract.py` verifies frontend/backend node-type and allowed-column-type sets remain identical.
- `tests/test_sanitize_parity_fixture.py` verifies the retained backend compatibility golden and minimum fixture width; it is not a frontend parity twin.

Tests are split between colocated `frontend/src/**/__tests__/` folders next to each source
file and a parallel `frontend/src/__tests__/`
tree that adds gap-coverage and adversarial cases; both trees run under the
same Vitest config.

- **API client** (`frontend/src/api/__tests__/client.test.ts`,
  `frontend/src/api/__tests__/client.retry.test.ts`, `frontend/src/api/__tests__/client.contract.test.ts`): unit tests cover
  retry/backoff/abort semantics directly; the contract suite covers concrete
  endpoint families with shared fixtures from
  `frontend/src/testSupport/uiContractFixtures.ts` plus explicit request/response
  matrices for the remaining trust-boundary endpoints. The structured-input
  inference cases pin complete-default request shape, the 30-minute timeout,
  and explicit sampling passthrough. Generic transport,
  raw-stream, and caller-generic JSON helpers are tested at their transport
  boundary rather than pretending to know a concrete response schema.
- **Assistant split boundary** (`frontend/src/api/__tests__/assistant.test.ts`):
  malformed status/session/history matrices and every SSE variant prove that
  feature-local parsers reject invalid fields before typed return or callback;
  non-OK transport responses remain `ApiError`, parser failures do not.
- **`frontend/src/api/dispersion.ts`**
  (`frontend/src/api/__tests__/dispersion.test.ts`):
  `estimateGlmDispersion` request
  shape (including the `source` default and the 600s timeout matching
  `/train`), `runDispersionEstimate`'s poll-to-completion and
  poll-to-terminal-failure paths, abort mid-poll rejecting with
  `DOMException("AbortError")` after awaiting `cancelDispersion`,
  and a completed-without-value rejection.
- **`frontend/src/types/guards.ts` and `frontend/src/types/trainGuards.ts`**
  (`frontend/src/types/__tests__/guards.contract.test.ts`):
  contract tests exercising the parse functions against both valid and
  malformed payloads, asserting the exact thrown-error shape for the
  malformed cases and accepting the backend's null directory size.
- **`useNodeResultsStore`**:
  `frontend/src/__tests__/stores/useNodeResultsStore.test.ts` covers cache
  identity, including a literal collision under the removed digest, root-only
  ephemeral stripping, JSON normalisation and object-key-order equivalence,
  plus entry bounds, LRU/pinning, job completion, and frontier selection;
  `frontend/src/__tests__/stores/useNodeResultsStore.renderPurity.test.tsx`
  pins render-safe derived getters;
  `frontend/src/__tests__/stores/previewCache.test.ts` covers preview cache
  identity. The colocated `frontend/src/hooks/__tests__/useJobPolling.dedup.test.ts` and
  `frontend/src/hooks/__tests__/useJobPolling.progressThrottle.test.ts` cover the polling flow that writes
  those stores.
  `frontend/src/hooks/__tests__/useStaleConfigEstimate.sourceKey.test.ts` additionally
  drives the store directly (not through a hook) for its second
  describe block, pinning that `completeSolveJob`/`completeTrainJob` stamp
  the in-flight job's `source`/`structuralVersion` onto the cached result.
- **`useSettingsStore`** (`frontend/src/stores/__tests__/useSettingsStore.addSource.test.ts`):
  covers the sanitize-then-dedup `addSource` path (asserting the
  discriminated `{ok, reason, key}` result for the empty and duplicate
  rejection cases, not just that state is unchanged) and the "reset to
  live if active source removed" behaviour.
- **`useToastStore`** (`frontend/src/stores/__tests__/useToastStore.test.ts`,
  `frontend/src/stores/__tests__/useToastStore.dedup.test.ts`): dedup-by-`(type,text)`, cap-at-10, and
  the counter-not-advancing-on-suppression invariant.
- **`useUIStore`** (`frontend/src/stores/__tests__/useUIStore.test.ts`,
  `frontend/src/stores/__tests__/useUIStore.dirty.derived.test.ts`): modal-mutual-exclusion (opening
  utility/imports/git closes the other two) and per-node selection-map
  helpers.
- **Chrome components**: `frontend/src/__tests__/components/ErrorBoundary.test.tsx`
  (root-level, under `frontend/src/__tests__/components/`),
  `frontend/src/components/__tests__/ModalShell.test.tsx` and
  `frontend/src/components/__tests__/ModalShell.focusTrap.test.tsx` (focus trap and
  restore-on-close in particular),
  `frontend/src/components/__tests__/Toast.test.tsx`,
  `frontend/src/components/__tests__/Tooltip.test.tsx`,
  `frontend/src/components/__tests__/ContextMenu.test.tsx`,
  `frontend/src/components/__tests__/NodeSearch.test.tsx`,
  `frontend/src/components/__tests__/Toolbar.test.tsx` (including the package-derived
  browser-version display),
  `frontend/src/components/__tests__/Toolbar.addSource.test.tsx` (the add-source form's rejection UI:
  empty-name and duplicate-name error text, `aria-invalid`/
  `aria-describedby` wiring, the error clearing on next keystroke and on
  successful submission),
  `frontend/src/panels/__tests__/ImportsPanel.test.tsx`,
  `frontend/src/__tests__/components/BreadcrumbBar.test.tsx` (root-level),
  `frontend/src/__tests__/components/KeyboardShortcuts.test.tsx` (root-level),
  `frontend/src/components/__tests__/BackgroundJobPolling.renderIsolation.test.tsx`
  (asserts the component
  itself never re-renders its own subtree — it exists purely to host the
  hook's side effects).
- **Save precondition**: `frontend/src/hooks/__tests__/usePipelineAPI.test.ts` proves the
  save payload carries the loaded `source_revision` as `base_revision` (`null` for a
  never-persisted document) and that a `409` whose detail starts with
  `stale_document_revision` leaves the graph dirty, marks the document unsynchronised, sets
  the sync banner, toasts the conflict, and keeps the request-facing revision ref unchanged,
  while any other `409` still surfaces the generic failure toast.
  `frontend/e2e/save-conflict.spec.ts` runs the two-page conflict against real routes: the
  stale save is rejected, the local edit survives until an explicit reload, and a fresh save
  succeeds afterwards.
- **Generic hooks**: `frontend/src/__tests__/hooks/useClickOutside.test.ts` + `frontend/src/__tests__/hooks/useClickOutside.gaps.test.tsx`,
  `frontend/src/__tests__/hooks/useDragResize.test.ts`, `frontend/src/__tests__/hooks/useJobPolling.test.ts` (root-level, generic
  poller mechanics) plus the colocated dedup/progress-throttle variants,
  `frontend/src/__tests__/hooks/useBackgroundJobs.test.ts` + `frontend/src/__tests__/hooks/useBackgroundJobs.gaps.test.ts` (root-level, orchestration
  wiring), `frontend/src/__tests__/hooks/useWebSocketSync.test.ts` and
  `frontend/src/__tests__/hooks/useWebSocketSync.gaps.test.ts` (root-level — note
  `useWebSocketSync` itself is a graph-canvas hook, but its session-expiry
  interaction with `frontend/src/api/client.ts`'s
  `HAUTE_SESSION_EXPIRED_EVENT` is
  exercised here since that event is this component's contract),
  `frontend/src/hooks/__tests__/useStaleConfigEstimate.sourceKey.test.ts`
  (the `context.source`/
  `context.structuralVersion` half of the staleness key: a source or
  structural-version-only change re-triggers the estimate even with an
  unchanged `configHash`, a cached result missing either field reads as
  stale, and the effect's dependency array re-fires on `context.source`/
  `context.structuralVersion` changes alone).
- **Generic utils**: `frontend/src/utils/__tests__/formatTime.test.ts`,
  `frontend/src/utils/__tests__/formatValue.test.ts`,
  `frontend/src/utils/__tests__/color.test.ts`,
  `frontend/src/utils/__tests__/portableKey.test.ts` (browser-owned key behaviour
  intentionally independent of executable identity), plus root-level
  `frontend/src/__tests__/utils/formatBytes.test.ts` and
  `frontend/src/__tests__/utils/dtypeColors.test.ts`.

Additional leaf coverage:
`frontend/src/components/__tests__/ToggleButtonGroup.test.tsx` covers click and
Arrow/Home/End radio-group selection/focus behaviour;
`frontend/src/components/form/__tests__/CommittedTextField.test.tsx`,
`frontend/src/__tests__/components/form/ConfigCheckbox.test.tsx`, and
`frontend/src/__tests__/components/form/EditorLabel.test.tsx` cover commit boundaries,
no-op blur, external-value draft reset, and form-label/control semantics.
`frontend/src/utils/__tests__/chartHelpers.test.ts` and
`frontend/src/utils/__tests__/formatTrace.test.ts` respectively pin numeric
ticks/formatting and trace substitutions/non-finite display.
`frontend/src/components/NodeTypeIcon.tsx`,
`frontend/src/components/form/index.ts`, and
`frontend/src/utils/mlflowOptimiser.ts` currently have no dedicated test file; the
icon/form files are simple presentation or re-export surfaces, while the optimiser
classifier is an uncovered pure helper.  `frontend/src/index.css` is pinned by
`frontend/src/__tests__/cssColorTokenization.test.ts` (design-token contract: no hex
outside `:root`, no dangling `var()` references, Tailwind-provided-token emission and
shadow guards).

Known gaps: `frontend/src/components/Toolbar.tsx`'s inline timing/memory formatting helpers
(`formatTiming`/`formatMemory`, distinct from and not delegating to
`frontend/src/utils/formatTime.ts`/`frontend/src/utils/formatBytes.ts`) have no dedicated
unit test, only
indirect coverage via `frontend/src/components/__tests__/Toolbar.test.tsx`.
`frontend/src/__tests__/semanticColorTokenization.test.ts` enforces that live components obtain
fixed colour literals from `frontend/src/theme/colors.ts` instead of redeclaring them locally.

## Runtime response contracts

### Execution-strategy diagnostics

The `api/` and `types/guards.ts` boundary defines one execution-strategy type and parser.
Generated declarations and a standalone validator derived from the canonical Pydantic model own
required fields, literals, unions, scalar bounds, safe-integer limits, and collection sizes. The
handwritten parser retains version compatibility, status/strategy relationships, bounded
collection state/count relationships, calibration consistency, canonical ordering, stable error
presentation, and projection to the UI shape. Version 1 requires integer `schema_version=1`, `status`, `strategy`, `profile`, `boundedness`
(`bounded|unbounded|unknown`), `reason_code`, `detail_state`
(`available|unavailable|truncated`), and `boundaries`, `reasons`, and `provenance`. It accepts
optional blocking/remediation, cost, metric, and provenance item detail. Human messages and
remediation are capped at 512 characters, and strategy diagnostics must never carry plans,
frames, or user data.

Each bounded collection is parsed as exactly
`{state: available|unavailable|truncated, total_count: number|null, items: T[]}`. `available`
requires a non-negative integer `total_count === items.length`; `truncated` requires an integer
`total_count > items.length`; and `unavailable` requires `total_count === null` and an empty
array. Boundary/reason arrays may contain at most 32 items and provenance at most 128. The
top-level `detail_state` must equal the worst wrapper state under
`truncated` > `unavailable` > `available`.

The parser validates, but never repairs, those invariants. An over-cap array, inconsistent
state/count/items combination, inconsistent top-level `detail_state`, or non-canonical item
ordering throws a contract error; the browser must not perform its own truncation or present the
malformed prefix as server-authoritative detail. Boundary ordering is
`(topological_rank, node_id, operator, boundary_kind)`, where ranks come from the server's
canonical topological sort. Reason ordering is
`(topological_rank or max, node_id or '', reason_code, operator or '')`; provenance ordering is
`(column, origin_kind, source_node_id or '', source_column or '')`. Comparators use ascending
Unicode code-point order for those primary tuples. The parser accepts any relative order within
an equal-primary group and preserves identical duplicates; it does not reproduce or validate the
producer's Python-only canonical-JSON tie-break.

The parser enforces the authoritative mapping: `projected` and `schema-all-except` map to
`projected`; `full-width-admitted-eager` to `admitted_eager`;
`unprojected-streaming-boundary` and `materialisation-boundary` to `boundary`;
`full-width-conservative` to `warned`; `unsupported` to `rejected`; and `not-planned` to
`not_planned`. The shared UI states are therefore `projected`, `boundary`, `admitted_eager`,
`warned`, `rejected`, and `not_planned`, plus a distinct diagnostic-unavailable render state.
`warned` means the run completed under its full reserved memory envelope because the
group-by estimate was unavailable; it is rendered as a warning, never an error, and a
terminal memory-limit failure or a memory-pressure event on the same run takes precedence
over the warned strategy in every consumer.

Consumers ignore unknown additive fields only within version 1. Missing or malformed required
fields and unknown version-1 enum values throw; unsupported higher versions produce diagnostic
unavailable. Neither path is preserved as an unknown success status. Guard tests pin every mapping,
all schema-version paths, all wrapper/detail states, Unicode primary-tuple ordering, every
equal-primary permutation and duplicate retention, the 32/128 cap boundaries, over-cap rejection
without client truncation, additive
version-1 fields, and all other invalid payload paths. Feature panels import this shared type and
guard rather than creating local readers. Typed HTTP 422 contract errors retain their stable code
and named fields for accessible display, including `trace_correlation_unsupported` with
`node_id`, ordered `key_columns`/`dtypes` arrays capped at 16, and `reason_code`. The group-by
error additionally retains `remediation` and nullable `estimated_peak_bytes`/
`headroom_bytes`.

### Data I/O responses

`frontend/src/api/types.ts`, `client.ts`, and `types/guards.ts` own the
versioned capability, input-cache job/status, and output-write models.
Removed `fetchIoFormats` and legacy Databricks cache/sink clients have no
compatibility wrappers. Settings/cache stores key remote work by safe
identity digest and job id, not table spelling. Guard tests cover every union
leg, order retention, unknown versions, readiness/freshness separation,
error/redaction fields, and malformed-payload rejection.

## Modelling config panes

The observable behaviour is defined by
[frontend-modelling-optimiser-ui](../frontend-modelling-optimiser-ui/high-level.md#modelling-config-panes).
The following remain shared-infrastructure-owned:

- `frontend/src/stores/useUIStore.ts` owns a `ModellingPane` five-value union plus
  `modellingPanes: Record<nodeId, pane>` and its immutable setter, following the existing Explore
  selection-memory pattern. It is browser UI state only and is not serialized into node config.
- `frontend/src/api/types.ts`, `frontend/src/types/trainGuards.ts`, and the train-progress type used by
  `frontend/src/stores/useNodeResultsStore.ts` share the backend's optional
  `train_loss_history` and `train_loss_history_truncated` status fields.
  `parseTrainStatusResponse` retains those fields and parses every present row through the same
  finite-number/required-iteration contract as completed `loss_history`; malformed present data
  throws, omitted history stays omitted, and no latest-loss reconstruction is invented.
- `frontend/src/stores/useNodeResultsStore.ts` keeps each active job's latest status/history
  snapshot unchanged and a bounded pair of distinct increasing iteration/elapsed samples for the
  browser-derived remaining-time estimate. A sample is usable only when iteration and elapsed time
  both increase and total iterations remain greater than the current iteration. Fewer than two
  samples, duplicate/stalled or non-monotonic progress, invalid/non-positive rates, terminal
  updates, and a new `startTrainJob` yield no estimate. Loss rows are never appended client-side;
  backend truncation remains authoritative and bounded.

`frontend/src/stores/useSettingsStore.ts` does not gain or remove a schema field: its section map is
generic. The modelling feature/MLflow/monotonic keys merely lose their consumers, and any
process-local entries become inert.

`frontend/src/stores/__tests__/useUIStore.test.ts` proves independent node memory.
`frontend/src/types/__tests__/guards.contract.test.ts` and
`frontend/src/api/__tests__/client.contract.test.ts` prove history/truncation retention, omission,
and strict malformed-row rejection.
`frontend/src/__tests__/stores/useNodeResultsStore.test.ts` proves latest-snapshot (not append)
semantics, every estimate show/hide case, terminal clearing, and reset on a replacement job for the
same node. Consumer ownership is recorded in `ownership.toml`.

## Recovery document ingestion

`frontend/src/types/pipelineDocument.ts` defines and validates schema version 1 of
`haute.pipeline_editor_document`. Exact-key validation applies recursively to document,
capabilities, diagnostics, spans, nodes, edges, unresolved connections, submodels, and ports;
duplicate recovery ids, non-finite coordinates, invalid spans, and edges with missing visual
endpoints throw at ingestion. When adapting recovery graphs, every submodel node's id and label must
strictly equal its alias (`${PARSER}: submodel node <recovery_id> id must equal its alias` and `${PARSER}: submodel node <recovery_id> label must equal its alias`). Every node requires `function_name`, nullable
`default_input_name`, `source_handle_input_names`, and nullable `config_reference`; every edge
requires nullable `input_name` (ready executable edges are non-null); every submodel port
carries exactly `name`; capabilities require the sorted reserved API-frame
labels. `adaptPipelineEditorDocument` clones configs and maps these values to transient
`_functionName`, `_defaultInputName`, `_sourceHandleInputNames`, `_configReference`,
and `_inputName` metadata alongside recovery presentation state.

`editorIdentities.ts` sends bounded prospective nodes to
`POST /api/pipeline/editor-identities`, requires response cardinality and order to exactly match
the request, sends `out__<name>` handles for occurrences and bare port names for drilled Input
boundaries, requires each returned source-handle map to cover exactly the requested handles, and enforces
ordinary-versus-multi-output default-identity nullability before attaching node and edge identities
immutably. Missing, reordered, malformed, semantically mismatched, or rejected identities throw
before callers commit graph or history state. No frontend
production module derives Python executable names or config references.

Raw canonical graphs entering an editable surface are resolved recursively and
atomically: the root is one identity scope, and each embedded submodel definition
is a separate scope. A definition scope adds a synthetic Input boundary whose
handles are its declared input-port names (the handle is the executable name, so
no identity map is kept), while child-node and child-edge
identities are attached normally. No partially resolved root or registry is
published. Conversely, every canonical graph request uses the shared recursive
projection that removes these editor-only fields before schema validation.

`frontend/src/stores/useDocumentStatusStore.ts` performs one atomic status transition and clones
all externally supplied arrays/objects. Its `capabilities` value is the shared UI admission fence;
components must not recreate status-to-capability policy locally. `documentReadOnlyReason()` is
the shared user-facing explanation for a blocked mutation/save: a live document-transport failure
names itself, a mutable-but-unsynchronised canvas names the pending on-disk change, and only a
genuinely non-mutable document blames unresolved load diagnostics. `systemFailure` is independent of
authored recovery diagnostics: setting it also marks the graph unsynchronised, while any newly
validated document atomically clears it. Reset leaves the editor without authority until a new
validated document arrives.
`usePipelineAPI.handleSave` sends `sourceRevisionRef.current` as `base_revision`; the ref holds
an empty string for a document that has never been persisted, which is sent as `null`. On a
`409` whose `detail` string begins with
`stale_document_revision:` it calls `setGraphSynchronized(false)`, sets the sync banner, toasts
the conflict, leaves the saved baseline untouched, and returns `false`.

`useWebSocketSync` validates version-1 editor-document frames with that same parser. It calls the
document-status transition before considering graph dirtiness; validated document nodes always
carry finite display positions, so external sync never generates layout and applies synchronously.
A dirty graph may reject snapshot replacement but cannot reject the status/capability fence. Any
current-source `parse_error` frame sets the sanitized system-failure state, clears the applied
document fingerprint, and prevents the retained graph from being treated as current; authored
recovery states are never delivered through that frame.
Recovery preview uses `api/client.ts`'s source/revision/target request and never serializes React Flow
recovery objects.

## Minimal repair transport

`api/client.ts` exposes remove-only dry-run and apply calls. Both send the root
document source, current raw revision, target source/recovery identity, and
explicit `delete_config`; apply adds the exact dry-run plan hash. Runtime
parsers reject unknown keys, non-remove discriminators, malformed hashes,
unbounded/invalid patch entries, and a malformed nested editor document.

The public dry-run plan contains bounded display diffs and artifact metadata,
not the bytes that apply will write. The server recomputes those bytes. A
config-retention toggle creates a new request and invalidates the previous plan
hash. API errors preserve structured repair detail for the confirmation UI.
No shared type defines a migration registry or upgrade request.
