# Execution engine roadmap

## Scope

The lazy walker and its display and sink walks, admission, the static analyses
that admit work (projection, column lineage, the RAM estimate, the chunk-local
classifier), the eager preview executor, the standalone `Pipeline.run` path, and
the worker processes that run engine work (the one-shot and protocol workers,
the warm interactive pool, the memory caps). Current behaviour is specified in
[the execution-engine specification](../execution-engine/high-level.md). These
packages come from the [25 October 2026 codebase review](codebase-review-2026-10-25.md);
the numbering continues from the September 2026 review's packages, which are
retired.

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| EXEC-R09 | Planned | P1 | The chunk-local classifier refuses a `select` whose expressions read no input column, so an input-sliced write never multiplies rows by the number of parts. Deployed request-order proof also refuses that select, preserving the per-quote output contract. |
| EXEC-R10 | Planned | P2 | The RAM estimate reads column widths from the node's own lineage schema and from the parquet metadata of nested types, so a widened graph estimates wider than its source. |
| EXEC-R11 | Planned | P2 | A display walk materialises a fan-out input only when more than one collected consumer reads it. |
| EXEC-R12 | Planned | P2 | A warm pool worker survives an ordinary user error, a failed replacement is retried, and a wait for a slot ends at the request's deadline. |
| EXEC-R13 | Planned | P2 | The one-shot and protocol parents arm their RSS watchdog from the child's capped baseline, not from a bare interpreter. |
| EXEC-R14 | Planned | P3 | Engine defaults that fail quietly fail loudly: contracts enforced by default, an all-stale column selection refused, a request-input edge never loses its name, `Pipeline.run` prunes like the walker, and a sink failure names its node. |
| EXEC-R15 | Planned | P3 | The static analyses cover the canvas join and the binning expressions, and the cardinality proof trusts `pl.lit` only with a literal argument. |
| EXEC-R16 | Decision | P3 | Preview cache extension either extends the cached preview or is removed with its specification sentence. |
| EXEC-R17 | Planned | P3 | The engine's never-fed evidence, dead entry points, test-only hooks and clone pairs are removed, and the preamble compile stops editing `sys.path` and bytecode caches. |
| EXEC-R18 | Planned | P3 | A node's optional inputs relax only the edges that feed it, not every column of that name in the graph. |

## Planned improvements

### EXEC-R09 — Column-free selects are not row-local
**Why:** `_CHUNK_LOCAL_CALL_SHAPE_VALIDATORS` has no validator for `select`, so
`classify_chunk_local_polars_code` accepts `df = source.select(pl.lit(1).alias("one"))`
as row-local: the node is written a slice of its input at a time, each slice yields the
literal's single row, and the written parquet holds one row per part (probe: native
height 1, written height 3 under three parts). `pl.len()` and `pl.int_range(...)` are
already refused as unsupported functions (probed); the gap is a `select` built from
literals alone, which the expression rules accept because they check functions and
methods, not whether any input column is read. A capture or an explicit build then
publishes a generation whose row count is wrong, and every consumer reads it as fresh;
only the deploy proof's `check_answer_rows` makes the shape loud at serve time.

**Plan:** Add a `select` validator that requires at least one expression to reference an
input column (a literal-only select is a one-row frame, not a row-local map), beside the
function and method rules that already refuse `pl.len`, `pl.int_range` and aggregations.
Record the rule in the chunked-writes section of the specification.

**Acceptance:** The probe shape is classified `eligible=False` with a reason naming the
expression; a corpus of `select` shapes written input-sliced has the native height for
each; the existing eligible shapes stay eligible.

**Dependencies:** None.

**Evidence:** `src/haute/chunking.py::classify_chunk_local_polars_code`;
`src/haute/chunking.py::_CHUNK_LOCAL_CALL_SHAPE_VALIDATORS`;
`src/haute/_chunked_writes.py`.

