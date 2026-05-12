# Engine-Wide Memory Safety Plan

## Goal

Haute should handle large datasets by planning and executing work in bounded
ways. Memory safety should come primarily from projection, streaming, chunking,
disk-backed checkpoints, and worker isolation where appropriate. RSS limits are
a circuit breaker, not the main execution strategy.

This applies to the whole execution engine:

- previews
- pipeline sinks
- model training preparation
- optimiser setup and solve preparation
- optimiser auto-range
- optimiser apply and explainability paths
- deploy live scoring
- deploy batch scoring
- generic chunked map-reduce execution

## Principles

1. Explicit environment caps are hard caps.
   If a user, CI job, or deployed API sets `HAUTE_*_MEMORY_LIMIT_*` or
   `HAUTE_*_PROCESS_RSS_LIMIT_*`, Haute must respect it exactly.

2. Local defaults should be adaptive.
   A local GUI should not refuse a useful medium/large workload simply because
   it crossed a small fixed RSS-growth number. Defaults should derive from
   available machine memory with an operating-system reserve.

3. Deployment defaults should remain conservative.
   API/deploy execution needs deterministic failure, request-size limits,
   process/container caps, and in-flight admission accounting. Local GUI and
   deployed API are different products from a memory-risk perspective.

4. No silent broad fallback.
   If bounded streaming/chunked execution cannot support a graph shape, fail
   loudly with the blocking node/operator and actionable diagnostics.

5. Metrics before guesses.
   Every long-running path should record the execution strategy, budget mode,
   memory baseline, peak RSS, pressure events, chunk sizes, and blocking
   bounded-execution reasons.

## Slice 1: Adaptive Engine-Wide Budget Policy

Status: implemented.

Replace fixed default growth caps with a profile-aware budget policy.

Acceptance criteria:

- Explicit profile env caps keep current hard-cap behavior.
- Explicit global env caps keep current hard-cap behavior.
- Explicit process RSS caps still override growth budgets.
- Without env caps, local execution profiles derive budgets from available RAM.
- The adaptive policy applies across all execution profiles, not just
  auto-range.
- Admission metadata records whether the budget came from an explicit env cap,
  a fixed server default, or adaptive local policy.
- Existing memory-limited terminal states and HTTP payloads remain compatible.

Initial profile policy:

- `preview_eager`: adaptive but interactive-biased. Keep enough OS reserve and
  avoid making previews the largest memory consumer.
- `lazy_sink`, `training_prep`, `optimiser_setup`, `auto_range`,
  `chunked_map_reduce`: adaptive local budgets with larger fractions of
  available RAM.
- `deploy_live`: strict fixed default unless explicitly overridden.
- `deploy_batch`: adaptive only in local/batch tooling; deployed server mode
  should use explicit process/container caps.

## Slice 2: Pressure Telemetry And User Diagnostics

Status: implemented.

Implemented:

- Shared execution metrics record bounded memory-pressure events at 50%, 75%,
  and 90% of the effective operation budget.
- Baselined contexts measure pressure against operation growth headroom, not
  total warm-process RSS.
- Pressure events are advisory and do not fail the job before the hard budget
  is exceeded.
- Pressure events are retained with bounded cardinality and rollup counts.
- Memory-limited failure payloads can include pressure events recorded before
  the failure.
- Admission metadata now includes `budget_policy`, `available_ram_bytes`, and
  `os_reserve_bytes`.
- Preview, training status, optimiser status, and auto-range status schemas can
  carry execution metrics without dropping pressure diagnostics.
- Running training and optimiser jobs publish fresh execution metrics to the
  job store immediately when a pressure threshold is crossed.
- Frontend API guards parse execution metrics as typed diagnostics instead of
  leaving them as `unknown`.
- Preview execution metrics flow into the preview pane data model/cache and are
  rendered as concise diagnostics with expandable technical detail.
- Optimiser and training progress panels render structured pressure diagnostics.
- Auto-range memory-limited failures derive user-facing messages from execution
  metrics instead of showing only a generic budget error.
- Optimiser and training job stores retain terminal status objects, including
  `terminal_reason` and `execution_metrics`, alongside the user-facing error
  string.
- Object-shaped auto-range error details no longer render raw JSON into the UI;
  known friendly fields are used, otherwise the typed error code is shown.
