# Frontend Shared Infrastructure — Low-Level Specification

## Module map

| File | Responsibility |
|---|---|
| `frontend/src/main.tsx` | Local-session bootstrap: establishes the browser-managed HttpOnly cookie before mounting `App` inside `StrictMode` + a root `ErrorBoundary`; renders an actionable reload state if the local backend is unavailable. |
| `frontend/src/api/client.ts` | Typed `fetch()` wrapper: same-origin cookie credentials, single-flight `bootstrapHauteSession`, retry/backoff, timeout, abort handling, session-expiry event, and one function per backend endpoint. Exports `request`/`post` so split-chunk endpoint modules can reuse the same fetch machinery, and a raw-stream helper (cookie credentials + `ApiError` mapping, no JSON parse) for split modules with non-JSON transports — the assistant SSE stream (see [frontend-assistant-ui](../frontend-assistant-ui/low-level.md)). |
| `frontend/src/api/dispersion.ts` | GLM dispersion-estimation endpoints (NB `theta` / Tweedie `var_power`): `estimateGlmDispersion`, `getDispersionStatus`, `cancelDispersion`, and `runDispersionEstimate` (starts + polls to completion, resolving with the estimated number). Split out of `client.ts` so its code — reachable only from the lazy-loaded modelling config panel — stays out of the initial JS bundle; built on `client.ts`'s exported `request`/`post` and owns its own runtime parsers (`parseDispersionEstimateResponse`, `parseDispersionStatusResponse`) rather than routing through `types/guards.ts`. |
| `frontend/src/api/types.ts` | Request/response TypeScript interfaces mirrored from `src/haute/schemas.py`; re-exports canonical node/trace types and owns the runtime `JOB_STATUS_VALUES`, `FAILED_JOB_STATUSES`, and `TERMINAL_JOB_STATUSES` shared by guards and pollers. |
| `frontend/src/types/node.ts` | Canonical persisted `PIPELINE_NODE_TYPES` vocabulary and `NodeTypeValue`; `HauteNodeData`/`PipelineFlowNode`/`SubmodelNodeData` shapes, `ColumnInfo`, `BackendNodeStatus`/`NodeStatus`, and the `nodeData()`/`effectiveNodeType()` accessors used everywhere a React Flow `Node.data` needs typed access. |
| `frontend/src/types/trace.ts` | Trace playback shapes (`TraceStep`, `TraceResult`, per-node-type `TraceNodeDetail` variants) mirroring backend trace output. |
| `frontend/src/types/banding.ts` | Banding-factor rule shapes shared between the banding node editor and its trace rendering. |
| `frontend/src/types/guards.ts` | Runtime parsers (`parse*`) and type guards for concrete JSON API response shapes; the JSON/DOM trust boundary. Generic transport helpers, the caller-generic `readJson<T>`, and split-module local parsers are explicit exceptions. |
| `frontend/src/stores/useNodeResultsStore.ts` | Zustand store: preview/solve/train/explore result caches, column cache, derived-getter memoization, LRU eviction. |
| `frontend/src/stores/useSettingsStore.ts` | Zustand store: row limit, streaming chunk size, section open/closed state, MLflow status cache, data sources, file-listing cache. |
| `frontend/src/stores/useToastStore.ts` | Zustand store: toast queue with dedup, capped at 10 entries. |
| `frontend/src/stores/useUIStore.ts` | Zustand store: modal/panel open flags (git/utility/imports/assistant, mutually exclusive by construction — each setter clears the others), sync banner, node panel width, per-node UI selection memory, hover highlight, node search open flag. |
| `frontend/src/theme/colors.ts` | CSS-variable-backed colour token constants (`STRUCTURE_COLORS`, `STATUS_COLORS`, `MODEL_COLORS`, `CHART_COLORS`, `SYNTAX_COLORS`) plus the fixed hex `NODE_GROUP_COLORS` palette. |
| `frontend/src/utils/formatBytes.ts` | Byte count → `B`/`KB`/`MB` string. |
| `frontend/src/utils/formatTime.ts` | Unix timestamp → `HH:MM` / coarse relative-time label. |
| `frontend/src/utils/formatValue.ts` | Renders backend's non-finite-float sentinel (`{__haute_type__: "non_finite_float", ...}`) as `NaN`/`Infinity`/`-Infinity`. |
| `frontend/src/utils/color.ts` | Hex → `rgba(...)` string with alpha, for CSS-var-driven accent colours. |
| `frontend/src/utils/dtypeColors.ts` | Dtype string → Tailwind text-colour class for column-type badges. |
| `frontend/src/utils/sanitizeName.ts` | Human label → valid Python identifier; MUST stay in sync with `src/haute/_graph_utils.py::_sanitize_func_name`. |
| `frontend/src/components/ErrorBoundary.tsx` | Class-component error boundary with a "Try again" fallback UI. |
| `frontend/src/components/Toast.tsx` | `ToastMessage` type + `ToastContainer`, rendering `useToastStore`'s queue with per-type icon/colour and auto-dismiss. |
| `frontend/src/components/ModalShell.tsx` | Shared dialog chrome: backdrop, Escape-close, full Tab focus trap, focus restore on unmount. |
| `frontend/src/components/Tooltip.tsx` | Zero-delay CSS-hover tooltip with edge-clamped horizontal position and top/bottom auto-flip. |
| `frontend/src/components/ContextMenu.tsx` | Node right-click menu: rename/duplicate/create-instance/dissolve-submodel/delete, arrow-key roving focus. |
| `frontend/src/components/KeyboardShortcuts.tsx` | `?`-triggered modal listing keyboard shortcuts, built on `ModalShell`. |
| `frontend/src/components/Toolbar.tsx` | App top chrome: package-derived browser version, source selector, row-limit/chunk-size inputs, undo/redo, timing/memory breakdowns, utility/imports buttons, zoom, centre/layout, save split-button. Composes `BreakdownDropdown` and `BranchIndicator` (git-ui). |
| `frontend/src/components/BreakdownDropdown.tsx` | Sorted, accessible timing/memory breakdown disclosure used by the shared toolbar. |
| `frontend/src/panels/ImportsPanel.tsx` | Active pipeline-imports right panel: `PanelShell` plus `CodeEditor`, explanatory always-included imports, and callback-only preamble mutation/close handling. `App.tsx` supplies the graph-store-backed preamble and selects it through `importsOpen`. |
| `frontend/src/components/BackgroundJobPolling.tsx` | Zero-render mount point (`memo`) that only invokes `useBackgroundJobs()`. |
| `frontend/src/components/NodeSearch.tsx` | Ctrl+K command palette: dynamically imported by `App.tsx` only while open, filters/windows the current React Flow node list, supports arrow-key navigation, and jumps the canvas viewport to the selected node. |
| `frontend/src/components/BreadcrumbBar.tsx` | Pipeline → submodel navigation trail; renders nothing at stack depth ≤ 1. |
| `frontend/src/hooks/useClickOutside.ts` | Attaches/detaches a `mousedown` listener that fires `onClose` when the click lands outside `ref`, only while `active`. |
| `frontend/src/hooks/useDragResize.ts` | Bottom-panel drag-to-resize: DOM-direct mutation while dragging, commits to React state on mouseup. |
| `frontend/src/hooks/useJobPolling.ts` | Generic background-job poller: healthy/error interval ramp from 500ms to a 5s steady state, 30s request timeout, 24h max lifetime, per-job state via refs, consecutive-failure toast. |
| `frontend/src/hooks/useBackgroundJobs.ts` | Wires `useJobPolling` to the optimiser/train/explore endpoints and `useNodeResultsStore` actions; mounted once in `App.tsx`. |
| `frontend/src/hooks/useMlflowBrowser.ts` | Lazy-loads MLflow experiments/runs/models/versions for dropdown UIs; shared by `ModelScoreEditor` and `OptimiserApplyEditor` (node-editors). |
| `frontend/src/hooks/useSchemaFetch.ts` | Fetch-schema-on-mount-and-on-path-change pattern used by `frontend/src/panels/editors/ApiInputEditor.tsx` and `frontend/src/panels/editors/DataInputEditor.tsx` (node-editors). |
| `frontend/src/hooks/useStaleConfigEstimate.ts` | Generic "estimate endpoint keyed by config hash + source + structural version, refetch when any of the three changes" pattern, built on `hashConfig`. Takes a required `context: {source, structuralVersion}` argument alongside the cached result. |
| `frontend/src/index.css` | Global Tailwind import and dark-theme CSS-variable contract: root sizing/type, native-control and scrollbar defaults, React Flow interaction overrides, and canonical semantic surface/status/chart/git-node tokens consumed directly by the theme module and components. |
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
  `CachedSolveResult` additionally carries both `result` (current,
  possibly frontier-point-derived) and `originalResult` (the as-solved
  baseline), so switching frontier points never loses the original. A
  direct `complete*Job` call with no active job recorded (no in-flight
  `ActiveSolveJob`/`ActiveTrainJob` to read `source`/`structuralVersion`
  from) falls back to `source: ""` and `structuralVersion: -1` — sentinels
  that can never equal a real value, so the record reads as stale rather
  than silently matching whatever the caller happens to be viewing.
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
  preamble?}` — the minimal shape every pipeline-mutating endpoint accepts;
  distinct from the richer `PipelineGraph` (adds pipeline metadata) that
  `loadPipeline`/`getCommitPipeline` return.

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
action (1) removes the corresponding entry from the `*Jobs` in-flight map,
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

**Background job polling** (`useJobPolling` + `useBackgroundJobs`):
`useBackgroundJobs` mounts three `useJobPolling` instances (solve/train/
explore), each driven by the store's `*Jobs` record. `useJobPolling`
reconciles active jobs against a ref-tracked map of running pollers on every
render; a poller not yet in the map gets a `setTimeout`-driven loop starting
at `BASE_INTERVAL_MS` (500ms). After each non-terminal response or retryable
poll error, the next interval doubles (`500ms → 1s → 2s → 4s → 5s`) and then
holds at `MAX_INTERVAL_MS` (5s) for the rest of that job. Each request is
capped by `POLL_TIMEOUT_MS` (30s) and the poller by
`MAX_LIFETIME_MS` (24h) total. `CONSECUTIVE_FAILURES_FOR_TOAST` consecutive
poll errors trigger a toast (poll errors are tolerated silently up to that
point — the network hiccup case is expected). A 404/410 from the poll
endpoint (`TERMINAL_MISSING_JOB_STATUSES`, checked via
`getMissingJobPollErrorMessage`) is treated as "job is gone, stop polling"
rather than a retryable transient error.

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

> NOTE: `useSettingsStore.fetchMlflow` guards re-entrancy with a *module-level*
> `let _mlflowFetchingGuard` boolean rather than store state. This means the
> guard is shared across every store instance created in the process
> (relevant for tests that create fresh store instances but don't reset this
> module-level flag) — tests that exercise `fetchMlflow` concurrency need to
> account for this shared guard rather than assuming per-instance isolation.

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

- `tests/test_frontend_backend_contract.py` covers frontend/backend contract parity.
- `tests/test_sanitize_parity_fixture.py` covers sanitization parity fixtures.

Tests are split between colocated `frontend/src/**/__tests__/` folders next to each source
file and a parallel `frontend/src/__tests__/`
tree that adds gap-coverage and adversarial cases; both trees run under the
same Vitest config.

- **API client** (`frontend/src/api/__tests__/client.test.ts`,
  `frontend/src/api/__tests__/client.retry.test.ts`, `frontend/src/api/__tests__/client.contract.test.ts`): unit tests cover
  retry/backoff/abort semantics directly; the contract suite covers concrete
  endpoint families with shared fixtures from
  `frontend/src/testSupport/uiContractFixtures.ts` plus explicit request/response
  matrices for the remaining trust-boundary endpoints. Generic transport,
  raw-stream, and caller-generic JSON helpers are tested at their transport
  boundary rather than pretending to know a concrete response schema.
- **`frontend/src/api/dispersion.ts`**
  (`frontend/src/api/__tests__/dispersion.test.ts`):
  `estimateGlmDispersion` request
  shape (including the `source` default and the 600s timeout matching
  `/train`), `runDispersionEstimate`'s poll-to-completion and
  poll-to-terminal-failure paths, abort mid-poll rejecting with
  `DOMException("AbortError")` after awaiting `cancelDispersion`,
  and a completed-without-value rejection.
- **`frontend/src/types/guards.ts`**
  (`frontend/src/types/__tests__/guards.contract.test.ts`):
  contract tests exercising the parse functions against both valid and
  malformed payloads, asserting the exact thrown-error shape for the
  malformed cases.
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
  `frontend/src/utils/__tests__/sanitizeName.test.ts` and
  `frontend/src/utils/__tests__/sanitizeParity.diff.test.ts` (the latter checks the frontend sanitizer
  stays byte-for-byte in parity with the backend's `_sanitize_func_name`
  via `frontend/src/utils/__tests__/sanitizeParity.fixture.json`), plus root-level
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
`frontend/src/components/form/index.ts`, `frontend/src/index.css`, and
`frontend/src/utils/mlflowOptimiser.ts` currently have no dedicated test file; the
icon/form/CSS files are simple presentation or re-export surfaces, while the optimiser
classifier is an uncovered pure helper.

Known gaps: `frontend/src/components/Toolbar.tsx`'s inline timing/memory formatting helpers
(`formatTiming`/`formatMemory`, distinct from and not delegating to
`frontend/src/utils/formatTime.ts`/`frontend/src/utils/formatBytes.ts`) have no dedicated
unit test, only
indirect coverage via `frontend/src/components/__tests__/Toolbar.test.tsx`;
`frontend/src/theme/colors.ts` has no test (it
is a constants file with no logic to verify beyond TypeScript's own
type-checking).

## Runtime response contracts

### Execution-strategy diagnostics

The `api/` and `types/guards.ts` boundary defines one execution-strategy type and parser.
Version 1 requires integer `schema_version=1`, `status`, `strategy`, `profile`, `boundedness`
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
`unprojected-streaming-boundary` and `materialisation-boundary` to `boundary`; `unsupported`
to `rejected`; and `not-planned` to `not_planned`. The shared UI states are therefore
`projected`, `boundary`, `admitted_eager`, `rejected`, and `not_planned`, plus a distinct
diagnostic-unavailable render state.

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
