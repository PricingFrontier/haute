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
execution-engine specification records the settings), and two packages that
change what a user sees (`PL2-12`, `PL2-19`).
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
with one row, in request order ([deploy](../deploy/high-level.md)).

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| PL2-02 | Planned | P1 | Nothing Polars 1 computed is reused once Polars 2 runs. |
| PL2-12 | Planned | P2 | New Polars dtypes meet Haute's dtype vocabularies loudly or are normalised where they enter. |
| PL2-13 | Planned | P2 | Code Haute generates and teaches runs on Polars 2 without deprecations. |
| PL2-14 | Planned | P2 | The operation registry and analysers describe Polars 2. |
| PL2-15 | Planned | P2 | The test suite asserts behaviour rather than Polars 1 accidents. |
| PL2-17 | Reverify | P2 | Every operator memory policy and RAM estimate rests on Polars 2 evidence. |
| PL2-18 | Reverify | P3 | Worker caps start from a Polars 2 baseline. |
| PL2-19 | Planned | P2 | A deployed pipeline runs the Polars the editor ran. |
| PL2-20 | Planned | P3 | The `price_contour` binary boundary is guarded against Polars' private API. |
| PL2-21 | Deferred | P3 | Structured inputs and Data Inputs can carry a JSON object as a `Map` column. |
| PL2-22 | Deferred | P3 | Banding and the step editor offer quantile and rank binning. |

## Planned improvements

