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

## Planned improvements

`EXEC-P05` is the only remaining package, and it requires a decision before it
can start. The operation registry with its
evidence-backed policies (sort, unique, join, join_asof, top_k, bottom_k,
reverse, explode, and window expressions admitted as estimated materialisation
boundaries; unpivot, rolling, dynamic group-by, shift, merge_sorted, and
interpolate streaming with recorded evidence), the profile-independent
projection planner, the operation-effect proof, the warned, hard-capped
conservative execution policy, the receiver-aware chunk classifier, the
version-pinned compatibility corpus, the performance-lane memory
certification, the hard-capped training-preparation and multi-row deploy
workers, and the schema-only declaration honoured end to end (an OUTPUT
document is described from its mapping and source schemas without being
assembled, and the assembled document carries that same derived schema) are
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
