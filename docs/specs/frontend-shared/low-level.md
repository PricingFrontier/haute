# Frontend Shared Infrastructure — Low-Level Specification

## Module map

| File | Responsibility |
|---|---|
| `frontend/src/main.tsx` | App bootstrap: mounts `App` inside `StrictMode` + a root `ErrorBoundary`. |
| `frontend/src/api/client.ts` | Typed `fetch()` wrapper: retry/backoff, timeout, abort handling, session-expiry event, and one function per backend endpoint. Exports `request`/`post` so split-chunk endpoint modules can reuse the same fetch machinery, and an authenticated raw-stream helper (auth headers + `ApiError` mapping, no JSON parse) for split modules with non-JSON transports — the assistant SSE stream (see [frontend-assistant-ui](../frontend-assistant-ui/low-level.md)). `runFrontier` starts a frontier sweep then polls `getFrontierStatus` to a terminal state, preserving the old resolve-with-final-payload contract over what is now a backend background job. |
| `frontend/src/api/dispersion.ts` | GLM dispersion-estimation endpoints (NB `theta` / Tweedie `var_power`): `estimateGlmDispersion`, `getDispersionStatus`, `cancelDispersion`, and `runDispersionEstimate` (starts + polls to completion, resolving with the estimated number). Split out of `client.ts` so its code — reachable only from the lazy-loaded modelling config panel — stays out of the initial JS bundle; built on `client.ts`'s exported `request`/`post` and owns its own runtime parsers (`parseDispersionEstimateResponse`, `parseDispersionStatusResponse`) rather than routing through `types/guards.ts`. |
| `frontend/src/api/types.ts` | Request/response TypeScript interfaces mirrored from `src/haute/schemas.py`; re-exports canonical node/trace types. |
| `frontend/src/types/node.ts` | `HauteNodeData`/`PipelineFlowNode`/`SubmodelNodeData` shapes, `ColumnInfo`, `BackendNodeStatus`/`NodeStatus`, the `nodeData()`/`effectiveNodeType()` accessors used everywhere a React Flow `Node.data` needs typed access. |
| `frontend/src/types/trace.ts` | Trace playback shapes (`TraceStep`, `TraceResult`, per-node-type `TraceNodeDetail` variants) mirroring backend trace output. |
| `frontend/src/types/banding.ts` | Banding-factor rule shapes shared between the banding node editor and its trace rendering. |
| `frontend/src/types/guards.ts` | Runtime parsers (`parse*`) and type guards for every API response shape; the JSON/DOM trust boundary. |
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
| `frontend/src/components/Toolbar.tsx` | App top chrome: source selector, row-limit/chunk-size inputs, undo/redo, timing/memory breakdowns, utility/imports buttons, zoom, centre/layout, save split-button. Composes `BreakdownDropdown` and `BranchIndicator` (git-ui). |
| `frontend/src/panels/ImportsPanel.tsx` | Active pipeline-imports right panel: `PanelShell` plus `CodeEditor`, explanatory always-included imports, and callback-only preamble mutation/close handling. `App.tsx` supplies the graph-store-backed preamble and selects it through `importsOpen`. |
| `frontend/src/components/SettingsModal.tsx` | Legacy pipeline-imports/preamble editor dialog (custom overlay, not `ModalShell`). It has component tests but no production import or render site; `ImportsPanel` is the active UI. |
| `frontend/src/components/BackgroundJobPolling.tsx` | Zero-render mount point (`memo`) that only invokes `useBackgroundJobs()`. |
| `frontend/src/components/NodeSearch.tsx` | Ctrl+K command palette: filters/windows the current React Flow node list, arrow-key navigation, jumps the canvas viewport to the selected node. |
| `frontend/src/components/BreadcrumbBar.tsx` | Pipeline → submodel navigation trail; renders nothing at stack depth ≤ 1. |
| `frontend/src/hooks/useClickOutside.ts` | Attaches/detaches a `mousedown` listener that fires `onClose` when the click lands outside `ref`, only while `active`. |
| `frontend/src/hooks/useDragResize.ts` | Bottom-panel drag-to-resize: DOM-direct mutation while dragging, commits to React state on mouseup. |
| `frontend/src/hooks/useJobPolling.ts` | Generic background-job poller: exponential backoff, 24h max lifetime, per-job state via refs, consecutive-failure toast. |
| `frontend/src/hooks/useBackgroundJobs.ts` | Wires `useJobPolling` to the optimiser/train/explore endpoints and `useNodeResultsStore` actions; mounted once in `App.tsx`. |
| `frontend/src/hooks/useMlflowBrowser.ts` | Lazy-loads MLflow experiments/runs/models/versions for dropdown UIs; shared by `ModelScoreEditor` and `OptimiserApplyEditor` (node-editors). |
| `frontend/src/hooks/useSchemaFetch.ts` | Fetch-schema-on-mount-and-on-path-change pattern shared by `DataSourceEditor`/`ApiInputEditor` (node-editors). |
| `frontend/src/hooks/useStaleConfigEstimate.ts` | Generic "estimate endpoint keyed by config hash + source + structural version, refetch when any of the three changes" pattern, built on `hashConfig`. Takes a required `context: {source, structuralVersion}` argument alongside the cached result. |
| File | Responsibility |
| --- | --- |
| `frontend/src/index.css` | Global Tailwind import and dark-theme CSS-variable contract: root sizing/type, native-control and scrollbar defaults, React Flow interaction overrides, and semantic surface/status/chart/git-node tokens consumed by the theme module and components. |
| `frontend/src/utils/chartHelpers.ts` | Small pure chart leaf helpers: compact K/M/scientific axis labels and inclusive evenly spaced Y ticks (a degenerate range yields one tick). |
| `frontend/src/utils/formatTrace.ts` | Cross-surface trace-value/expression/calculation/schema-summary presentation formatting: retains date-shaped strings, represents non-finite numbers explicitly, quotes ordinary strings, escapes column names before substitution, and uses longest names first to avoid partial replacement. |
| `frontend/src/utils/mlflowOptimiser.ts` | Pure MLflow run/model metadata classifier: explicit `params.mode` wins; legacy convergence metrics infer ratebook versus online; insufficient evidence yields the empty mode rather than guessing. |
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
- **`FrontierStatusResponse`** (`api/types.ts`): `{status: JobStatus,
  progress, message, elapsed_seconds, result: FrontierResponse | null,
  terminal_reason?, error_code?, http_status_code?, error_detail?,
  execution_metrics?}` — the poll target for `runFrontier`'s background
  job. `FrontierResponse` itself gained an optional `job_id` for the
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

