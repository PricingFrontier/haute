# Worker isolation roadmap

**Status:** Active

**Current as of:** 2026-07-20

**Owning queue:** [Background jobs and API lifecycle](components/background-jobs-api/README.md)

## Outcome

Heavy jobs can run outside the GUI/API host without losing truthful progress,
terminal state, artifacts, cancellation, or bounded-execution diagnostics. A
worker crash must be contained to that worker; the parent remains available and
the job ends in one typed terminal state.

## Current baseline

The engine already has the bounded-execution foundation: adaptive and
profile-aware memory budgets, process RSS policy, admission reservations,
memory-pressure telemetry, byte-budgeted chunk planning, and projection-aware
streaming/chunked execution. Explicit unprojected streaming boundaries remain
valid where they have a named, bounded contract; this roadmap does not revive a
blanket rule that every opaque or unprojected node must fail.

The process-isolation foundation is also present:

- `run_isolated_worker` starts a `spawn` child, returns picklable results, and
  maps remote exceptions, hard exits, timeouts, stop requests, required-cap
  failures, and cleanup failures to typed outcomes.
- The parent owns cleanup and `IsolatedJobSupervisor` maps typed worker
  outcomes into `JobLifecycle` without sharing `JobStore` with the child.
- Tests cover child isolation, remote errors, crashes, timeouts, cancellation-
  style stops, cleanup, memory-cap support, and parent-written lifecycle
  transitions.

Production heavy routes still use in-process background threads. Training
and dispersion estimation keep callbacks and rich result objects in route
threads; optimiser setup/solve retains solver and data-frame objects for
follow-on flows; frontier auto-range and frontier recomputation also run in
threads. Deployed `/quote` scoring runs inside the API process. No production
route currently launches `IsolatedJobSupervisor`.

## Remaining milestones

### 1. Make supervisor terminalisation total

`IsolatedJobSupervisor` currently converts `IsolatedWorkerError` into a
terminal lifecycle transition, but an unexpected parent-side exception can
escape its thread and leave a job marked `running`.

Build tests first for a supervisor-side unexpected exception, a lifecycle
transition race, a malformed child protocol payload, and a cleanup failure
combined with a primary failure. Then make every launched job reach exactly one
valid parent-written terminal outcome when its job record remains writable,
preserving the existing terminal-reason precedence and structured worker fields
where available. If terminal persistence itself fails, surface that
infrastructure failure explicitly instead of silently claiming success.

**Acceptance criteria:** no worker or supervisor failure path leaves a writable
job record running after the supervising thread exits; cancellation, timeout,
crash, protocol error, and cleanup cases retain a typed terminal reason; a late
outcome cannot overwrite a higher-precedence terminal state; and an
unpersistable terminal outcome is observable as a supervisor failure.

### 2. Establish artifact-and-event worker contracts

Define versioned, picklable request, progress-event, result-manifest, and
failure payload contracts. Workers must receive serialisable inputs and return
artifact handles plus lightweight metadata, never `JobStore`, open data frames,
solvers, callbacks, or route-owned mutable state. The parent validates every
event and manifest, updates its own job record, and owns artifact cleanup and
retention. Events need monotonic sequence numbers and a bounded transport so a
chatty worker cannot exhaust parent memory. Manifests must name artifact kind,
schema version, size, integrity metadata, and parent-owned lifetime; publication
must be atomic and confined to a parent-created artifact root.

Build contract tests first for spawn pickling, event ordering and validation,
cancellation while progress is in flight, child crash after artifact creation,
unknown artifact kinds, duplicate/out-of-order or oversized event streams,
partial publication, stale or tampered manifests, path traversal, and
TTL/terminal cleanup.

**Acceptance criteria:** each worker entrypoint can be spawned on Windows;
parent-visible status can be reconstructed solely from typed events and a
manifest; event retention and transport memory are bounded; manifest validation
cannot escape the parent-owned artifact root; artifact lifetime is deterministic
for success, failure, timeout, and cancellation; and no worker requires access
to the in-memory job store.

### 3. Migrate model training and dispersion estimation

