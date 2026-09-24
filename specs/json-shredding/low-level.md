# JSON Shredding — Low-Level Specification

## Module map

Studio's preview preflight prepares structured Quote Inputs through the
shared input-cache routes (`POST /api/input-cache/status` and `/build` with
`node_type: "apiInput"` and the current node config), exactly as it prepares a
snapshot-backed Data Input: a node whose tables are all ready and none stale is
left alone; otherwise the build job writes every missing or stale table and the
preflight waits for it before sending the preview request, showing the
building phase until completion. Status and build errors propagate through the
normal preview error surface. All requests respect the owning preview's
AbortSignal. The preview itself prepares any table still missing or stale on
the server (see [IO layer](../io-layer/low-level.md#automatic-preparation)).

| File | Responsibility |
|---|---|
| `src/haute/_api_input_schema.py` | V2 apiInput schema codec: `TypedDict` shapes, extension recognition, canonical table and column path semantics, filesystem label sanitisation, and fail-loud validation. |
| `src/haute/_json_shred/__init__.py` | Package docstring only: names the concern layout; deliberately exports nothing. |
| `src/haute/_json_shred/` | v2 per-frame structured-input engine, decomposed by concern into the submodules below; consumers import each concern module directly (there are no aggregating re-exports). |
| `src/haute/_json_shred/_records.py` | Streaming JSON/JSONL/XML record iteration, the shared bounded record limit, byte-range tiling with its parallelism policy, and the parallel-worker failure transport. |
| `src/haute/_json_shred/_shred.py` | Table specs, leaf resolution, the single-pass record walk, and root-conservation accounting. |
| `src/haute/_json_shred/_writer.py` | Aggregate-bounded Parquet row-group emission for table-snapshot builds and leased runtime spill bundles, plus parallel chunk execution. |
| `src/haute/_json_shred/_source_proof.py` | The one source-freshness proof for every local file, not only structured sources: native file revisions (Windows USN/file-id, POSIX stat), the settled-stat fallback, and the shared in-process content signature behind them. |
| `src/haute/_json_shred/_runtime_storage.py` | Process-owned runtime storage for generated standalone code: the disk budget and spill-directory leases, with start-up recovery of dead processes' spills. |
| `src/haute/_json_shred/_inference.py` | v2 schema inference from data: bounded sampling, type widening, and deterministic column naming. |
| `src/haute/_json_shred/_inference_filter.py` | Bounded learning of strict native structural filters; matching records skip repeated evidence collection while mismatches retain the complete inference walk. |
| `src/haute/_json_shred/_inference_cache.py` | Process-local, bounded complete-schema reuse behind strong source revisions, with concurrent request sharing and independent response values. |
| `src/haute/_json_shred/_snapshots.py` | Each emitting table as a shared input snapshot: its store identity, the source freshness proof, the one-shred build that publishes the tables, and the supervised worker build with its settlement. |
| `src/haute/_json_shred/_cache.py` | The runtime apiInput loader: store-leased tables for canvas execution, an in-process bounded shred for generated standalone code. |
| `src/haute/_json_safe.py` | Recursively converts Python/pipeline values into JSON-safe representations for API responses and preview rows. |
| `src/haute/_jsonpath.py` | The shared canonical array-outer JSON path parser and writer used by both INPUT and OUTPUT path addressing. |
| `src/haute/_output_assembler.py` | V2 OUTPUT mapping validation and document assembly: GYO residue/cut planning, bag-natural joins, array-prefix nesting, pruning, and collected-frame rendering. |
| `src/haute/_edge_join.py` | `edgeJoin` node config validation, Polars join-kwargs construction/execution, and the shared join column-demand-narrowing function used by both static projection and runtime narrowing. |

Submodel graph expansion and boundary rewiring are owned by
[submodels](../submodels/low-level.md), not this component.

## Key types and data structures

**`_api_input_schema.py`**

- `ColumnV2`, `TableV2`, `ApiInputV2Config` are total-false `TypedDict`s for
  the wire/sidecar shape. `ColumnType` is exactly `int|float|str|bool|date`;
  `ColumnStatus` is exactly `Confirmed|Inferred`; optional `emit` and
  `selected` fields, when present, are exact booleans rather than truthy
  lookalikes;
  `PathSeg` is `(key, is_array)` and only array segments increase relational
  depth.
- `ApiInputSchemaError(HauteError)` is the single typed schema/path failure
  consumed by the inference route's structured 422 response.

**`_output_assembler.py`**

- `OutputMappingSchemaError(HauteError)` is the OUTPUT grammar/structural mapping
  error. `OutputNestingKeyError(OutputMappingSchemaError)` is the fail-loud
  relation-key-null error with stable `frame`, `output_path`, and `key` fields.
  `_Core` and `_CutPlan` record the deterministic feedback-edge cut and the
  residual per-frame fields used for same-level assembly.
- An active mapping row is enabled and has non-blank `source_column` and
  `output_path` fields; incomplete editor rows are ignored consistently by
  validation, contracts, projection demand, and assembly. Every consumer uses
  `is_active_mapping_entry` rather than duplicating a weaker enabled-only test.

**`_json_shred/` package**

- `ShredSkipStats` — dataclass with `skipped_records: int` and
  `skipped_rows_by_table: dict[str, int]`. `.total` sums both; a build logs them.
- `_LeafSpec = tuple[str, str, str]` — `(column_name, leaf_path_dotted, type_token)`,
  used at build time.
- `_WalkSpec = tuple[str, str, str, int]` — as `_LeafSpec` plus the array-iteration
  `source_depth` at which the column's value lives (equal to the table's own depth
  for a normal column, shallower for an ancestor column whose value distributes over
  descendant rows). Used during the shred walk.
- `table_is_emitting(table) -> bool` — THE single predicate for "this table
  contributes a data frame" (`emit AND ≥1 selected column`); build, validity and load
  all route through it so they can never disagree about which parquets exist.
- `_POLARS_TYPE_MAP` — the five v2 `ColumnType` tokens (`int`, `float`, `str`,
  `bool`, `date`) mapped to Polars `DataType` classes.
- `_iter_xml_records(data_path)` — rejects DTD/entity declarations with a bounded
  chunk scan, then uses `XMLPullParser` rather than a complete-document byte buffer.
  A parallel event-only Expat tracker records each direct child's encoded start offset
  and reserves the maximum UTF-32 closing-tag width, so a child that could exceed the
  configured record bound fails closed before that chunk reaches the retaining parser.
  A validation pass classifies an attribute-free root containing homogeneous object
  children; an emitting pass then converts, yields, removes, and clears each direct
  child. Other XML shapes retain their one-root-record semantics only when the file
  fits `HAUTE_STRUCTURED_INPUT_MAX_RECORD_BYTES`; larger ones fail before a complete
  tree is retained. Element/attribute namespaces are stripped. Mixed content and
  field-name collisions raise `ApiInputSchemaError`.

**`_jsonpath.py`**

- `_Seg` (`NamedTuple`) — `(name, is_array)`, one output-path segment.
- `_ParsedPath` (frozen dataclass) — `raw` and
  `segments: tuple[_Seg, ...]`.
- `_PathError` (`Protocol`) — the injected error constructor
  `(message, **context) -> Exception`; lets the neutral grammar core raise each
  caller's own `HauteError` subclass (`ApiInputSchemaError` on the INPUT side,
  `OutputMappingSchemaError` on the OUTPUT side) without depending on either.