### PL2-02 — Nothing Polars 1 computed is reused once Polars 2 runs
**Why:** A node snapshot's identity signs the lineage, the runtime inputs and
a fixed execution-semantics version (`node-snapshot:v1`), and the preview
response cache and the deploy schema cache sign theirs the same way; none of
them includes the Polars version. After the upgrade a fresh snapshot written
by 1.44 seeds a 2.0 run, so one execution can mix 1.44 results upstream
(an empty list exploded to a null row, `Int64 + UInt64` summed as `Float64`,
a horizontal concat padded with nulls, 1.44's row order) with 2.0 results
downstream, and the answer differs from a cold run. Input snapshots go the
other way: their re-validation compares the recorded dtype string with a
fresh scan, so a column that 2.0 reads as `Map`, `Extension` or (from a
Parquet `ENUM`) `String` reports a healthy generation as corrupt. The request
fingerprint built from `hash_rows` already over-invalidates on a hash change,
by design.

**Plan:** Specify one rule in the caching specification: the installed Polars
version (the full version, since a 2.x release can change engine results too)
goes into the identity of everything whose contents Polars computed, and into
the metadata of copies Polars only described. Node snapshots, preview seeds
and responses, and the deploy schema cache take it in their identities, so an
upgrade cold-starts them. Input snapshots and structured-input table caches
are copies of a source whose bytes their part digests prove; keying them on
the version would rebuild every Databricks and lakehouse snapshot from the live
table at each Polars release, silently changing the data a pipeline runs on,
which today only the user's refresh does. They record the version in their
generation metadata instead, and on a mismatch re-derive the recorded column
descriptions from a fresh scan and rewrite the metadata, reporting the
generation as re-described, never corrupt. A generation whose parts hold a
physical Parquet map column (a Databricks snapshot keeps the source's Arrow
schema when it writes its parts) is the exception: a Polars 2 scan would
collapse its duplicate keys while the digests still matched, so it is
re-encoded when it is opened, as `PL2-12` decided: from its own local parts
through pyarrow into a new generation, one row group at a time, with no
provider fetch.

**Acceptance:** a node-snapshot store and a preview cache populated under one
Polars version seed and serve nothing under another (simulated by patching the
reported version); an input snapshot carried across versions is re-described,
keeps its data and part digests, and is never reported corrupt; an
Arrow-written snapshot with a map column holding a duplicated key, opened under
the new version, keeps every entry without fetching from its provider; a cache
hit under an unchanged version is unaffected.

**Dependencies:** `PL2-12` for map-bearing generations.

**Evidence:** `src/haute/_node_snapshots.py::NODE_SNAPSHOT_EXECUTION_SEMANTICS_VERSION`;
`src/haute/execution.py::PREVIEW_EXECUTION_SEMANTICS_VERSION`;
`src/haute/execution.py::dataframe_frame_input_fingerprint`;
`src/haute/_source_cache.py::SourceCacheIdentity`;
`src/haute/deploy/_schema.py::deploy_schema_cache_fingerprint`.

### PL2-12 — New Polars dtypes meet Haute's dtype vocabularies loudly or are normalised where they enter
**Why:** Rendered dtype strings are identical across versions for every
dtype 1.44 produced, so recorded contracts and schemas still compare. What
changes is which dtype a pipeline produces:
- `Int64` or `Int32` combined with `UInt64` now gives `Int128` (1.44 gave
  `Float64`). The feature contract records an `Int128` column as `Int64`
  (`contract_dtype_name`) and MLflow `long`; `to_arrow()` and `to_pandas()`
  raise `ArrowInvalid` on it and `to_numpy()` raises a `PanicException`, a
  `BaseException` that escapes `except Exception`, at the training, metrics,
  SHAP, pyfunc and served-model conversion sites; deploy has no MLflow type for
  it.
- Arrow, Parquet and Iceberg map columns load as `Map` (1.44: a list of
  key-value structs) and unknown Arrow extension types as `Extension` (1.44:
  their storage type). Haute's dtype codec cannot round-trip either
  (`dtype_to_spec` falls back to the rendered string, which `parse_dtype`
  refuses), deploy has no MLflow type for them, and arithmetic, a cast to text
  and a CSV write all fail on them. A Databricks table fetched as Arrow or a
  Parquet file with a map column reaches a Data Input this way.
- `pl.Categorical("lexical")`, 1.x's ordering spelling, now makes a named
  category pool, which `parse_rendered_dtype` cannot read back and which the
  training job's `dtype in (pl.Utf8, pl.Categorical, pl.String)` check does not
  recognise as categorical.
- `round`, `floor`, `ceil` and `sign` widen a `Decimal`'s precision by one, so
  recorded dtype strings change for the same pipeline.

**Plan:** Decided on 17 October 2026; specify in the io-layer, modelling and
deploy specifications. One policy covers every path a source takes into a
pipeline: a direct Parquet scan (which bypasses snapshots), an input-snapshot
build and schema-only resolution. Polars 2 repairs duplicate keys while it
reads a Parquet map (the last value wins), and it does so even when the scan
asks for the 1.44 shape with a `List(Struct({key, value}))` schema, the
workaround the upgrade guide offers (probed: a row holding `a: 1, a: 2, b: 3`
came back as `a: 2, b: 3` either way, where 1.44 kept all three). pyarrow keeps
them, and its cast from a map to a list of key-value structs loses nothing and
copies nothing (probed: the cast shared the map's buffers and allocated no
memory). A map column is therefore converted to a list of key-value structs at
the Arrow level, where Haute holds the data before Polars reads it: a Databricks
fetch, an input-snapshot build, and an existing generation whose parts still
hold a physical map column, re-encoded from its local parts one row group at a
time as `PL2-02` describes, so its memory stays at one row group. Every entry
survives. A map column in a Parquet file Haute scans directly is refused,
detected from the file's schema before Polars reads it, with a message offering
the snapshot-backed route. An unknown extension column loads as its storage
type, which loses nothing and costs nothing, through Polars' documented
`POLARS_UNKNOWN_EXTENSION_TYPE_BEHAVIOR=load_as_storage`, which every process
Haute starts already sets with its other Polars settings
(`src/haute/_polars_environment.py`), so direct scans and user Data Inputs are
covered alike. Both are recorded in the source's schema
until `PL2-21` gives `Map` a place in the vocabularies. Adopting Polars 2's
`Map` was rejected because duplicate keys would collapse to the last value
without a word; refusing map and extension columns on every path was rejected
because it saves nothing (the cast copies nothing) and would refuse Databricks
tables with map columns that Haute reads today.
Independently: refuse `Int128` and `UInt128` in feature contracts and served
signatures with a message to cast upstream, as `Decimal` is refused, and
convert them explicitly at the conversion sites; make the codec round-trip or
refuse every dtype Polars 2 can produce; read and detect categorical pools by
base type; and state the `Decimal` widening.

**Acceptance:** a pipeline producing `Int128` fails at training or deploy with
the cast message instead of a panic; a map column with a duplicated key keeps
every entry and value through a Databricks fetch, a snapshot build, an existing
Arrow-written generation opened under Polars 2 and schema-only resolution, and
a direct scan of such a file is refused before Polars reads it; the re-encode of
an existing generation holds no more than one row group in memory; an unknown
extension column loads as its storage type on each path; the codec's round-trip
test covers every dtype Polars 2 can produce or its refusal; a
`Categorical("lexical")` column trains as categorical.

**Dependencies:** None.

**Evidence:** `src/haute/_polars_dtypes.py::contract_dtype_name`;
`src/haute/_polars_dtypes.py::dtype_to_spec`;
`src/haute/_polars_dtypes.py::parse_rendered_dtype`;
`src/haute/_polars_dtypes.py::rendered_dtype_mlflow_type_name`;
`src/haute/deploy/_mlflow.py`; `src/haute/modelling/_training_job.py`;
`src/haute/_source_cache.py`; `src/haute/_databricks_io.py`;
`src/haute/_input_providers.py`.

### PL2-13 — Code Haute generates and teaches runs on Polars 2 without deprecations
**Why:** The runnable reference pipeline (`examples/reference/main.py`) and
the assistant's `linear_pricing` example bundle call `cut([25, 40, 65])`, and
Banding's equal-width bins call `cut(..., include_breaks=True)`: `cut` is
deprecated in 2.0 in favour of `bin_intervals`, which is left-closed by
default, takes required labels and returns `{bin, left, right}` instead of
`{breakpoint, category}`. The assistant's data check passes `strict=True` to a
horizontal `concat`, now deprecated. The assistant's authoring guide, which
teaches the model Haute's Polars idioms, carries none of 2.0's: parse text
with `str.to_date`, give `is_in` members the column's type, pass a series
haystack with `implode`, band with `bin_intervals`, empty lists explode to no
rows, alias `pl.datetime` and `pl.repeat`, and order a join with
`maintain_order`. The documentation's code blocks use no removed API.

**Plan:** Move the two examples to `bin_intervals(..., labels=[...],
right_closed=True)`, which reproduces their output exactly (probed); move
Banding's bins to `bin_intervals(..., include_intervals=True)` and read the
upper edge from `right`; drop the `strict` argument; add one paragraph of 2.0
idioms to the authoring guide and the examples that exercise them; extend the
documentation's code-block scan to the deprecated forms.

**Acceptance:** the reference pipeline, the assistant example portfolio and the
banding statistics tests run on Polars 2 with no Polars deprecation warning
(enforced by an error filter for Polars deprecations in those modules), and
their golden outputs are unchanged.

**Dependencies:** None.

**Evidence:** `examples/reference/main.py`;
`src/haute/assistant/assets/examples/linear_pricing/pipeline.py`;
`src/haute/_binning.py::equal_width_bins`;
`src/haute/assistant/_data_check.py`;
`src/haute/assistant/assets/authoring_guide.md`.

### PL2-14 — The operation registry and analysers describe Polars 2
**Why:** The registry classifies every Polars operation the analysers
recognise, and Polars 2.0 changed that surface. It still lists operations 2.0
removed (`melt`, `fetch`, `with_context`, `flatten`, `from_json`,
`register_plugin`, `str.concat`), harmless because the registry classifies
source text, but stale. New operations are unregistered and fail closed
(`bin_intervals`, `bin_quantiles`, `bin_ranks`, `erf`, `erfc`,
`approx_quantile`, `struct.eval`, `list.to_map`, the `map` namespace,
`cs.map()`), except one: `pipe_with_dtype(fn)` is reported `cardinality_proven`
although its callback can expand rows (probed: two rows became five), where
`pipe(fn)` is correctly unbounded, so a materialisation estimate can be
admitted on a row bound that does not hold. The `map` and `ext` namespaces are
not in the namespace set, so `pl.col(m).map.len()` is read as the frame-level
`len`. No test compares the registry with the installed Polars.

**Plan:** Register `pipe_with_dtype`, as an expression method and a `pl`
function, as `pipe` is (opaque, unbounded expansion, name-changing); add `map`
and `ext` to the namespace set with their methods as row-local namespace
entries without a chunk proof; register `bin_intervals`, `erf` and `erfc` as
row-local and `bin_quantiles`, `bin_ranks` and `approx_quantile` as fan-in;
remove the entries 2.0 removed with their test pins; and add a test that every
registered operation exists on the installed Polars, with a reviewed list of
public names deliberately left unregistered, so the next upgrade reports its
drift. Polars 2's `Expr.meta.is_row_separable()` and `is_length_preserving()`
discriminate as the registry does (`cum_sum`, `shift`, `rank`, `over` and a
format-less `str.to_date` preserve length but are not row-separable;
`explode` is the reverse), so the same test asserts that every entry the
registry calls row-local builds an expression Polars calls row-separable, and
no fan-in or window entry does: an oracle for drift in Polars' own semantics,
not a replacement for classifying source text. Measurement-backed policies for
the new entries come from `PL2-17`.

**Acceptance:** a `pipe_with_dtype` whose callback explodes reports its
cardinality unavailable; `map.len()` is classified in its namespace; the
live-surface test passes on Polars 2 and fails when an entry names a missing
operation.

**Dependencies:** None.

**Evidence:** `src/haute/_polars_operations.py::POLARS_OPERATIONS`;
`src/haute/_polars_operations.py::EXPRESSION_NAMESPACE_NAMES`;
`src/haute/_column_lineage.py::_expression_has_unbounded_row_effect`;
`tests/test_polars_operations.py`.

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

**Dependencies:** PL2-02, PL2-12, PL2-13 and PL2-14.

**Evidence:** `tests/test_edge_join.py`; `tests/test_execute_lazy_contracts.py`;
`tests/test_column_lineage.py`; `tests/test_standalone_nodes.py`;
`tests/test_codegen_execution_equivalence.py`; `tests/test_polars_operations.py`;
`.github/workflows/performance.yml`; `.github/workflows/container-smoke.yml`;
`.github/workflows/dependencies.yml`.

### PL2-17 — Every operator memory policy and RAM estimate rests on Polars 2 evidence
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
operations `PL2-14` registers; change a policy only where the new measurement
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
every Haute process runs, the cleaner thread never starts, so it matters only if `PL2-17`
turns spilling on, and then it is started before the cap; rerun the fallback
lane and classify its flake again.

**Acceptance:** the worker-isolation and native-limit modules pass on Polars 2
on every CI platform; if `PL2-17` turns spilling on, a capped worker that
spills does not fail on a thread start.

**Dependencies:** `PL2-17` for the spilling decision.

**Evidence:** `src/haute/_native_memory_limit.py::_start_polars_thread_pools`;
`tests/test_native_memory_limit.py`; `tests/test_worker_isolation.py`.

### PL2-19 — A deployed pipeline runs the Polars the editor ran
**Why:** A served MLflow model asks for `haute==<version>` and
`polars>=1.44.2`, so its environment resolves whatever Polars the range
admits, while the scoring container pins the editor's installed versions. A
Polars release can change engine behaviour that decides results (row order, as
2.0 did) and resource behaviour (spilling). The scoring container and
Databricks Model Serving would also spill into their writable layer by
default.

**Plan:** Decided on 17 October 2026: a served model pins the editor's exact
Polars version, as the container already does, and records it, so that its
`load_context` refuses another version with a message naming both (as the EBM
engine already does for interpret). The served model then runs with the memory
behaviour its deploy-time checks measured. The served model's environment
carries Haute's Polars settings (`src/haute/_polars_environment.py`), as the
container image already does, and the deploy specification records both. The major range `polars>=2.0,<3` was rejected: a
2.x release could change a deployed model's results and memory behaviour
without a redeploy.

**Acceptance:** a logged model's requirements name the editor's exact Polars
version, and loading it under another fails with the named message; the
container's Dockerfile and the served model's environment carry the Polars
settings.

**Dependencies:** None.

**Evidence:** `src/haute/deploy/_mlflow.py::_pip_requirements`;
`src/haute/deploy/_container.py::_SCORING_RUNTIME_DEPENDENCIES`;
`src/haute/modelling/_ebm.py::EBMModel`.

### PL2-20 — The price_contour binary boundary is guarded against Polars' private API
**Why:** price-contour 0.6.0's native module embeds pyo3-polars 0.25 with the
Polars 0.52 Rust crates and moves frames through Polars' private Python methods
(`Series._import_arrow_from_c`, `_newest_compat_level`) and
`to_arrow(compat_level=...)`. On Polars 2.0 all 139 tests that drive the real
library pass, so nothing breaks today, but nothing ties that boundary to the
installed Polars either: a later Polars that renames a private method would
fail mid-solve.

**Plan:** Extend the compatibility guard in `haute._price_contour` with a
one-row frame round trip through a price_contour entry point when the module
is first imported, failing with a named message; ask the price-contour project
for a release built on a pyo3-polars line for Polars 2.

**Acceptance:** a test that removes `_import_arrow_from_c` from the installed
Polars fails the guard with its message, not a solve.

**Dependencies:** None.

**Evidence:** `src/haute/_price_contour.py::price_contour`;
`tests/test_price_contour_guard.py`;
`tests/test_optimiser_routes_real_library.py`.

### PL2-21 — Structured inputs and Data Inputs can carry a JSON object as a Map column
**Why:** Polars 2.0 adds a `Map` dtype and reads Arrow, Parquet and Iceberg
maps as `Map` with `dict` values. A source whose object keys are data rather
than schema (claims by peril, factors by name) is flattened or normalised
today. The package is deferred because it adds capability; until it is taken,
`PL2-12` decides how a map column enters.

**Plan:** If reopened, decide where a `Map` may appear (API Input tables, Data
Input from Parquet or Databricks) and how the editor, contracts and the deploy
schema carry it.

**Acceptance:** Defined by the decision.

**Dependencies:** PL2-12; a source that needs it.

**Evidence:** `src/haute/_json_shred/_shred.py`; `src/haute/_polars_dtypes.py`.

### PL2-22 — Banding and the step editor offer quantile and rank binning
**Why:** Polars 2.0 adds `bin_intervals`, `bin_quantiles` and `bin_ranks`
and deprecates `cut` and `qcut`. `PL2-13` moves Haute's own binning to
`bin_intervals`; offering quantile or rank binning in Banding or as a step is
new capability, which the step editor's roadmap leaves out of scope, so this
is deferred.

**Plan:** If reopened, decide whether Banding gains quantile and rank methods
and whether the step editor gains a binning step.

**Acceptance:** Defined by the decision.

**Dependencies:** PL2-13; a decision to add the capability.

**Evidence:** `src/haute/_binning.py::equal_width_bins`;
`src/haute/_banding_config.py`.