**28 October full-pass addition:** The deployed request-row proof also reports
`in_request_order` for `quotes.select(pl.lit(100).alias("premium"))`. A real three-quote score
then fails RequestRowCountError with one output row, instead of being routed through the
declared quote-by-quote path. **Plan/acceptance:** reuse a sound column/cardinality rule in
both chunk admission and deploy row proof; assert the proof selects quote-by-quote for this
supported per-quote constant expression, then score three quotes and get three premiums. Keep
the final row-count guard as defense, not as the proof. Evidence:
`src/haute/deploy/_request_rows.py`; executed `score_graph` and native whole/per-slice
comparisons.

### EXEC-R10 — Column widths from the lineage, not the nearest source
**Why:** `_resolve_target_columns_from_index` walks breadth-first to the nearest source
and takes its width, so a node downstream of a join that doubles the columns is estimated
at the source's width (probe: 48,000 bytes for the widened graph, the same as the plain
graph, against 4,016,000 bytes of actual input). Variable-width Arrow types are
recognised by matching tokens in the type's string form, and a nested type (`List`,
`Struct`) falls through to the flat `_BYTES_PER_COL`. The estimate is what admission
compares with the budget, so an under-estimate admits work the host cannot hold.

**Plan:** Resolve the width of each node from its own lineage output schema (the
projection already computes it) with per-dtype sizes, and read nested-type widths from
the source parquet's column statistics or a bounded sample; replace the token match with
a dtype walk.

**Acceptance:** The widened-graph probe estimates within a factor of two of its measured
input bytes; a nested-parquet probe likewise; the existing estimate tests pass with
updated expectations.

**Dependencies:** None.

**Evidence:** `src/haute/_ram_estimate.py::_resolve_target_columns_from_index`;
`src/haute/_ram_estimate.py::_is_variable_width_arrow_type`;
`src/haute/_ram_estimate.py::_BYTES_PER_COL`.

### EXEC-R11 — Fan-out caching only where a collected consumer needs it
**Why:** `_input_frame` wraps a fan-out input in `.cache()` whenever its lineage fan-out
is greater than one in a display walk, counted over the lineage's edges regardless of
which consumers the display policy collects. A preview therefore holds a materialised
copy of every fan-out input (probe: 610 MB peak cached against 329–365 MB without the
cache, same results), which is the opposite of what the cache is for and counts against
the preview's admitted budget.

**Plan:** Cache only when at least two collected consumers read the frame in this walk,
or never (the probe's uncached runs held 329–365 MB with Polars' common-subplan
elimination on and 318–340 MB with it off, so neither the cache nor the elimination was
needed on that graph); measure both on the probe graph before choosing.

**Acceptance:** The probe graph's preview peak is within ten percent of the uncached run
with identical results; a graph where two collected nodes share an expensive input still
computes it once.

**Dependencies:** None.

**Evidence:** `src/haute/_graph_walker.py::_input_frame`;
`src/haute/_graph_walker.py::_fanout_counts`.

### EXEC-R12 — Warm pool resilience
**Why:** In `_wait_for_result`, `raise user_error` sits inside the `try` whose
`except BaseException` calls `_stop_and_replace`, so every remote user error (a bad
expression, the most common editor failure) kills the warm worker after the ack/release
handshake that exists to keep it, and the next request pays a spawn and the
`haute.routes.pipeline` preload; the test that covers it asserts only that the pid is not
the server's. `_replace_slot` closes the old slot before `_start_slot`, so one failed
replacement (a 30 s readiness timeout under load, a spawn error) leaves a closed slot
that `_current_slot` refuses for the life of the server, with no retry — half of all
previews and traces then fail until a restart — and the optimiser estimate route catches
neither `InteractiveWorkerStartError` nor `InteractiveWorkerProtocolError` nor
`IsolatedWorkerHostError`, answering a bare 500. `_wait_for_scheduling_lock` has no bound
but `stop_reason`, which the optimiser passes as `None`. `_start_slot` creates its queues
and the `ProgressCell` before its `try`, so a second queue failure leaks the first and
the `IsolatedWorkerHostError` branch cannot be reached in production.