**Response parsing**: every exported client function pipes its raw
`request<unknown>()` result through the matching `parse*` guard from
`types/guards.ts` before returning — `previewNode`, `loadPipeline`, etc.
never hand an unvalidated object to a caller. `loadPipeline` is the one
function with a `.catch` that inspects `err.status`: a 404 becomes an empty
`PipelineGraph` (first-run UX); every other error rethrows. `api/dispersion.ts`
is the one endpoint module that parses its own responses (`parseDispersion*`)
rather than adding to `types/guards.ts`, for the bundle-size reason it's
split out in the first place.

**Client-embedded job polling** (`runFrontier`, `runDispersionEstimate`):
a third polling shape alongside `useJobPolling` and `useNodeResultsStore`'s
result caches — the poll loop lives directly inside the `async` client
function rather than in a hook or store action. The initial POST either
returns the finished payload inline (small/fast sweeps) or a
`{status: "started", job_id}` handle; on the latter, the function loops
`await getFrontierStatus(jobId)` / `getDispersionStatus(jobId)` on a fixed
interval (`FRONTIER_POLL_INTERVAL_MS` / `pollIntervalMs` option, both
500ms by default) until the status is terminal, resolving or rejecting the
single outer promise. Because there is no store entry for this job, a
caller that unmounts mid-poll relies entirely on its own `AbortSignal` to
stop the loop — there is no background-job registry to fall back on the
way `useBackgroundJobs` provides for solve/train/explore.

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
at `BASE_INTERVAL_MS` (500ms), doubling up to `MAX_INTERVAL_MS` (5s) on
success/no-change, capped by `POLL_TIMEOUT_MS` (30s) per request and
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

## Edge cases and invariants

- **Retry budget is `maxRetries + 1` attempts total**, not `maxRetries`
  retries after the first try counted separately from it — the loop is
  `attempt <= maxRetries`.
- **External abort takes priority over timeout**: `attemptFetch` tracks
  which source (`"timeout"` vs `"external"`) fired first via
  `abortSource ??= source`; only a timeout-sourced abort becomes
  `ApiTimeoutError`, so a caller-cancelled request never gets misreported
  as a timeout even if both fire near-simultaneously.
- **`hauteSessionToken()`** prefers `window.__HAUTE_SESSION_TOKEN__` over
  the Vite env var — the former can be injected post-load (e.g. by a test
  harness or embedding shell) without a rebuild.
- **Column cache freshness** (`useNodeResultsStore.getColumns`) is
  `structuralVersion === useGraphStore.getState().structuralVersion` — a
  direct cross-store read at call time, not a subscription, so a stale
  read only happens if the caller doesn't re-invoke `getColumns` after a
  structural change.
