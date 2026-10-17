# Polars 2 review — 17 October 2026

A review of what moving Haute from Polars 1.44.2 to Polars 2.0 breaks,
changes silently, makes redundant and makes possible, made at
`polars-2-migration` `5c184d8d` (the tree `main` has after PR #311). It
records its sources, method, limits and evidence. It is a dated supporting
report and owns no work: every finding maps to a package in the
[Polars 2 roadmap](polars-2.md).

## Sources

- The Polars 2.0 release post (6 October 2026) and the pre-release post
  (2 September 2026) on pola.rs.
- The upgrade guide, read as its source (`docs/source/releases/upgrade/2.md`
  in pola-rs/polars at tag `py-2.0.0`).
- The GitHub release notes for `py-2.0.0`, `py-2.0.0-rc.2` and
  `py-2.0.0-rc.1`.
- Polars' configuration and out-of-core sources at `py-2.0.0`
  (`crates/polars-config`, `crates/polars-ooc`, `crates/polars-error`,
  `crates/polars-python/src/utils.rs`, `py-polars/src/polars/config.py`).

## Method

1. **Inventory.** A pattern scan for every API the upgrade guide removes or
   changes ran over every tracked file: 10,514 matching lines in 1,168 files,
   1,995 of them under `src/haute/`. Most are false positives (Python
   `str.join`, NumPy `flatten`, JavaScript `replace`). Two read-only audits
   classified them, one for row order (about 200 sites: every collect and
   every order-relevant operation, plus the 18 rendered step kinds) and one
   for the API surface (registries, the step renderer against 17 column
   dtypes, dtype vocabularies, generated and teaching code, tests). Each
   decisive claim the roadmap relies on was reproduced at its source or by a
   probe before it was used.
2. **Probes.** A separate environment held every locked dependency except
   Polars, which was 2.0.0 (`polars-runtime-32` 2.0.0). The same probe scripts
   ran under 1.44.2 (the repository's environment) and 2.0.0 and their results
   were compared.
3. **Bounded test runs under Polars 2.0.0.** Three runs over about 230 backend
   test modules chosen for the execution engine, caching, I/O, deploy scoring,
   rating, modelling, the optimiser, structured inputs, tracing, Explore,
   golden outputs, the reference pipeline and the assistant's example
   portfolio: 11,397 tests, 68 distinct failures. A scratch pytest plugin
   patched two causes already proven by probes (the IR version gate and the
   chunk-size key) inside the test process, so they would not hide others;
   each was also run without it. The 139 tests that drive the real
   `price_contour` library passed.
4. **Reviews.** Codex (`gpt-6-astra`, `ultra` reasoning) reviewed the plan;
   every finding it raised changed the plan, and the map duplicate-key finding
   was confirmed by probe before it did. A second opinion
   on the judgement calls ran its own probes (joins at three more shapes,
   ordered unpivot, the spilling switches and abort, `collect_batches`,
   `Expr.meta`); its numbers are cited below as the second opinion's.
5. **Decision measurements.** The same day the six decisions were taken by
   one rule: of the robust options, the one that uses least memory, then the
   fastest. The probes behind them ran under 2.0.0, one fresh process per
   case, reading the operating system's peak working set: spilling with its
   input on disk, a join's `validate` contract (under 1.44.2 as well), ordered
   full joins, a stable sort, typed membership against a comparison per
   member, a date parse with and without its format, and pyarrow's map cast.
   They are in *Measurements behind the decisions*.

Not run: the full backend suite, the frontend unit tests, the Playwright
suites, the mutation, performance and dependency-floor lanes, a deployed
container and Databricks. The first round's pull request runs the CI,
mutation and dependency workflows; the performance, container-smoke,
unlocked-resolve and property-exploration lanes run only on a schedule or by
dispatch, so `PL2-15` dispatches them against the branch before it merges.

## The bounded test runs

| Cause | Distinct failures | Package |
|---|---:|---|
| A typed error raised inside a Haute Python scan reaches the caller as `RuntimeError: haute-python-scan-failure:<token> …` | 35 | `PL2-03` |
| The chunk-size setting is written under Polars 2's name and read back under 1.44's | 14 | `PL2-08` |
| Joins collected with Polars' default engine no longer keep their order (several of these flake rather than fail every run) | 11 | `PL2-04`, `PL2-10` |
| `drop` of every column keeps the frame's height | 3 | `PL2-11` |
| The corpus and the I/O catalogue record 1.44.2; two `scan_csv` defaults became `None` | 3 | `PL2-01`, `PL2-07` |
| Data Output writes in spawned workers, which the plugin does not reach, are never sliced | 2 | `PL2-09` |

The 14 chunk-size failures come from tests that read the removed variable
directly and from workers they spawn, which the plugin does not reach.
Without the plugin `sliceable` answers no to every plan, so each assertion
that a plan is sliceable fails as well. Model families (CatBoost, LightGBM,
XGBoost, EBM, RustyStats, t-boost, the GLM terms), MLflow signatures and the
real pyfunc, the optimiser with the real `price_contour`, caching, structured
inputs, tracing, previews, Explore and the golden outputs passed.

## Probed behaviour, 1.44.2 against 2.0.0

| Behaviour | 1.44.2 | 2.0.0 |
|---|---|---|
| `pl.Config.set_streaming_chunk_size(1234)` writes | `POLARS_STREAMING_CHUNK_SIZE` | `POLARS_IDEAL_MORSEL_SIZE` |
| Exception raised in a registered IO source | `ComputeError` with its text | the exception itself, class and attributes intact, under every engine, in a background collect and through a sink |
| IO source `batch_size` on the default engine | `None` (the in-memory engine passes none) | the morsel size (100,000 by default; the streaming engine passed it on 1.44 too) |
| IR visitor version | 14 | `(15, 2)`; a scan's `n_rows` is an `(offset, length)` pair |
| 200,000-row left join, default `collect()` | left order kept | left order lost |
| Scan-to-scan left join, `engine="in-memory"` | left order kept in 8 of 8 runs | in about half |
| Ten-row left join, streaming engine | order lost (0 of 20) | order lost (0 of 20); `maintain_order="left"` keeps it |
| `join` then `unique(subset="k", keep="first")` | the first left rows | other rows, varying by run |
| Sort with ties over a Parquet scan, both engines | one result | varies by run; `maintain_order=True` fixes it |
| Lazy `drop` of every column, then `select(pl.len())` | 0 | 3 |
| `select([])` | `(0, 0)` | `(0, 0)` |
| `pl.LazyFrame().with_columns(pl.lit(1))` | 1 row | 0 rows |
| `pl.DataFrame().with_columns(<2-row series>)` | `(2, 1)` | `ShapeError` |
| `cast(pl.Date)` on text | value, with a deprecation warning | `InvalidOperationError`, even with `strict=False` |
| `pl.col(<Float64>).is_in([100, 300])` | 2 rows | `InvalidOperationError` |
| `explode` of `[[1], [], None]` | 3 rows, with a warning | 2 rows |
| `Int64 + UInt64` | `Float64` | `Int128`, which `to_numpy()` panics on and `to_arrow()` refuses |
| `std()` of a `Duration` | value | `InvalidOperationError` |
| Struct cast dropping a field | truncates | `InvalidOperationError` |
| Read a Parquet buffer straight after writing it | reads | `ComputeError` until `seek(0)` |
| Headerless CSV column names | `column_1`… | `column_0`… |
| CSV `schema` | matched by position | matched by name |
| `pl.repeat(...)` output name | `repeat` | `literal` |
| Hash values (`hash`, `hash_rows`) | — | all different |
| `estimated_size` of a frame with a literal column | 120 bytes | 84 bytes |
| `concat(..., how="horizontal", strict=True)` | silent | deprecation warning |
| `cut(...)` | silent | deprecation warning; `bin_intervals(..., labels, right_closed=True)` reproduces it, dtype included |
| `str()` of every dtype 1.44 produced | — | unchanged; `Map(String, Int64)` is new |
| Parquet map column holding `a: 1, a: 2, b: 3` | `List(Struct)`, all three entries | `Map` with `a: 2, b: 3`; scanning with a `List(Struct)` schema also returns two entries; pyarrow's map-to-list cast keeps all three |
| Left join with `validate="m:1"`, streaming engine | runs in the in-memory join node; 69% more peak | the same; 39% to 76% more peak |
| Categorical sort order | lexical | lexical |

The background-collect handle (`fetch`, `fetch_blocking`, `cancel`), the
internal module paths the sandbox names, `LazyFrame.map_batches(streamable=)`,
`pl.PartitionBy` sinks and `pl.Utf8` behave the same on both.

## Out-of-core spilling

Polars 2.0 reads its spilling configuration from the environment once per
process (`POLARS_OOC_MEMORY_BUDGET_MB`, `POLARS_OOC_MEMORY_BUDGET_FRACTION`
default 0.8, `POLARS_OOC_DISK_BUDGET_MB` default 64,000,
`POLARS_OOC_SPILL_DIR`, `POLARS_OOMKILL_THRESHOLD_MB`), and again whenever
`pl.Config.load` or `pl.Config.restore_defaults` reloads the environment; it
offers no per-option setter. It measures its own Rust allocations through its
global allocator and nothing else. A local probe on this Windows host:

| Probe | Result |
|---|---|
| 40-million-row sort sunk to Parquet, default budget | no spill; 2.79 GB peak resident; 1.7 s |
| Same, 150 MB budget | 384 spill files, 845 MB on disk; 2.61 GB peak resident; 9.7 s |
| Same, killed mid-spill | 768 files, 1.76 GB left in `<spill dir>/<pid>/` |

Polars' source adds three facts: it removes a dead process's directory only
when a later process starts spilling into the same directory; passing the disk
budget sets a process-wide abort flag; and a spill file that cannot be created
or written panics. The second opinion's probes added the rest: the abort
surfaces as an untyped `RuntimeError("BindingsError: \"query aborted, raise
POLARS_OOC_DISK_BUDGET_MB\"")` with Rust panics on stderr and also aborts the
next unrelated query in the process; the threshold is the smaller of the MB
value and the fraction times total memory, so a huge MB value alone does not
turn spilling off, `POLARS_OOC_MEMORY_BUDGET_MB=0` spills everything, and only
a very large `POLARS_OOC_MEMORY_BUDGET_FRACTION` turns it off; and a spill did
not lower peak resident memory in its runs either.

Both of those probes held their input in memory, which could hide a saving,
so a third read forty million rows (five numeric columns) from Parquet on disk
and sank each result to Parquet, one fresh process per case:

| Workload | Spilling off | 1 GB budget | 256 MB budget |
|---|---|---|---|
| Sort | 1.8 s, 3,341 MB | 13.5 s, 3,090 MB | 11.0 s, 3,970 MB |
| Stable sort | 1.9 s, 3,421 MB | 13.8 s, 2,969 MB | 10.8 s, 4,309 MB |
| Group-by into ten million groups | 0.8 s, 1,825 MB | 9.4 s, 1,603 MB | 9.9 s, 1,675 MB |
| Left join against ten million unique keys | 1.6 s, 2,423 MB | 1.6 s, 2,474 MB, no spill | 1.6 s, 2,486 MB, no spill |
| Window sum over a million keys | 1.3 s, 2,346 MB | 3.0 s, 2,172 MB | 2.9 s, 1,898 MB |

The figures are the process's peak working set; spill files peaked at 0.6 to
1.5 GB on disk. A budget moved the peak by between -19% and +26% and never
brought it near the budget, at 2.2 to 12 times the time, and the join did not
spill. Every spilling run here was on Windows, where Polars allocates through
mimalloc; Linux, with jemalloc, is `PL2-17`'s to measure.

## Row order

Haute's own internal queries are order-safe on 2.0: internal joins that feed an
order-dependent consumer set `maintain_order`, internal sorts that feed a
`head` or a slice carry a unique tiebreaker, and Model Score attaches
predictions to the frame it scored. The changes are where Haute collects a
user's pipeline with the default engine (`Pipeline.run`, `Pipeline.score`, an
eagerly collected Edge Join, Workbench tables) and where its step renderer
leaves order to Polars (the Join step and Edge Join without **Maintain
order**, **Sort rows**, **Unpivot to rows**). One defect predates 2.0: deploy
scoring has always collected on the streaming engine and answers request row
*i* with result row *i*, so a multi-quote request through an unordered join can
be answered against the wrong quotes on 1.44 as well; and even with every
order defined, an added sort, an inner join or a one-to-many join changes the
rows a positional answer pairs with, and a row count cannot tell (requests
`A, B` inner-joined to a lookup holding `A` twice give two rows, both for A),
so deployed answers need a proof that the pipeline keeps one row per request in
request order, or a request identity, rather than an order. A positional training
split inherits whatever order its prepared frame has. On the common pricing
shape (ten million quotes against a 5,000-row lookup) an ordered streaming join
measured within a tenth of an unordered one's time at the same peak memory;
the second opinion found the same at three more shapes (one-to-many with
fan-out four, and three million rows on each side), with an ordered full join
the worst case at 23% more time and 16% more peak memory, a stable sort as fast
as an unstable one, and the streaming sink keeping a `maintain_order` join's
order. Defining an unpivot's order through a row index and a sort cost three
times its time and memory.

## Measurements behind the decisions

Polars 2.0.0, one fresh process per case, peak working set; the joins read
the Parquet files of the spilling probe above and sank their result to
Parquet.

| Left join, `maintain_order="left"` | `validate="m:m"` | `validate="m:1"` |
|---|---|---|
| 1,000 requests against a ten-million-row unique lookup | 0.15 s, 661 MB | 0.16 s, 921 MB |
| Forty million rows against a 5,000-row lookup | 1.27 s, 2,107 MB | 1.28 s, 3,637 MB |
| The same under Polars 1.44.2 | 1.23 s, 2,144 MB | 1.26 s, 3,619 MB |
| Forty million rows against ten million unique keys | 1.60 s, 2,422 MB | 1.99 s, 4,251 MB |

With `POLARS_VERBOSE` set, both versions report the validated join running in
the streaming engine's in-memory join node; a duplicated key fails with
`ComputeError: join keys did not fulfill m:1 validation`. `PL2-04` therefore
proves a deployed lookup's join keys unique once, when the deployment is built
and over the bytes it ships, rather than through `validate`.

| Full join, twelve million rows a side | Unordered | `maintain_order="left_right"` |
|---|---|---|
| Sunk to Parquet as it is | 0.55 s, 1,323 MB | 0.56 s, 1,400 MB |
| Followed by an unordered group-by | 0.73 s, 1,360 MB | 0.74 s, 1,372 MB |

A stable sort of forty million rows took 1.9 s and 3,421 MB against an
unstable sort's 1.8 s and 3,341 MB (the spilling table's first column).