**Plan:** Raise the user error and return the result outside the replacing `try`,
replacing only on protocol, ack or release failures and on memory-shaped remote errors;
mark a slot "needs start" when its replacement fails and retry `_start_slot` at the next
`run()` under the scheduling lock, attaching the start error as a note to the original
exception; count the slot wait against the request's deadline and check the shutdown
event in the loop; create the queues inside the `try`; map the start, protocol and host
errors in the optimiser estimate route. Make the pool tests real-process tests for the
crash and cap-breach paths, and assert the same pid before and after an ordinary remote
error.

**Acceptance:** A remote `ValueError` leaves the worker pid unchanged; after one forced
`_start_slot` failure the next request on that index succeeds; a slot wait longer than
the request deadline raises `InteractiveWorkerTimeoutError`; a real-process test
breaches the growth cap and the next request is served.

**Dependencies:** None (`ROAD-WORKER-05` would subsume the pool but is not needed first).

**Evidence:** `src/haute/_interactive_workers.py::InteractiveWorkerPool._wait_for_result`;
`src/haute/_interactive_workers.py::InteractiveWorkerPool._replace_slot`;
`src/haute/_interactive_workers.py::InteractiveWorkerPool._current_slot`;
`src/haute/_interactive_workers.py::InteractiveWorkerPool._wait_for_scheduling_lock`;
`src/haute/_interactive_workers.py::InteractiveWorkerPool._start_slot`;
`src/haute/routes/optimiser.py`; `tests/test_interactive_worker_pool.py`.

### EXEC-R13 — The RSS watchdog measures the capped child
**Why:** `_create_worker_rss_watchdog` samples the child's RSS straight after
`process.start()`, when it is a bare interpreter, and arms `baseline + memory_limit_bytes`;
the child's own native cap measures its baseline after its imports and the Polars thread
pools. The watchdog is therefore the tighter bound by the child's import footprint (probe:
51 MiB, a fifth of a 256 MiB budget; a training child also preloads CatBoost or LightGBM)
and kills jobs the hard cap would admit, as `worker_rss_limit_exceeded`. The pool and the
dedicated worker sample a warm child, so only the one-shot and protocol parents differ.

**Plan:** Add a readiness handshake after the child's imports, thread-pool setup and
native-cap attempt, before user work starts. The parent samples that child's actual RSS,
arms the growth watchdog and acknowledges readiness before the child runs the job. Send
readiness even when a native cap is unavailable. Do not use `NativeMemoryLease.baseline_bytes`
as RSS: it records virtual address space for RLIMIT_AS, private commit on Windows and
cgroup usage on the cgroup backend. Keep native accounting separate.

Retain bounded startup supervision until readiness: a startup deadline and a startup
memory ceiling sized for the supported imports, specified separately from the job's
post-readiness growth allowance. Preserve the existing failure when required memory
supervision cannot be established; readiness must not silently disable it.

**Acceptance:** A child that imports Polars and a model engine under a 256 MiB budget is
not killed merely for its supported import footprint; the watchdog still kills a child
that grows past the budget on a host without a native cap. Cover RLIMIT_AS, cgroup,
Windows and no-cap accounting separately. A child that hangs or exceeds the startup
ceiling before readiness fails and is cleaned up; allocations in the job cannot inflate
the sampled baseline. Test process behaviour on supported hosts, not just message wiring.

**Dependencies:** None.

**Evidence:** `src/haute/_worker_isolation.py::_create_worker_rss_watchdog`;
`src/haute/_worker_isolation.py::run_isolated_worker`;
`src/haute/_worker_protocol.py::run_worker_protocol`;
`src/haute/_native_memory_limit.py::NativeMemoryLease`.

### EXEC-R14 — Quiet failures made loud
**Why:** Five engine behaviours fail quietly. `execute_lazy_graph` defaults
`enforce_contracts=False` while every production caller passes `True` and the
specification says contracts are enforced on both paths. `_apply_selected_columns`
returns the whole frame when every selected name is stale, so a renamed upstream column
silently widens a node's output (three tests pin the passthrough). `_build_funcs` drops
the name of a request-input edge with a null `sourceHandle` but keeps its frame, and the
transform and live-switch builders bind names to frames by position, so every later frame
is bound to the previous edge's name (reachability from the editor is unconfirmed).
`Pipeline.run` and `score` build every node and invoke every source on the inactive
branch of a Source Switch, which the walker prunes first. On the sink path a
data-dependent Polars error surfaces at the final collect with no node attribution, and
`haute run` prints the node id without its label or `error_line`.

