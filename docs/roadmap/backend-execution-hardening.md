# Backend execution hardening roadmap

**Status:** Active

**Current as of:** 2026-07-20

## Outcome

Make the execution engine demonstrably safe to evolve: one strict contract and
error boundary, consistent admission and lifecycle semantics, measurable resource
behaviour, and regression tests that exercise failures rather than only happy
paths. This roadmap is deliberately the remainder after the delivered execution
foundation; it is not a rewrite plan.

## Current baseline

The engine already has the major foundations in place: bounded stage metrics and
admitted execution contexts, projection and chunk-planning seams, bounded
collect/sink helpers, request-scoped Polars chunk-size control, preview cache
cleanup, typed long-running job lifecycle, source-boundary checks, and bounded
deploy, training, and optimiser setup paths. The dependency constraint is
currently `polars>=1.39.3,<2`.

Performance and dependency plumbing also exist. `scripts/run_perf_suite.py`
already runs the opt-in Python performance lane and writes versioned JSON,
Markdown, and JUnit artifacts; `scripts/memory_smoke.py` can sample child peak
RSS. The dependency-floor and scheduled unlocked-resolution lanes exercise a
high-signal core subset, including Polars interface checks. The missing pieces
are an execution-engine counter/RSS envelope shared by the domain scale suites
and a focused lower/current-1.x bounded-execution conformance lane. See the
[performance checks](../PERFORMANCE_CHECKS.md) for the current commands.

The remaining work is hardening around those seams. In particular,
`_effective_contract` can still turn selected resolution failures into an opaque
contract while the projection planner reports them loudly; eager execution also
contains selective preamble-error reinjection for only `POLARS` and
`LIVE_SWITCH`. Optimiser estimate creates a profile-bearing context without
going through admission, training status polling still contains a direct
terminal-status write, and completed optimiser jobs can retain multiple heavy
sets until their time-based expiry. A few request-local and status paths still
need the same admission, terminal-reason, and heavy-object-cleanup discipline as
the long-running paths.

The earlier ratebook factor-source memory gap is closed: factor input is
projected, preflighted, persisted as parquet, and consumed through the chunked
factor-context builder. The open optimiser issue here is bounded retention of
completed-job solver/grid/context state across repeated solves, not a return to
full-factor-source collection.

## Scope and ownership

This roadmap owns cross-cutting execution safety. It does **not** own:

- migrating training, optimiser, or auto-range work into isolated workers; see
  [Worker isolation](worker-isolation.md);
- defining physical execution strategies, group-by reducers, or user-facing
  strategy diagnostics; see [Polars execution strategy](polars-execution-strategy.md);
- defining richer per-column projection provenance or the compatibility policy
  for non-strict/no-seed surfaces; those are planner semantics owned by the
  Polars roadmap;
- defining the Polars-specific 1m/10m join-and-training workloads or their
  strategy invariants; that roadmap owns those fixtures, while this roadmap
  owns the common report envelope and release aggregation;
- new business capabilities or a global priority queue/disk-budget subsystem.

The last item is an intentional constraint. Reconsider scheduling or disk
admission only with measured evidence that the current one-process local-engine
model is insufficient; do not add it pre-emptively.

Several older aspirations are explicit decision gates, not hidden omissions:

- the current `ExecutionContext` already has a `ContextVar`, separate metrics
  recorder, injected sampler, and external admission provider. Milestone 1
  measures the remaining coupling and RSS-sampling cost before splitting more
  types merely for structural symmetry;
- process-wide execution queueing and disk admission remain out of scope while
  the product is a one-user local engine. Milestone 3 must record rejection
  rates and temporary-disk high-water marks; measured contention or exhaustion,
  or a multi-user requirement, triggers a separate scheduler/budget decision;
- deploy JSON bodies remain size-limited and admitted rather than streamed.
  A streamed upload contract is a product/API change, not execution hardening;
- exhaustive dtype/nullability contracts for every node are not a blanket
  milestone. Source schemas, join-key compatibility, and train/score parity are
  enforced today; extend typed boundaries when a supported operation needs
  them or invariant testing exposes a gap.

## Milestone 1 - Canonical execution boundary

Unify the answer to "is this graph executable under this profile?" across
projection, lazy execution, eager preview, and route entry points.

1. Define one strict contract-resolution result which carries either the
   effective contract or a typed resolution failure. Remove the divergent
   opaque-on-error behaviour where it would permit a bounded path to proceed
   without an enforceable contract.
2. Define preamble failure propagation once, including model-score and other
   nodes that consume preamble bindings. A failure must be either an explicit
   request error or an explicitly unaffected path; it must never be silently
   deferred to an unrelated downstream failure.
3. Audit preview, trace, sink, deploy, optimiser estimate, training, and
   request-local writes so each receives an explicit execution profile,
   admission decision, typed error mapping, and cleanup owner. Replace direct
   terminal-status mutation where the shared lifecycle contract applies.
