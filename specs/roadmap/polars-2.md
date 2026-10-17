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
out-of-core spilling kept off and contained (the pin turns it on), and six
packages that change what a user sees or where Haute writes (`PL2-04`,
`PL2-06`, `PL2-07`, `PL2-12`, `PL2-16`, `PL2-19`). Their designs were decided
on 17 October 2026 by one rule: of the robust options, the one that uses least
memory, then the fastest. Each entry records the measurement behind its
choice. The second round re-establishes the engine's memory evidence under
Polars 2 and, on that evidence and by the same rule, decides where to turn
spilling on. The pin moves only with the whole first round, so `main` never
runs Polars 2 with a seam built for 1.44.
The plan was reviewed by Codex and by a second opinion whose own measurements
it cites as such. Two defects that predate Polars 2 are fixed on the way: a multi-quote
deploy request through an unordered join can be answered against the wrong
quotes (`PL2-04`), and a training run's seed does not fix its split when the
prepared frame's order varies (`PL2-05`).

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| PL2-01 | Planned | P1 | Haute requires Polars 2, and its version-pinned snapshots describe it. |
| PL2-02 | Planned | P1 | Nothing Polars 1 computed is reused once Polars 2 runs. |
| PL2-04 | Planned | P1 | Row order is defined wherever Haute builds the query, and deployed answers stay with their quotes. |
| PL2-05 | Planned | P2 | Training, Workbench tables and Haute's tied cuts give the same result on every run. |
| PL2-06 | Planned | P2 | The step editor renders only code Polars 2 runs, and **Change types** turns text into dates. |
| PL2-07 | Planned | P1 | CSV inputs read as they did, or fail with a message that says what changed. |
| PL2-10 | Planned | P2 | Every Haute collect names its engine. |
| PL2-12 | Planned | P2 | New Polars dtypes meet Haute's dtype vocabularies loudly or are normalised where they enter. |
| PL2-13 | Planned | P2 | Code Haute generates and teaches runs on Polars 2 without deprecations. |
| PL2-14 | Planned | P2 | The operation registry and analysers describe Polars 2. |
| PL2-15 | Planned | P2 | The test suite asserts behaviour rather than Polars 1 accidents. |
| PL2-16 | Planned | P2 | Out-of-core spilling stays off wherever Haute starts Polars, and anything that spills lands in Haute's storage. |
| PL2-17 | Reverify | P2 | Every operator memory policy and RAM estimate rests on Polars 2 evidence. |
| PL2-18 | Reverify | P3 | Worker caps start from a Polars 2 baseline. |
| PL2-19 | Planned | P2 | A deployed pipeline runs the Polars the editor ran. |
| PL2-20 | Planned | P3 | The `price_contour` binary boundary is guarded against Polars' private API. |
| PL2-21 | Deferred | P3 | Structured inputs and Data Inputs can carry a JSON object as a `Map` column. |
| PL2-22 | Deferred | P3 | Banding and the step editor offer quantile and rank binning. |

## Planned improvements

### PL2-01 — Haute requires Polars 2
**Why:** `pyproject.toml` admits `polars>=1.44.2,<2` and `uv.lock` holds
1.44.2. Three version-pinned artefacts describe 1.44.2 and fail under 2.0:
the I/O argument catalogue (`src/haute/_polars_io_arguments.json`, generated
by `scripts/extract_polars_io.py`), which decides what the Data Input and
Data Output nodes may pass to a reader or writer; the compatibility corpus
(`tests/polars_compatibility_corpus.json`), which records how the analysers
classify a representative shape of every registered operation; and the
served model's pip requirements, which ask for `polars>=1.44.2`. Polars 2.0
removes reader and writer arguments (`rechunk`, `memory_map`, `n_threads`,
`batch_size`, `sample_size`, `allow_missing_columns`, `file_cache_ttl`,
`retries` and others) and adds some (`extra_columns`, `missing_columns`,
`infer_schema_files`, `with_column_names`). The registry's own exclusion sets
still name `retries`, `file_cache_ttl` and `dtypes`.

**Plan:** Move the floor and cap to `polars>=2.0,<3` at the newest 2.0 patch
when the round starts, and relock `polars` with `polars-runtime-32`.
Regenerate the catalogue, review its diff argument by argument, tidy the
registry's exclusion sets, and give a saved config that still names a removed
argument the canonical-only treatment: it is refused by name, never dropped.
Regenerate the corpus with `python -m tests.test_polars_compatibility_corpus`
and review each changed classification against the registry before committing
it; the analyser-derived fields showed no drift under 2.0, so any change is in
the planner's strategy fields and is a finding for `PL2-17`, not a fixture to
accept. Move the served model's requirement to the exact pin `PL2-19` decided,
and the example pin in `docs/COMMIT_STANDARDS.md`. The pin lands with the rest
of the first round on one branch.