**Plan:** Default `enforce_contracts` to `True`; raise for an all-stale selection naming
the node and the names (updating the three tests); raise in `_build_funcs` for a
request-input edge without a handle; prune the registered edges with
`prune_source_switch_edges` before `_topo_order`; record the terminal node on the
exception at the Data Output write seam and print label and line in `haute run`.

**Acceptance:** A caller omitting `enforce_contracts` gets contract errors; an all-stale
selection fails with the node's name; a null-handle request-input edge fails validation;
a Source Switch's inactive Data Input is never resolved by `Pipeline.run`; a sink-path
cast failure names its node.

**Dependencies:** None.

**Evidence:** `src/haute/execution.py::execute_lazy_graph`;
`src/haute/_execute_lazy.py::_apply_selected_columns`;
`src/haute/_execute_lazy.py::_build_funcs`; `src/haute/_user_exec.py`;
`src/haute/pipeline.py::Pipeline.run`; `src/haute/errors.py::mark_failing_node`;
`src/haute/executor.py::prepare_data_output`.

### EXEC-R15 — Static analysis coverage
**Why:** `materialising_operator_sequences_by_input_names` skips every non-Polars node, so
a canvas Edge Join is never a materialisation boundary and never receives the registry's
measured `join` policy that `df.join(...)` in a Polars node gets; the exposure is an
execution that captures nothing (caching off, deploy scoring), where the consequence under
a native cap is a kill after admission. `pl.Expr.qcut` and `pl.Expr.cut` exist in Polars
2.0 and are absent from `POLARS_OPERATIONS` (only `bin_quantiles` is registered), so a
binning expression is unregistered and refused or unmeasured. The cardinality proof
allow-lists `pl.lit(...)` whatever its argument, so `df.select(pl.lit(name))` with a
preamble Series longer than the frame emits more rows than the proven bound.

**Plan:** Record Edge Join nodes with operator `join` in the boundary sequences (the
cardinality side already handles them), or state in the specification that an Edge Join
is bounded only by capture and the chunked writer; register `qcut` and `cut` with
measured policies; in cardinality mode accept `pl.lit` only with a literal scalar or
collection argument.

**Acceptance:** A graph whose only boundary is an Edge Join receives a join estimate;
`qcut` and `cut` are registered with tests; the `pl.lit(<Series>)` probe returns
`row_expansion_unbounded`.

**Dependencies:** None.

**Evidence:** `src/haute/projection.py::materialising_operator_sequences_by_input_names`;
`src/haute/_polars_operations.py::POLARS_OPERATIONS`;
`src/haute/_column_lineage.py::_expression_has_unbounded_row_effect`.

### EXEC-R16 — Preview cache extension
**Why:** The specification promises that a preview whose row limit grows extends the
cached preview, but `_execute("preview_cache_extend")` runs the full eager executor with
no cached frames, and the lineage cache key includes `target_node_id`, `row_limit`,
`requested_columns` and `port_label`, so nothing can be extended. Either the extension is
built or the mode and its specification sentence go.

**Plan:** Decide. If kept, reuse the collected frames for the same target when only the
row limit grows; if dropped, delete the mode and correct the specification.

**Acceptance:** Either a second preview with a larger row limit reuses the first's
frames (measured), or the mode is gone and the specification no longer promises it.

**Dependencies:** None.

**Evidence:** `src/haute/executor.py::_execute`; `src/haute/execution.py` (the lineage
cache key).