- Memory-pressure diagnostics are supporting context only. They do not replace
  contract, timeout, cancellation, supersession, or generic error messages
  unless the terminal status/reason/error code is explicitly memory-limited.
- Initial optimiser/training POST failures preserve structured `ApiError`
  detail through `rawDetail`, so admission failures can retain
  `execution_metrics` before a background job exists.
- Immediate optimiser startup failures are cached as explicit terminal failures
  instead of disappearing when no active solve job has been registered yet.

Add non-terminal pressure reporting before memory-limited failure.

Acceptance criteria:

- Metrics include pressure thresholds crossed, active stage, current RSS, peak
  RSS, available RAM, budget, and policy mode.
- Auto-range, optimiser solve, training, preview, and deploy paths expose this
  information in their existing status/response payloads where applicable.
- GUI terminal memory messages name the stage and give concrete next actions.
- GUI keeps technical details in an expandable area, not as the primary message.
- Non-memory terminal failures stay primary even when the operation had earlier
  memory-pressure events.
- Startup/admission errors keep structured execution metrics from the HTTP
  error detail object.

## Slice 3: Byte-Budgeted Chunk Planning

Status: implemented.

Move chunk sizing from row-count defaults to byte-aware plans.

Implemented:

- Generic chunk plans accept either explicit row chunks or target byte budgets,
  but never both.
- Projected schema width drives byte-per-row estimates, so unused wide source
  columns do not shrink chunks.
- Scenario expansion reduces source chunk sizes before rows are expanded.
- Auto-range uses byte-budgeted chunk plans by default, with explicit
  `auto_range_chunk_size` and `chunk_size` overrides preserved.
- Optimiser grid and ratebook factor-context setup derive price-contour row
  chunks from projected parquet byte metadata when no explicit `chunk_size` is
  configured.
- Setup chunk provenance is recorded under `setup_chunking` instead of being
  serialised as synthetic user config.
- Empty or malformed setup parquet metadata fails loudly before chunked
  price-contour builders run.

Acceptance criteria:

- Projected byte-per-row estimates are computed from metadata/schema/sample.
- Wide projected schemas choose smaller chunks than narrow schemas.
- Scenario expansion and model scoring include expansion factors in chunk
  estimates.
- User-configured chunk-size overrides remain available and explicit.
- Streaming and lazy fallback parity tests continue to pass.

## Slice 4: Structural Bounded-Execution Contracts

Status: implemented.

Require every large-data route to prove one of the supported physical
strategies:

- projected streaming sink
- projected streaming collect batches
- chunked map-reduce
- explicit small eager materialisation
- isolated worker execution

Acceptance criteria:

- Bounded profiles carry a projection plan.
- Opaque fan-in/user-code nodes fail with `ProjectionImpossibleError` unless
  they can be proven bounded.
- Source scans receive concrete physical columns where supported.
- Ratebook factor side inputs are projected by first-class planner rules.
- Plain JSON remains a small/live-data path, not a large-data path.

Implemented:

- Strict bounded profiles now compute and carry a public projection plan even
  when the caller did not provide a projection seed.
- Opaque fan-in and arbitrary user-code nodes fail with
  `ProjectionImpossibleError` in bounded profiles unless a concrete contract
  proves the required columns.
- Empty Polars pass-through nodes remain concrete pass-throughs, so they do
  not force parents back to all columns.
- Source user-code paths are rejected under strict bounded profiles unless
  declared with a concrete contract.
- Data-source remaps in deploy scoring receive the same physical projection
  columns as normal source scans.
- Ratebook data-input and banding-source parent demands are routed by the
  shared projection planner, including the shared-input case.
- Auto-range ratebook projection seeds only the optimiser data frame; factor
  side requirements are owned by the optimiser parent-demand planner rule.

## Slice 5: Local GUI Versus API/Deploy Policy Split

Status: implemented.

Introduce explicit runtime policy mode.

Acceptance criteria:

- Local `haute serve` defaults to permissive adaptive budgets.
- Deployed/API runtime defaults to strict fixed or container-aware caps.
- Request-body limits remain hard in deploy/live paths.
- API batch scoring supports async or streaming output for large payloads.
- Concurrent heavy jobs are admitted against total in-flight budget rather than
  each receiving the full machine budget independently.

Implemented:

- `HAUTE_EXECUTION_MEMORY_POLICY` selects `local_adaptive`, `fixed`, or
  `strict_server` budget policy.