**Acceptance:** `uv lock --check` passes on Polars 2; the interface-contract,
compatibility-corpus, dependency-contract and pin-lint modules pass; a Data
Input config naming `rechunk` fails validation with a message naming it; CI's
floors lane resolves Polars 2.0.0 and the unlocked lane the newest 2.x.

**Dependencies:** None. It ships with the first round's other packages.

**Evidence:** `pyproject.toml`; `uv.lock`; `src/haute/_polars_io_arguments.json`;
`scripts/extract_polars_io.py`;
`tests/test_polars_io_interface_contracts.py::TestCommittedSnapshotMatchesPinnedPolars`;
`tests/test_polars_compatibility_corpus.py::test_corpus_pins_the_installed_polars_version`;
`src/haute/deploy/_mlflow.py::_pip_requirements`; `docs/COMMIT_STANDARDS.md`.

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

**Dependencies:** PL2-01; `PL2-12` for map-bearing generations.

**Evidence:** `src/haute/_node_snapshots.py::NODE_SNAPSHOT_EXECUTION_SEMANTICS_VERSION`;
`src/haute/execution.py::PREVIEW_EXECUTION_SEMANTICS_VERSION`;
`src/haute/execution.py::dataframe_frame_input_fingerprint`;
`src/haute/_source_cache.py::SourceCacheIdentity`;
`src/haute/deploy/_schema.py::deploy_schema_cache_fingerprint`.

### PL2-04 — Row order is defined wherever Haute builds the query
**Why:** On Polars 2.0 a `collect()` with the default engine runs the
streaming engine, which keeps a join's input order only when `maintain_order`
says so; 1.44's default was the in-memory engine, which kept it. Paths that
still used the default changed with the upgrade: `Pipeline.run` and
`Pipeline.score` (`_collect_standalone_output`) returned a three-row Edge
Join's quotes in base order in about half of 20 runs (every run on 1.44, none
at a thousand rows), an eagerly collected Edge Join in 16 of 20, and a
Workbench Output's many-row tables follow the same frames. On 2.0 the
in-memory engine is no longer a guarantee either: it kept a scan-to-scan
join's left order in only about half of repeated runs. Two step kinds changed
without any join: **Sort rows** renders no `maintain_order`, and on 2.0 ties
over a Parquet scan come out in a different order from run to run on both
engines (stable on 1.44); **Unpivot to rows** now emits row-major order where
1.44's default path emitted variable-major, and Polars guarantees neither.
Order decides values wherever an order-dependent operation follows: **Limit
rows**, `first` or `last`, `cum_sum`, `shift`, forward and backward fills, a
row index, `unique(keep="first")` (probed: `join` then `unique(keep="first")`
keeps different rows on every 2.0 engine, `maintain_order` on the `unique`
notwithstanding).

