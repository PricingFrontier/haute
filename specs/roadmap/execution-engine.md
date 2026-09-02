# Execution engine roadmap

## Scope

User-authored Polars transforms should work consistently across Data Output,
preview, Explore, modelling, optimiser, and deploy workflows. Static analysis
should improve projection, chunking, admission accuracy, and diagnostics; an
analysis gap should not by itself make valid Polars functionality unavailable.
This roadmap owns operation-effect analysis, materialisation admission, chunk
eligibility, warned conservative execution, the worker boundaries that make
warned execution enforceable, and bounded preparation of input formats.
Current behaviour is specified in
[the execution-engine specification](../execution-engine/high-level.md) and
[the IO-layer specification](../io-layer/high-level.md).

The governing invariant is containment before optimisation. Haute should
support as much sandbox-valid Polars as possible. Proven plans use the narrowest
streaming, projection, chunking, or estimated-materialisation strategy. When a
proof is unavailable and the run already executes inside a hard-capped isolated
worker, production pipelines continue by default through a full-width,
pessimistically admitted worker with enforceable memory, timeout, cancellation,
and publication boundaries. The run emits a structured warning; it never
silently claims that the fallback is bounded by an estimate. A surface that
still runs in a server thread or in-process has no enforceable cap, so an
unknown estimate or effect remains a typed rejection there until that surface
gains a worker.

A warning is not permission for unsafe execution. Haute may still reject before
user work when it cannot reserve a positive conservative envelope, install the
required hard worker limit, or guarantee that a result is published at most
once. Invalid code or contracts remain errors, and a worker that actually
exceeds its memory/time envelope terminates with a typed failure. These are
resource, isolation, or semantic failures, not operation-name or profile
allowlists. Global operations are never executed independently in each chunk.

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| EXEC-P04 | Planned | P2 | Give the pinned Polars surface one support-first execution policy and certification suite. |
| EXEC-P05 | Decision | P2 | Build snapshots for eager-only or schema-unknown inputs automatically inside a warned hard-capped worker. |
| EXEC-P06 | Planned | P2 | Run training preparation and multi-row deploy scoring inside hard-capped workers so the warned policy reaches them. |

## Planned improvements

`EXEC-P04` is the next startable package. The operation-effect proof, the
warned, hard-capped conservative execution policy, and the receiver-aware chunk
classifier that it builds on are current behaviour specified in the
execution-engine and optimiser specifications: an unavailable estimate runs
once under the full reserved envelope on the worker-backed surfaces (Data
Output writes, preview and trace, Explore, JSON cache builds) and remains a
typed rejection elsewhere, and a chunk-ineligible auto-range suffix falls back
to the full-lazy path with a recorded warning. `EXEC-P06` extends the
conservative policy to the thread-backed modelling and deploy surfaces;
optimiser surfaces wait for `ROAD-WORKER-04`.
`EXEC-P05` requires an explicit architecture decision and is not startable
until the specification amendment it names is approved. Each package extends a
closed, tested optimisation model while retaining a general conservative
execution path. No package creates an unbounded in-process eager fallback.

### EXEC-P04 — Certify a support-first Polars operation policy

**Why:** Group-by has an explicit materialisation boundary and memory gate, but
other whole-frame or stateful operations are classified inconsistently. Dynamic
group-bys, joins, sort, unique, pivot or unpivot, explode, rolling or window
operations, and opaque batch callbacks are rejected by chunk planning yet do
not all receive an equivalent full-lazy preflight policy. This is both a user
consistency problem and a robustness gap.

**Plan:** Make one receiver-aware Polars operation registry authoritative for
lineage/cardinality analysis, projection, admission, and chunk planning. Record
whether an operation is row-local, order-dependent, fan-in stateful,
row-expanding, or opaque. Prefer inspection of the sandbox-built Polars lazy
plan where it is stable and sufficient; use source AST evidence for authored
contracts that the Polars plan cannot retain. Comments, literals, aliases, and
non-Polars methods must not create false classifications.