4. Make status publication release or evict request-local/heavy data according
   to an owned lifecycle, without exposing internal objects in status payloads.
5. Measure the remaining `ExecutionContext` coupling. Preserve the existing
   `current_execution_context()` access for nested helpers and user code; add an
   explicit no-RSS-sample stage mode where no memory guarantee depends on the
   sample. Split cancellation, budget, and metrics objects further only if
   tests demonstrate an independent lifetime, injection, or ownership need.
6. Complete the source boundary promised by the adapter: support partitioned
   Hive-style parquet through one explicit path, prove projected columns and
   partitions are pruned, and report source bytes/columns without a second
   materialising scan.

**Tests first**

- Add a route/profile matrix for contract-resolution, preamble, cancellation,
  memory-limit, and internal failures; assert status code, terminal reason, and
  bounded metrics payload.
- Add paired planner/executor tests proving the same malformed or unavailable
  contract fails with the same typed result on every relevant surface.
- Add lifecycle tests for completion, error, cancellation, timeout, and a
  concurrent status poll, asserting no retained request-local frame, cache pin,
  or temporary artifact.
- Add nested-context and user-code cancellation tests, plus a stage test proving
  the no-sample mode never calls the RSS sampler and does not weaken a
  memory-budgeted stage.
- Add partitioned-parquet fixtures covering projected reads, partition-column
  filters, missing columns, mixed schemas, empty partitions, and bounded-profile
  failure before materialisation.

**Acceptance criteria**

- No bounded-memory profile continues after contract resolution fails.
- Equivalent graph failures have one typed cause and one user-visible mapping
  regardless of execution surface.
- All audited entry points either use the common lifecycle/admission boundary or
  have a documented, tested reason not to.
- Context composition has a measured, recorded decision; helpers can reach the
  correct active context, and RSS sampling is paid only where its contract
  requires it.
- Partitioned parquet has one tested adapter contract, with pruning and source
  counters verified by controlled I/O instrumentation rather than brittle
  `explain()` string matching alone.

## Milestone 2 - Fault injection and cleanup SLOs

Turn cancellation and cleanup guarantees into repeatable fault tests, including
native-operation boundaries where cancellation is cooperative.

1. Provide a deterministic fault harness for collect, sink/checkpoint,
   reducer, response shaping, and terminal transition boundaries.
2. Measure cancellation latency per profile and publish profile-specific limits
   only after baseline runs. While native work is active, status must remain
   `cancelling`; it may become terminal only at a cooperative boundary.
3. Exercise cleanup when failure occurs before, during, and after each owned
   artifact is registered, including races with cancellation and supersession.

**Tests first**

- Inject cancellation at each named boundary and assert terminal precedence,
  elapsed time, cleanup, and absence of error wrapping.
- Inject filesystem, parquet, metrics-serialization, and status-store failures
  and assert typed propagation plus idempotent cleanup.

**Acceptance criteria**

- CI enforces a recorded cancellation-latency budget for every supported
  profile, with controlled test doubles for non-deterministic native work.
- Every injected terminal path leaves no owned temporary file, cache pin, or
  live heavy-object reference.

## Milestone 3 - Reproducible scale evidence and metric completeness

Extend the existing performance lane so resource claims are versioned,
reviewable, and comparable across execution profiles.

1. Extend the `run_perf_suite.py` report schema rather than creating a second
   runner. Attach environment identity, dependency versions, input shape,
   execution profile, peak RSS from the memory-smoke helper, bytes
   read/written, collect/checkpoint counts, temporary-disk high-water mark,
   admission/rejection counts, and metrics-payload size.
2. Provide deterministic, small cross-profile smoke fixtures for preview,
   sink, deploy batch, training preparation, optimiser setup, and auto-range.
   Consume
   domain-owned scale fixtures, including the Polars roadmap's 1m/10m cases,
   through the same report envelope instead of duplicating them here.
3. Account for route-handler wall time explicitly: report bounded stage time
   plus a named `unaccounted_ms` remainder. Populate row/byte counters when the
   boundary already knows them; never trigger an extra scan merely to fill a
   metric.
4. Keep a small CI smoke gate and a reproducible local scale lane. Pin numeric
   budgets only after collecting stable baselines; reject regressions outside
   a documented noise envelope.

**Tests first**

- Test the extended report schema, backward-incompatible schema-version changes,
  deterministic fixture generation, and merging of performance-runner,
  memory-smoke, and execution-metric data before setting thresholds.
- Test that the smoke suite records peak RSS and execution counters rather than
  relying on elapsed time alone, and that unavailable counters remain explicit
  `null` values rather than invented zeros.
- Test wall-clock attribution on canonical fixtures with a recorded tolerance.

**Acceptance criteria**

- Reviewers can compare an artifact from two revisions without reconstructing
  the workload or environment.
- Every cross-profile smoke run reports peak RSS and deterministic execution
  and source counters, temporary-disk use, and admission outcomes; route wall
  time is fully partitioned into stage time plus `unaccounted_ms` within the
  pinned tolerance.