- **`hashConfig`** strips `_nodeId`/`_columns`/`_schemaWarnings`/
  `_availableColumns` (transient runtime fields) before hashing, and
  recursively sorts object keys so key-order differences in the same
  logical config don't produce different hashes. It's a djb2 hash, not
  cryptographic — collisions are a theoretical staleness false-negative,
  accepted for this use case.
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
- **`useStaleConfigEstimate`'s staleness check fails toward "stale", never
  toward "current", on an incomplete cached-result shape.** A
  `cachedResult` missing `source`/`structuralVersion` (a pre-contract
  shape) compares as `undefined !== context.source`, which is always
  true, so the estimate is always treated as stale rather than
  risking a false "still current" read against an old cache entry.
- **Toast dedup** compares only `(type, text)`; it does not advance the
  toast id counter on a suppressed duplicate, so the counter's absence of
  increment is itself the observable "nothing was added" signal used by
  tests.
- **`NodeSearch`** windows rendering to `NODE_SEARCH_VISIBLE_ROWS +
  2 × NODE_SEARCH_OVERSCAN_ROWS` rows regardless of result-set size, but
  keeps the currently-active result in the accessibility tree (visually
  hidden, off-screen-clipped) even when scrolled out of the rendered
  window, so `aria-activedescendant` always resolves to a real DOM node.
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
- Every `parse*` function in `types/guards.ts` throws a plain `Error` with
  a message of the form `"<parser>: expected <shape>, got <actual>"` —
  there is no dedicated parse-error type; callers that need to distinguish
  "backend contract violation" from "network/HTTP error" do so by checking
  `instanceof ApiError` first (parse errors are never `ApiError`).
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

Tests are split between colocated `__tests__/` folders next to each source
file and a parallel `frontend/src/__tests__/{hooks,components,stores,utils}`
tree that adds gap-coverage and adversarial cases; both trees run under the
same Vitest config.

- **API client** (`api/__tests__/client.test.ts`,
  `client.retry.test.ts`, `client.contract.test.ts`): unit tests cover
  retry/backoff/abort semantics directly; `client.contract.test.ts` loads
  shared fixtures from `testSupport/uiContractFixtures.ts` and asserts every
  exported client function's request/response shape against them, so a
  backend schema change that isn't mirrored in `api/types.ts` fails here
  first. `client.test.ts`'s "runFrontier background polling" block covers
  the started→poll→completed happy path, continued polling while
  `"running"`, an `ApiError` on a terminal non-completed status (message +
  `http_status_code` preserved), a completed-without-result rejection, and
  the inline-answer (no `job_id`) fast path resolving from a single fetch.
- **`api/dispersion.ts`** (`api/__tests__/dispersion.test.ts`): the same
  shape of coverage as `runFrontier` — `estimateGlmDispersion` request
  shape (including the `source` default and the 600s timeout matching
  `/train`), `runDispersionEstimate`'s poll-to-completion and
  poll-to-terminal-failure paths, abort mid-poll rejecting with
  `DOMException("AbortError")` and firing a best-effort `cancelDispersion`,
  and a completed-without-value rejection.
- **`types/guards.ts`** (`types/__tests__/guards.contract.test.ts`):
  contract tests exercising the parse functions against both valid and
  malformed payloads, asserting the exact thrown-error shape for the
  malformed cases.
- **`useNodeResultsStore`**: covered indirectly through consumer tests
  (`hooks/__tests__/useJobPolling.dedup.test.ts`,
  `useJobPolling.progressThrottle.test.ts`) rather than a standalone store
  test file — the store's eviction/derived-cache logic is exercised via the
  polling flow that drives it in practice.
  `hooks/__tests__/useStaleConfigEstimate.sourceKey.test.ts` additionally
  drives the store directly (not through a hook) for its second
  describe block, pinning that `completeSolveJob`/`completeTrainJob` stamp
  the in-flight job's `source`/`structuralVersion` onto the cached result.
- **`useSettingsStore`** (`stores/__tests__/useSettingsStore.addSource.test.ts`):
  covers the sanitize-then-dedup `addSource` path (asserting the
  discriminated `{ok, reason, key}` result for the empty and duplicate
  rejection cases, not just that state is unchanged) and the "reset to
  live if active source removed" behaviour.
- **`useToastStore`** (`stores/__tests__/useToastStore.test.ts`,
  `useToastStore.dedup.test.ts`): dedup-by-`(type,text)`, cap-at-10, and
  the counter-not-advancing-on-suppression invariant.
- **`useUIStore`** (`stores/__tests__/useUIStore.test.ts`,
  `useUIStore.dirty.derived.test.ts`): modal-mutual-exclusion (opening
  utility/imports/git closes the other two) and per-node selection-map
  helpers.