- Local GUI profiles default to adaptive budgets; `deploy_live` remains fixed
  by default unless explicitly configured.
- Explicit profile/global memory caps and process RSS caps override adaptive
  defaults.
- Deployed quote requests are bounded before JSON materialisation and can
  stream NDJSON responses for large batch results.
- Heavy profiles reserve their admitted memory budget against a process-wide
  in-flight pool derived from available RAM minus OS reserve. A second heavy
  job is refused only when active reservations would exceed that pool.
- Preview/live-small profiles do not reserve the heavy in-flight pool.
- Admission reservations are released by preview, sink, deploy scoring,
  optimiser setup/solve, auto-range, and training-prep lifecycles.

## Slice 6: Worker Isolation For Heavy/Production Work

Status: foundation implemented; route migrations remain intentionally
gated behind event/artifact-first worker contracts.

Run heavyweight jobs in isolated worker processes where native memory spikes
could otherwise take down the GUI/API host.

Acceptance criteria:

- Worker failures become typed terminal states.
- Temp directories and artifacts are cleaned up on worker termination.
- API deployments can enforce process/container memory caps.
- Local GUI can continue running if a heavy worker is killed.

Implemented:

- Added a spawn-based isolated worker runner that does not inherit the host
  process's large native heaps.
- Worker success, remote exceptions, non-zero hard exits, timeouts, explicit
  stop reasons, unsupported required memory caps, and cleanup failures are
  typed.
- Parent-owned cleanup callbacks run after success, failure, timeout, crash,
  and cancellation-style stop reasons.
- Child address-space limits are applied with `resource.RLIMIT_AS` on
  platforms that support it. Required memory caps fail loudly where the
  platform cannot enforce them.
- Added a parent-side `IsolatedJobSupervisor` that maps isolated-worker
  outcomes into the existing `JobLifecycle` terminal states without sharing
  `JobStore` across processes.
- Tests prove that a killed worker does not kill the parent process, cleanup
  still runs, stopped workers preserve `cancelled`/`timed_out` style terminal
  reasons, and lifecycle transitions are written by the parent.

Boundary:

- Training, optimiser solve/setup, and auto-range are still route-thread jobs
  until their heavy worker outputs are artifact/event based. Windows spawn
  cannot share the current in-memory `JobStore`, and optimiser solve currently
  retains in-process heavy objects for follow-on workflows. A direct thread-to-
  process swap here would be brittle and could lose status updates.

## Slice 7: Regression Gates

Status: implemented.

Keep the architecture honest with tests.

Required test families:

- explicit env caps remain hard
- adaptive defaults apply to every execution profile
- process RSS caps beat adaptive budgets
- cumulative RSS ratcheting is caught when process caps are configured
- no bounded path silently broad-collects
- unsupported graph shapes fail loudly and name the blocker
- byte-budgeted chunks shrink for wide schemas
- streaming/chunked and lazy paths produce equivalent results
- frontend displays actionable memory diagnostics

Implemented coverage:

- Execution admission tests pin explicit profile/global caps, adaptive local
  defaults across engine profiles, deploy-live fixed defaults, process RSS caps
  overriding adaptive operation budgets, in-flight heavy-job reservations, and
  cumulative warm-process RSS ratcheting when a process cap is configured.
- Projection and lazy-execution tests reject uncontracted/opaque bounded paths,
  assert blocker diagnostics include the offending `node_id` and `node_type`,
  and prove bounded profiles carry projection plans.
- A static bounded-collect guard prevents bounded execution modules from
  calling Polars `.collect()` directly. Collection must go through the shared
  streaming/memory-aware helpers.
- Chunk planning tests cover unsupported graph shapes, explicit unbounded
  opt-in, byte-budgeted chunk sizing, wide schema shrinkage, unused wide-column
  immunity, scenario expansion, and streaming/chunked parity.
- Optimiser route tests cover bounded-sink usage, byte-budgeted optimiser setup
  chunking, auto-range streaming/lazy parity, and fail-loud bounded execution
  errors.
- Worker-isolation tests cover typed child failures, hard death, timeout,
  cancellation-style stop reasons, cleanup, lifecycle transitions, and
  platform-gated process memory caps.
- Frontend unit/component tests cover structured execution metrics, friendly
  memory diagnostics, and preserving non-memory terminal errors as the primary
  message.