**`_json_safe.py`**

- `MAX_SAFE_INTEGER = 2**53 - 1`; ints outside `[-MAX_SAFE_INTEGER,
  MAX_SAFE_INTEGER]` are stringified so JS/JSON consumers don't silently lose
  precision.
- `NON_FINITE_FLOAT_KEY = "__haute_type__"`, `NON_FINITE_FLOAT_TYPE =
  "non_finite_float"`, `NON_FINITE_FLOAT_VALUES = {"nan", "inf", "-inf"}` — the
  sentinel object shape `{"__haute_type__": "non_finite_float", "value": "nan"}`
  used to round-trip NaN/±Infinity through JSON.

**`_edge_join.py`**

- `JoinKey = str | list[str]`.
- `EDGE_JOIN_DECORATOR_TO_CONFIG` / `EDGE_JOIN_CONFIG_TO_DECORATOR` — snake_case
  Python-decorator-kwarg ↔ camelCase graph-config-key maps for join options such as
  `left_on` ↔ `leftOn`; role arguments are deliberately absent.
- `_ALLOWED_HOW = {"inner", "left", "right", "full", "semi", "anti", "cross"}`.
- `resolve_edge_join_role_indices(target_handles)` validates exactly one `base` and one `join`
  incoming target handle and returns their positions. It does not inspect source node ids.

## Control flow

**V2 codec** — `validate_v2_schema(config)` first requires the `tables` list,
then validates each table's label/path/columns, unique raw and sanitised
labels, unique column names, supported column type/status/levels shapes,
exact-boolean `emit`/`selected` values when present, ancestor-or-own column
paths, and `row_id_column`. Scalar-shape failures name the exact
`tables[i]...` field path and happen before any truthiness consumer.
Labels must additionally be ASCII-only
Python identifiers and not hard keywords — `label.isascii() and
label.isidentifier() and not keyword.iskeyword(label)` (invariant B4): the
label is consumed verbatim as the downstream parameter name by codegen and
the executor, so an invalid label would only fail later and further from its
cause. ASCII is part of the invariant, not a convenience: Python
NFKC-normalises source identifiers (PEP 3131), so a non-NFKC Unicode label
would silently bind under a *different* parameter name once the generated
file is parsed — exactly the hidden mapping the label≡argument identity
forbids — and the ASCII rule is mirrorable exactly in the frontend, where
Unicode `str.isidentifier()` is not. Soft keywords (`match`, `case`, `type`,
`_`) are legal parameter names and stay allowed. Under B4 the B2
sanitised-collision check compares **casefolded** filesystem stems
(`sanitise_label_for_filesystem(label).casefold()`): a build shreds each table into a
Parquet named by that stem, on case-insensitive filesystems (Windows/macOS), where `Items.parquet` and
`items.parquet` are one file, so two labels differing only by case would
silently clobber a frame at build time. ASCII identifier labels are fixed
points of the sanitiser, so post-B4 a case-only collision is the *only* way
two distinct valid labels can meet at one stem — B2 rejects the pair loudly,
naming both labels and the shared stem. Inference's casefold-aware
uniqueness pass guarantees inferred schemas never trip it. The frontend
mirrors B4 in `apiInputLabelIssue` (ASCII-identifier regex plus the
hard-keyword list) and treats duplicates case-insensitively to match B2,
before commit. `parse_table_path`/`parse_column_path_full`/
`parse_column_path` delegate grammar acceptance to `_jsonpath.py`; `make_table_path`
delegates canonical rendering to the same writer.

`validate_v2_schema` checks declared `emit` and `selected` values as exact
booleans and `status` as exactly `Confirmed|Inferred`, naming the field path
before shredding can consume either value by truthiness.

**OUTPUT mapping** — `assemble_output_from_mapping(frames, mapping)` groups active
rows by source port after running `validate_v2_output_mapping`, selects/aliases source
columns to output paths, and passes the field frames to `_assemble_document`. The
validator parses every distinct active path once, sorts the parsed destinations, and
uses adjacent comparisons for duplicate/prefix and array-prefix-chain conflicts
(`O(n log n)`, not an `O(n²)` pair scan). It also rejects divergent emit prefixes
within one source frame before any frame collection. `_assemble_document` resolves
the lazy schemas before data materialisation, groups frames by their emit prefix, and
collects the final plan for each emitting prefix exactly once. In particular, frames
emitting at the same array prefix are not first collected individually and then read
again for their join. They are planned by `_plan_cut` and `_execute_plan` before the
single collection; residual shared fields are full bag-
joined (fan-out is retained), cut/disconnected groups are diagonal-concatenated as
partials, and joins preserve the deterministic sorted-member left-to-right row order
(`maintain_order="left_right"`) under both automatic and streaming Polars execution.
Every fold member must overlap the accumulated connected component; a violated plan
invariant fails loudly instead of falling back to an unbounded Cartesian join.
The prefix-tree builder nests child arrays by ancestor values without
joining siblings. An object's identity at a level is the tuple of its own leaf
values, canonicalised by `_identity`: scalars (including `None`) pass through
unchanged, while container-valued leaves (`List`, `Struct`, `Array`) are
canonicalised into hashable tuples — recursively, with struct fields kept in
their stable polars field order rather than sorted. Container leaves are
therefore ordinary valid OUTPUT leaves, grouped and ancestor-indexed by value
like any scalar. Relation-key guards examine a row only when that row actually
contains the key; an absent column in another mapping frame is not a null. A present
null component raises `OutputNestingKeyError`. `_prune` removes null-valued object fields and empty collection
values from objects, and removes empty-object elements from arrays; null or
empty-list elements already present inside arrays are retained.
`render_output_document` applies that same pruning to the collected Polars shape.

`output_document_schema(source_schemas, mapping)` derives the document's schema
from the mapping paths and the source frames' schemas alone, mirroring
`_assemble_document`'s nesting exactly: a leaf's dtype is its source column's
dtype, object segments nest as `Struct`, array segments nest as `List(Struct)`,
each leaf sits at its own subpath within its array element (so an ancestor key
carried by a deeper frame for matching is emitted at the level it belongs to and
never re-emitted inside the child element), and child arrays are attached after
the level's own fields in sorted order — the field order `_set_nested` produces.
A missing source port or column, and one output path mapped from source columns
of different dtypes, are `OutputMappingSchemaError` rejections.

