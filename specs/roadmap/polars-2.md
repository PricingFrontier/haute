# Polars 2 roadmap

## Scope

Moving Haute from Polars 1.44.2 to Polars 2.0: the dependency pins, the
engine seams that read Polars internals or rely on its defaults, the
behaviour users see change, and the evidence the engine's memory policies rest
on. The findings, the probes behind them and three bounded test runs under
Polars 2.0.0 are in the [Polars 2 review](polars-2-review-2026-10-17.md). What
Polars 2 adds beyond the move itself (the `Map` dtype as a column type Haute
carries, quantile and rank binning) is deferred.

The packages form two rounds. The first makes Haute correct on Polars 2 with
CI green: the pin, every seam that breaks or silently changes, Polars'
out-of-core spilling kept off and contained (the pin turns it on; the
execution-engine specification records the settings).
Their designs, and the spilling settings, were decided
on 17 October 2026 by one rule: of the robust options, the one that uses least
memory, then the fastest. Each entry records the measurement behind its
choice. The second round re-establishes the engine's memory evidence under
Polars 2 and, on that evidence and by the same rule, decides where to turn
spilling on. The pin moves only with the whole first round, so `main` never
runs Polars 2 with a seam built for 1.44.
The plan was reviewed by Codex and by a second opinion whose own measurements
it cites as such. A defect that predates Polars 2 was fixed on the way: a training
run's seed selects the same rows, and its charts the same samples and cuts, whatever
order the prepared rows arrive in; a row limit's sample alone still follows the
pipeline's order ([modelling](../modelling/high-level.md)). Every join Haute builds
now states its row order, and a deployment proves it answers each quote of a request
with one row, in request order ([deploy](../deploy/high-level.md)). Polars 2's new
dtypes meet Haute's vocabularies loudly: a categorical in a named category pool is a
categorical everywhere, a column holding a 128-bit integer is refused with the cast that
fixes it before a model, a feature contract or a deploy signature meets it, and a map
column reaches a pipeline as a list of key-value structs holding every entry, while a
direct scan of a Parquet file holding one is refused
([io-layer](../io-layer/low-level.md)). Nothing Polars 1 computed is reused: previews,
node snapshots and the deploy schema cache sign the installed Polars version, and an input
snapshot carried across versions is re-described from its own parts, or re-encoded when
they hold a physical map column, never fetched again ([caching](../caching/low-level.md)).
A served model pins the editor's exact Polars, refuses to load under another and runs
under Haute's Polars settings ([deploy](../deploy/low-level.md)). The operation registry
describes Polars 2, and a test compares it with the installed Polars, so the next upgrade
reports its drift ([execution engine](../execution-engine/low-level.md)). The code Haute
generates and teaches (the reference pipeline, the assistant's examples and authoring guide,
Banding's histogram) uses no deprecated Polars call, and the assistant's guide teaches
Polars 2's idioms.

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| PL2-15 | Planned | P2 | The test suite asserts behaviour rather than Polars 1 accidents. |
| PL2-17 | Reverify | P2 | Every operator memory policy and RAM estimate rests on Polars 2 evidence. |
| PL2-18 | Reverify | P3 | Worker caps start from a Polars 2 baseline. |
| PL2-20 | Planned | P3 | Haute runs a price-contour release that declares and tests Polars 2, behind the guard. |
| PL2-21 | Deferred | P3 | Structured inputs and Data Inputs can carry a JSON object as a `Map` column. |
| PL2-22 | Deferred | P3 | Banding and the step editor offer quantile and rank binning. |

## Planned improvements

### PL2-15 — The test suite asserts behaviour rather than Polars 1 accidents
**Why:** Most of the bounded runs' failures are the packages above, but some
tests encode an accident of 1.44 rather than a promise: an order no contract
gave (the edge-join, lazy join-projection, lineage, standalone-node and
codegen-equivalence tests), the removed chunk-size variable, the registry's
removed operations, and the version pins. The 38 tests that expect a Polars
exception class or message still get the same class and text on 2.0. The
2.0 changes also open gaps no test covers: a numeric "is one of" on a decimal
column, a text-to-date step run against text, `std` on a duration, `Int128`
through training and deploy, `pipe_with_dtype`'s cardinality, `Map` and
`Extension` through the dtype codec, and a snapshot store carried across a
Polars upgrade.

**Plan:** Fold each test change into the package whose behaviour it asserts;
make a test that asserts order assert the order its specification now promises
or compare sorted frames where none is promised, never sorting to
hide a promised order; add the gap tests with their packages; then push the
round. The pull request runs the CI workflow and, for this change, the
mutation and dependency workflows; the performance (at its `ci` and `1m`
scales), container-smoke, unlocked-resolve and property-exploration lanes run
only on a schedule or by dispatch, so dispatch each against the branch's head
and record their results in the pull request before it merges.

**Acceptance:** the pull request's checks are green on Polars 2, and the
dispatched lanes above are green against the same revision with their run
links recorded; each gap above has a test; no test sorts away an order the
specifications promise.

**Dependencies:** None.

**Evidence:** `tests/test_edge_join.py`; `tests/test_execute_lazy_contracts.py`;
`tests/test_column_lineage.py`; `tests/test_standalone_nodes.py`;
`tests/test_codegen_execution_equivalence.py`; `tests/test_polars_operations.py`;
`.github/workflows/performance.yml`; `.github/workflows/container-smoke.yml`;
`.github/workflows/dependencies.yml`.

### PL2-17 — Every operator memory policy and RAM estimate rests on Polars 2 evidence
**Status:** since 17 October 2026 the estimate's advice for a many-to-many join names
the `validate` contract's cost, and the execution strategy guide adds the deployment's own
key check; a declared join was already estimated holding both inputs. Since 18 October 2026:
- Every boundary and streaming witness in the registry rests on Polars 2.0. The
  certification lane re-measured each on the Linux reference runner (CI perf runs
  39099589384, 39099036905 and 39099038828; 39121069339 and 39121207008 for the entries
  below), and `MEMORY_EVIDENCE_POLARS` names the release, held to the installed Polars by
  a test.
- `sort`'s peak reached 4.33 times the estimator's rows x width x 3.0 figure, so its factor
  is 450 basis points. `over_narrow` measures about 1.44x `scan_narrow`, so it is held to
  the 1.3x streaming ceiling.
- Polars 2's `bin_quantiles` and `bin_ranks`, and an `approx_quantile` set beside every
  row, hold the frame until the column is read. Their extra memory grows 4.5 to 5.4 times
  at four times the rows, so they are boundaries at 150 basis points.
- Capped workers keep spilling off. Measured against each boundary operator's own peak,
  a budget raised `over`'s peak by 47% to 77% and `join`'s by 28% to 33% on Linux, and
  `sort`'s by 24% on Windows, although `sort`'s fell 70% on Linux without writing a file.
- `_malloc_trim` runs on Linux alone. There glibc's trim returned 256 MB of 268 MB of
  NumPy buffers freed beneath one that stays, and 61 MB of the 73 MB left resident when
  every buffer was freed, but nothing of a Polars frame. Windows' heap compaction
  returned nothing.
- The performance lane's baselines are keyed by Polars version. The 1.44 ones compare as
  incompatible, and the first Polars 2 runs recorded new ones (39099036905 at `ci`,
  39099038828 at `1m`). Its artifacts now upload: the action had skipped their folder,
  whose name starts with a dot.
- The capture-point rationale cites pola-rs/polars#24206 for what it reports.

What remains: the training estimate's overhead multiplier (3.0) and the width probes
against Polars 2's `estimated_size` (a literal column now estimates 84 bytes where 1.44
gave 120), and the operator measurements at the monthly `10m` scale.
**Why:** The operation registry admits `sort`, `unique`, `join`, `join_asof`,
`top_k`, `bottom_k`, `reverse`, `shift`, `explode`, `group_by` and `over` as
materialisation boundaries, and keeps `unpivot`, `rolling`,
`group_by_dynamic`, `merge_sorted`, `interpolate` and `filter` streaming, each
on a fresh-process peak-RSS measurement taken on Polars 1.4x; the
specification requires new evidence for any policy change. Polars 2.0 changed
the join and group-by hash tables, added a streaming sort that spills, moved
scalar windows and fixed-size rolling windows onto the streaming engine, and
changed `estimated_size`, which the RAM estimate and the disk-headroom checks
use (a frame with a literal column now estimates 84 bytes where 1.44 gave
120). Two rationales in the specifications also date from 1.x: that
`_malloc_trim` returns Polars' freed memory, although Polars allocates through
mimalloc on Windows (its runtime embeds it) and jemalloc on Linux; and the
description of pola-rs/polars#24206, still open at 2.0, as plan duplication. A
third still holds: `collect_batches` still runs ahead of a slow consumer on
2.0 (the second opinion measured 1.9 GB held while the consumer slept, against
2.0 GB for a whole collect), so `bounded_collect_batches` keeps issuing one
query per batch. One operator the registry does not model: a join with a
`validate` contract other than `"m:m"` runs through the streaming engine's
in-memory join node, on 1.44 and 2.0 alike, at 39% to 76% more peak memory in
this review's probes, while the RAM estimate's advice for a join bounded only
by its many-to-many product asks the author to declare such a contract
(`_MANY_TO_MANY_JOIN_REMEDIATION`).