| "Is one of" on a Float64 column, rows | Members | Typed `is_in` | A comparison per member |
|---|---:|---|---|
| Ten million | 30 | 0.00 s, +6 MB | 0.01 s, +9 MB |
| Ten million | 300 | 0.00 s, +6 MB | 0.05 s, +106 MB |
| Ten million | 1,000 | 0.00 s, +6 MB | 0.20 s, +359 MB |
| Forty million | 30 | 0.01 s, +7 MB | 0.02 s, +9 MB |
| Forty million | 300 | 0.01 s, +6 MB | 0.17 s, +28 MB |
| Forty million | 1,000 | 0.01 s, +6 MB | 0.57 s, +311 MB |

Memory is the peak above the frame itself. Typed membership needs only the
column's numeric kind: `Int64` members matched `Int8`, `Int32` and `UInt16`
columns, `Float64` members matched `Float32` and `Float64` columns, and
decimals parsed from text at the column's scale matched `Decimal(38, 2)` and
`Decimal(10, 4)` columns, each list passed with `implode()`. A typed list
without `implode()` raises a deprecation warning, and a list of another kind
(`Int64` members on a float column, `Float64` members on a `Decimal` column)
is refused.

`str.to_date` over ten million rows took 0.01 s with the format
`"%Y-%m-%d"` and 1.04 s inferring it, at the same peak (413 and 409 MB);
`Expr.meta.is_row_separable()` is true with the format and false without it.
pyarrow 23.0.1 cast a two-million-row map column (six million entries, 92 MB)
to a list of key-value structs in half a millisecond, allocating nothing and
sharing all three of its child buffers, with every entry equal; re-encoding a
36 MB Parquet file of ten row groups one row group at a time took 0.88 s and
grew resident memory by 51 MB.