One consequence predates Polars 2. Deploy scoring has always collected on the
streaming engine, which reorders a left join even at ten rows on 1.44 and 2.0
alike (probed: no run of 20 in order), while OUTPUT assembly answers request
row *i* with result row *i* (`render_output_document`: "a batch's answers stay
aligned with its requests"). A multi-quote request through an Edge Join or a
Join step without **Maintain order** can therefore be answered against the
wrong quotes today. Rating steps are safe: their lookups already set
`maintain_order="left"`.

The step editor already renders **Remove duplicates**, **Group and
aggregate** and **Pivot to columns** with `maintain_order=True`. Ordered joins
cost nothing measurable on Polars 2's streaming engine at pricing shapes: ten
million quotes against a 5,000-row lookup (this review), and in the second
opinion's measurements five million against 5,000, two million against 50,000
keys with fan-out four, and three million against three million, where the
worst case, a full join ordered `"left"`, took 23% longer at 16% more peak
memory, and one ordered `"left_right"` 18% longer at 7% more. A stable sort
measured as fast as an unstable one. An ordered unpivot did not:
defining its order through a row index and a sort took three times the time
and memory of a plain unpivot. Polars 2's streaming sink keeps a
`maintain_order` join's order, so the chunked writer's special treatment of
ordered joins is no longer needed: today it takes its whole-join native path
only for unordered joins (`_join_within_memory_budget`), collects ordered parts
in memory instead of sinking them, and refuses to chunk an ordered full join
(`full_join_ordered`), which under an ordered default would send every full
Edge Join through one native sink.

**Plan:** Decided on 17 October 2026; specify in the execution-engine,
frontend-node-editors and deploy specifications, restating the io-layer policy
(a query that defines its order keeps it through every seam; an unordered one
has none). Haute's own query builders define order wherever it costs little
or nothing:
- `maintainOrder` becomes a required field of the Join step and the Edge Join
  (canonical-only: a saved join without it is refused by name, and Recover
  offers the value). The editor writes `"left_right"` for a new inner, left,
  full or cross join, `"right_left"` for a right join and `"left"` for a semi
  or anti join (`"left"` alone leaves the order of one row's matches open in a
  one-to-many join, which varied between runs), and **None** remains the
  explicit choice. The order costs memory only where something reads it: a
  full join of twelve million rows a side ordered `"left_right"` peaked 6%
  higher than an unordered one when it was sunk as it was, and 1% higher when
  an unordered group-by followed it; at lookup and one-to-many shapes the cost
  was within noise.
- **Sort rows** renders a stable sort, which on forty million rows took 1.9
  seconds against 1.8 and peaked 2% higher than an unstable one.
- **Unpivot to rows** keeps an undefined order, said on its node page, with a
  following sort where order matters: defining its order cost three times the
  time and memory.
- The chunked writer drops its in-memory ordered parts, which hold a whole part
  in memory where a sink now streams it in order, and its unordered-only native
  path, and chunks ordered full joins, on a measurement of a large chunked Edge
  Join.

Leaving order undefined is cheaper still and was rejected: `first`, `cum_sum`,
a row index, a positional answer and a written file would change from run to
run. Collecting on the in-memory engine was rejected: it uses more memory, and
on 2.0 it neither keeps a scan's join order nor settles sort ties.

A defined order is not enough for deployed answers: a sort the author adds
reorders a request's rows, an inner join or a filter drops one, a one-to-many
join or an unpivot repeats one, and **None** stays available. Deploy validation
therefore proves, from the cardinality analysis and the operation registry,
that every operation between the request input and OUTPUT keeps exactly one row
per request, in request order: row-local expressions, Haute's row-preserving
nodes (Rating Step lookups, Model Score), and left joins driven by the request
side, with a request-side order, whose other side is unique. Uniqueness does
not rest on Polars' `validate`, which on 1.44 and 2.0 alike sends a join
through the streaming engine's in-memory join node, at 39% to 76% more peak
memory from 1,000 requests against a ten-million-row lookup to forty million
rows against 5,000. The other side is unique by construction (a Rating Step
lookup, or a group-by or `unique` on the join keys that the analysis follows
to the join), or it is computed only from inputs the deployment carries. Then
Haute evaluates that side's join keys once, when the deployment is built,
through the pipeline's own code for that branch, a Data Input's post-load code
included, over exactly the bytes the deployment ships (a snapshot's leased
generation, or a direct Parquet input's bundled copy), with a streaming
duplicate check. The check is bound to each artefact's content signature,
which the build checks again when it copies the artefact, so a file that
changes in between fails the build. A side derived from the request needs a
structural proof or a declared `validate` of `"m:1"` or `"1:1"`, whose
in-memory join the request bounds; `"1:m"` and `"m:m"` prove nothing about the
other side, and the editor never adds a contract. Any other operation is
refused with the node and the operation named: a filter, an inner, semi or
anti join (which drop requests), a join whose other side is not proven unique
(which can repeat one), a sort or a join with no order (which reorder), an
explode or unpivot, and free code the analysis cannot prove. A count alone is
not enough: requests `A, B` inner-joined to a lookup holding `A` twice give
`A, A`, two rows in order, with B answered by A's row. A run-time check that
the result has as many rows as the request stays as a backstop, failing loudly
with both counts; a single-row request needs neither.
The proof costs nothing per request. It tightens the contract: a pipeline that
declines a quote by filtering it out is refused for multi-quote requests, so a
decline becomes a flag column on the answer, stated on the deploy page.

Carrying a private request identity to OUTPUT and realigning by it was
rejected: it carries an extra column through every operation and realigns at
the end, and it fails every pipeline whose code selects named columns or
aggregates request rows. The count check alone was rejected: it misses a drop
and a repeat that cancel out.

The Edge Join, Join, Sort and Unpivot node pages state the result.

**Acceptance:** execution parity and deployed answers are tested apart. The
frame a pipeline with an Edge Join, a Join step and a sort with ties produces is
identical, rows and order, across 20 runs of a preview, `Pipeline.run`,
`Pipeline.score`, a Data Output write and the deploy scorer before OUTPUT
assembly, and an unpivot's frame is identical as a set; the edge-join, lazy
join-projection, lineage, standalone-node and codegen-equivalence tests that
failed on order pass without sorting; a saved join without `maintainOrder` is
refused by name; the chunked writer's handling of ordered joins rests on a
recorded measurement. Separately, a multi-quote deploy request through ordered
left joins against a bundled lookup with unique keys, with no `validate`, is
answered in request order. The deployment build refuses, naming the input and
the key, the same lookup with a duplicated key, a lookup whose keys become
duplicated only through its post-load code, and a direct Parquet lookup
replaced between the check and its copy into the bundle. Deploy validation
refuses, naming node and operation, a pipeline with a sort, an unordered join,
a join whose other side is not proven unique (including one whose only
contract is `validate="1:m"` or `"m:m"`), an inner join, a filter or an
unpivot between the request input and OUTPUT, including requests `A, B` against
a lookup holding `A` twice, where a drop and a repeat leave the count
unchanged. A pipeline whose row count changes at run time despite the proof
fails the request with both counts.

**Dependencies:** PL2-01.

**Evidence:** `src/haute/_edge_join.py::build_edge_join_kwargs`;
`src/haute/_polars_steps.py::_Renderer`;
`src/haute/pipeline.py::_collect_standalone_output`;
`src/haute/deploy/_scorer.py::score_graph_lazy`;
`src/haute/deploy/_bundler.py`;
`src/haute/_output_assembler.py::render_output_document`;
`src/haute/_chunked_writes.py::_join_within_memory_budget`;
`src/haute/_polars_operations.py::POLARS_OPERATIONS`;
`docs/building-models/nodes/edge-join.md`;
`tests/test_edge_join.py::test_edge_join_declaration_joins_in_pipeline_run`;
`tests/test_codegen_execution_equivalence.py::test_multi_frame_api_input_run_matches_executor_by_each_frame_label`;
`specs/io-layer/low-level.md`.

### PL2-05 — Training, Workbench tables and Haute's tied cuts give the same result on every run
**Why:** Haute's internal queries are almost all order-safe on 2.0: every
internal join that feeds an order-dependent consumer sets `maintain_order`,
internal sorts that feed a `head` or a slice carry a unique tiebreaker, and
Model Score attaches predictions to the frame it scored. What remains reads a
frame's order without fixing it:
- Training selects rows by position. The seeded downsample
  (`_seeded_training_sample`), the random split and partition mask
  (`_random_split`, `_random_mask`), the temporal mask's tie at its boundary,
  and the evaluation and SHAP samples pick rows by index over the prepared
  frame, whose order is its pipeline's streaming order. Canonical grouped
  validation and cross-validation (`_balanced_groups`, `_groups_near_target`)
  collect groups in the order rows arrive and then shuffle that list with the
  seed, so equal-sized groups can change partition when the order does. The
  same seed then trains on different rows when that order varies: an unordered
  join on 1.44 and 2.0, a sort with ties on 2.0.
- A Workbench Output's many-row table is the connected frame's rows in the
  order they arrive.
- Tied counts decide which categories, grid values, segments and values make a
  cut in the training charts' category fold, the partial-dependence grid, the
  deploy impact report and the column profile's top values.
- The optimiser's grid builder reads whole quotes from the solver input that
  `bounded_sink` writes; an unordered join placed after a Scenario Expander
  interleaves a quote's rows there, and whether price_contour's builder
  rejects or mis-builds that input has not been traced.

**Plan:** With `PL2-04` in place, give training a selection that does not
depend on row order, and never a Polars hash, whose values changed between
1.44 and 2.0, so a model could not be retrained from its seed after an
upgrade. In order of preference: sort the prepared frame by its row identity
before the seeded positional selection; where no identity exists, sort it by
every column, measured once per training run; where a Python pass is
affordable, a Haute-owned hash as the older group helper already uses (MD5 of
the seed and the value). Order the groups of grouped validation and
cross-validation by their key rather than by arrival before the seeded
shuffle. Add tiebreakers to the four cosmetic cuts; give a many-row Workbench
table an order from its Workbench Output definition or state that it follows
the upstream order; and trace the optimiser's grid builder with interleaved
quotes, making it fail loudly if it does not already.

**Acceptance:** a training run repeated over a permutation of the same
prepared rows selects identical train, validation and holdout rows (whether the
model is then identical also depends on the model library's own determinism,
which is not this package's); canonical grouped validation and cross-validation
assign every group to the same partition under a permutation, keep each group
whole and keep their partition-size contracts; the four cuts are identical
across repeated runs with ties; an interleaved solver input fails with a
message naming the quote column, or is proven correct.

**Dependencies:** PL2-04.

**Evidence:** `src/haute/routes/_training_preparation.py::_seeded_training_sample`;
`src/haute/modelling/_split.py::_random_split`;
`src/haute/modelling/_split.py::_random_mask`;
`src/haute/modelling/_evaluation.py::_balanced_groups`;
`src/haute/modelling/_evaluation.py::_groups_near_target`;
`src/haute/modelling/_shap.py`;
`src/haute/_workbench_output.py::workbench_response`;
`src/haute/modelling/_metrics.py`; `src/haute/deploy/_impact.py`;
`src/haute/_frame_profile.py`; `src/haute/routes/_optimiser_input.py`.

### PL2-06 — The step editor renders only code Polars 2 runs, and Change types turns text into dates
**Why:** Rendering every step template against 17 column dtypes on both
versions, and running the failures through Haute's renderer and executor,
found these steps failing on 2.0 where 1.44 ran them:
- **Change types** renders `pl.col(c).cast(pl.Date)` (and `Datetime`)
  whatever the column's type, and the formula's `cast` and `try_cast` render
  the same cast. Polars 2.0 refuses a cast from text to a date, datetime or
  time, even with `strict=False`, so `try_cast` raises where it returned nulls.
  Text columns of dates are what a CSV or JSON source usually delivers.
- A **Filter rows** condition "is one of" with whole numbers on a float
  column renders `is_in([100, 300])`, which 2.0 refuses as a lossy comparison
  (`'is_in' cannot check for Float64 values in List(Int64) data`); JSON cannot
  tell `100` from `100.0`. Integer columns with fractional members and
  `Decimal` columns with float members fail the same way.
- A cast from a categorical column to an integer raises (1.44 returned the
  category codes, which was never what an analyst meant).
- `std` and `var` aggregations raise on a duration column, and `log` and
  `exp` raise on non-numeric input (1.44 returned nulls, nonsense, or panicked
  on a categorical).
- Date members of "is one of" render a same-dtype `Series`, which Polars warns
  is ambiguous and asks to `implode`.
The renderer turns steps into code without the input's schema, so it cannot
choose a method from the column's type; the editor knows the column types. The
refused cast also passes `collect_schema()` and fails only when rows are read,
so schema resolution does not catch it at edit time. `str.to_date` without a
format infers it from the column, so Polars does not call it row-separable and
Haute's chunking admits it only with a literal format.

**Plan:** Decided on 17 October 2026; specify in the frontend-node-editors and
execution-engine specifications. The saved step says what it does. **Date** and
**Datetime** stay the targets; for a text column the editor records in the step
that the cast parses text, with a required format, and the step renders
`str.to_date(format)` or `str.to_datetime(format)` (`strict=False` for
`try_cast`); the formula language gains `to_date` and `to_datetime` with a
required format. The format is what makes the parse cheap: with it Polars calls
the parse row-separable, so chunked writes admit the step and its memory stays
bounded, and ten million dates parsed in 0.01 seconds where inferring the
format took 1.04. The renderer stays schema-free, the same step renders the
same code whatever arrives, and a column that stops being text fails loudly.
Separate **Date from text** and **Datetime from text** targets were rejected as
equivalent with a longer type list, and passing the input schema to the
renderer to choose the parse silently was rejected because the same step would
render different code for different upstream data, and the code shown to the
user would change without an edit.

Independently: a numeric "is one of" renders `is_in` over its members typed
to the column's numeric kind, which the step records from the dtype the editor
shows: whole numbers for an integer column (a fractional member, which can
never match, is refused in the editor), floats for a float column, and for a
`Decimal` column decimals at the column's scale, parsed from text so they stay
exact; the list is passed with `implode()`. Typed membership costs the same
whatever the member count, where a comparison per member
(`pl.any_horizontal(pl.col(c) == v, …)`) matched it up to 30 members but took
0.57 seconds and 311 MB more on forty million rows at 1,000 members. A column
whose numeric kind changes upstream fails loudly with the step's name; a change
of width within a kind keeps working. Offer integer casts, `std`/`var`, `log`
and `exp` only for column types they accept, from the dtype the editor shows;
render date members with `implode()`. A saved step Polars 2 refuses is caught
where the editor knows the column type, and otherwise fails at run time with
the step's name and the replacement.

**Acceptance:** each case above, rendered by Haute's renderer and run on
Polars 2 on its own column type, returns the rows 1.44 returned, or is refused
in the editor, or fails at run time naming the step; a text-to-date step's
generated code shows the parsing call with its format, and one without a format
is refused; a numeric "is one of" matches by value on integer, float and
`Decimal` columns of every width, and one whose column changes numeric kind
fails naming the step; the catalogue parity test covers the new options and
functions; golden render tests gain a numeric "is one of" on a float column and
on a `Decimal` column.

**Dependencies:** PL2-01.

**Evidence:** `src/haute/_polars_steps.py::_Renderer`;
`src/haute/_polars_steps.py::CAST_DTYPES`;
`frontend/src/panels/editors/polarsSteps/catalogue.ts`;
`tests/test_polars_steps.py`; `tests/test_polars_steps_catalogue.py`.

### PL2-07 — CSV inputs read as they did, or fail with a message that says what changed
**Why:** Polars 2.0 changes CSV reading in ways Haute can see. A `schema`
given to `scan_csv` or `read_csv` is matched by name against the header
instead of by position: a Data Input config that renamed columns positionally
through `schema` now raises, and one that listed columns in another order
than the file was silently mislabelled on 1.44 and now reads the right
values. Headerless files name their columns `column_0, column_1, …` instead
of `column_1, column_2, …`, in `read_excel` and `read_ods` as well as the CSV
readers, so a pipeline that reads a headerless CSV, Excel or ODS file and
refers to `column_1` silently receives the next column. `raise_if_empty` and
`truncate_ragged_lines` default to `None`, resolved from other arguments; a
guard test written for exactly this fails under 2.0 and asks for a decision.
A multi-file scan infers its schema from the first ten files only, and a
`schema_overrides` list must cover every column. Haute's own scan
(`read_source`) passes a dict of overrides, which is unaffected.

**Plan:** Decided on 17 October 2026; specify in the io-layer specification.
Haute's own CSV scan passes `raise_if_empty=True` and
`truncate_ragged_lines=False` explicitly, keeping today's loud failures for
empty and ragged files. A headerless Data Input, through any reader the
registry admits with `has_header` (CSV, Excel, ODS), must name every one of its
columns, through a full `schema` (which still binds a headerless file by
position on 2.0), `new_columns` for CSV (which Polars would accept for only some
of them), or whichever reader option or config field the package settles on for
Excel and ODS, and a config without them is refused with a message that says
why, so no pipeline depends on a generated column name. A CSV `schema` whose
names are not the file's header is refused with a message pointing at
`new_columns` and `schema_overrides`, rather than left to Polars' own hint,
`extra_columns='ignore'`, which would silently drop the mismatched columns.
By-name matching, the ten-file inference and the full-coverage override list
are adopted and documented. The checks read only the config and the file's
header, so they cost no memory or time beyond the scan. Adopting every Polars 2
default was rejected: a pipeline that refers to `column_1` would silently read
the next column.

**Acceptance:** the CSV default guard passes against the explicit arguments;
a headerless CSV, Excel and ODS config without names for every column fails
validation with a message saying how to name them, a CSV config naming only
some columns fails the same way, and a headerless CSV with a full `schema`
reads; a positional-rename `schema` fails with Haute's message; a declared
schema in another order than the header reads each column by name; the Data
Input page documents the behaviour.

**Dependencies:** PL2-01.

**Evidence:** `src/haute/_io.py::read_source`;
`src/haute/_polars_io_registry.py`;
`tests/test_polars_io_interface_contracts.py::TestNoBreakingDriftAcrossTheCap`;
`docs/building-models/nodes/data-input.md`.

### PL2-10 — Every Haute collect names its engine
**Why:** `execution_collect` defaults to `engine="auto"`, and 20 of its 25
callers pass no engine; nor do the direct `LazyFrame.collect()` calls in
deploy batch scoring, the scoring container, the RAM estimate's probe,
Workbench parameters, Explore's relationships, the native pyfunc and the
optimiser's routes. Polars 2.0 resolves `auto` to the streaming engine for a
lazy query, so each of those calls changed engine with the upgrade, and each
would change again if a user or host set `POLARS_ENGINE_AFFINITY`. The engine
decides memory behaviour and which operations stream; on 2.0 it no longer
decides join order, which only `maintain_order` or a sort fixes (`PL2-04`).

**Plan:** Make the engine a required argument of `execution_collect` and the
background collect, and give every direct collect in `src/haute/` an explicit
engine: `streaming` wherever the call is a bounded or run-sized
materialisation, `in-memory` only for a small frame that is materialised in
memory anyway. No site relies on an engine for row order. Add a lint test that
rejects an engine-less collect in `src/haute/`.

**Acceptance:** no `collect()` in `src/haute/` relies on Polars' default
engine, held by the lint test; setting `POLARS_ENGINE_AFFINITY=in-memory`
changes no test result.

**Dependencies:** PL2-04, so the engine chosen at each site serves the order
contract.

**Evidence:** `src/haute/_polars_utils.py::execution_collect`;
`src/haute/_polars_utils.py::_cancellable_collect`;
`src/haute/_polars_utils.py::bounded_collect_batches`;
`src/haute/routes/_optimiser_outcomes.py`.

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
`POLARS_UNKNOWN_EXTENSION_TYPE_BEHAVIOR=load_as_storage`, set with the other
Polars settings at every entry point Haute owns (`PL2-16`), so direct scans and
user Data Inputs are covered alike. Both are recorded in the source's schema
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

**Dependencies:** PL2-01; `PL2-16` for the entry-point settings.

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

**Dependencies:** PL2-01; `PL2-04` for the join idiom the guide teaches.

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

**Dependencies:** PL2-01.

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
(`PL2-04`) or compare sorted frames where none is promised, never sorting to
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

**Dependencies:** PL2-01 to PL2-14.

**Evidence:** `tests/test_edge_join.py`; `tests/test_execute_lazy_contracts.py`;
`tests/test_column_lineage.py`; `tests/test_standalone_nodes.py`;
`tests/test_codegen_execution_equivalence.py`; `tests/test_polars_operations.py`;
`.github/workflows/performance.yml`; `.github/workflows/container-smoke.yml`;
`.github/workflows/dependencies.yml`.

### PL2-16 — Out-of-core spilling runs inside Haute's memory and disk budgets, or stays out
**Why:** Polars 2.0 spills operator state to disk by default (in this review's
probes a sort, a group-by and a window function spilled; a join did not). It
starts when its own allocations pass the smaller of
`POLARS_OOC_MEMORY_BUDGET_MB` and 80% of the machine's memory (a cgroup limit
where one applies, read once per process), and writes up to 64,000 MB into
`POLARS_OOC_SPILL_DIR`, by default `%TEMP%\polars-<user>\spill` on Windows and
`/var/tmp/polars-<user>/spill` on Linux, one directory per process. Probes on
Polars 2.0.0 show what that means for Haute:
- A worker capped by a Windows Job Object, a cgroup or `RLIMIT_AS` below 80%
  of the host never reaches the threshold, so spilling never helps it; Polars
  counts only its own allocations, not Python's, NumPy's or a model library's.
- The budget is a soft target for operator state, not a cap, and spilling did
  not dependably lower resident memory. With forty million rows read from
  Parquet on disk, a 1 GB or 256 MB budget changed the peak by between -19% and
  +26% across a sort, a stable sort, a group-by and a window function, at 2.2
  to 12 times the time, and never brought it near the budget; the join did not
  spill. With the input held in memory, a 150 MB budget wrote 384 files and
  845 MB, took 5.7 times as long, and peaked at 2.6 GB against 2.8 GB
  unspilled. All of it was measured on Windows, where Polars allocates through
  mimalloc.
- A process killed mid-spill, as Haute kills a worker on timeout,
  cancellation or a memory breach, left 768 files and 1.76 GB behind; Polars'
  source removes a dead process's directory only when a later process spills
  into the same place.
- Per Polars' source, passing the disk budget sets a process-wide abort flag,
  and a spill file that cannot be created or written panics. The second
  opinion's probes showed the abort surfacing as an untyped
  `RuntimeError("BindingsError: \"query aborted, raise
  POLARS_OOC_DISK_BUDGET_MB\"")`, not a Polars error, with Rust panics on
  stderr, and aborting the next, unrelated, non-spilling query in the same
  process too: in the editor server, one analyst's spilling preview could fail
  another's.
- The switches have traps: the threshold is the smaller of
  `POLARS_OOC_MEMORY_BUDGET_MB` and the fraction times total memory, so a huge
  MB value alone does not turn spilling off, `POLARS_OOC_MEMORY_BUDGET_MB=0`
  spills everything, and only a very large `POLARS_OOC_MEMORY_BUDGET_FRACTION`
  turns it off (second opinion, probed).
The spill directory lies outside Haute's runtime storage, its disk-headroom
checks and its write sandbox, on the system drive of every uncapped process:
the editor server, the optimiser's thread mode, macOS hosts, Databricks Model
Serving and the scoring container. 1.44 did not spill at all.

**Plan:** The pin turns spilling on, so this package ships with the first
round. Decided on 17 October 2026: spilling stays off, as on 1.44, and Haute
owns its directory; specify in the execution-engine, deploy and
sandbox-security specifications. By the memory-first rule this is the memory
answer: on 2.0.0 a budget bought no dependable saving in resident memory and
cost several times the time (above), a capped worker never reaches Polars'
default threshold, and the out-of-disk abort is process-wide. Every entry point
Haute owns sets, before Polars first reads its configuration, a
`POLARS_OOC_MEMORY_BUDGET_FRACTION` large enough that spilling never starts
(with the MB value alongside), a spill directory under the runtime storage and
a small disk budget, so that anything that does spill lands where Haute counts
and cleans it: the editor server and the CLI commands at start-up, each spawned
worker through `start_process_with_environment` (its directory removed by the
parent when the worker ends), the scoring container and the served model's
environment (`PL2-19`). A pipeline run from a user's own Python process
(`Pipeline.run`) inherits that process's Polars settings: Haute applies no
spilling settings in a process it does not own, since Polars holds them
process-wide and reloading them through `pl.Config.load` would change the
user's other queries too. The documentation gives the settings that turn
spilling off or redirect it. Turning spilling on is `PL2-17`'s measured
decision, by the same rule. Turning it on now was rejected, since there is no
measured saving to buy with its time and its process-wide abort; so were
Polars' defaults, which spill outside Haute's storage, sandbox and headroom
checks and expose the editor server to the abort.

**Acceptance:** a sort that spills under Polars' defaults when the reported
total memory is small (`POLARS_OVERRIDE_TOTAL_MEMORY_MB`) spills nothing in a
spawned worker, the server, the CLI, the container and the served model's
environment, each of which reports the settings it applied; a spill forced in a
test lands inside the runtime storage and is removed when its worker ends; a
`Pipeline.run` in a user's process leaves that process's Polars settings as it
found them; the docs and the specification state the off switch, including
that `POLARS_OOC_MEMORY_BUDGET_MB=0` spills everything.

**Dependencies:** PL2-01.

**Evidence:** `src/haute/_worker_isolation.py::start_process_with_environment`;
`src/haute/_json_shred/_runtime_storage.py`;
`src/haute/_file_ops.py::ensure_disk_headroom`;
`src/haute/_native_memory_limit.py`.

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
with spilling off as `PL2-16` sets it, carried into each probe's own
subprocess environment so a spill cannot hide a peak, and record each
operator's factor and its streaming control in the registry, including the
operations `PL2-14` registers; change a policy only where the new measurement
crosses its rule, both ways. Measure a join with a `validate` contract the same
way, charge it in the RAM estimate as the materialisation of both inputs it
is, and make the estimate's advice name that cost beside Haute's own
uniqueness proofs (`PL2-04`) instead of recommending the contract alone. Then
measure the same boundaries in a capped worker with spilling on and its
threshold set from the worker's admitted envelope (static per spawn, or per
task through `pl.Config.load`), on Linux as well as Windows, since the
allocators differ (jemalloc against mimalloc) and the review measured only
Windows. Decide by the rule `PL2-16` was decided by, memory first: turn
spilling on in capped workers if, at the threshold the measurement settles on,
no boundary operator's peak rises and at least one falls materially or
completes under a cap it breaches today; the time it costs is accepted and
recorded beside the decision. Spilling is turned on only in spawned, capped
workers, never in a shared process (the editor server, the optimiser's thread
mode, the scoring container, the served model), because the out-of-disk abort
is process-wide. Turning it on brings the typed storage failure for that abort
(a match on Polars' message text, pinned by a test) and the cleanup `PL2-16`
already provides. Re-measure the
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

**Dependencies:** PL2-16, whose controlled spill settings the measurements
use.

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
under each backend (Job Object, cgroup, `RLIMIT_AS`); with spilling off
(`PL2-16`) the cleaner thread never starts, so it matters only if `PL2-17`
turns spilling on, and then it is started before the cap; rerun the fallback
lane and classify its flake again.

**Acceptance:** the worker-isolation and native-limit modules pass on Polars 2
on every CI platform; if `PL2-17` turns spilling on, a capped worker that
spills does not fail on a thread start.

**Dependencies:** PL2-16; `PL2-17` for the spilling decision.

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
behaviour its deploy-time checks measured. The container image and the served
model's environment carry the Polars settings `PL2-16` chose, and the deploy
specification records both. The major range `polars>=2.0,<3` was rejected: a
2.x release could change a deployed model's results and memory behaviour
without a redeploy.

**Acceptance:** a logged model's requirements name the editor's exact Polars
version, and loading it under another fails with the named message; the
container's Dockerfile and the served model's environment carry the Polars
settings.

**Dependencies:** PL2-01, PL2-16.

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

**Dependencies:** PL2-01.

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
