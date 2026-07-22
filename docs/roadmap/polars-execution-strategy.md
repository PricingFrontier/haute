# Polars execution strategy

**Status:** Active

**Current as of:** 2026-07-20

**Implementation plan:**
[`F_0.6.0_polars-backend-remediation.plan.md`](../trip/plans/F_0.6.0_polars-backend-remediation.plan.md)

## Outcome

Every execution surface should choose and report the narrowest semantics-preserving
strategy for a Polars graph. A graph must either run within its declared memory
budget or fail with a typed, actionable reason; an uncertain static projection
must never cause a hidden full-width or eager fallback.

## Current baseline

- `haute.projection` is the shared projection-planning source of truth. It
  supports exact demand, schema-derived `AllExcept` demand, per-parent join
  demand, strict-profile unprojected streaming boundaries, and structured
  reason payloads.
- `haute.execution` exposes `plan_execution_strategy` and
  `plan_prepared_execution_strategy`. Lazy execution calls the prepared facade
  when projection analysis is triggered, and the chunk planner uses the same
  seam; training, optimiser, sink, and deploy-batch paths inherit that planning
  where they run through lazy or chunked execution. Initial eager preview
  without requested columns and seedless deploy-live execution do not yet enter
  the planner; requested-column eager preview uses an executor compatibility
  wrapper instead of the public execution facade. The existing facade is
  deliberately thin, rather than a first-class strategy algebra.
- Training derives schema-based `AllExcept` feature demand, while model scoring
  can narrow to model features. Existing planner, CatBoost-demand, lazy
  execution, optimiser, chunk-runner, and frontend contract tests cover this
  baseline.
- When an execution context receives a shared plan, its metrics carry bounded
  strategy summaries and opaque-boundary reasons. The UI validates that payload
  and presents memory-pressure diagnostics, but does not yet explain selected
  strategies or rejected alternatives to users.
- `BoundedChunkReducer` and the chunk runner provide a generic bounded-reducer
  protocol. They are **not** a catalogue or implementation of safe group-by
  partial/final aggregation.
- Group-by dependency inference and partial/final reduction are not implemented.
  Group-by code is excluded from chunk-local planning and remains on an admitted
  non-chunked path, an explicit opaque boundary, or a typed rejection according
  to the execution profile and graph shape.

## Remaining work

### 1. Decide and codify the strategy model

Choose one explicit design and document it in code:

- promote the present projection-plan states into a first-class, typed strategy
  result with a closed set of strategies and capability evidence; or
- retain the projection-based design and explicitly define its stable strategy
  vocabulary, profile semantics, and ownership boundaries.

Do not add a second planner alongside `haute.projection`.

Resolve the current non-strict fan-out case as part of this contract: when a
caller supplies a seed at a node with opaque demand from multiple consumers,
the planner must either preserve it safely or report why it cannot apply. It
must not leave the seed indistinguishable from an unseeded full-width result.

Audit `haute.projection`, `haute.execution`, their exported names, and their
docstrings at the same time. Comments must describe implemented behaviour, not
future strategy types; only the route-facing facade and deliberately supported
diagnostic types should be presented as stable API.

**Tests first:** table-driven tests for every strategy, profile, reason, and
unsupported result, including strict and non-strict multi-consumer seeds;
conformance tests prove callers receive the same result for the same prepared
graph, and API-hygiene tests pin the deliberately public surface.

**Done when:** one public execution-planning contract is the sole route-facing
entry point, has versioned JSON-safe diagnostics, and a change in strategy is
observable in tests and metrics.

### 2. Make planner coverage universal

Complete strategy conformance across preview, training preparation, optimiser
estimate/setup/solve/auto-range, sinks, deploy batch scoring, and deploy-live
scoring. In particular, move requested-column eager preview off its private
compatibility wrapper, and cover initial preview and deploy-live execution when
no caller supplies a projection seed.

**Tests first:** a shared graph fixture matrix covers exact projection,
schema-derived all-except, contract-free fan-in, a supported opaque boundary,
and an unsupported shape for each surface. Assert both output semantics and
the selected strategy/reason.

**Done when:** no execution entry point owns private projection rules; every
surface either calls the shared facade or is explicitly excluded with a tested,
documented reason.

### 3. Explain boundary cost and training feature choice