- Domain scale suites publish through the same schema and report peak RSS rather
  than claiming bounded memory from a streaming flag alone.

## Milestone 4 - Compatibility, invariant, and telemetry testing

Exercise the engine's seams across valid graph shapes and both supported Polars
endpoints.

1. Add a deterministic graph/property generator for valid DAGs, declared
   fan-in, contracts, and execution profiles. Assert planner determinism,
   typed rejection for unsupported shapes, and no unexpected exception or
   unintended broad collect.
2. Add a focused conformance lane against the lower supported Polars release
   (`1.39.3`) and a deliberately pinned current upper `1.x` release. This
   complements, rather than replaces, the existing all-dependency floor and
   unlocked-resolution lanes. Cover the bounded collect/sink helper, streaming
   chunk-size scope, projection, and canonical fixture outputs.
3. Emit bounded structured execution events from the existing metric schema at
   named heavy and terminal boundaries. Then add optional OpenTelemetry export
   behind explicit configuration, mapping only bounded, redacted fields to
   spans and testing an in-memory exporter end-to-end.
4. Decide whether long jobs need a persisted, versioned trace artifact after
   structured events and telemetry are measured. If added, it must have a size
   cap, explicit retention owner, and the same terminal cleanup guarantees as
   other artifacts; status payloads remain the default bounded surface.

**Tests first**

- Seed every generated failure so it can be replayed as a regression fixture.
- Snapshot conformance output and error taxonomy on both matrix endpoints.
- Assert structured event names, redaction, payload limits, and terminal-reason
  parity with the source execution metrics.
- Assert disabled telemetry has no exporter dependency or behavioural effect;
  assert enabled telemetry emits the documented span names and attributes.
- Test the trace-artifact decision: either retention/cleanup conformance for an
  implemented artifact or a documented no-artifact contract guarded against
  accidental unbounded trace writes.

**Acceptance criteria**

- A generated graph cannot make the planner or chunk runner panic; unsupported
  semantics fail with a named diagnostic.
- Both Polars endpoints satisfy the same bounded-execution contract.
- Structured events and optional telemetry are redacted, bounded in
  cardinality, and traceable back to the existing execution metric schema.
- Long-job trace retention has one explicit, tested policy; no unbounded or
  ownerless trace file is introduced.

## Milestone 5 - Startup and request-local housekeeping

Make cleanup resilient to process death and to ordinary short-lived requests.

1. Introduce a server-startup reaper for only Haute-owned temporary artifact
   directories. Require an ownership marker and stale-process evidence before
   removal; never glob-delete arbitrary system temporary paths.
2. Enumerate request-local temporary files, cached response materialisations,
   and `JobStore` heavy keys. Give every item a creator, owner, terminal cleanup
   path, and test-only inspection hook.
3. Bound completed-job heavy retention by ownership key as well as time. At
   most one unpinned heavy optimiser set may remain per graph/node setup key;
   any explicitly pinned interactive session needs a count/expiry cap visible
   to admission and status diagnostics.
4. Add cleanup verification for successful requests as well as failures, so a
   busy interactive session cannot retain old frames until process exit.

**Tests first**

- Create synthetic owned and unowned temporary directories with live and stale
  owner markers; prove startup removes only the stale owned case.
- Complete several optimiser jobs for the same and different setup keys; prove
  older same-key heavy objects are released, different-key state follows the
  configured bound, metadata and artifact handles remain usable, and pins
  cannot extend retention without limit.
- Run repeated request/status-poll cycles and assert heavy-object expiry and
  artifact cleanup without changing public response data.

**Acceptance criteria**

- A restart reclaims stale Haute artifacts safely and preserves unrelated
  temporary data.
- Request-local and heavy-object retention is observable, count- and
  time-bounded, visible to admission where it consumes memory, and covered by
  success, failure, cancellation, pin, supersession, and expiry tests.

## Delivery order and dependencies

1. Complete the canonical boundary first; fault expectations are meaningless
   while surfaces disagree about errors or lifecycle ownership.
2. Add fault injection immediately after it, then use those tests to protect
   cleanup work.
3. Extend the reproducible performance artifact in parallel with the focused
   compatibility matrix, using the canonical contract as their shared oracle.
4. Add bounded structured events and optional telemetry after the metric schema
   and artifact envelope are stable.
5. Finish with startup/request-local housekeeping, validated by the fault suite
   and repeated-session scale runs.

## Completion and retirement

This roadmap can retire when all execution entry points use the canonical
contract/lifecycle boundary; fault, scale, fuzz, and version-matrix checks run
in their appropriate CI or reproducible local lanes; structured events and
opt-in telemetry are schema-aligned; trace retention has an explicit bounded
policy; and restart/request cleanup is safe and tested. At that point, keep the
conformance and benchmark artifacts as maintained engineering evidence rather
than another historical implementation log.