For each recognised global operation, select the best policy supported by
evidence: proven streaming or spill-safe execution; an operator-specific
admitted materialisation estimate; or one admitted pre-materialisation followed
by a proven chunk-local suffix. Every other sandbox-valid operation defaults to
the current pessimistically admitted, hard-capped conservative execution with a
warning on worker-backed surfaces, and to the warned typed rejection elsewhere,
never to an operation-name rejection. Prioritise complete common coverage
across `LazyFrame` and expression string, temporal, list, struct, aggregation,
join, rolling, window, reshape, and callback namespaces, including dynamic
group-by, sort/unique, bounded and as-of joins, pivot/unpivot, `explode`,
`map_elements`, and `map_batches`. Do not treat every global operation as if
it were a group-by.

Remove profile names from semantic proof decisions. Given the same graph and
inputs, workflows may differ because their memory/time budgets and requested
outputs differ, but not because one profile has an unrelated operator
allowlist. If projection cannot be proved but full-width execution has an
admissible bound, retain all columns at the boundary; contradictory declared
contracts remain hard errors. The optimiser's input planning, which OPT-P13
extracts into its own module, consumes this registry through the shared
planner and keeps no operator list of its own.

Maintain a version-pinned compatibility corpus that records whether each
representative standard Polars shape is optimised or uses the warned fallback.
An upgrade may improve that classification, but cannot silently turn a working
shape into a rejection. Newly encountered operations enter the warned fallback
automatically; adding them to an optimisation class still requires proof.

**Acceptance:** A table-driven cross-profile contract covers every registered
operation and verifies identical classification across Data Output, preview,
Explore, modelling, optimiser, and deploy. Full-versus-planned equivalence
tests cover ordering, schema, row multiplicity, and multi-input column
retention. Peak-memory calibration or spill evidence supports every newly
admitted global policy. False positives on comments, literals, and non-Polars
methods are absent; aliases and ordinary chained Polars calls are detected.
Unknown operations execute through the conservative envelope on worker-backed
surfaces and report stable, bounded, sanitised warning codes and actionable
remediation on every user-facing surface. Warnings state when projection,
chunking, streaming, or estimate-based admission was unavailable and
distinguish that from an actual execution failure.

The compatibility corpus runs against every supported workflow and includes
representative standard operations from each maintained namespace. A Polars
version upgrade must rerun it and cannot merge if an operation changes value,
schema, ordering, warning visibility, or working-to-rejected status without an
approved specification change. Hard-failure tests prove that no fallback runs
without an enforceable reserved envelope and that resource exhaustion cannot
damage the server or publish partial results.

**Dependencies:** The current chunk-classifier decision contract, the
conservative-execution policy, the projection and execution strategy
contracts, and the Polars version pin.

**Evidence:** `src/haute/projection.py`; `src/haute/_column_lineage.py`;
`src/haute/chunking.py`; `src/haute/execution.py`;
`tests/test_projection_planner.py`; `tests/test_projection_lineage_integration.py`;
`tests/test_execute_lazy.py`; `tests/test_polars_backend_strategy_contract.py`;
`tests/performance/test_execution_engine_certification.py`.

### EXEC-P05 — Build snapshots for eager-only inputs automatically

**Why:** Every non-Parquet Data Input already executes from an immutable
snapshot generation, and the explicit snapshot build already accepts
eager-only formats such as plain JSON through an admitted-eager build class.
The remaining friction is that the build is explicit, so a bounded workflow
that meets a missing or stale generation fails and the user must go back and
trigger a build; that the admitted-eager build runs in a server thread under
RSS-sampled admission rather than a hard cap; and that CSV without a complete
declared schema is refused outright. Those refusals prevent hidden full-file
reads, but the round trip is avoidable friction.

**Decision required:** The IO-layer specification states that the component
"keeps source acquisition separate from pipeline execution", and the
execution-engine specification states that "graph execution never builds or
refreshes a snapshot". This package relaxes both: execution may schedule a
snapshot build, but only through the existing build path, inside a hard-capped
worker, with the generation store's leases, quotas, atomic publication, and
source-signature identity unchanged. The package is not startable until that
amendment is approved in both specifications.