`assemble_output_from_config` uses the same assembler and constructs the final
document frame under that derived schema rather than by Python inference, which
makes the derivation the single schema authority for both OUTPUT paths. Under a
schema-only execution (`schema_only=True`) it returns an empty frame under the
derived schema and never assembles; otherwise it returns a `limited_python_scan`
(execution engine) under the same schema. A limit `n` that Polars pushes to the
scan reaches `assemble_output_from_mapping(..., row_limit=n)` and
`_assemble_document`: an emitting root level reads only its first `n` rows, and
every deeper emitting level reads only the rows whose keys match its nearest
collected ancestor level (`is_in` for one key, a semi join for several), so each
returned top-level object equals the unlimited assembly's object for the same
root rows. Root rows sharing their own-field values collapse, so fewer than `n`
objects may return. A root synthesised from descendants has no rows of its own
to limit and is assembled in full. Without a limit assembly reads every row. Declaring the schema is rendering-neutral —
`render_output_document` prunes the null padding a uniform schema introduces —
and an empty document keeps the typed schema instead of losing its columns.
OUTPUT is an inherent terminal
materialisation boundary because its public result is a complete nested Python/JSON
document. Every lazy collection therefore routes through the shared streaming helper;
when an `ExecutionContext` is active it uses native-query cancellation polling, records
the collection, and enforces admitted RSS limits. DataFrame-to-row conversion and the
Python nesting loops checkpoint at a fixed row interval so cancellation and memory
pressure remain observable after native collection. The assembler retains the one
materialised row representation it needs, rather than also retaining a second
`DataFrame.to_dicts()` copy. A standalone generated pipeline with no active admitted
context still receives streaming Polars execution but does not acquire an implicit
memory guarantee. Complete-schema inference preserves late non-null nested fields
without another upstream read.

**Table identity** — `api_input_snapshot_source(config, data_path)` validates the
v2 config and returns the source file (resolved) with its emitting tables in
schema order. Each table's store identity (`haute._source_cache.SourceCacheIdentity`,
provider `api_input`) has the descriptor
`{path: <resolved source file>, table: {path: <table path>, columns: [[name,
column path, type], ...selected columns in config order]}, shred_version: 1}`.
Sibling tables and the port label are not part of it: editing one table moves only
that table's identity, and two tables with identical specifications share one.
`group_digest` hashes the source path with the node's distinct table digests and
keys node-level work (single-flight, route jobs). `shred_version`
(`SHRED_SEMANTICS_VERSION`) is raised whenever the walk or the frame it writes
changes, so older generations stop matching.

**Freshness** — `api_input_source_signature(path)` is the table generations'
`source_signature`: `xxh64:<digest>:<size>` from the shared
`_source_proof.file_signature` (see the source-proof invariants below), or `missing` when
the path is not a file. The
store's `status` marks a table whose recorded signature differs as `stale`.

