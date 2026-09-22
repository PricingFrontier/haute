# PR #227 implementation evidence

Implementation and local verification, 22 September 2026. This records changes
against reviewed head `97f3e9909998460bae5dbdfedf821eb9189581a6`. PR CI remains
the final compatibility gate; its result is recorded below when available.
The [focused plan](pipeline-cache-memory-design.md) remains the scope.

## Correctness and lifecycle

- Input and node snapshots share cross-process generation markers and the same
  lease lock. Focused independent-reader/Clear/replacement/dead-owner tests cover
  both store entry points; nested local readers retain their marker.
- Publication indexes a candidate before committing its generation/pointer.
  Pointer failures preserve the previous usable generation and pin. A later
  index-pruning failure leaves the committed generation discoverable and pinned.
- Replacement admission credits only reclaimable older signatures of the same
  slot; credited data cannot also be evicted before commit or counted twice.
- Statistics responses carry the complete current question identity. Superseded
  requests are aborted before the replacement debounce finishes. Profile consumers
  re-request after a store epoch reset and reject replies from the previous epoch.
- Join internal names avoid user columns. Canonical numeric part names work
  beyond 99,999 and sort numerically.
- Cross joins slice both inputs and constrain each output part. Derived resident
  plans are staged before batch reading when their source cannot be safely sliced.

The cache regression group passed 131 tests; writer/batch contracts passed 267
before the additional byte-sizing changes. Byte-sizing/native-admission focused
modules passed 112 writer, 192 execution-context and 192 RAM-estimator tests.
These are targeted results, not a substitute for final integration/CI.

## Measurements

Reproducible scripts and machine-readable results are next to this file:

- `pr-227-implementation-benchmark.py/json`: fresh workers, fixed two Polars
  threads, independent join input sizes, write-only RSS, parent verification,
  logical query-source occurrences and temporary bytes.
- `pr-227-budget-benchmark.py/json`: explicit execution budgets. Both wide
  datasets exceed the selected 256 MiB allowance: approximately 760 MB and
  3.04 GB decoded. Initial measurements selected about 10.8k rows per slice and
  used approximately 99–128 MB incremental RSS. This is evidence for these
  fixture shapes, not arbitrary larger-than-memory operations.
- `pr-227-allocation-benchmark.py/json`: real CatBoost Pool construction, the
  installed price-contour grid adapter, and exact profiling. Fixture construction
  happens outside the measured child. No model fitting is included.

For 1M training rows, 250k validation rows and 32 numeric features, moving
validation loading after training Pool construction reduced total sampled peak
RSS from 806–808 MiB to 763–771 MiB. Raw allocations are released in the new
order, but allocator retention makes the benefit smaller than the raw-frame
size. Two runs per variant are reported; this is not a universal saving.

The resident optimiser grid measured 17–20 MiB incremental RSS for 500k rows
and 52–53 MiB for 2M rows (20 scenarios per quote, four constraints, 100k-row
read slices). The final grid remains resident; chunked reads do not make the
solver out of core.

Exact profiling of 17 columns originally rose from about 138 MiB incremental
RSS at 500k rows to 1,124–1,130 MiB at 2M rows. The shipped calculation writes
large whole-row distinct inputs once into temporary hash partitions and sums
exact distinct counts within them. Hash collisions cannot approximate away
distinct rows. The parent job removes scratch after the worker exits.

`pr-227-profile-benchmark.py/json` measures the production calculation. At 2M
rows, incremental peak fell to 404–409 MiB (total RSS 483–489 MiB), with
5.14–5.47 seconds elapsed versus the original 4.17–5.23 seconds. The extra write
is 68,385,851 bytes across 16 files/eight partitions. At 500k rows, a unique
column proves there are no duplicate rows and avoids that write. Scalar
verification checks duplicates, every null/distinct count, row count and schema
width without collecting the source. Per-column exact quantile/distinct state,
hash skew and allocator retention remain covered by existing execution limits.