### EXEC-R17 — Dead evidence, dead entry points and clones
**Why:** The cache-proof hit/miss recorders (`record_cache_proof_hit`,
`record_cache_proof_miss`, `record_cache_direct_fallback`) and
`ExecutionCacheProofMissReason` are fed by no production code, so every `cache_proof`
count in the metrics payload and the telemetry is always zero while the specification
calls them operational evidence; the five per-stage counters on `ExecutionStageMetric`
(`rows_in`, `rows_out`, `bytes_read`, `bytes_written`, `columns_scanned`) are never set and
are `null` in every payload and the generated frontend contract. The chunk-walk entry
points in `_graph_walker.py`, `plan_projection`, `build_linear_execution_chain_functions`,
`topo_sort_ids_filtered`, `_preamble_has_imports` and `_preview_projection_cache_suffix`
have no caller outside `execution.__all__`; two `execution_context is None` branches in
`prepare_data_output` are unreachable; `_snapshot_fault_point` is a no-op test hook the
high-level specification forbids and the low-level one documents. The declaration
validation of `row_local_python_scan` and `fanout_python_scan` is a verbatim 27-line
clone, and the POLARS, MODEL_SCORE and EXTERNAL_FILE branches of
`_resolve_row_cardinality_from_index` are the same twelve lines three times. The preamble
compile reorders `sys.path` process-wide on every compile. Its deletion of utility
bytecode is a documented freshness defence: eviction from `sys.modules` and import-cache
invalidation alone can reuse stale `.pyc` files after an edit preserving size and mtime.

**Plan:** Wire the cache-proof recorders at the one place that decides a source proof or
delete them with the DTO, the telemetry attributes and the specification sentence
(regenerating the contracts); remove the five stage counters or populate them at the
collect and sink seams; delete the dead entry points and branches; replace the fault
point with monkeypatching of the real write and align the two specifications; extract
`_validate_scan_declarations` and `_code_cardinality` helpers; compile the preamble with
an explicit `sys.path` entry scoped across compilation and node execution, including
imports deferred inside helper functions, and restore it on success and failure. Preserve
the existing utility `.pyc` invalidation; a replacement is acceptable only if its
source-loading mechanism is specified and proves the same freshness guarantee. Publicly
exported helpers need an API-use assessment before removal; an absent internal caller
alone does not establish that a library entry point is dead.

**Acceptance:** vulture and the repository greps show none of the named symbols
unreferenced; the metrics payload carries no always-null field; `sys.path` is unchanged
after compilation and execution, including failures; a same-size/same-mtime edit to
`utility.py` changes the next run's result; a helper importing `utility.sub` during node
execution resolves the correct project's package; the clone pairs are gone from jscpd's
report. Keep these behavioural guarantees alongside the mechanical cleanup checks.

**Dependencies:** `ENGQ-R01` owns dead code outside the engine.

**Evidence:** `src/haute/_execution_context.py::ExecutionCacheProofMissReason`;
`src/haute/_execution_context.py::ExecutionStageMetric`;
`src/haute/_execution_schemas.py`; `src/haute/execution.py`;
`src/haute/executor.py::prepare_data_output`;
`src/haute/executor.py::_prioritise_preamble_import_paths`;
`src/haute/executor.py::_evict_utility_import_state`;
`src/haute/_execute_lazy.py::_snapshot_fault_point`;
`src/haute/_polars_utils.py::row_local_python_scan`;
`src/haute/_polars_utils.py::fanout_python_scan`;
`src/haute/_ram_estimate.py::_resolve_row_cardinality_from_index`.

### EXEC-R18 — Optional inputs relax only their own edges
**Why:** A column a node reads only where it exists (`Contract.optional_inputs`, a Model
Score's exposure) is collected into one graph-wide `ProjectionPlan.optional_columns`, and
the walker drops any missing column of that name from every edge's projection. A different
node that genuinely needs a column of the same name then loses the edge's early,
named refusal and fails later, at its own boundary check or in Polars, with a less clear
message.

**Plan:** Carry optional demand per edge through the planner, so an edge's projection
drops a missing column only when every consumer reading it through that edge reads it
optionally.

**Acceptance:** A graph where a Model Score reads `exposure` optionally and a Polars node
downstream of the same source requires `exposure` refuses the Polars node's edge by name
when the source lacks it, while the Model Score alone still prices a full policy term.

**Dependencies:** None.

**Evidence:** `src/haute/projection.py::ProjectionPlan`;
`src/haute/_graph_walker.py::_WalkProjection`.