**Build an API Input's tables** — `build_api_input_tables(source, labels, *, store,
profile, cancellation, deadline, execution_context, plans, scratch_token,
defer_retirement)`:
1. Take the named tables, one per distinct identity, in schema order; an
   unknown or non-emitting label raises `KeyError`. With `plans`, they must name
   exactly those identities.
2. Compute the source signature before reading; an absent source raises
   `FileNotFoundError` before anything is written.
3. Shred the source once into a private scratch directory,
   `<inputs root>/.shred/.staging-<token>`, one Parquet per table (named by the
   sanitised label), inside the `api_input_shred` execution stage when an
   execution context is given. Two paths produce identical files:
   - **Serial** (default) — `shred_to_buffers(_counted_records(), v2_config,
     stats=skip_stats, _row_sink=writer.emit)` consumes `_iter_records` directly.
     `_BoundedParquetRowGroupWriter` owns one aggregate row/estimated-byte budget
     across every table and flushes all non-empty buffers when either limit is met.
   - **Parallel** (`_should_shred_in_parallel`: a `.jsonl`/`.ndjson` source of at
     least `_PARALLEL_MIN_BYTES` that splits into more than one range) —
     `_write_tables_in_parallel`, described below. When a managed execution context
     is active, this path additionally requires an aggregate native lease (`cgroup`
     or Windows Job Object). A per-process `RLIMIT_AS` lease, unavailable best-effort
     enforcement, or an ordinary context uses the same bounded serial writer so
     process fan-out cannot multiply the admitted memory budget.
4. Conservation assertion at the root level: for every emit-true root table,
   `emitted + skipped_rows_by_table[label] == record_count`, else `RuntimeError`.
   The parallel path asserts this per chunk; ranges tile the file exactly, so
   holding it on every chunk holds it on the whole file. Skipped records and rows
   are logged (`json_shred_records_skipped`), not stored with the generation.
5. The shared writer converts each bounded buffer through `_buffer_to_frame`, writes
   it as a zstd Parquet row group with `_per_frame_metadata`, and immediately releases
   the Python rows. Closing the writers also produces valid schema-carrying empty
   parquets.
6. Compute the source signature again; a change raises
   `SourceChangedDuringCacheBuildError` and nothing is published.
7. Publish each table through the store's ordinary build
   (`SourceCacheStore.build(identity, builder, refresh=True, source_signature=...)`,
   build class `bounded`): the builder scans the table's scratch Parquet, the store
   rewrites it as sliced part files, validates the staged generation, and moves the
   pointer. A plan names the generation id and staging token. Each table publishes
   on its own, so a table published before a later failure stays current.
8. Remove the scratch directory whatever happened. A build that dies leaves it for
   the store's stale-staging sweep.

**Supervised build** — `run_supervised_api_input_build(source, labels, *, store,
profile, budget, worker_config, spawn)` runs the same build in a hard-capped spawned
worker (`build_api_input_tables_worker`, which opens an isolated execution context
from the admitted budget and defers retirement). The parent chooses every
generation id and staging token and the scratch token. After a worker failure or
death it reconciles each table (`reconcile_unpublished`) and removes the scratch
directory; the build still counts as done when every table was published, and
otherwise the worker's failure is raised. A base exception (an interrupt, a system
exit) is never turned into success. On success the parent retires superseded
generations, where its own lease counts are visible.

**Parallel shred** — `_write_tables_in_parallel(...)`. Legitimate because the
shred is a per-record walk: ancestor values are distributed at walk time, and
`row_id_column` names an *existing* data column rather than a generated counter,
so no state crosses records. A split must therefore preserve only row ORDER and
the skip/conservation accounting.

- `_jsonl_byte_ranges` splits the source into `[start, end)` ranges, each
  boundary advanced past the next newline so no range splits a record. Ranges
  tile the file exactly — gapless, non-overlapping, in file order.
- Chunk size (`_PARALLEL_CHUNK_BYTES`) and worker count
  (`_PARALLEL_MAX_WORKERS`, `_parallel_worker_count`) are deliberately separate
  knobs. Worker count bounds parallelism; the shared row-group writer, rather than
  the complete source range, bounds decoded rows retained by each worker. Sizing
  chunks as `file_size / n_workers` would make queued work and recovery time grow
  with the file even though retained row buffers are independently bounded.
- `_shred_chunk` runs in a worker process: it is module-level and
  argument-driven so it survives `spawn` pickling, and it returns a
  `_ChunkResult` rather than raising, so a failure can be re-raised in the
  parent. Rows are written through `_BoundedParquetRowGroupWriter` as compressed
  Parquet parts in the scratch dir, never returned through the pool's result channel.
- The parent reads one part row group at a time and feeds it into the shared writer
  **in chunk order** (so row order matches the serial shred exactly), unlinking each
  part as it is consumed. Parent and child peak memory are therefore bounded by one
  configured row group plus one logical record, rather than one source range. Disk
  is the trade: workers may finish writing every part before assembly starts, so the
  scratch directory transiently holds the part parquets alongside the growing final
  parquets. Only the final parquets are published, and any failure removes the
  scratch directory with the parts in it. A chunk that produced
  no part for an emitting table (worker/parent spec divergence — never legitimate)
  fails the build rather than publishing a parquet with silently missing rows.
- `_raise_chunk_error` rebuilds the worker's failure in the parent rather than
  pickling arbitrary exception objects. The envelope carries an
  `ApiInputSchemaError`'s raw `message` plus complete `context`, an
  `orjson.JSONDecodeError`'s `msg`/`doc`/`pos`, and the constructor evidence for
  documented builtin failures (`OSError` subclasses, `RuntimeError`,
  `MemoryError`, and `ValueError`). Those failures surface with the same type,
  message, and structured context as the serial path. The worker catches
  `Exception`, not `BaseException`; process-control exceptions are never
  disguised as data failures. A genuinely unexpected ordinary exception is a
  parent `RuntimeError` that names its original qualified type instead of
  pretending it was a conservation failure. Serial/parallel comparison tests
  pin the structured schema and JSON parser evidence, filesystem exception
  identity, and the process-control escape boundary.
- Only newline-delimited sources are split: a line boundary is findable without
  parsing. A root JSON array would need a serial byte-level scan to locate
  element boundaries, costing about what it saves; XML is not delimited at all.
  Both keep the serial path.
- The `spawn` start method is selected explicitly, so every platform exercises
  the same picklable-arguments path rather than only Windows. As with any
  `spawn` user, a caller that invokes the build from module-level script code
  must guard it with `if __name__ == "__main__":`; the packaged entry point
  (`haute = haute.cli:cli`) already does.
- Parallel eligibility is therefore a performance choice only after memory ownership
  is proven. Builds without an execution context use the parallel path; isolated
  worker builds never treat a per-process limit as an aggregate descendant budget.

**Shred core** — `shred_to_buffers(records, v2_config, stats=None)`:
1. Validate schema; collect emit-true tables' `(label, segments, col_specs)`, where
   each column spec carries its own `source_depth` (its own table's array depth, or
   a shallower ancestor depth for a W1 "distribute a parent value to every
   descendant row" column).
2. `_reject_reserved_leaf_collision` per table: a `$value` leaf may not coexist with
   another own-depth column. A table is scalar only when `$value` itself lives at
   that table's depth; an ancestor `$value` distributed into a descendant object
   table does not change the descendant's shape classification.
3. Group tables by their full `(key, is_array)` segment position
   (`tables_by_pos`), and compute the object-hop + array-key "descents" needed to
   reach each child array from its parent position (`descents_by_pos`).
4. Walk: `_emit_at(pos, record, ancestors)` emits a row into every table registered
   at `pos` (skipping — and counting — a shape-mismatched record for that table),
   then descends into each child array via `_walk_array`, which iterates the array
   and recurses into `_emit_at` per element (a `None` element is a real row for a
   scalar table, a counted skip for an object table; a nested list is a counted
   shape mismatch, never fabricated as a null scalar row). Declared string columns
   use the shared deterministic JSON-scalar renderer; dict/list values remain
   shape values and are rejected or counted rather than stringified.
5. Returns `{table_label: [row_dict, ...]}`.

**Runtime load** — `load_v2_api_source(data_path, config, *, port_columns=None,
read_snapshots=False, store=None)`:
1. Validate the v2 schema at this public boundary, then require at least one
   emit-true table and at least one selected column (the latter two raise
   `RuntimeError` with an actionable configuration message otherwise).
2. Construct complete `_EmittingTableSpec`s once: parsed table position plus every selected
   column's name, leaf, declared type, and source array depth. Builds, the
   standalone shred, strict frame construction, and ancestor broadcast all consume
   these specs. `port_columns=None` selects every emitting port at full width.
   Otherwise it is a non-empty mapping from emitting label to either `None` (that
   port at full width) or a subset of its declared selected column names. An empty
   subset is the row-cardinality-only demand and physically retains the first
   declared column as a carrier because Polars cannot represent a non-zero-row,
   zero-column frame. Invalid labels or columns fail before any read. Projected specs
   retain schema order.
3. With `read_snapshots` (canvas execution: the executor's API Input builder passes
   it through `resolve_api_input_from_config`), lease each demanded table's current
   generation from the shared store (`lease_input_generation`, released by the
   execution context's cleanup after collection, or owned by the returned plan
   outside one) and select the demanded columns in declared order. The source file
   is never read. A table with no generation raises `PolarsIoConfigError`
   (`input_snapshot_missing: ...`): automatic preparation publishes the tables
   before an admitted execution, so this reaches only a run that was not prepared.
4. Otherwise (generated standalone code, which runs without a project store),
   `_iter_records` plus the shared shred walker uses only the requested projected
   specs. The same `_BoundedParquetRowGroupWriter` used by builds owns one aggregate
   byte/row-bounded buffer across all requested tables. Crossing either bound
   flushes every non-empty table buffer through the same strict `_buffer_to_frame`
   conversion into a PyArrow `ParquetWriter` row group, then releases those Python
   rows. The resulting `{label: scan_parquet(...)}` bundle preserves schema order,
   row order, carrier-column cardinality, skip accounting, strict bool/date errors,
   and root conservation. JSON root arrays are tokenised one complete top-level
   value at a time; JSON root objects, individual JSONL lines, repeated XML
   children, and one-root XML documents are all subject to the hard
   structured-input record-byte limit. The XML parser releases repeated record
   elements at each direct-child end. Checkpoints run during parsing, emission,
   conversion, and flush. Spill files live below the process working directory's
   `.haute_cache/.runtime-spills`, outside the shared store; this path never writes
   store state. Each spill allocation claims its UUID-named child with an exclusive
   directory create. A name collision fails closed and leaves the pre-existing entry
   untouched; rollback removes a spill child only after this allocation successfully
   created it. Orderly-exit cleanup reports residue through structured warnings;
   cleanup failures during a primary build error are attached as exception notes
   rather than hidden. A managed `ExecutionContext` owns the spill lease until
   collection/cleanup; an unmanaged LazyFrame is conservatively process-pinned until
   orderly exit.
5. Return a `{label: LazyFrame}` dict in schema order for every requested frame
   (or every eligible frame when `port_columns` is absent) — there is no bare-frame single-table special case, so a
   sole frame routes through the same per-edge `source_port` resolution as
   eight frames (see [execution-engine](../execution-engine/low-level.md)
   `_pick_source_frame`), and adding or removing a sibling frame never changes
   the shape a consumer receives.

**File locks.** The runtime storage budget serialises on
`_file_lock.file_lock_for(<cache root>/.runtime-storage-budget.lock)`, the one
cross-process lock helper the [IO layer](../io-layer/low-level.md) specifies: re-entrant
within a thread, a per-process `RLock` combined with an OS advisory lock (`flock` on
POSIX, one-byte `msvcrt.locking` on Windows), and a lock path that must be a plain
regular file (symlinks, reparse points, and file-identity swaps are rejected before
the lock is trusted).

**Runtime storage budget.** `.runtime-spills` lives below the project cache root and
uses owner directories named by PID plus a random token. Owner
metadata records a format version and creation time. A global OS-locked budget
(`HAUTE_JSON_RUNTIME_DISK_BUDGET_BYTES`, positive integer) counts unique allocated
file identities so hard links are not double charged. Allocation/flush checks run
under that lock; crossing the budget raises `JsonRuntimeDiskBudgetExceededError` and
cleans the caller's partial spill. Startup and first-use recovery remove
only plain owner directories older than the configured grace whose PID is no longer
live; active, young, malformed, symlink, and reparse-point entries are preserved and
logged. Budget accounting is fail-closed: a preserved non-plain or unreadable entry
raises `JsonRuntimeStorageIntegrityError` and blocks new runtime allocation until the
entry can be inspected or removed. A concurrently released plain entry may disappear
during the scan and is treated as a benign reduction in usage, never as zero-sized
evidence for an entry that still exists.

**Admission metadata uses the published generation.** Per-port metadata used by
materialisation admission reads the table's current generation in the store
(`open_generation`, which verifies part digests, footers and schema before
returning) and sizes the boundary from its part files. A table with no generation
leaves the estimate unavailable; automatic preparation runs before strategy
planning, so an admitted execution sizes the generation it will read.

**Schema inference** — `infer_v2_schema_from_data(data_path, sample_size=None)`:
1. Input dispatch preserves a complete scan by default. JSONL/NDJSON at or above
   `_PARALLEL_MIN_BYTES` is split at newline boundaries with the same exact byte
   tiling used by parallel table builds. Spawned workers infer compact
   `_InferenceState` accumulators, and the parent merges results in file order;
   it never transfers records between processes. Smaller newline-delimited
   files, XML, root JSON arrays, and every explicit bounded sample remain serial.
   A root-array sample uses `_iter_sampled_json_array_records`, which hand-parses
   only enough of the array to avoid materialising the whole file. Parallel
   inference compares source device/inode/size/mtime before and after the worker
   scan and fails clearly if the source changed instead of returning evidence
   merged from different file generations.
2. Recursive `_InferenceState.walk(value, level, obj_prefix)`: a nested dict
   stays at the same `level`, deepening `obj_prefix` (object folding); a nested
   list of objects descends to a new `level` keyed by the full
   `(key, is_array)` segment tuple; a nested scalar list widens a
   `scalar_levels[level]` type; a bare scalar widens
   `levels[level][obj_prefix + (k,)]`. `_reject_unexpressible_key` fails loud on
   a `$value`-colliding or dot-containing source key before it can be silently
   mis-addressed later. Each distinct key is validated once per accumulator;
   repeated records do not rerun the same identifier checks.
   After a bounded prefix of 10,000 records, a native structural filter may skip
   repeated Python evidence collection for records already covered by the
   accumulated observations. It must reject unknown keys at every object depth
   and avoid scalar coercion; new fields/types and ambiguous array forms still
   pass through the ordinary walker. The filter is an optimization, never a
   completeness limit: every record is parsed and checked, and the complete
   schema, ordering, naming and structured errors match the ordinary walk.
   The filter uses strict `msgspec.convert` on already-parsed records, with
   wire aliases for generated attributes and `forbid_unknown_fields=True` at
   every object depth. Parallel JSONL ranges also use a strict typed
   `msgspec.json.Decoder` to combine parsing and known-structure checking without
   first building a separate dictionary tree. Successful checks discard the
   decoded value; only records decoded by the existing `orjson` parser supply
   new inference evidence. Decoder shape/syntax failures and invalid UTF-8
   return the original bytes to the ordinary parser/walker, preserving its
   accepted input, duplicate-key behavior, scalar types and structured errors.
   Native integer fields in this JSON fast path are limited to signed 64-bit
   values: larger JSON integers must use `orjson`, which can parse them as
   unsigned integers or floating-point values. A parser mismatch never becomes
   a user-facing error or silently discards an input record. It recompiles
   after each 100 records requiring the ordinary walker, at most eight times
   per stream; after that, mismatches still receive complete inference without
   further filter learning. Mixed object/scalar/nested-list array forms and
   nullable scalar arrays without prior null-only evidence deliberately remain
   on the ordinary walk to preserve array-mode semantics and errors.
   Shapes deeper than 64 levels use the ordinary walk without native filtering
   so native type construction cannot impose a new input nesting limit.
   Depth starts at zero for the root object and increases at each object field
   and object-array item. A supported shape exactly at depth 64 remains eligible
   for native filtering. Learned scalar/object arrays accept their known forms;
   empty and null-only arrays become eligible only after those forms were observed.
   Parallel JSONL inference learns the first 10,000 object records once before
   dispatch. It merges that prefix's exact evidence first and sends a picklable
   snapshot of the learned structure to each range. Each range makes its own
   copy and compiles its own native type; later learning cannot mutate another
   range's starting structure. The initial compilation counts toward the eight
   compilation limit. Existing byte ranges are retained, so the short prefix
   is parsed again during the parallel scan without repeating its full inference
   walk when it matches. This avoids learning 10,000 records independently in
   every range, while preserving first-observation ordering and complete input
   validation. Serial and explicitly sampled inference learn from up to 10,000
   records within their existing scan limit. Filters remain local to each stream;
   only exact inference evidence is merged between ranges. Bounded raw range
   reads are shared with the ordinary JSONL record reader, retaining record byte
   limits, newline boundaries and file-order error reporting. Other input formats
   and explicit sample limits retain their existing parser path.
3. `_InferenceState.merge` unions container/null evidence and applies the same
   associative `_widen_type` operation to scalar and object leaves. States are
   merged in range order and dictionaries keep first-observation order, making
   parallel output byte-for-byte identical to serial output, including column
   and table ordering. Worker failures use the same structured reconstruction
   envelope as parallel table builds.
4. Table assembly, per observed level, in `(array_depth, len, tuple)` sort order:
   a level only ever seen as a scalar array becomes a one-column `$value` table
   (`_SCALAR_VALUE_COLUMN`); otherwise its object-folded columns are named via
   `_assign_column_names` (bare leaf where unique, else the underscore-joined full
   path, with a final numeric-suffix dedup pass) and typed via the widened
   `levels` map. Only the root level defaults `emit=True`.
5. Label assignment — inferred `label`s are B4-valid identifiers, never raw
   table paths (`path`/`displayPath` still carry the path). The root level is
   labelled `quote_info`; every other level is labelled by its innermost array key
   through `derive_identifier_label(raw)` (`src/haute/_api_input_schema.py`): the
   `_sanitize_func_name` character pipeline (strip; spaces/hyphens → `_`;
   ASCII alnum/underscore kept; other ASCII dropped; non-ASCII reversibly
   encoded `_x<hex>_`) with frame-flavoured repairs — empty → `table`,
   digit-leading → `_`-prefixed, hard keyword → trailing `_` (`class` →
   `class_`). A uniqueness pass in the sorted table order then resolves
   collisions symmetrically: every table whose label is shared with another
   is re-labelled with the underscore-join of ALL its level keys (object
   hops and array keys, each through `derive_identifier_label`) — so
   `$[:].a.items[:]` and `$[:].b.items[:]` become `a_items`/`b_items`, not
   `items`/`items_2`; the root's join is empty so it keeps `quote_info`. Any
   labels still colliding after qualification take deterministic numeric
   suffixes (`_2`, `_3`, …) in the sorted order, first occurrence keeping
   its label. The closure property — inference output passes
   `validate_v2_schema` unchanged (B4 + unique labels) — is a contract, not
   a coincidence.

The frontend's ordinary **Infer Tables** action requests the complete inference
contract: `inferJsonCacheSchema` omits `sample_size` unless a caller explicitly
supplies one, and gives this endpoint the same 30-minute request budget as a
table build instead of the shared 30-second default. A hidden head-sample is not
permitted here. A field that first appears after the sample is not a type
widening of a declared column; the subsequent build legitimately ignores that
unknown field, so it cannot act as a completeness backstop. Bounded inference
therefore remains an explicit programmatic opt-in whose caller owns the
incomplete-schema trade-off.

Complete inference results reuse only behind the shared freshness token
(`_source_proof.observe_freshness`). The cache key
includes the absolute source path (preserving its parser-selecting extension)
and the configured record-byte limit; only unbounded inference participates.
Positive explicit samples bypass the cache. An unchanged reusable token may
reuse a successful full result; a missing/unreadable file still fails normally,
and a token that is not reusable (a young file without a native revision) uses the
ordinary scan without retaining its result. A miss checks the token before and
after inference; a changed proof
raises the existing structured changed-during-inference error and retains no
result. Waiters revalidate after an in-progress result becomes available.

The process-local cache reuses the common `LRUCache` with bounds of 32 entries
and 16 MiB of serialized schema payloads. An oversized schema is returned but
not retained. Serialized payloads are immutable and each caller receives a
fresh decoded mapping, so editor changes cannot alter cached inference.
Concurrent requests for the same path/settings/revision share one active scan,
including its failure; failed scans are not retained and a later request can
retry. Active request coordination is released on success or failure. Clearing
retained results does not split an active request, and forked processes replace
inherited cache/coordination locks rather than acquiring them. This cache is
not persisted across server restarts and does not store or modify source data
or node configuration.

JSONL range readers and newline-boundary discovery obey the same configured
record byte limit as serial reads. Their line reads are bounded to the limit
plus one byte; an oversized record/remaining line fragment raises the same
structured record-limit error instead of allocating an unbounded line. Range
readers stop at their end boundary before requesting the next record.
Sequential JSONL reads, including parallel range readers, use a fixed 64 KiB
binary read buffer to amortize filesystem calls. This is read-ahead only:
logical records still obey the configured byte limit, and a record spanning
multiple buffer fills is yielded intact. Boundary discovery keeps its small
default buffer because its reads are sparse seeks rather than a sequential scan.

**Edge-join execution** — `execute_edge_join(base, join, config,
collect_eager=False)`: normalises both frames to `LazyFrame`, calls
`base_lf.join(join_lf, **build_edge_join_kwargs(config))`, and returns a concrete
`DataFrame` only when both original inputs were eager and `collect_eager` is set. That eager
compatibility path materialises through the shared `execution_collect` seam with
Polars' order-compatible automatic engine. An active execution context records the
boundary and polls the native query for cancellation and RSS enforcement; no
production edge-join path calls bare Polars `.collect()`.
`build_edge_join_kwargs` accepts exactly `inner`, `left`, `right`, `full`,
`semi`, `anti`, and `cross`. `cross` rejects `on`, `leftOn`, and `rightOn`;
every other mode requires either a non-empty `on` value or non-empty,
equal-length `leftOn`/`rightOn` values, and rejects mixing the two forms.

## Edge cases and invariants

- **Object-nesting transparency is absolute**: `$[:].a.b.c` and `$[:].p.q` at the
  same array depth are the same table; only `[:]` advances relational depth. This
  is asserted by `test_v2_object_nesting_inference.py`.
- **`$value` sentinel exclusivity**: a table may carry `$value` as its sole
  own-depth column, plus any number of shallower ancestor columns (which
  distribute), but never another own-depth sibling — enforced at both inference
  (source key never literally `$value`) and shred time (`_reject_reserved_leaf_collision`,
  for hand-edited configs inference could never itself produce).
- **A dotted column leaf crossing a non-empty array fails loud**, not "take
  element 0" — that would silently discard every other element. Crossing an
  *empty* array resolves to `None` (nothing was discarded).
- **Empty-array type inference doesn't poison later concrete types**:
  `scalar_levels` seeds `None` (type-unknown) for an only-ever-empty array, not
  `"str"`, so a later `[1, 2]` still infers `int` rather than being forced to
  `str` by an earlier `[]`.
- **A `None` array element** is a legitimate row for a scalar child table (its
  `$value` resolves to `None`) but a counted shape-mismatch skip for an object
  table.
- **Conservation is asserted, not assumed**: every shred (a table build or the
  standalone path) cross-checks `emitted + skipped == records_read` for every
  emit-true root table and raises `RuntimeError` on any discrepancy — a shred bug
  that silently lost or duplicated rows cannot publish a table.
- **Bool-into-numeric and int/bool-into-Date are rejected even though Polars'
  "strict" build would accept them** (`bool` is an `int` subclass, so Polars won't
  raise on its own for the first case; a raw JSON int/bool successfully
  reinterprets as a days-since-epoch offset for the second) — both checked
  explicitly in `_buffer_to_frame` before the Polars build.
- **Table freshness remains content-authoritative.** The source file's complete
  content hash is reused only behind its freshness token: a native revision
  comprising file identity, length, last-write value, and an
  unforgeable-by-normal-write change token (`ctime_ns` on POSIX; the file USN read
  with `FSCTL_READ_FILE_USN_DATA` plus `FILE_ID_INFO` on Windows), so an in-place
  same-size rewrite followed by an mtime restore and an atomic same-stat replacement
  both force a new hash. Haute does not substitute the weaker
  `FILE_BASIC_INFO.ChangeTime`. Where no native revision can be read, the token is
  the file's stat and is trusted only once the file is two seconds old, the rule
  every source kind shares (see [caching](../caching/low-level.md)). The published
  table generations themselves are verified by the store (part digests, footers and
  schema) before they are read.
- **Source signatures use bounded in-process proof reuse**: the shared
  `file_signature` cache keys at most 256 immutable `FileSignature` entries by
  canonical path; per-path single-flight prevents a concurrent hashing herd. The
  token is read before and after hashing and the result is published only if it
  held; one moving token retries, a second raises `SourceChangedError`. Nothing is
  persisted: a new process hashes each source once. Loader failure publishes
  nothing, and least-recently-used entries are evicted at the bound. A path with no
  native revision logs `source_revision_unavailable` once, so a platform capability
  problem stays operationally visible.
- **Inference accepts only expressible keys** through
  `_jsonpath.is_identifier_name`; non-ASCII/non-identifier keys, dots, and the
  reserved `$value` sentinel fail before a schema is returned. Config sidecars use
  duplicate-key-rejecting loading; raw JSON/NDJSON retains the streaming decoder's
  native duplicate-key semantics and is not rescanned.
- **Table generations expose real columns** with their dtype strings in the
  store's generation metadata; placeholder names and the constant `"v2"`
  pseudo-dtype are never recorded.
- **`narrow_join_parent_demand` only narrows `inner`/`left`/`semi`/`anti` joins**
  with at least one key and a non-empty suffix; `cross`/`full`/`right`, keyless
  joins, and an ambiguous suffixed-name-that-is-itself-a-real-column all return
  `None` (keep the parent boundary full-width) rather than guess.

## Error handling

- `haute._api_input_schema.ApiInputSchemaError` — raised by the `_json_shred/` package for
  every schema/data-shape problem: malformed v2 config passed to
  `shred_to_buffers`/`api_input_snapshot_source` (via
  `validate_v2_schema`, including wrong-typed `emit`/`selected` and invalid
  `status` values with exact field paths), a dotted leaf crossing a non-empty array, a `$value`/real-
  column collision, a column value that doesn't match its declared type (including
  the silent-coercion guards), inference's unexpressible-key rejections, invalid XML,
  XML DTD/entity declarations, mixed XML content, XML field-name collisions, and a
  logical JSON/XML record exceeding `HAUTE_STRUCTURED_INPUT_MAX_RECORD_BYTES`.
  Schema/table errors carry their normal `column=`/`table=` context; XML decode errors
  carry a direct safe message.
- `RuntimeError` — raised by the shared file-shred path on a root conservation-
  assertion failure, and by `load_v2_api_source` for "no emitting tables" or "no
  selected columns on any emitting table".
- `PolarsIoConfigError` (`input_snapshot_missing: ...`) — raised by
  `load_v2_api_source(read_snapshots=True)` for a demanded table with no published
  generation.
- `haute._json_shred._snapshots.SourceChangedDuringCacheBuildError` (a `RuntimeError`
  subclass) — raised when the source signature no longer matches after the shred
  and before publication.
- `haute.errors.ConfigError` — raised by `_edge_join.py` for any malformed
  `edgeJoin` config: wrong connected-input count/distinctness, unresolved or
  ambiguous base/join role, unsupported `how`, missing/conflicting join keys,
  mismatched key counts, non-string suffix, malformed `on`/`leftOn`/`rightOn`.
- `ValueError` — used by the small config validators in `_edge_join.py` for
  malformed non-graph-sourced inputs.
- Grammar rejections in `_jsonpath.parse_path` / `parse_data_path` raise whatever
  the caller injected as `error` — `ApiInputSchemaError` from the INPUT side,
  `OutputMappingSchemaError` from the OUTPUT side — carrying the offending
  `output_path`.
- `OutputMappingSchemaError` covers a non-array root, two different columns from
  one port targeting the same path, leaf/container prefix collisions, and one frame
  targeting divergent emit prefixes. `assemble_output_from_mapping` itself runs the
  validator before collecting any frame, so direct/runtime and route callers receive
  the same typed failure. Missing `frames[port]` or `pl.col(source_column)` failures
  remain loud and are never converted into an empty output.
- `OutputNestingKeyError(OutputMappingSchemaError)` is raised when an active
  participating row contains null in a simple/composite nesting key. It identifies
  `frame`, `output_path`, and `key` and maps to HTTP 422. Rows from frames that do not
  carry the key are non-participants, not null-key orphans.

## Testing

- `tests/test_apiinput_flat_output_dry_run.py` verifies flat API-input-to-output graph execution and dry-run route responses.
- `tests/test_output_nested_roundtrip.py` verifies nested output round-trips and deploy-scorer rendering.

Shred / inference / table snapshots (the `_json_shred/` package):

- `tests/test_json_shred_properties.py` — Hypothesis property tests: exactly one
  root row per record, one scalar-array child row per element, order-independent
  inference (set-based type widening), exact partition/merge equivalence across
  nested/null/scalar-array evidence, and full conservation accounting.
- `tests/test_v2_codec_and_shred.py` — canonical schema validation and layered
  per-port shred behaviour, including that an ancestor `$value` distributed into
  a descendant object table does not suppress that object's rows.
- `tests/test_v2_object_nesting_inference.py` — the 2026-06-17 object-nesting
  transparency ruling, end to end through inference/shred/grammar agreement.
- `tests/test_scalar_array_and_inference.py` — scalar-array-as-its-own-child-table
  regression coverage, plus non-mocked exercise of `infer_v2_schema_from_data`.
- `tests/test_xml_api_input.py` — XML record normalisation, inference, table
  build/read values, and fail-loud rejection of DTD/entity declarations.
- `tests/test_json_shred_parallel.py` — byte-range splitting (exact tiling, no
  record split, order preserved) and serial-equivalence of parallel inference
  and build: identical inferred schema ordering, late-field discovery and type
  widening, identical frames and row order, identical skip accounting
  (including per-table row skips crossing chunk boundaries and a source without
  a trailing newline), identical typed failures, and the scratch directory
  cleaned up on failure. Dispatch is witnessed in both directions for inference and build alike:
  an eligible source must actually take the parallel path, and a single-range
  or explicitly sampled source must stay serial.
- `tests/test_json_shred_w1_conservation.py` — fail-loud/accounting regressions:
  reserved-key rejection, `$value`/sibling-column rejection, empty-array type
  non-poisoning.
- `tests/test_json_shred_mut_*.py` (`parser`, `shred`, `validity`, `records`,
  `infer`, `stragglers`) and
  `tests/test_json_shred_mutation_witnesses.py`,
  `tests/test_json_shred_native_revision_mutation.py`,
  `tests/test_json_shred_publication_mutation.py`,
  `tests/test_json_shred_runtime_control_mutation.py`,
  `tests/test_json_shred_signature_mutation.py`, and
  `tests/test_json_shred_stream_mutation.py` — targeted mutation-testing witness
  suites; each pins specific observable branches, boundary values, failure
  evidence, and state transitions so a mutation-testing run cannot silently
  survive a change to them.
- `tests/test_inference_identifier_labels.py` — focused mutation witnesses for
  inferred table-label derivation, symmetric collision qualification,
  deterministic suffixing, case-only collisions, and validation closure.
- `tests/test_load_v2_api_source.py` — direct coverage of the shared runtime entry
  point: emit checks, `port_columns` projection rules, store-leased table reads
  (a missing table is `input_snapshot_missing`; a built table reads without its
  source), the standalone in-process shred, scalar/empty arrays, typed raw-data
  failures, and the uniform `{label: LazyFrame}` return shape from one eligible
  frame up. Label invariant B4
  (ASCII-identifier-only labels; hard keywords rejected; valid *Unicode*
  identifiers such as `café` rejected with the ASCII rule named in the error)
  is pinned alongside the existing blank/duplicate cases in the
  schema-validation suites; the B2 check compares casefolded stems — a
  case-only pair such as `Items`/`items` is rejected naming both labels and
  the shared stem — and Unicode identifier labels are pinned as
  B4 rejections. Inference label derivation is pinned in the `infer` suites:
  `derive_identifier_label` character/repair cases (spaces, punctuation,
  digit-leading, hard keyword, empty, non-ASCII `_x<hex>_` encoding), root →
  `quote_info`, innermost-key labelling, symmetric collision qualification
  (`a_items`/`b_items`), the numeric-suffix backstop, and the closure
  property that inferred output passes `validate_v2_schema` unchanged.
- `tests/test_api_input_table_snapshots.py` — table identities (one per emitting
  table, label- and sibling-independent), the source signature and table
  freshness, the one-shred build (full width, shared identities built once, exact
  plans, missing and changing sources, cancellation, skip reporting, serial/parallel
  dispatch, scratch cleanup), store-leased reads without the source, the supervised
  worker build and its settlement, and automatic preparation (built, reused,
  one edited table rebuilt, source touched refreshes every table, missing source,
  corrupt table, worker and cap-unavailable paths).
- `tests/test_json_direct_spill.py` — standalone JSON/JSONL direct-spill streaming, validation, disk-budget, and cleanup regressions.
- `tests/test_json_runtime_storage.py` — owned runtime-storage orphan recovery, symlink/reparse preservation, hard-link accounting, and budget-integrity safeguards.
- `tests/test_json_cache_coverage_uplift.py`,
  `tests/test_json_cache_corrupt_and_errors.py`, and
  `tests/test_json_cache_mut_witnesses.py` — the inference route's path
  confinement, error arms, and inferred schemas building cleanly;
  `tests/test_multi_frame_end_to_end.py`,
  `tests/test_apiinput_multi_port_runtime.py`,
  `tests/test_apiinput_nested_relative_path.py` — broader integration coverage
  (multi-frame ports, relative data paths, nested apiInput contexts).

Path grammar (`_jsonpath.py`):

- `tests/test_jsonpath_canonical.py` — direct grammar unit coverage for the
  canonical writer and INPUT-mode `parse_data_path` (`allow_root`, the `$value`
  reserved leaf). The OUTPUT-mode `parse_path` is additionally exercised
  through the assembler suite.

V2 schema codec and OUTPUT shape:

- `tests/test_v2_codec_and_shred.py`,
  `tests/test_v2_object_nesting_inference.py`, and the inference error suites
  own v2 recognition, canonical parse/write behaviour, label/
  column/type/row-ID invariants, structured schema errors, and ancestor-column
  rules.
- `tests/test_output_assembler.py` and
  `tests/test_output_assembler_mutation_witnesses.py` own mapping validation,
  focused mutation boundaries, deterministic cyclic
  cuts, bag fan-out, unmatched partials, sibling-array non-explosion, pruning,
  rendering, exact assembled shapes, one-parse-per-distinct-path validation,
  incomplete editor rows, multi-frame relation keys absent from a
  non-participating frame, and limited assembly (the first documents read only
  their own children's rows, limited multi-port levels emit unlimited documents,
  a synthesised root is complete, duplicate root rows collapse, and a limited level
  filters on its nearest collected ancestor's own key with `is_in`, semi-joins on
  several, and reads every row when it carries none);
  `tests/test_output_nest_example_contract.py`
  pins the fixture-level nested-document contract, while
  `tests/test_executor_builders.py` and `tests/test_codegen_builders.py` own the
  executor/generated-code integration boundary, and
  `tests/test_output_schema_only.py` owns `output_document_schema` — its fidelity
  against the assembler's own nesting and field order, its dtype fidelity and
  rendering-neutrality, and the schema-only build that never assembles.
- `frontend/src/__tests__/editors/OutputEditor.test.tsx`,
  `frontend/src/__tests__/editors/OutputEditorPathTools.test.tsx`, and
  `frontend/src/__tests__/editors/jsonpath.test.ts` own the UI-adjacent mapping,
  conflict-display, CSV import/export, and canonical-path contracts;
  their production modules remain owned by the frontend editor spec.

Edge join (`_edge_join.py`):

- `tests/test_edge_join.py` — backend contracts for `edgeJoin` node config
  validation (including the exact seven-mode set and same-name/paired/cross
  key invariants) and codegen decorator round-tripping.
- `tests/test_trace_edge_join.py` — lineage/trace correlation specifically for
  join-role columns (base vs. join, suffix-renamed duplicates).
- `tests/test_preview_json_serialization.py` — regression coverage for
  `to_json_safe`/preview payload shaping (dates, non-finite floats, etc.).

Projection planning and its `tests/test_projection_planner.py` coverage are owned
by [execution-engine](../execution-engine/low-level.md).

## Canonical cache-artifact contract

JSON shredding creates, validates, and cleans only the current layouts: table
generations in the shared input-snapshot store, the build's scratch directory,
and standalone spills. It contains no discovery or deletion code for cache
files, temporary directories, backups, or manifests emitted by an earlier Haute
implementation: the former `.haute_cache/working` and `.haute_cache/committed`
JSON cache directories are neither read nor removed. Current transactional
cleanup remains covered; there are no migration-only cleanup tests.