Scoring now accepts proven-sliceable LazyFrames in its existing batch adapter,
avoiding the complete temporary input copy for single/multipart snapshot scans.
It preserves schema order, projection and empty-input semantics; complex input
keeps its existing owned staging file. The affected scorer suites passed 159
tests with one existing skip. Additional direct/multipart/derived input cases
prove no borrowed input is deleted after prediction or disk failures.

Optimiser chunk sizing now considers decoded sample widths as well as Parquet
page bytes. A dictionary regression previously chose 21,845 rows under a 64 KiB
target for strings that occupy 4,096 bytes each; it now chooses at most 16.
Grid setup now borrows an unchanged single-file scan under its caller's lease.
Predicates, slices, casts, multipart input and virtual columns retain one adapter
file. The existing solver API takes one filename. Before library construction,
the decoded sample and row count estimate the resident grid plus conversion,
sorting and reader overlap; an oversized estimate raises the existing admission
error. The conservative quote-ID allowance assumes up to one quote per row until
the solver validates layout, so it can overestimate multi-scenario inputs.

Diagnostics compute and release validation quality before loading holdout,
and predict through bounded batches into one final prediction array. Exact
metric/AvE inputs and the model's diagnostic Pool remain resident where required.
Weak-reference tests cover frame/target/prediction/weight lifetimes and preserve
offsets, returned validation/holdout labels, multidimensional predictions and
cancellation. The training memory-safety module passes 20 tests.

`pr-227-budget-benchmark-final.json` records the final admission rule: the
400k-by-400k validated join now executes natively in 0.125–0.133 seconds with
87–102 MiB incremental RSS, versus 1.25–1.27 seconds on the initial budgeted
sliced path. Large and unknown-multiplicity joins retain their existing recipe.
All 12 final runs verify the result. The 1M-row wide cases use 72–123 MiB
incremental RSS for approximately 3.04 GB decoded input under a 256 MiB allowance.

## CI repairs under validation

The [CI evidence inventory](pr-227-ci-implementation-evidence.md) records the
original failures and downloaded artifacts. The initial frontend bundle is now
290.5 KiB gzip against the unchanged 292 KiB gate. Cache settings load on demand.
The screenshot update comes from the inspected Linux CI artifact and reflects
the intended cache-control UI. The Explore profile-reset defect has ordinary
deferred-response regressions. The workflow ledger and write-sandbox fixtures
are corrected (68 relevant tests passed).

Mutation reports contain 165 executor and 11 JSON-cache survivors confined to
postponed type annotations. Those precise annotation locations receive the
existing `pragma: no mutate` convention; runtime operations and thresholds are
unchanged. Registry tests now check the default slice transparency and explicit
barrier behavior (40 tests passed). Final CI mutation results remain outstanding.

## Outstanding completion evidence

Single-file, multipart, recipe and batch-reader sizing now use decoded widths
and the existing context allowance. Disk checks cover cache staging, part/file
sinks and scored batches; refresh failures preserve old data and cleanup keeps
borrowed inputs. Targeted cache/source/scorer/file-operation checks passed
179 tests with one existing skip. A subsequent real process-death regression
and pointer-plus-pin-rollback failure regression also pass.

The corrected performance certificate passes at 1.5M and 6M rows/40 columns.
At 6M rows, sliced unnest uses 205.5 MiB incremental RSS in 4.69 seconds;
input-sliced filtering uses 169.0 MiB in 5.49 seconds. Both satisfy the declared
256 MiB allowance and the paired runtime tolerance. Native cases remain
diagnostics; the test no longer requires their memory use to grow.

The final profiling/analysis/optimiser group passes 123 tests; another 18 grid
construction/cleanup contracts pass. Source-write/workflow-ledger checks pass
68 tests. Specifications have been reconciled with the implemented contracts.
PR CI, including browser, compatibility, mutation and full coverage gates,
remains outstanding. These workload measurements do not guarantee bounded RAM
for every arbitrary Polars plan; existing typed refusals and worker limits are
still required.