Extend metrics for unprojected boundaries with streamability evidence, input
and output width, requested versus physically scanned columns, bytes read and
written where the I/O backend can report them, estimated bytes,
chunk/checkpoint counts, and observed peak RSS. Add per-column demand provenance
so a retained column can be traced to its seed, contract, expression, join key,
or conservative boundary. Add training diagnostics that state the final ordered
feature set or count, retained metadata columns, and excluded columns with their
reasons.

**Tests first:** verify payload shape, size bounds, deterministic feature order,
explicit-feature and all-except training, missing-column failures, physical
source projection, supported I/O byte counters, provenance across fan-out and
fan-in, and no collection before validation. When a backend cannot expose a
counter, require an explicit unavailable state rather than a fabricated zero.

**Done when:** a user can determine why a boundary was accepted or rejected and
why a training column was used or excluded without reading server logs.

### 4. Resolve group-by semantics deliberately

Make a product decision for associative group-by partial/final reducers:

- if supported, implement a narrowly specified reducer catalogue with exact
  Polars-equivalence tests for group keys, nulls, dtypes, empty inputs, and
  partition order; or
- record group-by chunked reduction as out of scope and reject every unsupported
  aggregate shape loudly with its blocking operator and profile.

Order-sensitive and non-associative operations must not be approximated. Small
eager execution remains available only after normal admission checks.

**Tests first:** for every candidate aggregate, compare full execution with
partitioned execution across group keys, nulls, dtypes, empty inputs, and
partition order. If the decision is to defer reducer support, prove every such
shape stays off the chunk runner and receives the documented admitted or typed
rejection outcome on each relevant profile.

**Done when:** every group-by plan is either assigned a tested safe strategy or
returns a typed rejection; no generic reducer is represented as group-by
support.

### 5. Make planning useful in the product

Expose compact strategy and rejection diagnostics in preview, training, and
optimiser flows: selected strategy, blocking node/operator, profile, boundary
cost, and a concrete remediation. Keep raw diagnostic payloads available for
support without making the normal UI noisy.

**Tests first:** backend/frontend schema contracts plus component tests for
projected, boundary, and unsupported states, including truncated payloads and
unknown future fields.

**Done when:** users can tell whether a run is projected, boundary-based,
admitted eager, or rejected—and what to change—without inspecting JSON.

### 6. Define Polars scale scenarios and close public documentation gaps

Define deterministic, generated 1m-row and 10m-row join-plus-training scenarios
for the shared backend performance harness. This roadmap owns their Polars
semantics and invariants: output equivalence, selected strategy, source width,
no accidental full-width collect, and the distinction between a budgeted
rejection and a successful bounded run. Keep smaller semantic equivalents in
the always-on suite. The backend-hardening roadmap owns the runner, environment
capture, peak-RSS measurement, artefact schema, and threshold calibration.

Update public documentation and remove comments that describe deferred or
future behaviour as present behaviour. Cover contracts, common joins,
all-except training, boundaries, group-by support or rejection, and reading
execution diagnostics.

**Tests first:** prove deterministic scenario generation, output parity,
strategy/rejection assertions, and full-width-collect guards at CI size before
running the large variants. Back every public example and stated limitation with
a focused executable test.

**Done when:** the shared scale suite runs the Polars scenarios with documented
hardware-independent semantic invariants, and public examples are backed by
focused tests.

## Dependencies and boundaries

This roadmap owns planner semantics, Polars capabilities, feature-demand
diagnostics, user-facing strategy information, and Polars-specific scale
scenarios and invariants. It depends on
[worker isolation](worker-isolation.md) only where a selected execution surface
moves into a worker; worker lifecycle, cancellation, artifact transport, and
supervision remain that roadmap's responsibility.

[Backend execution hardening](backend-execution-hardening.md) owns cross-cutting
fault injection, lifecycle hygiene, telemetry plumbing, the shared performance
runner, benchmark artefacts, and release hardening. This roadmap supplies the
Polars-specific strategies, metrics, conformance fixtures, and 1m/10m workload
invariants those checks exercise; it does not duplicate the harness or gates.

Physical partition pruning is intentionally outside this roadmap. The current
planner models column demand, not predicate or partition demand, and Polars may
optimise a lazy scan without Haute being able to attribute that optimisation.
Metrics and public docs must not infer partition pruning from column projection.
Re-open it only if source predicates become an explicit planning input with a
backend-observable conformance contract.

## Completion criteria

The roadmap is complete when all supported execution surfaces share one tested
planning contract; planner results are visible and actionable; group-by has an
honest supported/rejected boundary; and shared 1m/10m scale runs demonstrate no
unadmitted eager or full-width materialisation for their supported pipelines.