**Plan:** Re-run the certification lane's operator memory probes on Polars 2
with spilling off as `src/haute/_polars_environment.py` sets it, carried into each probe's own
subprocess environment so a spill cannot hide a peak, and record each
operator's factor and its streaming control in the registry, including the
operations registered for Polars 2 (`bin_quantiles`, `bin_ranks`, `approx_quantile`); change a policy only where the new measurement
crosses its rule, both ways. Measure a join with a `validate` contract the same
way, charge it in the RAM estimate as the materialisation of both inputs it
is, and make the estimate's advice name that cost beside Haute's own
uniqueness proofs ([deploy](../deploy/high-level.md)) instead of recommending the
contract alone. Then
measure the same boundaries in a capped worker with spilling on and its
threshold set from the worker's admitted envelope (static per spawn, or per
task through `pl.Config.load`), on Linux as well as Windows, since the
allocators differ (jemalloc against mimalloc) and the review measured only
Windows. Decide by the rule spilling was kept off by, memory first: turn
spilling on in capped workers if, at the threshold the measurement settles on,
no boundary operator's peak rises and at least one falls materially or
completes under a cap it breaches today; the time it costs is accepted and
recorded beside the decision. Spilling is turned on only in spawned, capped
workers, never in a shared process (the editor server, the optimiser's thread
mode, the scoring container, the served model), because the out-of-disk abort
is process-wide. Turning it on brings the typed storage failure for that abort
(a match on Polars' message text, pinned by a test) and the spill-folder
cleanup Haute already performs. Re-measure the
RAM estimator's overhead multiplier and the calibration seeds against Polars
2's `estimated_size`. Measure whether `_malloc_trim` returns any memory;
delete it and its periodic calls if it returns none. Correct the #24206
wording. Reset the performance lane's baselines, which are keyed by Polars
version.

**Acceptance:** every boundary and streaming witness in the registry carries a
Polars 2 measurement and the version it was taken on; the certification lanes
pass at their scales on Polars 2; the estimator's figures are re-derived and
its tests updated from the measurements, not by hand; a join with a `validate`
contract is estimated as materialising both inputs; the decision whether to
turn spilling on is recorded in the execution-engine specification with the
measurement behind it.

**Dependencies:** None.

**Evidence:** `src/haute/_polars_operations.py::POLARS_OPERATIONS`;
`tests/performance/_operation_memory_probe.py`;
`tests/performance/test_execution_engine_certification.py`;
`src/haute/_ram_estimate.py`; `src/haute/_polars_utils.py::_malloc_trim`;
`src/haute/execution.py::_MANY_TO_MANY_JOIN_REMEDIATION`;
`specs/execution-engine/high-level.md`.

### PL2-18 — Worker caps start from a Polars 2 baseline
**Why:** Under `RLIMIT_AS` a worker starts both Polars engines' thread pools
before it measures the cap's baseline, so the cap counts only what the job
allocates (`_start_polars_thread_pools`). Polars 2.0 starts its out-of-core
cleaner thread lazily, on the first spill, and dropped memory-mapping from
Parquet scans, so the address space a worker reserves after its baseline may
differ, and a thread that cannot start under the cap is a memory-limit
failure. The RLIMIT fallback lane already flakes with `can't start new thread`
on CI's Python 3.11.

**Plan:** Measure a worker's address-space and resident baseline on Polars 2
under each backend (Job Object, cgroup, `RLIMIT_AS`); with spilling off, as
every Haute process runs and as `PL2-17` keeps it in capped workers, the cleaner
thread never starts; rerun the fallback lane and classify its flake again.

**Acceptance:** the worker-isolation and native-limit modules pass on Polars 2
on every CI platform.

**Dependencies:** None.

**Evidence:** `src/haute/_native_memory_limit.py::_start_polars_thread_pools`;
`tests/test_native_memory_limit.py`; `tests/test_worker_isolation.py`.

### PL2-20 — Haute runs a price-contour release that declares and tests Polars 2
**Status:** since 17 October 2026 the guard passes a one-row frame through the native
module when it first verifies the install, and a Polars without
`Series._import_arrow_from_c` fails it by name. No Rust Polars or pyo3-polars release
matches Python Polars 2.0.0 yet, so price-contour cannot be rebuilt on Polars 2's Rust
code. What remains is price-contour 0.7.0, specified in
[price-contour on Polars 2](price-contour-polars-2.md), and Haute's move to it.
**Why:** price-contour 0.6.0's native module embeds pyo3-polars 0.25 with the
Polars 0.52 Rust crates. It moves frames through Polars' private Python methods
(`Series._import_arrow_from_c`, `_newest_compat_level`) and
`to_arrow(compat_level=...)`, rechunking every incoming column on the way. On
Polars 2.0 all 139 tests that drive the real library pass, so nothing breaks
today. But price-contour declares `polars>=0.20`, its own CI has only run Polars
1.38, and two of its result orders rest on joins Polars 2 does not promise to keep
in order.

**Plan:**
- Once price-contour 0.7.0 is on PyPI, move `pyproject.toml`'s `price-contour`
  specifier and `REQUIRED_SPECIFIER` to `>=0.7.0,<0.8`, and relock with uv 0.8.22.
- If 0.7.0 moves to pyo3-polars 0.28, make the guard's private-method test delete
  `pl.Series._import`, the method that line returns frames through. Update the
  guard's docstring and the optimiser low-level specification to match.
- Run every test module that drives the library.
- Independently of the release, `_prepare_online_apply_frame` passes only the
  columns the apply reads.

**Acceptance:**
- Haute pins price-contour 0.7.0.
- A test that removes the private method the installed build returns frames
  through fails the guard with its message, not a solve.
- Every module that drives the real library passes.
- An Optimiser Apply hands price-contour only the columns it reads.

**Dependencies:** None in Haute. The move waits for price-contour 0.7.0 on PyPI.

**Evidence:** `src/haute/_price_contour.py::price_contour`;
`src/haute/_builders.py::_prepare_online_apply_frame`;
`tests/test_price_contour_guard.py`;
`tests/test_optimiser_routes_real_library.py`.

### PL2-21 — Structured inputs and Data Inputs can carry a JSON object as a Map column
**Why:** Polars 2.0 adds a `Map` dtype and reads Arrow, Parquet and Iceberg
maps as `Map` with `dict` values. A source whose object keys are data rather
than schema (claims by peril, factors by name) is flattened or normalised
today. The package is deferred because it adds capability; until it is taken, a map
column enters as a list of key-value structs (io-layer specification, *Map columns*).

**Plan:** If reopened, decide where a `Map` may appear (API Input tables, Data
Input from Parquet or Databricks) and how the editor, contracts and the deploy
schema carry it.

**Acceptance:** Defined by the decision.

**Dependencies:** A source that needs it.

**Evidence:** `src/haute/_json_shred/_shred.py`; `src/haute/_polars_dtypes.py`.

### PL2-22 — Banding and the step editor offer quantile and rank binning
**Why:** Polars 2.0 adds `bin_intervals`, `bin_quantiles` and `bin_ranks`
and deprecates `cut` and `qcut`. Haute's own binning uses
`bin_intervals`; offering quantile or rank binning in Banding or as a step is
new capability, which the step editor's roadmap leaves out of scope, so this
is deferred.

**Plan:** If reopened, decide whether Banding gains quantile and rank methods
and whether the step editor gains a binning step.

**Acceptance:** Defined by the decision.

**Dependencies:** A decision to add the capability.

**Evidence:** `src/haute/_binning.py::equal_width_bins`;
`src/haute/_banding_config.py`.