Move training preparation, fit/evaluation, and model-output publication behind
the worker contracts. Move the GLM dispersion profile-likelihood job through
the same boundary rather than leaving a second training-data materialisation
path in a route thread. Preserve the current cancellation checks, admission
reservation, bounded loss history, structured execution metrics, model path,
and user-facing training and dispersion responses. The parent should stream
progress into the existing status endpoints and publish only validated final
manifests.

Build route-level tests first for normal completion, cancellation, timeout,
memory limit, child crash, invalid result manifest, and cleanup of training
artifacts. Cover both training and dispersion status/cancellation flows. Include
a Windows-spawn test fixture rather than relying on fork behaviour.

**Acceptance criteria:** a killed training worker cannot take down the host;
the training and dispersion status endpoints preserve progress and a correct
terminal state; all model and temporary artifacts are either retained through
their declared lifecycle or cleaned up; and both routes retain their existing
response and diagnostics contracts.

### 4. Migrate optimiser setup, solve, auto-range, and frontier recomputation

Split optimiser work at explicit artifact boundaries. Setup produces a
re-openable prepared-input manifest; solve publishes serialisable results and
only durable handles needed for supported follow-on actions; auto-range emits
progress and a final range manifest; frontier recomputation reopens declared
solver/input artifacts instead of receiving retained in-memory objects. Preserve
single-flight ownership, cancellation/supersession, admission release,
projection and chunk provenance, and execution metrics in the parent.

Build tests first for setup-to-solve hand-off, retry after cancellation, solver
or child crash, auto-range cancellation mid-stream, malformed or stale
manifests, frontier recomputation after process restart, and cleanup of temporary
partition/output artifacts.

**Acceptance criteria:** setup, solve, auto-range, and frontier recomputation can
be process-isolated without sharing solver/data-frame state; supported follow-on
workflows reopen only declared artifacts; no single-flight or admission
reservation leaks; and each path has deterministic terminal and cleanup
behaviour.

### 5. Decide the deployment enforcement boundary

Document whether deployed API and batch paths require process/container
enforcement in addition to their existing admission and RSS policy. The answer
must name supported platforms, required versus best-effort caps, operator
configuration, failure behaviour when enforcement is unavailable, and how
limits are surfaced to callers. The generated container must select and expose
the intended server policy explicitly rather than inheriting the local adaptive
default by accident. Do not silently claim checkpoint/RSS sampling is a hard OS
or container limit.

Build policy tests first for local versus generated-container modes, live versus
batch scoring, supported and unsupported process caps, concurrent heavy-job
admission, and API-visible memory-limit failures.

**Acceptance criteria:** deployment documentation and runtime configuration
make the enforcement guarantee unambiguous; required limits fail loudly where
they cannot be enforced; and the selected policy is exercised in CI on every
supported platform class.

## Dependencies and ownership

This roadmap consumes the engine's existing bounded-execution, projection,
chunking, telemetry, and admission primitives. It should use the
[execution-engine contract](../specs/execution-engine/high-level.md) when a
worker needs an explicit streaming boundary, but does not redefine planner
semantics.

The [backend execution hardening](backend-execution-hardening.md) roadmap owns
cross-cutting lifecycle guardrails, fault-injection infrastructure,
observability, and benchmark/scale gates. This roadmap owns worker protocol
design, route migration, worker terminal semantics, artifact boundaries, and
deployment isolation policy. Changes that touch both must keep the ownership
split explicit in their tests and docs.

## Non-goals

- Replacing bounded projection, streaming, chunking, admission, or RSS policy
  with process isolation. Isolation is a containment layer, not the primary
  memory strategy.
- Sharing the current in-memory `JobStore`, callbacks, data frames, or solver
  objects across a spawn boundary.
- Treating every unprojected graph boundary as invalid when it has an explicit,
  bounded streaming contract.

## Retirement criteria

Retire this roadmap when training, dispersion estimation, optimiser setup/solve,
auto-range, and frontier recomputation use the typed worker contracts where
isolation is selected; all worker outcomes terminalise in the parent; artifact
cleanup is proven across every terminal path; and the deploy/API enforcement
decision is implemented and documented.
