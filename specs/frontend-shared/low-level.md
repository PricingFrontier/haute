# Frontend Shared Infrastructure — Low-Level Specification

## Module map

| File | Responsibility |
|---|---|
| `frontend/src/api/responseValidation.ts` | `ApiResponseValidationError` distinguishes invalid API responses from retryable transport failures while preserving the parser error as its cause; `validateApiResponse` wraps a parser call in that contract. Every background job poller (train, optimiser, Explore, pivot) reports it as a terminal response error. |
| `frontend/src/utils/editorIdentities.ts` | Builds bounded identity requests, applies exact-order server responses, and attaches authoritative node/edge metadata without mutating the candidate graph. |
| `frontend/src/main.tsx` | Local-session bootstrap: establishes the browser-managed HttpOnly cookie before mounting `App` inside `StrictMode` + a root `ErrorBoundary`; renders an actionable reload state if the local backend is unavailable. |
| `frontend/src/api/client.ts` | Typed `fetch()` wrapper: same-origin cookie credentials, single-flight `bootstrapHauteSession`, retry/backoff, timeout, abort handling, session-expiry event, and one function per backend endpoint. Exports `request`/`post` so split-chunk endpoint modules can reuse the same fetch machinery, and a raw-stream helper (cookie credentials + `ApiError` mapping, no JSON parse) for split modules with non-JSON transports — the assistant SSE stream (see [frontend-assistant-ui](../frontend-assistant-ui/low-level.md)). Modelling train/status/estimate methods dynamically import `types/trainGuards.ts` only after their response arrives so the large training contract stays out of the initial bundle. Likewise, a converted response module group's generated validator module is imported dynamically when its first response arrives and checked through `expectGeneratedContract`, so no generated response validator is in the initial bundle. |
| `frontend/src/api/errors.ts` | Reads structured API error details: `apiErrorCode` returns a detail object's `error_code` so callers dispatch on the code rather than the HTTP status, and `apiErrorMessage` returns the user-facing text (a structured detail's `message`, else the string detail via `executionErrorDetailMessage`, else a non-HTTP error's message, else the caller's fallback) so no surface renders `ApiError: HTTP <status>`. It is the one error-text helper: without a fallback, an HTTP error with no detail gives its own message and a non-error value its string form. |
| `frontend/src/api/dispersion.ts` | GLM dispersion-estimation endpoints (NB `theta` / Tweedie `var_power`): `estimateGlmDispersion`, `getDispersionStatus`, `cancelDispersion`, and `runDispersionEstimate` (starts + polls to completion, resolving with the estimated number). Split out of `client.ts` so its code — reachable only from the lazy-loaded modelling config panel — stays out of the initial JS bundle; built on `client.ts`'s exported `request`/`post`; its two responses are checked by the generated `modelling` group validators (`DispersionEstimateResponse`, `DispersionEstimateStatusResponse`) rather than routing through `types/guards.ts`. |
| `frontend/src/api/types.ts` | Request/response TypeScript interfaces mirrored from backend contracts, including nullable directory sizes and canonical evaluation/tuning reports and previews; its execution-strategy pilot aliases the generated declarations while normalising nullable reason fields for the stable UI shape. A converted response module group's types are re-exported from the generated declarations (under their existing UI names where those differ), never redeclared. It re-exports canonical node/trace types and owns the runtime `JOB_STATUS_VALUES`, `FAILED_JOB_STATUSES`, and `TERMINAL_JOB_STATUSES` shared by guards and pollers. |
| `frontend/src/types/node.ts` | Canonical persisted `PIPELINE_NODE_TYPES` vocabulary and `NodeTypeValue`; `HauteNodeData`/`PipelineFlowNode`/`SubmodelNodeData` shapes, `ColumnInfo`, `BackendNodeStatus`/`NodeStatus`, and the `nodeData()`/`effectiveNodeType()` accessors used everywhere a React Flow `Node.data` needs typed access. |
| `frontend/src/types/trace.ts` | Trace playback shapes (`TraceStep`, `TraceResult`, per-node-type `TraceNodeDetail` variants) mirroring backend trace output. |
| `frontend/src/types/banding.ts` | Banding-factor rule shapes shared between the banding node editor and its trace rendering. |
| `frontend/src/types/guards.ts` | Shared runtime parser primitives plus parsers (`parse*`) and type guards for eagerly used concrete JSON API response shapes; part of the JSON/DOM trust boundary. A converted response module group has no parser here; the Explore pivot responses keep only their UI rules (`explorePivotRunFromContract`, `explorePivotStatusFromContract`, `explorePivotMembersFromContract`): each member key narrowed to the value its kind carries, in its canonical string form, and every cell inside the declared matrix naming a declared value. The optimiser status, frontier status and auto-range status responses keep theirs (`optimiserStatusFromContract`, `frontierStatusFromContract`, `frontierAutoRangeStatusFromContract`): one frontier point summary per point, the typed fields the UI reads from each open frontier point object, and execution metrics parsed by the shared parser. Execution-strategy parsing delegates matching-version structural assertions to its generated standalone validator, then applies explicit compatibility, relationship, ordering, and calibration semantics. Generic transport helpers, the caller-generic `readJson<T>`, and split-module local parsers are explicit exceptions. |
| `frontend/src/types/generatedContractValidation.ts` | Adapter for generated-validator errors: constructs stable instance paths (including missing required properties), formats contract failures, and locates matching keyword/path errors without coupling callers to Ajv internals. `expectGeneratedContract(contract, validate, value)` returns the payload typed by its generated validator or throws `<contract>: invalid contract at <path>: <keyword>`; `api/client.ts` uses it for every converted response. It holds no contract data, so importing it eagerly hoists nothing Explore-only. |
| `frontend/src/generated/api-contracts.schema.json`, `frontend/src/generated/api-contracts.generated.ts`, `frontend/src/generated/api-contracts.constants.generated.ts`, `frontend/src/generated/api-contracts.execution-strategy-diagnostic.validators.mjs`, `frontend/src/generated/api-contracts.execution-strategy-diagnostic.validators.d.mts`, `frontend/src/generated/api-contracts.explore-charts.validators.mjs`, and `frontend/src/generated/api-contracts.explore-charts.validators.d.mts` | Committed contract source, static declarations, lazy Explore constants, and split self-contained validators owned by [engineering-quality](../engineering-quality/low-level.md) and consumed by frontend trust boundaries. The execution validator co-exports its generated schema version and is eager; the Explore chart validator and option constants stay behind its lazy panel chunk. |
| `frontend/src/types/trainGuards.ts` | Dynamically imported runtime parsers for modelling train/status/estimate responses, outside the initial JavaScript graph. `parseTrainResponse` and `parseTrainStatusResponse` take the whole structure, including the evaluation and tuning reports and the fit evidence, from the generated `training` validators, and shape by hand only what the server model leaves open: the diagnostic row lists, loss history, GLM inference and regularisation, EBM terms and the feature-selection report. The evaluation and tuning invariants are checked once on the server, where their artifacts are produced, so the browser does not re-check them. `parseTrainEstimateResponse` takes the estimate's structure from the generated `TrainEstimateResponse` validator and applies only the cross-field rules by hand: the evaluation preview's strategy/validation consistency, and the unavailable reason's figures and blocking node (narrowed to a discriminated union). |
| `frontend/src/types/pipelineRepair.ts` | Exact-key minimal repair apply wire types and parsers. Apply delegates its nested document to `parsePipelineEditorDocument`; no response or request type contains replacement source bytes or migration operations. |
| `frontend/src/stores/useNodeResultsStore.ts` | Zustand store: preview/solve/train/explore/pivot result and job caches (`startTrainJob` records the `trainingLineage` of the submitted training payload that the editor passes in; a fence-current completion remembers the job in the browser handles of `utils/trainedJobHandles` and an error or failure forgets it; `restoreTrainResult` puts back a completed training result restored after a reload, never replacing an existing result or running job), authoritative training history plus bounded ETA samples, column cache, derived-getter memoization, LRU eviction, and the atomic per-pivot start claim (one current claim per Explore node + pivot id holding the owning node id, the requested dataframe cache key, calculation identity, and a unique generation token; taking a claim before submission serialises concurrent consumers, an identical automatic target no-ops, every manual Retry and every newer automatic target atomically replaces the generation, only the current token may promote it to a job or release it — superseded outcomes are discarded — and clearing a node's results drops exactly the claims whose stored node id matches). |
| `frontend/src/stores/useNodeDataStore.ts` | Zustand store of the data every consumer of one point shares, keyed by slot (`producerNodeId|portLabel|source`): the point's kind, the generation its producer's current signature has (id, column set, rows, bytes, retention, freshness), the running build — a node-data job with its progress, or a delegated build with its message, its canceller, and the token that owns it — the profile of the data version it holds and why the last profile failed — attributed to the version that profile was computed for, so a generation published while it ran is still profiled — how the point is built or cleared, and a monotonic node-data epoch. It also records, per consumer node, the slot it was last told it reads together with the identity that answer was given for, so a read for another identity returns nothing. A consumer's own availability is derived from the entry and its column demand, never stored. |
| `frontend/src/hooks/useNodeDataCache.ts` | One consumer node's view of the data it reads: the point, its availability for that consumer's demand, and `run`, `refresh`, `cancel`, and `clear`. |
| `frontend/src/hooks/useNodeDataProfile.ts` | The shared `profile` analysis of the data one consumer reads: asked for once per slot and data version while the point is `current`, fenced against a document that has moved on, stored per slot so every pane showing that data gets it, with `cancel` for the running job, and `error` plus `refresh` so a failed attempt is retried rather than left as an empty pane. Each request names the version it asks about, which the job it starts carries. |
| `frontend/src/utils/operationToken.ts` | `nextOperationToken`: a process-unique token that tells one asynchronous operation apart from the operation that replaced it. |
| `frontend/src/panels/dataPointIdentity.ts` | `buildNodeDataCacheIdentity`: the identity that gates a consumer's `point` request — its upstream subgraph plus the original of every instance in it, each node's data-affecting configuration, every edge with its handles, the submodels, and the preamble. |
| `frontend/src/stores/useSettingsStore.ts` | Zustand store: row limit, the server's streaming chunk size (loaded from and written to `/api/execution-settings`), section open/closed state, the MLflow destinations inventory cache (fetched once with probing, re-fetched by `invalidateMlflow()`), data sources, file-listing cache. The pure destination helpers live in `frontend/src/utils/mlflowDestinations.ts`, and the shared per-node control is the destination selector component described under the MLflow destination surface below. |
| `frontend/src/stores/useToastStore.ts` | Zustand store: toast queue with dedup, capped at 10 entries. |
| `frontend/src/stores/useUIStore.ts` | Zustand store: modal/panel open flags (git/utility/imports/assistant, mutually exclusive by construction — each setter clears the others), sync banner, node panel width, per-node Explore/modelling selection memory (editor pane, preview pane, and the configured chart/pivot Configure-subview ids), hover highlight, node search open flag. |
| `frontend/src/theme/colors.ts` | CSS-variable-backed colour token constants (`STRUCTURE_COLORS`, `STATUS_COLORS`, `MODEL_COLORS`, `CHART_COLORS`, `SYNTAX_COLORS`) plus the fixed `NODE_GROUP_COLORS`, `PIVOT_CHART_COLORS`, and `PIVOT_CONDITIONAL_FORMAT_COLORS` visualisation palettes. |
| `frontend/src/components/MlflowDestinationSelector.tsx` | The per-node MLflow destination control mounted by the modelling Export pane, the optimiser config section and both MLflow-sourced read-node editors: a labelled radio group of the three destinations in fixed order, the selected option following the node's effective destination (the remote it names, else Local folder; there is no automatic choice and no "Use auto" control), a connection light and tooltip per remote from the inventory, greyed unconfigured remotes that open the settings modal instead of being selected, the resolved-destination line, and re-check and settings buttons. It only reads and reports the node value through `value`/`onChange`; its store mutations are limited to the inventory fetch and invalidation. |
| `frontend/src/utils/mlflowDestinations.ts` | Pure MLflow destination helpers shared by every node surface: the ordered destination keys and labels, `effectiveMlflowDestination` (`databricks` or `server` when stored, else `local`), `mlflowDestinationConfigValue` (the stored value for a choice: the remote's key, or `undefined` for Local folder so the key is removed), `mlflowDestinationEntry`, `mlflowLight` (green/amber/grey/pending; local has no light), `mlflowLogAvailability` (loading, package missing, or the node's own key unconfigured make logging unavailable; a failed probe does not), and `defaultExperimentName` (`/Shared/haute/<label>` for Databricks, else the label). No store imports, so panels and editors can derive state from an inventory snapshot. |
| `frontend/src/utils/formatBytes.ts` | The byte formatters: `formatBytes` (byte count → `B`/`KB`/`MB`/`GB` with one decimal) and `formatByteSize` (budget-sized values up to `TB`, whole numbers from ten). |
| `frontend/src/utils/formatTime.ts` | Unix timestamp → `HH:MM` / coarse relative-time label. |
| `frontend/src/utils/objectLiteral.ts` | `isObjectLiteral`: true only for a plain object literal (`{}` or a null-prototype object), the object shape a JSON config holds. |
| `frontend/src/utils/formatValue.ts` | Renders backend's non-finite-float sentinel (`{__haute_type__: "non_finite_float", ...}`) as `NaN`/`Infinity`/`-Infinity`, and owns `formatDuration`, the one seconds formatter (`0.4 s`, `12 s`, `2m 05s`). |
| `frontend/src/utils/color.ts` | Hex → `rgba(...)` string with alpha, for CSS-var-driven accent colours. |
| `frontend/src/utils/dtypeColors.ts` | Dtype string → Tailwind text-colour class for column-type badges. |
| `frontend/src/utils/portableKey.ts` | Browser-owned persistence key; intentionally not Python-compatible or reversible. Executable identity comes only from server metadata. |
| `frontend/src/components/ErrorBoundary.tsx` | Class-component error boundary with a "Try again" fallback UI. |
| `frontend/src/components/Toast.tsx` | `ToastMessage` type + `ToastContainer`, rendering `useToastStore`'s queue with per-type icon/colour and auto-dismiss. |
| `frontend/src/components/ModalShell.tsx` | Shared dialog chrome: backdrop, Escape-close, full Tab focus trap, focus restore on unmount; the panel is centred, or top-aligned with `placement="top"` (the node-search palette). |
| `frontend/src/components/Tooltip.tsx` | Zero-delay hover and focus tooltip. The bubble renders into the document body with fixed positioning, so scrolling or overflow-clipped panels never cut it off; it is placed from the anchor's viewport rectangle, clamped inside the viewport horizontally, flipped between top and bottom when the preferred side would clip, closed on scroll or resize, wraps long unbroken text such as URLs, and describes the control that takes focus: a single element child receives the tooltip in its described-by reference (unless its accessible label already is the tooltip text), a function child receives the id to place on a nested control such as a native radio inside its label, and only text or fragment children leave the reference on the hover wrapper. |
| `frontend/src/components/ContextMenu.tsx` | Node right-click menu: rename/duplicate/create-instance/dissolve-submodel/delete, arrow-key roving focus. |
| `frontend/src/components/KeyboardShortcuts.tsx` | `?`-triggered modal listing keyboard shortcuts, built on `ModalShell`. |
| `frontend/src/components/Toolbar.tsx` | App top chrome: 56px 2-tier stacked column layout with package-derived browser version, source selector, undo/redo with visible text labels, integer-ms timing and memory breakdowns, Submodel/Instance selection actions, utility/imports buttons, assistant and a Help menu (external Documentation link, Hotkeys opening the keyboard-shortcuts modal, and Report a bug linking to a new GitHub issue; focus lands on the first item, arrows move, Escape closes and returns focus to Help), zoom in/out, centre/layout, and Save + Commit nested under `BranchIndicator`. Actions share the `.toolbar-btn` surface; selection actions carry `aria-disabled` rather than `disabled` so unavailable actions stay focusable with informative tooltips. Composes `BreakdownDropdown` and `BranchIndicator` (git-ui). The Source selector (on the shared `.toolbar-btn` surface and type) and a Pipeline button share a two-row grid column, so the Pipeline button is exactly as wide as the selector above it whatever the active source is named; the Pipeline button reads "Calculating" under automatic calculation and "Manual" under manual, and opens `PipelineSettingsModal` from the toolbar's own local state, unlike the MLflow modal's UI-store flag. The preview row limit and streaming chunk size live in that pane, not in the toolbar. |
| `frontend/src/components/MlflowSettingsModal.tsx` | `ModalShell`-based MLflow destinations inventory editor: an MLflow server URL field, a Local folder field showing the resolved folder, a read-only Databricks block (selected profile, the dedicated MLflow host, or the missing configuration), one Test action per remote with its inline categorised result, and Save through `PUT /api/mlflow/settings` (`tracking_uri` and `folder` only) followed by `invalidateMlflow()`. Rendered by the toolbar while the UI store's MLflow-settings-open flag is set; opened from each node's MLflow gear or greyed light. |
| `frontend/src/components/PipelineSettingsModal.tsx` | `ModalShell`-based Pipeline settings pane: a Calculation radio group (Automatic / Manual, session-only UI-store state); a Preview section with the preview row limit (0 = no limit, negatives clamp to 0) and streaming chunk size (clamped to the backend bounds, non-numeric input ignored) fields, both writing `useSettingsStore` and suppressing native spinners. The chunk size is loaded from the server when the pane opens (reopened while a save is in flight, it shows that save's outcome instead) and committed to it on blur or Enter, never per keystroke; saves reach the server in order, and a failed commit restores the server's value and reports the error in a toast; then the Cached data inventory: every node of the open pipeline with its state, size, cached-at time, build duration and per-entry clear control; a group for cached data belonging to no node of it; and a footnote for unattributed bytes. The whole store's size (`CacheStoreSize`) sits beside the Cached data heading. No budget cards, limit variables or generation counts. Reads `POST /api/cache/nodes` on open, explicit Refresh and successful clear. Rendered by the toolbar from its own local open state and opened by the toolbar's Pipeline button. |
| `frontend/src/components/CacheStoreSize.tsx` | The snapshot store's whole size beside Pipeline settings' Cached data heading, read from `GET /api/cache/usage` with each inventory read. |
| `frontend/src/components/PreviewOutOfDateBadge.tsx` | "Out of date" marker in the data-preview header, shown only under manual calculation when the displayed stored preview's structural version or node-data epoch is behind the current one; isolated so its store subscriptions re-render only the badge. |
| `frontend/src/components/BreakdownDropdown.tsx` | Sorted, accessible timing/memory breakdown disclosure used by the shared toolbar. |
| `frontend/src/panels/ImportsPanel.tsx` | Active pipeline-imports right panel: `PanelShell` plus `CodeEditor`, explanatory always-included imports, and callback-only preamble mutation/close handling. `App.tsx` supplies the graph-store-backed preamble and selects it through `importsOpen`. |
| `frontend/src/components/BackgroundJobPolling.tsx` | Zero-render mount point (`memo`) that only invokes `useBackgroundJobs()`. |
| `frontend/src/components/NodeSearch.tsx` | Ctrl+K command palette on a top-aligned `ModalShell` (which owns Escape, backdrop close and the focus trap): dynamically imported by `App.tsx` only while open, filters/windows the current React Flow node list, supports arrow-key navigation, and hands the chosen node to `App.tsx`, which selects it and asks `useActiveNodeReveal` to centre it at zoom 0.8 within the canvas area the inspector leaves (see [frontend-graph-canvas](../frontend-graph-canvas/high-level.md), Active node visibility). |
| `frontend/src/components/BreadcrumbBar.tsx` | Pipeline → submodel navigation trail; renders nothing at stack depth ≤ 1. |
| `frontend/src/hooks/useClickOutside.ts` | Attaches/detaches a `mousedown` listener that fires `onClose` when the click lands outside `ref`, only while `active`. |
| `frontend/src/hooks/useDebouncedCallback.ts` | The one debounce for a scheduled call: `schedule(args, delayMs?)` runs the latest callback with the latest arguments once the delay passes without another schedule (a per-call delay overrides the hook's), `flush()` runs a waiting call now and returns its result, `cancel()` drops it, and `pending()` reads its arguments. On unmount a waiting call is dropped, or run when the owner asks for `onUnmount: "flush"`. The code editor's change commit, the utility panel's autosave and the canvas preview fetch use it; a delayed request inside an effect, whose cleanup clears the timer and aborts the request, stays in that effect. |
| `frontend/src/hooks/useDragResize.ts` | Bottom-panel drag-to-resize: DOM-direct mutation while dragging, commits to React state on mouseup. |
| `frontend/src/hooks/useJobPolling.ts` | Thin React adapter that keeps one `JobPollingController` configured, reconciles the current job record after commit, and disposes it on unmount. |
| `frontend/src/hooks/jobPollingController.ts` | The single state authority for generic background polling: active poller identities, timers, abort controllers, interval ramp, progress throttling, replacement, terminal completion/error, and disposal. Also exports `waitForJob`, the only way to await one job's terminal status inside an operation, and its `JobWaitTimeoutError`. |
| `frontend/src/hooks/useBackgroundJobs.ts` | Wires `useJobPolling` to the optimiser/train/explore/node-data endpoints and the `useNodeResultsStore` and `useNodeDataStore` actions; mounted once in `App.tsx`. |
| `frontend/src/hooks/useMlflowBrowser.ts` | Lazy-loads MLflow experiments/runs/models/versions for dropdown UIs from one destination (`destination` option: the node's stored value, `""` = the local folder, passed to every discovery request); shared by `ModelScoreEditor`, `OptimiserApplyEditor` (node-editors), and the modelling Export pane's experiment suggestions. |
| `frontend/src/hooks/useSchemaFetch.ts` | Fetch-schema-on-mount-and-on-path-change pattern used by `frontend/src/panels/editors/ApiInputEditor.tsx` and `frontend/src/panels/editors/DataInputEditor.tsx` (node-editors). |
| `frontend/src/hooks/useStaleConfigEstimate.ts` | Generic "estimate endpoint keyed by config hash + source + structural version, refetch when any of the three changes" pattern, built on `hashConfig`. Takes a required `context: {source, structuralVersion}` argument alongside the cached result. |
| `frontend/src/index.css` | Global Tailwind import and dark-theme CSS-variable contract: root sizing/type, native-control and scrollbar defaults, React Flow interaction overrides, canonical semantic surface/status/chart/git-node tokens consumed directly by the theme module and components, and typography role tokens (`--font-data`) that alias Tailwind theme tokens rather than redeclaring them (no Tailwind theme token may be redeclared in the file's plain blocks — they are unlayered and would shadow `@layer theme`; a deliberate override belongs in an `@theme` block, which the gate exempts automatically; and components conventionally reference the role token rather than the raw Tailwind name — adoption and emission both pinned by `frontend/src/__tests__/cssColorTokenization.test.ts`). Also owns the `.toolbar-btn` action-button surface (resting/hover/pressed fills, engaged `aria-pressed` toggle, and a flat unavailable state that keeps its label readable) and the `.toolbar-number-input` spinner suppression. |
| `frontend/src/utils/chartHelpers.ts` | The axis and scale helpers every SVG chart uses: `formatChartNumber` (three significant digits, compact from ten thousand, exponential below 0.0001), `chartDomain` (an 8% padded domain that gives constant series a finite scale), `chartTicks` (inclusive evenly spaced ticks; a degenerate range yields one tick), `chartLabelIndices` (label thinning that keeps both ends) and `chartAxisLabel` (truncation to the plot width). |
| `frontend/src/utils/formatTrace.ts` | Cross-surface trace-value/expression/calculation/schema-summary presentation formatting: retains date-shaped strings, represents non-finite numbers explicitly, quotes ordinary strings, escapes column names before substitution, and uses longest names first to avoid partial replacement. |
| `frontend/src/utils/mlflowOptimiser.ts` | Pure MLflow run/model metadata classifier: the canonical `params.mode` value selects ratebook versus online; absent or invalid values yield the empty mode. |
| `frontend/src/utils/mlflowModelMetadata.ts` | Pure MLflow model metadata helpers for Model Score: `resolveLoadedVersion` (the loaded version a stored choice resolves to; `latest` is the newest) and `recordedModelTask` (a run's recorded `task` param when it is `regression` or `classification`, else `null`). |
| `frontend/src/components/NodeTypeIcon.tsx` | Shared node-type icon wrapper: looks up canonical metadata and deliberately renders the Polars icon for an absent or unknown type, so compact lists never crash on incomplete historical data. |
| `frontend/src/components/ToggleButtonGroup.tsx` | Generic controlled segmented single-choice group with radio semantics, roving `tabIndex`, Arrow/Home/End selection and focus movement, optional accessible name, and token-derived active styling. |
| `frontend/src/components/form/CommittedTextField.tsx` | Controlled-looking input/textarea with a local draft: commits once on blur (and Enter for the input), skips no-op commits, and discards a stale draft when the external value changes, preserving one edit/one undo snapshot. `ValidatedTextField` is the validated single-line variant the API Input and Output editors share: an invalid candidate is refused with its error beside the field and the draft kept, an invalid committed value shows its error too, a commit its owner refuses (`{ ok: false }`) keeps the draft, an owner's `commitError` shows while the value itself is valid, and a non-blocking `warning` shows only when there is no error. |
| `frontend/src/components/form/ConfigCheckbox.tsx` | Labelled controlled checkbox using a caller id or React `useId`, disabled semantics, and shared accent/text tokens. |
| `frontend/src/components/form/EditorLabel.tsx` | Consistent micro-label primitive; can be a correctly associated `<label>` or non-form span/div for display-only content. |
| `frontend/src/components/form/index.ts` | Public barrel for the committed text field/area, checkbox, and editor-label primitives; editor callers import the shared contract rather than deep paths. |
| `frontend/src/utils/scopedSaveGuards.ts` | Pure node-scoped-save guards: one-node-at-a-time edit fencing and the stale-response document-identity fence. |
| `frontend/src/hooks/useScopedNodeSave.ts` | App-lifetime scoped-save coordination: per-document edited-node tracking, edit refusal while a save is in flight or another node is dirty, the response fence, and adoption hand-off. |
| `frontend/src/stores/useRecoverySummaryStore.ts` | Transient per-session recovery summary (field outcomes, completeness, previous config, diffs) keyed by recovery id. |

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
  possibly a frontier point's server summary applied) and `originalResult`
  (the as-solved baseline), so switching frontier points never loses the original. A
  direct `complete*Job` call with no active job recorded (no in-flight
  `ActiveSolveJob`/`ActiveTrainJob` to read `source`/`structuralVersion`
  from) falls back to `source: ""` and `structuralVersion: -1` — sentinels
  that can never equal a real value, so the record reads as stale rather
  than silently matching whatever the caller happens to be viewing.
- **`CachedExplorePivotResult` / `ActiveExplorePivotJob`** (`stores/useNodeResultsStore.ts`): records keyed by
  `${exploreNodeId}:${pivotId}`. A cached matrix stores its backend calculation identity and
  dataframe-cache key; an active job stores the same composite ownership plus job id/source.
  Completion replaces only that pivot's result, failure stays local, and disabling a card does
  not delete either record. `useBackgroundJobs` polls every active pivot job through the guarded
  pivot status parser and applies the standard completed/failed terminal split.
- **`AddSourceResult`** (`stores/useSettingsStore.ts`): `addSource`'s
  return type — `{ok: true, key: string}` on success, or
  `{ok: false, reason: "empty"}` / `{ok: false, reason: "duplicate", key}`
  on rejection, allowing callers to surface why the add failed.
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
- **`NodeResultsState`**: the store's full shape — five result and job
  record pairs (`previews`, `solveResults` + `solveJobs`, `trainResults` + `trainJobs`,
  `exploreResults` + `exploreJobs`, `pivotResults` + `pivotJobs`), `pivotStartClaims`,
  a `columnCache` keyed `"nodeId"` or `"nodeId:source"`, and a `pinnedPreviewNodeId`
  that is exempted from LRU eviction in the four caches trimmed by
  `trimCacheByRecency` and whose pivot entries are likewise exempted by
  `trimExplorePivotCache`.
- **`SettingsState.mlflow`**: `{status: "pending"|"ready"|"error",
  installed, importable, destinations, detail}` — populated from
  `GET /api/mlflow/destinations?probe=true`
  (`parseMlflowDestinationsResponse`), fetched once on the first render of
  any MLflow section under a 15-second deadline — the backend may spend its
  full 5-second probe budget per remote, so a remote that exhausts its
  budget arrives as an amber entry rather than tripping a whole-inventory
  error. `status` is `"ready"` when the inventory arrived,
  whatever the probes said; it is `"error"` when the package is missing or
  unimportable or the request failed, with the reason in `detail`.
  `destinations` mirrors the three wire entries (`key`, `configured`,
  secret-free `destination`, `config_source`, `detail`, `probed`, `ok`,
  `category`). `useMlflowDestinations()` (exported alongside the store)
  maps `"pending"` to `"loading"` for display purposes only; the store
  itself never uses the word "loading". `invalidateMlflow()` resets
  `status` to `"pending"` and triggers a re-fetch with probing — the
  settings modal calls it after a successful `PUT /api/mlflow/settings`,
  and the selector's re-check action calls it directly, so a configuration
  change or a recovered connection is reflected without a page reload. Node
  config, not this store, holds each node's choice (`mlflow_destination`,
  absent = the local folder); the store only says what the environment offers.
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
exceptions. Split modules such as `api/dispersion.ts` keep their parsing local to
preserve the lazy bundle boundary (dispersion's through its generated validators).
`api/assistant.ts` likewise requests status/session JSON as `unknown`, validates
every history row locally, and parses every required field of each SSE variant
before invoking its callback. These feature parsers tolerate unrelated additive
fields but reject missing or mistyped required fields with ordinary `Error`
values; only the shared transport manufactures `ApiError`.

**Awaited job waits** (`waitForJob`): a job that one operation starts and
awaits, with no store entry, is waited for by `waitForJob` in
`hooks/jobPollingController.ts`. It polls at once and then at the caller's
fixed interval, calls `onStatus` with each non-terminal status, and resolves
with the first terminal one. A poll error rejects the wait at once, because
the transport already retries transient failures. The caller's `AbortSignal`
rejects the wait with an `AbortError` and aborts the request in flight; the
deadline (the controller's 24-hour lifetime unless the caller passes a
shorter `timeoutMs`) rejects it with `JobWaitTimeoutError`. The wait never
cancels the job; a caller that owns the job decides that. The callers are:

- `runDispersionEstimate`: starts the job, waits at the configured interval
  (500ms by default) with the caller's signal, and resolves or rejects the
  single outer promise. An abort after job creation awaits
  `cancelDispersion(jobId)` before the outer promise rejects; cancellation
  failure remains visible. `GLMTargetConfig` owns one controller per estimate
  and aborts it on unmount, so a closed panel cancels its estimate and never
  applies the value; a cancellation that fails after the panel closed is
  reported as an error toast, because the estimate may still be running.
- `ensureInputSnapshots` waits for each build with the ensure pass's signal;
  `cancelInputSnapshotBuild` waits at most 48 seconds for the cancelled build
  to stop, then raises `CancellationFailedError`.
- `useOptimiserAutoRange` waits with the active run's signal and retires the
  run from `onStatus` once it is no longer current.

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
`updateFrontierAfterSelect`): selecting a point applies the server's summary
for it (`frontier.point_summaries[i]`, derived by the optimiser's
`frontier_point_summary`) to `originalResult` — no network call, and nothing
is derived from the frontier row; a `null` summary field clears that field.
A completed solve with frontier points selects point 0.
`updateFrontierAfterSelect` is the network-driven counterpart used after an
explicit backend `/optimiser/frontier/select` (ratebook materialisation); it
validates the echoed `point_index` matches the request, stores the response
as that point's summary (so later re-selecting that point doesn't need
another round trip), and — critically — if the user has since selected a
*different* point while the request was in flight, it keeps the stored
summary but does not regress the displayed `result`/`selectedPointIndex` to
the stale response's point (the "stale-response guard").

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
rather than a retryable transient error. An `ApiResponseValidationError`
from any of the four status parsers is likewise terminal
(`getJobPollErrorMessage`): the same payload would fail identically on every
poll, so the job ends with the validation message instead of holding stale
progress behind the retry loop. Reconciliation aborts and retires a
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
  `maxEntries`, always excluding `pinnedKey` (and `trimExplorePivotCache`
  similarly excludes every pivot entry of `pinnedPreviewNodeId`) — if pinning
  would leave more entries than `maxEntries`, the pinned entries are still never
  evicted (the bound is soft in that one case).
- **`addSource`** performs no state change for a blank/whitespace-only name
  (`{ok: false, reason: "empty"}`) or for a name whose sanitized key
  already exists in `sources` (`{ok: false, reason: "duplicate", key}`) —
  callers must check `result.ok` before reading `result.key` and setting
  `activeSource`. `Toolbar`'s add-source form keeps itself open and shows
  the reason as inline error text on rejection rather than closing
  silently.
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

**MLflow destination selector (`MlflowDestinationSelector`).** The
destination is a per-node decision, so the control lives on the node: the
toolbar renders no MLflow chip, and no surface's copy refers to one. The
selector takes the node's stored value (`databricks` or `server`; absent,
`""` or anything else is the local folder) and an `onChange` that reports the
chosen key; callers store `mlflowDestinationConfigValue(key)`, which removes
`mlflow_destination` for Local folder. It renders a `radiogroup` labelled "MLflow destination" of three
**native** radios (so the radio role carries its keyboard behaviour) in the
fixed order Databricks, MLflow server, Local folder. The selected radio is
the *effective* destination (`effectiveMlflowDestination(value)`), so a
node without a choice shows Local folder selected whatever remotes are
configured; there is no automatic choice, no "auto" label and no "Use auto"
control, and clicking the already selected option emits nothing. Each remote carries a light dot
(`mlflowLight`): green when configured and its probe passed (tooltip: the
secret-free destination), amber when configured but the probe failed
(tooltip: the probe detail), grey when unconfigured (the option is
`aria-disabled`, its tooltip names what to set, and activating it opens
the settings modal instead of selecting), and pending while the inventory
loads. Local has no light — it always works. Under the row sits the
resolved destination line (`<Label> — <destination>`, or the entry's
reason when unconfigured, or the store's package reason), a re-check icon
(`invalidateMlflow()`, disabled while loading) and a gear icon that opens
`MlflowSettingsModal` (`useUIStore.setMlflowSettingsOpen`). Setting
`showDestinationDetails={false}` hides
the successful resolved-location line, but keeps unavailable-state feedback,
re-check and settings controls. The option defaults to true for existing callers.
Connection lights are informational only: they never select, block or redirect logging, and
nothing is written until the user clicks.
Colours come from the theme tokens (`--success`, `--warning-strong`,
`--text-muted`).

The toolbar wraps complete control groups onto additional rows when the
viewport cannot contain them on one row. Its height adapts, all actions remain
reachable, and it never causes document-level horizontal overflow or scrolling
that displaces the pipeline canvas. Menus and settings dialogs remain unclipped.

Centre and Layout pair their labels with decorative 13px Lucide icons (`Scan`
and `Network`, respectively), using the same icon-to-label spacing as the other
toolbar actions. While auto-layout runs, its spinner overlays the hidden icon
and label so the button keeps its idle width and the toolbar does not reflow.

**Pipeline settings (`PipelineSettingsModal`).** The toolbar's Pipeline
button opens a "Pipeline settings" pane. Its first section, Calculation, is a
radio group (one tab stop, arrow keys move and select) choosing Automatic or
Manual calculation (`useUIStore.calculationMode`, session-only, starting
Automatic; behaviour in the canvas spec's Manual calculation rule). Its Preview section holds the preview
row limit, which applies to the next preview, and the streaming chunk size, an
editor-wide server setting: the field shows the server's value when the pane opens,
and a change is sent to the server, which applies it to every execution started
afterwards. Requests carry no chunk size. Below it, the "Cached data" section
lists the current pipeline's node data and cached data
outside it, with size, status, cached-at time, build duration and per-entry clear
controls, under a "Cached data" heading with no explanatory copy. Beside the heading,
`CacheStoreSize` shows the whole snapshot store's size ("5.0 GB cached"), read from
`GET /api/cache/usage` with each inventory read; its tooltip gives the automatic
captures' share of their budget, and a failed read shows nothing. There are no budget
cards, caps, quota variables or generation counts. `POST /api/cache/nodes` is read on
open, explicit Refresh and successful clear. Closing abandons the latest read; a failed refresh
keeps the last good inventory beside the error. Existing source-sharing and
clear ownership rules remain. Unattributed-byte footnotes remain, but there is
no claim that entries count against budgets. There are no backend cache byte
or entry-count caps, budget-driven eviction or cache-quota diagnostics. Every
stored dataset is accounted for beside its node or in the remaining-data group
and users clear the data they no longer need. Active reads remain protected.
Displayed node identifiers omit the leading `submodel_runtime/` namespace while
retaining the submodel and child-node names. This also applies to references in
row details and cached nodes outside the current pipeline. Cache identities and
clear requests retain their original values; input file paths are not reformatted.

Refreshing is explicit. The server answers the endpoint by walking every
identity, generation and staging entry, which is what an admission pays, so
the pane never polls and nothing subscribes it to store changes; a surface
that wants to poll needs an incremental count in the store first. A failed
read renders `apiErrorMessage` beside the last good inventory rather than
replacing it, because stale inventory with a stated error is more use than an
empty pane. Closing the pane abandons whichever read is in flight — the
latest, which after a Refresh is not the one the opening effect started.

The pane **tabulates every node of the open
pipeline** for the active source under one column header, with no group row naming the
pipeline or source — node, status, size, when it was
cached, and how long that took — with numeric columns right-aligned and tabular so they
compare down the column, from `POST /api/cache/nodes`. It builds the graph payload itself from
`usePanelGraphContext` and the graph store rather than taking it as a prop, since the
toolbar has no graph of its own to pass. Each row shows the node's label (its id when the
canvas has none, which is how a node the store holds but the graph has lost still reads),
its state, its size, and — from the generation's own metadata — when it was cached and the
seconds it took. An unrecorded duration renders as an em-dash, never as zero: a store
predating the recording must not claim instant builds. A row that carries bytes also
carries a visible clear control, which clears exactly the identities that
row reported and then re-reads the inventory. A row carrying nothing has no control, because it would
have nothing to act on and a row whose bytes belong to another row must not appear to own
them. A failed clear renders `apiErrorMessage` and leaves the table as it stands. What a row does *not* show is as deliberate: a node reading an
upstream point says "reads <that node>", because the point's bytes belong to that node's
row — it still shows a size of its own when the store holds its captured output, which a
node in the middle of a lineage often has; a node sharing an input snapshot with an earlier
row says "same source as <that node>" and shows no size, because that identity is reported
once; a direct file read says "Read directly" rather than "Cached", because a
Parquet scan holds no cache whatever its point's state says. A second group names cached
data belonging to no node of this pipeline — a deleted node, another source, an unread
input snapshot — with status "Stored", describing its presence without claiming
freshness for a graph that is not being resolved. A footnote states any bytes that
could not be attributed. The whole body scrolls beneath a fixed title and
Refresh, because a large pipeline's list is longer than any viewport.

**MLflow settings modal (`MlflowSettingsModal`).** The modal is the
inventory editor. It fetches `GET /api/mlflow/settings` on mount, reads the
inventory from the store (triggering the first fetch if pending), and
renders: an "MLflow server URL" field prefilled from the stored
`tracking_uri`; a "Local folder" field prefilled from the stored `folder`
with the resolved folder as placeholder and helper line; a read-only
Databricks block naming the selected profile (a `databricks://` inventory
destination), the host from `DATABRICKS_MLFLOW_HOST` when no profile is selected, or the missing
configuration (a profile reference or the host/token variables); one Test
action per remote — "Test server" POSTs `{destination: "server",
tracking_uri: <draft>}` and "Test Databricks" POSTs `{destination:
"databricks"}` — each rendering its own inline categorised result, cleared by
any draft edit and protected by a request-sequence guard against a stale
completion; and Save/Close. Save PUTs `{tracking_uri, folder}` exactly as
drafted (an empty folder lets the backend persist the resolved folder) with
every input locked while the request is in flight — a "Saved"
acknowledgement can therefore never sit beside edits it does not cover —
then calls `invalidateMlflow()` and re-renders from the PUT response, so
every node's selector refreshes without a reload; a `400` renders its
field-naming `detail` in the modal's error area. Load, test and save failures
all render `apiErrorMessage`, so the server's message (never the bare HTTP
status) reaches the user. The browser never renders
or submits a secret: stored server URLs are credential-free by backend
validation, displayed destinations arrive pre-redacted, the modal adds no
credential inputs, and an unchanged save of an env-derived configuration
keeps its authentication because resolution re-attaches matching env
credentials server-side.

**Process-wide MLflow fetch guard.** `useSettingsStore.fetchMlflow` guards
re-entrancy with a module-level `let _mlflowFetchingGuard` rather than store
state. The guard is shared across every store instance in the process, so tests
that create fresh instances must retain process-wide concurrency semantics
rather than assume per-instance isolation. `invalidateMlflow()` cooperates
with the guard rather than bypassing it: it resets the cached status to
`"pending"` and calls `fetchMlflow`, and an invalidation issued while a fetch
is already in flight still results in exactly one follow-up fetch.

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

### The shared data cache

Every consumer node — an Explore preview, a Banding editor, a Rating Step editor — reads the
data of one *point*, and the backend's `/api/node-data/point` response is the only authority on
it (specified in the [server API](../server-api/low-level.md#node-data-builds)).

1. `panels/dataPointIdentity.ts` builds the identity that gates the request: the consumer's
   upstream subgraph and the original of every instance in it (execution runs an instance with
   its original's configuration, and that original can sit outside the consumer's own edges),
   each node's data-affecting configuration (Explore's overview, pivot, chart, and formula
   settings excluded), every edge with its source and target handles, the submodels, and the
   preamble. Covering more than the backend's signature costs an extra request, never a wrong
   answer.
2. `hooks/useNodeDataCache.ts` asks for the point on mount, whenever that identity changes, and
   whenever the store's node-data epoch changes; a rerender that keeps the identity (a canvas
   drag) does not re-ask. Every request, build, cancel, and clear captures the document
   execution fence, and no answer — from an inspection or from an action — is published unless
   that fence is still current *and* the identity it answers for is still the one the consumer is
   asking about, so neither another document's answer nor an action started before an edit can
   reach the shared store or strand the consumer without an answer. An answer is kept with the
   identity and the fence it answered for, so a consumer stops showing it the moment either
   moves on, and inspections also abort in flight.
3. `stores/useNodeDataStore.ts` keeps one entry per slot, so consumers of the same point share
   the generation and the running build. `run` and `refresh` post `/api/node-data/run`; a
   `started` or `joined` response registers the build under the slot, and `useBackgroundJobs`
   polls it once for the whole application. Every terminal outcome — completed, failed, or
   cancelled — clears the build and raises the epoch, so consumers ask what is there now instead
   of showing a cancel control for a build that has stopped. A `delegated` response hands a
   snapshot-backed Data Input or an API-input table to the input-snapshot orchestration
   (`hooks/ensureInputSnapshots.ts`, which builds a Quote Input's tables through the same
   input-cache job with `node_type: "apiInput"`), recorded under the slot as a
   delegated build with its progress message, its canceller, and a token that owns it, so every
   consumer of the point sees and can stop it while a callback from an abandoned pass can neither
   report progress for nor clear the build that replaced it. That orchestration leaves data it
   considers ready alone, which is what a preview wants, so a refresh — or a point the backend
   already reports `stale` — passes its `force` option, which replaces the data rather than
   serving it. Cancelling aborts the pass, and the pass cancels the build it is waiting for and
   waits for it to stop, so the point no longer reports itself as building when the delegated
   build ends; a cancellation is reported as the outcome the user asked for rather than a
   failure. A cancellation the server refused, or a build that has not stopped, is reported as
   exactly that and keeps the delegated build in the slot with a control that cancels that same
   build again, because work the user asked to stop may still be running and a control that
   vanished would leave the point building with nothing watching it. That recovery only updates
   the build it still owns, so a pass whose cancellation fails after another build has claimed
   the slot changes nothing.
   `clear` removes the point's analyses through `/api/node-data/clear` and then clears delegated
   data through the route the point names. A document fence change drops every slot, because slots belong to one
   document and source, and the epoch keeps counting through that reset so a consumer whose
   graph did not change still asks again. The node-data epoch is raised for a `captured`
   generation the store has not seen before. The store records the generation ids it has
   announced, bounded so a long session cannot grow it without limit, oldest dropped first,
   and cleared by the store's reset while the epoch keeps counting. When the epoch rises,
   every consumer asks again; a stored preview records the epoch its request was sent under
   (`useNodeResultsStore.setPreview`), and `advancePreviewEpoch` re-stamps only the entry still
   holding the same response.
4. Availability is per consumer: a fresh generation covering the consumer's demand is `current`,
   a fresh one that does not is `partial`, a superseded one is `stale`, and a point with no
   answer yet is `checking` rather than `missing`.

## Testing

- `tests/test_frontend_backend_contract.py` verifies frontend/backend node-type and allowed-column-type sets remain identical.
- `tests/test_sanitize_parity_fixture.py` verifies the retained backend compatibility golden and minimum fixture width; it is not a frontend parity twin.

Tests are split between colocated `frontend/src/**/__tests__/` folders next to each source
file and a parallel `frontend/src/__tests__/`
tree that adds gap-coverage and adversarial cases; both trees run under the
same Vitest config.

- **Shared data cache** (`frontend/src/stores/__tests__/useNodeDataStore.test.ts`,
  `frontend/src/hooks/__tests__/useNodeDataCache.test.tsx`,
  `frontend/src/panels/__tests__/dataPointIdentity.test.ts`,
  `frontend/src/__tests__/hooks/useBackgroundJobs.nodeData.test.ts`): one slot entry shared by
  two consumers, the epoch rising only when the data changes or a preview's own capture is
  announced — a duplicate announcement leaving the epoch untouched while a new generation
  raises it once, and a reset making the next announcement count again — a build adopted from
  another client, two consumers deriving `current` and `partial` from one narrow generation,
  progress
  from a build either of them started, delegation handing over exactly the producer node, a
  forced or stale delegated build replacing data the ensure pass would skip, a delegated build
  shown to another consumer and cancelled through the slot, delegated clears, a point read
  directly offering no build, a clear that leaves nothing claimed as cached and a refused clear
  that changes nothing, an answer for a superseded identity never landing, a document that has
  moved on never being written — including a clear that lands after it — the shared poller
  completing or failing a build, and the fence dropping every slot. The identity suite covers
  the instance original of an upstream instance and a moved port handle.
  `frontend/src/stores/__tests__/useNodeDataStore.test.ts` covers the store's record of
  announced captures.
- **Delegated data-point builds** (`frontend/src/hooks/__tests__/useNodeDataCache.delegation.test.tsx`):
  two consumers of one delegated point drive the real `ensureInputSnapshots` helper against the
  API seam — a stale snapshot is rebuilt with `refresh` instead of being served as it is, a
  forced table rebuild removes the working cache the build endpoint would answer with no work, a
  cancellation from the second consumer cancels the input-snapshot job on the server and only
  ends the delegated build once that job is terminal (both consumers then showing the point as it
  is, with no error toast), a cancellation the server refuses is reported to the user and leaves
  a control that cancels the same build again and succeeds, a build still running after every
  cancellation poll is reported with its control kept, an accepted cancellation whose outcome
  cannot be read is reported the same way, and a cancellation that fails after another build has
  claimed the slot leaves that replacement untouched.
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
  identity, the node-data epoch a preview records, and a re-stamp that leaves a replaced
  response's successor alone. The colocated `frontend/src/hooks/__tests__/useJobPolling.dedup.test.ts` and
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
  utility/imports/git/assistant closes the other three) and per-node selection-map
  helpers.
- **MLflow destination surface**
  (`frontend/src/components/__tests__/MlflowDestinationSelector.test.tsx`,
  `frontend/src/utils/__tests__/mlflowDestinations.test.ts`,
  `frontend/src/components/__tests__/MlflowSettingsModal.test.tsx`,
  `frontend/src/__tests__/stores/useSettingsStore.test.ts`): the three light
  states per remote from a mocked inventory including hover text and the
  disabled greyed state that opens the modal; a node without a choice staying
  on Local folder under any inventory with no "Use auto" control; explicit choice
  callbacks and Local folder removing the key; the resolved line, re-check, and gear; the availability
  helper's loading / package-missing / unconfigured-own-destination /
  amber-but-available cases; the modal's prefilled fields, the three
  Databricks block variants, per-remote tests posting the draft or the key,
  Save PUTting both fields then `invalidateMlflow()`, a `400` detail with
  nothing saved and no invalidation, and inputs locked while saving; the
  store fetching with `probe=true`, mapping package facts to `"error"`, and
  the dedup/queued-refetch guard. `frontend/src/components/__tests__/Toolbar.test.tsx`
  proves no MLflow chip renders and the modal still opens from the UI flag.
- **Chrome components**: `frontend/src/__tests__/components/ErrorBoundary.test.tsx`
  (root-level, under `frontend/src/__tests__/components/`),
  `frontend/src/components/__tests__/ModalShell.test.tsx` and
  `frontend/src/components/__tests__/ModalShell.focusTrap.test.tsx` (focus trap and
  restore-on-close in particular; centred or top placement),
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
  `frontend/src/components/__tests__/PipelineSettingsModal.test.tsx` (the preview
  row-limit and chunk-size fields' clamping and store writes; the inventory
  and clear controls without budget meters, limit variables or generation counts;
  one read on open and one per Refresh with ten minutes of fake time
  advanced on either side of the click so a poll would fail the test, the
  opening read and a Refresh's read each abandoned when the pane closes, a
  failed read reported beside the last good inventory;
  and for the node list: every node listed including one with nothing cached,
  the canvas's label with the id as fallback, a direct read never called
  "cached", a node that reads elsewhere carrying no size and naming that node,
  cached data belonging to no node of the pipeline, the unattributed-byte
  footnote, and one node request per read for the active source),
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
  `frontend/src/hooks/__tests__/useDebouncedCallback.test.ts` (latest arguments and callback,
  per-call delay, flush result, cancel, stable object, drop or flush on unmount),
  `frontend/src/__tests__/hooks/useDragResize.test.ts`, `frontend/src/__tests__/hooks/useJobPolling.test.ts` (root-level, generic
  poller mechanics) plus the colocated dedup/progress-throttle variants and
  `frontend/src/hooks/__tests__/jobPollingController.test.ts` (controller
  mechanics and `waitForJob`: terminal resolution, abort in flight, deadline,
  poll errors, `onStatus` retirement),
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
no-op blur, external-value draft reset, `ValidatedTextField`'s refused candidates, owner
refusals, commit errors and warnings, and form-label/control semantics.
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

Known gaps: `frontend/src/components/Toolbar.tsx`'s inline millisecond timing helper
(`formatTiming`) is explicitly verified by unit tests in
`frontend/src/components/__tests__/Toolbar.test.tsx`; its memory breakdown uses `formatBytes`.

**One helper per repeated concern** (`frontend/src/__tests__/lint/helperBans.test.ts` runs ESLint on probes). Error text comes from `apiErrorMessage`, byte counts from
`formatBytes`/`formatByteSize`, durations in seconds from `formatDuration`, and the
non-array-object check from `isPlainObject`/`expectPlainObject` in `types/guards.ts`. ESLint's
`no-restricted-syntax` rejects a local function named `errorMessage`, `errorMsg`, `errorDetail`,
`requestErrorDetail`, `previewErrorDetail` or `gitErrorMessage` (declared or assigned, with no
exceptions; the tracing hook's `technicalDetail` is not error text but the raw detail its Technical
details disclosure shows whole), the
`e.detail || e.message` idiom, a local `formatBytes`/`formatMemory`/`formatSize`/`formatDuration`/
`formatElapsed`, and a local `isRecord`/`asRecord`/`isPlainRecord`/`isPlainObject`/`isObjectLiteral`
outside the owning modules. `utils/objectLiteral.ts`'s `isObjectLiteral` is the stricter check a
JSON config needs (a plain object literal, not a class instance); the Explore chart and pivot
configs, `utils/banding.ts` and the rating table utilities use it.
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
Settings/cache stores key remote work by safe
identity digest and job id, not table spelling. Guard tests cover every union
leg, order retention, unknown versions, readiness/freshness separation,
error/redaction fields, and malformed-payload rejection.

## Modelling config panes

The observable behaviour is defined by
[frontend-modelling-optimiser-ui](../frontend-modelling-optimiser-ui/high-level.md#modelling-config-panes).
The following remain shared-infrastructure-owned:

- `frontend/src/stores/useUIStore.ts` owns a `ModellingPane` six-value union (`target`, `features`, `params`, `split`, `train`, `export`) plus
  `modellingPanes: Record<nodeId, pane>` and its immutable setter, following the existing Explore
  selection-memory pattern. It is browser UI state only and is not serialized into node config.
- `frontend/src/api/types.ts`, `frontend/src/types/trainGuards.ts`, and the train-progress type used by
  `frontend/src/stores/useNodeResultsStore.ts` share the backend's `train_loss_history` and
  `train_loss_history_truncated` status fields, which the server always sends.
  `parseTrainStatusResponse` requires both and parses every row through the same
  finite-number/required-iteration contract as completed `loss_history`; malformed or missing
  history throws, and no latest-loss reconstruction is invented. The store's type keeps them
  optional for the entries it makes itself.
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

`api/client.ts` exposes one apply call per repair family; there is no dry-run
call or plan hash. The removal call sends the root document source, current raw
revision, target source/recovery identity, and explicit `delete_config`; the
recovery call sends `action: update | reset | recover` and no deletion option to
`/api/pipeline/repair/recover/apply`. Runtime parsers reject unknown keys,
unexpected discriminators, unbounded/invalid patch entries, and a malformed
nested editor document. The response's bounded display diffs describe what the
server wrote; the browser never sends bytes. API errors preserve structured
repair detail for the confirmation UI. Recover responses additionally parse the
field-outcome report, completeness entries and previous configuration. Requests
never carry client-authored replacement source/config. `saveNodeScoped` posts one
node's proposed configuration to `/api/pipeline/node/save` and parses the
authoritative document.

## Recovery surfaces

Recovery has no draft dialog, draft transport, draft validators, or persistent
client recovery state. The single `PipelineRepairDialog` confirms all four
actions (remove, update, reset, recover) with one Apply click; there is no
preview step. Applying a recover stores the apply response's field
outcomes, completeness entries, previous configuration and bounded diffs in
`useRecoverySummaryStore`, keyed by the owning source file plus the target's
recovery id (ids repeat across documents) — transient session state, never
persisted. The node panel shows a dismissible summary for
the selected recovered node with retained/defaulted/needs-input/removed counts
and on-demand expanders for the field details, the previous configuration and
the source diff.

In a degraded document, a node whose server-derived `scoped_editable` flag is
true mounts its normal editor: ready nodes instead of the static JSON
inspector, and upstream-blocked nodes instead of the recovery inspector, with
their execution blockers reported alongside the editor rather than replacing
it. The panel offers a node-scoped save that posts the node's current
configuration through `saveNodeScoped`, adopts the returned authoritative
document, retains the target selection, and surfaces the document's remaining
completeness entries for that node. Scoped edits save one node at a time, coordinated for the App's lifetime by
`hooks/useScopedNodeSave.ts` so no guarantee dies with a panel unmount:
starting to edit a second node is refused while the first holds unsaved
scoped edits (the deadlocked two-dirty-node state is unreachable), every
scoped edit on any node is refused while a save is in flight, the saving
panel's editor freezes for the flight, and a late response is discarded
whenever the on-screen document identity (source file and revision) moved
after submission (`utils/scopedSaveGuards.ts`). Reloading the pipeline clears
the edited-node tracking.
Whole-graph mutation, save and undo fences
stay exactly as the capability model dictates; the scoped save never widens
them. Ready nodes that are not `scoped_editable` keep the static JSON
inspector in read-only documents.

The document adapter retains server-provided source/recovery identities and the
`scoped_editable` flag even for ready nodes. These remain transient metadata
and are stripped from normal graph saves.
Live undo/redo snapshots retain `_recoveryId` and `_sourceFile` for root and child
nodes, so reverting an edit preserves recovery targeting. Canonical save payloads
and dirty fingerprints continue to strip these server-owned identities.
The repair dialog loads only when a recovery action is opened. Its local
Suspense boundary leaves the mounted canvas intact; recovery does not increase
the initial JavaScript budget.
### Pipeline settings loading

The toolbar loads the pipeline settings modal on demand when Pipeline is opened. Its code
is excluded from the initial JavaScript bundle; its existing loading, error,
refresh and close behavior is unchanged once mounted. This follows the app's
existing local Suspense convention for user-opened dialogs.

### Profile recovery after document/store invalidation

The shared profile hook deduplicates only an outstanding request for the current
slot, data version and node-data store epoch. Resetting the shared store must let
a still-mounted consumer obtain that profile again, even when the persisted data
version is unchanged. A discarded response from an older document fence or epoch
cannot leave the consumer permanently marked as already requested, publish an old
job, or report an obsolete failure. Completed profiles and active jobs in the
shared store still suppress duplicate work; a recorded current failure still
requires the existing explicit retry. This preserves automatic pivot calculation
after saving presentation/configuration edits and returning to cached data.
