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
| EXEC-P05 | Decision | P2 | Build snapshots for eager-only or schema-unknown inputs automatically inside a warned hard-capped worker. |
| EXEC-P07 | Planned | P2 | Admit sort, unique, join, and window materialisation with peak-memory evidence instead of unproven streaming. |
| EXEC-P08 | Planned | P3 | Make the schema-only execution declaration enforceable for graphs that end in an OUTPUT document. |

## Planned improvements

`EXEC-P07` is the next startable package. The operation registry, the
profile-independent projection planner, the operation-effect proof, the
warned, hard-capped conservative execution policy, the receiver-aware chunk
classifier, the version-pinned compatibility corpus, and the hard-capped
training-preparation and multi-row deploy workers that it builds on are
current behaviour specified in the execution-engine, modelling, deploy, and
optimiser specifications: an unavailable estimate runs once under the full
reserved envelope on the worker-backed surfaces (Data Output writes, preview
and trace, Explore, JSON cache builds, training preparation, multi-row deploy
scoring) and remains a typed rejection elsewhere, and a chunk-ineligible
auto-range suffix falls back to the full-lazy path with a recorded warning.
Optimiser surfaces wait for `ROAD-WORKER-04`.
`EXEC-P05` requires an explicit architecture decision and is not startable
until the specification amendment it names is approved. Each package extends a
closed, tested optimisation model while retaining a general conservative
execution path. No package creates an unbounded in-process eager fallback.

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

### EXEC-P07 — Admit global operations beyond group-by with memory evidence

**Why:** The operation registry records `sort`, `unique`, `join`, `join_asof`,
window expressions, rolling and dynamic group-bys, `explode`, and `unpivot` as
streaming through the lazy engine without per-operator admission. Polars'
streaming engine buffers the build side of a join, the whole input of a sort,
and every window partition in memory, so those operations are only as bounded
as the host. Unlike group-by they have no estimate-based gate and no
conservative envelope, and the logical plan Polars prints cannot show which
nodes fall back to in-memory execution.

**Plan:** For each registered fan-in stateful or order-dependent frame
operation, measure peak RSS in the performance lane over representative widths
and row counts with the pinned Polars version, then select the narrowest policy
the evidence supports: proven streaming or spill-safe execution (keep the
`streaming` policy and record the evidence), an operator-specific admitted
materialisation estimate (the cardinality bound times physical width, with a
join's build side sized from its own port), or one admitted pre-materialisation
followed by a proven chunk-local suffix. Change a registry policy only with
that evidence, add the operator to the planner's boundary set, and extend the
cross-profile contract and the compatibility corpus in the same change. Do not
treat every global operation as a group-by: an operation the evidence proves
bounded keeps streaming.

**Acceptance:** Peak-memory calibration or spill evidence in the performance
lane supports every policy change. Full-versus-planned equivalence tests cover
ordering, schema, row multiplicity, and multi-input column retention for every
newly admitted operation. The compatibility corpus records each operation's
policy before and after the change. Operations without evidence keep the
`streaming` policy they have today, and unknown operations keep the
conservative policy.

**Dependencies:** The operation registry and compatibility corpus, the
cardinality proof, the materialisation-calibration contract, and the
performance lane.

**Evidence:** `src/haute/_polars_operations.py`; `src/haute/projection.py`;
`src/haute/_ram_estimate.py`;
`tests/performance/test_execution_engine_certification.py`;
`tests/test_polars_compatibility_corpus.py`.

### EXEC-P08 — Honour the schema-only declaration for OUTPUT documents

**Why:** `execute_lazy_graph(schema_only=True)` and
`plan_prepared_execution_strategy(schema_only=True)` declare that the caller
resolves lazy schemas and never collects a frame, and the planner skips the
group-by admission gate on that declaration. An OUTPUT node's apply step
(`_node_apply.py` calling `_output_assembler.assemble_output_from_mapping`)
materialises its document while the graph is being built, regardless of the
declaration, so a schema-only caller whose lineage includes an OUTPUT node (the
assistant's schema read, or a bundle-time schema inference) collects in the
calling process with the gate relaxed. EXEC-P06 found this while proving the
deploy dry-run and side-stepped it by running that dry-run inside a
hard-capped worker instead.

**Plan:** Give the OUTPUT assembler a schema-only build that derives the
document schema from the assembled lazy plan (nesting, list wrapping, and
null-filling are plan-level operations) without collecting, and route OUTPUT
nodes through it when `schema_only` is declared; where a shape cannot be
resolved lazily, reject the schema-only request with a typed error instead of
collecting. Either way a schema-only execution must never reach
`execution_collect`, and a tripwire test pins that.

**Acceptance:** A schema-only execution over a graph ending in an OUTPUT node
reports the same output schema the collected document has, with a tripwire
proving no physical collection; the assistant schema read and chunk planning
pass unchanged; the deploy bundle may then replace its capped dry-run for the
conservative case with schema-only inference once that equivalence is proven
for every OUTPUT shape.

**Dependencies:** The OUTPUT assembler contract, the execution facade's
`schema_only` declaration, and the assistant schema-read path.

**Evidence:** `src/haute/_output_assembler.py`; `src/haute/_node_apply.py`;
`src/haute/_execute_lazy.py`; `src/haute/assistant/_application.py`;
`src/haute/chunking.py`; `tests/test_output_assembler.py`;
`tests/test_assistant_application.py`; `tests/test_chunk_plan.py`.