- **Chrome components**: `components/__tests__/ErrorBoundary.test.tsx`
  (root-level, under `__tests__/components/`), `ModalShell.test.tsx` +
  `ModalShell.focusTrap.test.tsx` (focus trap and restore-on-close in
  particular), `Toast.test.tsx`, `Tooltip.test.tsx`, `ContextMenu.test.tsx`,
  `NodeSearch.test.tsx`, `Toolbar.test.tsx`,
  `Toolbar.addSource.test.tsx` (the add-source form's rejection UI:
  empty-name and duplicate-name error text, `aria-invalid`/
  `aria-describedby` wiring, the error clearing on next keystroke and on
  successful submission),
  `SettingsModal.gaps.test.tsx` (the standalone legacy dialog), `ImportsPanel.test.tsx`
  (the active imports surface), `BreadcrumbBar.test.tsx` (root-level),
  `KeyboardShortcuts.test.tsx` (root-level),
  `BackgroundJobPolling.renderIsolation.test.tsx` (asserts the component
  itself never re-renders its own subtree — it exists purely to host the
  hook's side effects).
- **Generic hooks**: `useClickOutside.test.ts` + `.gaps.test.tsx`,
  `useDragResize.test.ts`, `useJobPolling.test.ts` (root-level, generic
  poller mechanics) plus the colocated dedup/progress-throttle variants,
  `useBackgroundJobs.test.ts` + `.gaps.test.ts` (root-level, orchestration
  wiring), `useWebSocketSync.test.ts` + `.gaps.test.ts` (root-level — note
  `useWebSocketSync` itself is a graph-canvas hook, but its session-expiry
  interaction with `api/client.ts`'s `HAUTE_SESSION_EXPIRED_EVENT` is
  exercised here since that event is this component's contract),
  `useStaleConfigEstimate.sourceKey.test.ts` (the `context.source`/
  `context.structuralVersion` half of the staleness key: a source or
  structural-version-only change re-triggers the estimate even with an
  unchanged `configHash`, a cached result missing either field reads as
  stale, and the effect's dependency array re-fires on `context.source`/
  `context.structuralVersion` changes alone).
- **Generic utils**: `utils/__tests__/formatTime.test.ts`,
  `formatValue.test.ts`, `color.test.ts`, `sanitizeName.test.ts` +
  `sanitizeParity.diff.test.ts` (the latter checks the frontend sanitizer
  stays byte-for-byte in parity with the backend's `_sanitize_func_name`
  via a shared fixture, `sanitizeParity.fixture.json`), plus root-level
  `__tests__/utils/formatBytes.test.ts` and `dtypeColors.test.ts`.

Additional leaf coverage: `components/__tests__/ToggleButtonGroup.test.tsx` covers click and Arrow/Home/End radio-group selection/focus behaviour; `components/form/__tests__/CommittedTextField.test.tsx`, `__tests__/components/form/ConfigCheckbox.test.tsx`, and `__tests__/components/form/EditorLabel.test.tsx` cover commit boundaries, no-op blur, external-value draft reset, and form-label/control semantics. `utils/__tests__/chartHelpers.test.ts` and `formatTrace.test.ts` respectively pin numeric ticks/formatting and trace substitutions/non-finite display. `NodeTypeIcon.tsx`, `components/form/index.ts`, `index.css`, and `mlflowOptimiser.ts` currently have no dedicated test file; the icon/form/CSS files are simple presentation or re-export surfaces, while the optimiser classifier is an uncovered pure helper.

Known gaps: `Toolbar.tsx`'s inline timing/memory formatting helpers
(`formatTiming`/`formatMemory`, distinct from and not delegating to
`utils/formatTime.ts`/`formatBytes.ts`) have no dedicated unit test, only
indirect coverage via `Toolbar.test.tsx`; `theme/colors.ts` has no test (it
is a constants file with no logic to verify beyond TypeScript's own
type-checking).

## Polars backend contracts (0.6.0)

See [the remediation plan](../../trip/plans/F_0.6.0_polars-backend-remediation.plan.md).
The `api/` and `types/guards.ts` boundary will define one execution-strategy type and parser.
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
ordering makes the full diagnostic unavailable; the browser must not perform its own truncation
or present the malformed prefix as server-authoritative detail. Boundary ordering is
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
fields, unknown version-1 enum values, and unsupported higher versions produce diagnostic
unavailable; they are not preserved as an unknown success status. Guard tests pin every mapping,
all schema-version paths, all wrapper/detail states, Unicode primary-tuple ordering, every
equal-primary permutation and duplicate retention, the 32/128 cap boundaries, over-cap rejection
without client truncation, additive
version-1 fields, and all other invalid payload paths. Feature panels import this shared type and
guard rather than creating local readers. Typed HTTP 422 contract errors retain their stable code
and named fields for accessible display, including `trace_correlation_unsupported` with
`node_id`, ordered `key_columns`/`dtypes` arrays capped at 16, and `reason_code`. The group-by
error additionally retains `remediation` and nullable `estimated_peak_bytes`/
`headroom_bytes`.