## The API surface

The step renderer, run against 17 column dtypes and through Haute's executor,
fails on 2.0 for a text-to-date cast, a numeric "is one of" on a decimal
column, a categorical-to-integer cast, `std` or `var` on a duration, and `log`
or `exp` on non-numeric input. `Int128` arises from ordinary arithmetic and
then fails at the modelling and export conversions and in deploy's MLflow
mapping, and the feature contract records it as `Int64`. Map and extension
columns from Arrow and Parquet sources do not fit Haute's dtype codec. Input-less
nodes start from a frame that now has height 0. The new `pipe_with_dtype`
escapes the cardinality analysis. The operation registry lists operations 2.0
removed and none of those it added, and no test compares it with the installed
Polars. No identity of a persisted snapshot or cache includes the Polars
version. The 38 tests that expect a Polars exception class or message still
get the same class and text, and the compatibility corpus's analyser-derived
fields show no drift.

## Third-party libraries

`price_contour` 0.6.0's native module embeds pyo3-polars 0.25 and the Polars
0.52 Rust crates and calls `Series._import_arrow_from_c`,
`_newest_compat_level` and `to_arrow(compat_level=...)` on the Python side;
its 139 real-library tests pass on 2.0. `rustystats` 0.9 and `t-boost` 0.8.1
use Polars from Python and pass Haute's family tests. Of the libraries Haute
hands data to, only scikit-learn reads `__dataframe__`, which Polars 2.0
removes, and Haute passes it NumPy arrays.