**Plan:** When a bounded workflow needs a generation that does not exist or
whose source signature is stale, plan an automatic build through the existing
`build_input_snapshot` path before execution starts, run it in an admitted
hard-capped worker, and publish atomically. Emit a warning before the build
where planning has enough information, retain its actual cost/status in
terminal diagnostics, and let the pipeline continue without a confirmation
step. Bounded workflows then scan the generation and reuse it while its
signature remains valid. Run the admitted-eager build class on the same
hard-capped worker instead of an in-process thread.

For CSV, schema discovery must inspect the admitted source completely or use a
declaration; sample-only inference must not silently mis-type later rows. When
a scanner exists but eager `read` mode was selected, warn and scan the source
directly if the configured read arguments are accepted by the scanner; build a
snapshot only when they are not. Automatic preparation is visible and
contained, never an unbounded or unreported materialisation.

**Acceptance:** Prepared and direct reads have schema/value parity for valid
inputs. Tests cover mixed late types, malformed records, source mutation,
cache invalidation, concurrent preparation, cancellation, timeout, and
out-of-memory cleanup. Failed preparation publishes no generation; successful
publication is atomic and subsequent bounded profiles use only the scanner.
Diagnostics expose the automatic build, reserved limit, cache reuse, and
remediation without leaking secrets or unsafe paths. All supported formats run
when a build envelope can be reserved; a source is rejected only when the
build cannot be isolated/admitted or its parsing semantics are invalid.

**Dependencies:** The approved specification amendment above, the current
hard-capped conservative execution policy, the IO registry, the source-cache identity and
cleanup contracts, worker isolation, and execution admission. The IO-layer
specification must own the user-visible preparation lifecycle before
implementation.

**Evidence:** `src/haute/_polars_io_registry.py`;
`src/haute/_input_providers.py`; `src/haute/_source_cache.py`;
`src/haute/routes/input_cache.py`; `tests/test_polars_io_registry.py`;
`tests/test_io.py`; `tests/test_source_cache.py`;
`tests/test_input_cache_route.py`; `tests/test_worker_isolation.py`.

### EXEC-P06 — Run training preparation and multi-row deploy in hard-capped workers

**Why:** Training preparation runs in a server thread, and deploy scoring runs
in-process inside the generated service. Both rely on RSS-sampled admission,
which the execution-engine specification treats as supervision rather than a
kernel cap, so the warned conservative fallback cannot exist there and unknown
shapes remain typed rejections on the workflows where users most often author
custom Polars. The training fit already runs in a protocol worker; the
preparation that executes the pipeline does not.

**Plan:** Run training preparation inside a hard-capped worker with the same
admitted budget envelope, native cap, timeout, cancellation, and
result-transport rules the Data Output writer uses, and hand its result to the
training worker through a file-backed artifact rather than an in-memory frame.
Route multi-row deploy requests, which already select the batch profile,
through an isolated or warm hard-capped worker inside the generated service.
Keep single-row live scoring in-process for latency, and validate the graph's
execution policy at bundle build time so an unanalysed shape is reported once
when the bundle is built rather than on every request. Optimiser workflows
remain thread-backed until ROAD-WORKER-04's activation trigger is met and keep
the typed rejection with the conservative policy's warning content.

**Acceptance:** Training preparation and multi-row deploy prove the same
memory-exhaustion, timeout, cancellation, worker-death, and publication-failure
outcomes as the Data Output writer, release admission exactly once, and leave
no partial artifact. The conservative-execution cross-surface suite then passes on those
surfaces without changes to its expectations. Bundle validation reports the
warned policy exactly once, and live scoring adds no per-request process
spawn. Preparation-to-training hand-off has value/schema parity with the
in-thread path.

**Dependencies:** The current conservative-execution policy, the
worker-isolation and result-transport contracts, and the training and deploy
publication contracts. Optimiser
isolation stays with ROAD-WORKER-04.

**Evidence:** `src/haute/routes/_training_lifecycle.py`;
`src/haute/routes/_training_preparation.py`; `src/haute/deploy/_scorer.py`;
`src/haute/deploy/_container.py`; `src/haute/_worker_isolation.py`;
`tests/test_modelling_routes.py`; `tests/test_deploy_internals.py`;
`tests/test_worker_isolation.py`.
