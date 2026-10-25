# Polars 2 roadmap

## Scope

The remaining verification and calibration work after Haute's move to Polars 2.0:
RAM estimates, operator measurements at the monthly `10m` scale, and worker
baselines. Current behaviour and memory-policy decisions are specified in
[the execution engine](../execution-engine/high-level.md), with dtype and cache
contracts in [the IO layer](../io-layer/low-level.md) and
[caching](../caching/low-level.md). The
[Polars 2 review](polars-2-review-2026-10-17.md) is the dated migration report,
not the current implementation backlog.

New capabilities beyond the migration—the `Map` dtype as a column type Haute
carries, and quantile and rank binning—remain deferred.

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| PL2-17 | Reverify | P2 | RAM-estimate calibration and large-scale operator measurements rest on Polars 2 evidence. |
| PL2-18 | Reverify | P3 | Worker caps start from a Polars 2 baseline. |
| PL2-21 | Deferred | P3 | Structured inputs and Data Inputs can carry a JSON object as a `Map` column. |
| PL2-22 | Deferred | P3 | Banding and the step editor offer quantile and rank binning. |

## Planned improvements

### PL2-17 — Remaining RAM-estimate calibration and large-scale memory evidence
**Why:** The operator registry's current boundary and streaming policies already
carry Polars 2 evidence, but the training estimate's overhead multiplier (3.0),
the width probes against Polars 2's `estimated_size`, and the operator
measurements at the monthly `10m` scale still need verification. Polars 2
changed size accounting: a frame with a literal column measured 84 bytes where
1.44 gave 120, so the existing calibration cannot be assumed to transfer.

**Plan:** Measure the RAM estimator's overhead multiplier and width-calibration
seeds against Polars 2's `estimated_size`, and run the operator certification
lane at `10m` on the reference runner. Record peak RSS, estimated bytes and
runtime alongside the workload, platform and Polars version. Keep spilling
off as `src/haute/_polars_environment.py` specifies, including in each probe's
subprocess environment. Change a factor or policy only where the new
measurement crosses the rule in the execution-engine specification, in either
direction; derive any estimate changes from the measurements.

**Acceptance:** the training overhead and width-calibration evidence identifies
its Polars version and workloads, with the resulting estimates covered by the
RAM-estimate tests; the `10m` certification results are recorded and pass, or
their failures are resolved through measured policy changes. The current
spilling and allocator decisions remain in the execution-engine specification.

**Dependencies:** None.

**Evidence:** `src/haute/_ram_estimate.py::_OVERHEAD_MULTIPLIER` and its width
probes; `tests/test_ram_estimate.py`;
`src/haute/_polars_operations.py::MEMORY_EVIDENCE_POLARS`;
`tests/performance/_operation_memory_probe.py`;
`tests/performance/test_execution_engine_certification.py`;
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
the current execution-engine policy requires in capped workers, the cleaner
thread never starts; rerun the fallback lane and classify its flake again.

**Acceptance:** the worker-isolation and native-limit modules pass on Polars 2
on every CI platform.

**Dependencies:** None.

**Evidence:** `src/haute/_native_memory_limit.py::_start_polars_thread_pools`;
`tests/test_native_memory_limit.py`; `tests/test_worker_isolation.py`.

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