## What Polars 2 makes redundant, and what it does not

| Engine mechanism | Verdict on Polars 2 | Package |
|---|---|---|
| Parking a Python-scan exception under a token and re-raising it from a `ComputeError` | Redundant and now harmful: Polars 2 delivers the exception itself | `PL2-03` |
| `execution_collect`'s `auto` engine and the `auto`/`streaming` checkpoint labels | Redundant: every collect names its engine | `PL2-10` |
| Collecting ordered join parts in memory, and the whole-join native path for unordered joins only | Redundant: Polars 2's streaming sink keeps a `maintain_order` join's order, and ordered joins cost no more (the second opinion's measurements); confirmed on a large chunked Edge Join before removal | `PL2-04` |
| One query per batch in `bounded_collect_batches` | Still needed: `collect_batches` still runs ahead of a slow consumer on 2.0 (the second opinion's measurement) | — |
| `_malloc_trim` (process-heap compaction, glibc trim) | Possibly dead on any Polars version: Polars allocates through mimalloc on Windows and jemalloc on Linux; measured | `PL2-17` |
| Lineage's fail-closed rule for a program that can drop its last column | Narrower: `drop` keeps a frame's height, `select([])` still empties it | `PL2-11` |
| Carrier columns for an empty projection (`projected_or_carrier_columns`) | Still needed: a zero-column `select` still yields no rows | — |
| Hard-capped workers and the static admission estimate | Still needed: spilling is a soft target that counts only Polars' own allocations, did not dependably lower peak resident memory when it ran, did not spill a join, and never triggers below a worker's cap unless Haute sets its budget | `PL2-16`, `PL2-17` |
| A hand-maintained operation registry | Still needed, since Haute classifies source text and free code; Polars 2's `Expr.meta.is_row_separable()` and `is_length_preserving()` give it a test oracle | `PL2-14` |
| Capture points and shared snapshots | Still needed: pola-rs/polars#24206 (multi-join streaming memory) is open at 2.0 | `PL2-17` (wording) |
| Chunked writes and the explode chunk cap | Still needed: the streaming chunk is still process-wide and still multiplied by an `explode` | `PL2-08` |
| `sliceable` and the optimiser's input reuse, through the private IR visitor | Still needed; the version gate moves to 15 | `PL2-09` |

## Checked and unaffected

- `dataframe_frame_input_fingerprint` already over-invalidates on a hash change.
- The hash buckets in `_chunked_writes` and `_frame_profile` live within one
  run, as do the optimiser's key fingerprints.
- Deploy scoring and OUTPUT assembly already collected on the streaming engine.
- No source file in `src/haute/` calls an API 2.0 removed; the remaining hits
  are Haute names, other libraries' methods or the registry's data.
- The CI floors and unlocked-resolve lanes follow the new cap without change.
