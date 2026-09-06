# JSON Shredding — Low-Level Specification

## Module map

| File | Responsibility |
|---|---|
| `src/haute/_api_input_schema.py` | V2 apiInput schema codec: `TypedDict` shapes, extension recognition, canonical table and column path semantics, filesystem label sanitisation, and fail-loud validation. |
| `src/haute/_json_shred/__init__.py` | Package docstring only: names the concern layout; deliberately exports nothing. |
| `src/haute/_json_shred/` | v2 per-frame structured-input engine, decomposed by concern into the submodules below; consumers import each concern module directly (there are no aggregating re-exports). |
| `src/haute/_json_shred/_records.py` | Streaming JSON/JSONL/XML record iteration, the shared bounded record limit, byte-range tiling with its parallelism policy, and the parallel-worker failure transport. |
| `src/haute/_json_shred/_shred.py` | Table specs, leaf resolution, the single-pass record walk, and root-conservation accounting. |
| `src/haute/_json_shred/_writer.py` | Aggregate-bounded Parquet row-group emission for cache artifacts and leased runtime spill bundles, plus parallel chunk execution. |
| `src/haute/_json_shred/_publication.py` | Cross-process cache publication: the per-generation OS file lock, staging-path minting/validation, atomic swap, and crash recovery. |
| `src/haute/_json_shred/_source_proof.py` | Strong native file revisions (Windows USN/file-id, POSIX stat) and SHA-256 content signatures with persisted-proof reuse and rebinding. |
| `src/haute/_json_shred/_runtime_storage.py` | Process-owned runtime storage: the disk budget, spill-directory leases, and verified parquet snapshots with their bounded cache. |
| `src/haute/_json_shred/_inference.py` | v2 schema inference from data: bounded sampling, type widening, and deterministic column naming. |
| `src/haute/_json_shred/_cache.py` | Per-port cache lifecycle (prepare/commit/discard/build, manifest and bundle validation, load) and the runtime apiInput source loader. |
| `src/haute/_json_flatten.py` | Dual-layer (`working/`/`committed/`) cache-directory infrastructure for structured apiInput sources: process-CWD-rooted path resolution, delete, save-time promotion, and preview-cache fingerprint contribution. |
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
  consumed by the cache route's structured 422 response.

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
  `skipped_rows_by_table: dict[str, int]`. `.total` sums both; `.as_meta()` returns
  the `{records, rows_by_table}` shape written into `meta.json` and route responses.
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
(`sanitise_label_for_filesystem(label).casefold()`): parquet caches live on
case-insensitive filesystems (Windows/macOS), where `Items.parquet` and
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
derived schema and never assembles; otherwise it assembles as before and
declares the same schema. Declaring the schema is rendering-neutral —
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

**Build a structured-input cache** — `build_per_port_cache(data_path, v2_config, cache_dir)`:
1. `validate_v2_schema(v2_config)` up front.
2. Acquire the per-cache-directory lock (`per_port_cache_publication_lock`) and retain that lock
   strongly for the complete critical section.
3. Mint the unique sibling staging temp path.
4. Still under the lock, build the shared `_EmittingTableSpec`s once (`_emitting_table_specs`:
   `table_is_emitting` plus parsed table/column paths), the schema fingerprint (`_v2_fingerprint`),
   and the data-file signature (`_data_file_signature`) *before* reading records. When a strong
   native revision is available, the signature includes its strict versioned representation and a
   SHA-256 binding of the signature to that revision so the completed proof can survive a process
   restart without trusting an accidentally altered manifest field. The signature is shared by this
   logical operation and, while its strong revision remains unchanged, by later planner and loader
   operations. A later process can seed the same bounded memo from a live working/committed
   manifest only when its current native revision matches the persisted revision exactly and all
   matching manifests agree on the signature.
5. No-op trapdoor: if `is_per_port_cache_valid` already holds for the current in-memory schema and
   the already-computed data-file signature, return the existing `meta.json` payload without
   rebuilding.
6. Create the unique sibling staging temp dir. It precedes the shred because parallel workers write
   their parts into it; a failure anywhere below removes the whole directory.
7. Shred, by one of two paths that produce identical artifacts:
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
8. Conservation assertion at the root level: for every emit-true root table,
   `emitted + skipped_rows_by_table[label] == record_count`, else `RuntimeError`.
   The parallel path asserts this per chunk; ranges tile the file exactly, so
   holding it on every chunk holds it on the whole file.
9. The shared writer converts each bounded buffer through `_buffer_to_frame`, writes
   it as a zstd Parquet row group with `_per_frame_metadata`, and immediately releases
   the Python rows. Closing the writers also produces valid schema-carrying empty
   parquets. After close, each final artifact is recorded with its derived filename,
   row/column counts, dtypes, and `{size, sha256}` `content_signature`; then
   `meta.json` is written.
10. `_swap_dir_into_place(tmp_dir, cache_dir)` — recoverable two-rename publish
    (below).

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
  Parquet parts in the staging dir, never returned through the pool's result channel.
- The parent reads one part row group at a time and feeds it into the shared writer
  **in chunk order** (so row order matches the serial shred exactly), unlinking each
  part as it is consumed. Parent and child peak memory are therefore bounded by one
  configured row group plus one logical record, rather than one source range. Disk
  is the trade: workers may finish writing every part before assembly starts, so the
  staging directory transiently holds the part parquets alongside the growing final
  parquets. The swap into place still publishes only the final artifacts, and any
  failure removes the staging directory with the parts in it. A chunk that produced
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
  is proven. Direct library builds without an execution context use the parallel path;
  isolated route builds never treat a per-process limit as an aggregate
  descendant budget.

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

**Runtime load** — `load_v2_api_source(data_path, config, *, port_columns=None)`:
1. Validate the v2 schema at this public boundary, then require at least one
   emit-true table and at least one selected column (the latter two raise
   `RuntimeError` with an actionable configuration message otherwise).
2. Construct complete `_EmittingTableSpec`s once: parsed table position plus every selected
   column's name, leaf, declared type, and source array depth. Cache build, direct
   shred, strict frame construction, and ancestor broadcast all consume these specs.
   `port_columns=None` selects every emitting port at full width. Otherwise it is a
   non-empty mapping from emitting label to either `None` (that port at full width)
   or a subset of its declared selected column names. An empty subset is the
   row-cardinality-only demand and physically retains the first declared column as a
   carrier because Polars cannot represent a non-zero-row, zero-column frame. Invalid
   labels or columns fail before cache access. Projected specs retain schema order.
3. Try `working/`, then `committed/`. Read each candidate manifest once. Runtime
   loading, admission metadata, and public cache-validity probes compute a complete
   source-content signature lazily only after a candidate has the right schema
   identity; when no layer has plausible metadata, they do not perform an
   otherwise-unused full-file hash pass. A candidate must
   pass fingerprint/source validity; contain exactly one entry per emitting label;
   derive the expected filename from that label; and carry a strict size/SHA-256
   signature. Missing, duplicate, malformed, or unsigned entries invalidate
   the candidate. A full-bundle call opens every payload; a demand-scoped call
   opens only requested payloads, because an unused payload is not observable in
   that execution. `_snapshot_cache_artifact` first pins each requested path into
   a private process-owned snapshot directory using a same-filesystem hard link.
   Creating the link is atomic with respect to Haute's rename-based publisher: it
   either captures the manifest's generation or a different generation whose
   signature is then rejected. Filesystems without hard-link support use a
   bounded streaming-copy fallback. The first observation of a visible artifact
   generation verifies size and SHA-256 from the pinned artifact in fixed-size chunks;
   the complete compressed payload is never held in a Python `bytes`/`BytesIO` object.
   The verified snapshot may remain in a process-local LRU bounded by
   `HAUTE_JSON_RUNTIME_SNAPSHOT_CACHE_MAX_ENTRIES` and
   `HAUTE_JSON_RUNTIME_SNAPSHOT_CACHE_MAX_BYTES`. A later probe acquires that snapshot
   without hashing only when the current visible path has the exact strong native
   identity/change revision captured after verification and the private file still
   exists. The warm-hit path performs the fork-safe process-state reset and native
   revision checks before allocating or scanning runtime storage; it creates and
   disk-budget-checks a snapshot directory only after the verified-snapshot lookup
   misses. Revision movement, atomic replacement, cache eviction, fork reset, or an
   unavailable strong revision takes the full capture/hash path. Cache eviction removes
   only the cache's pin; an active execution lease or unmanaged process pin keeps its
   snapshot alive until its existing lifetime boundary. Repeated access to the same
   artifact generation reuses one process snapshot; independently mutable artifacts
   remain separate even when their current bytes are identical. Private snapshot
   filenames use a fixed 128-bit SHA-256 prefix so deeply nested Windows project paths
   do not cross the legacy path limit. The cache key and content
   verification still use the complete SHA-256; any truncated-name collision is
   detected by file identity and published under a short UUID fallback instead of
   substituting one artifact for another.
   `scan_parquet(snapshot_path)` remains a native file-backed scan. Before any
   requested-column selection, `LazyFrame.collect_schema()` must expose the port's
   complete exact declared name-to-Polars-dtype mapping. Physical parquet column
   order is irrelevant: an accepted lazy frame is projected into requested-column
   order as inherited from the current declaration. On collection Polars can read
   the footer and selected column chunks without reading unrelated column payloads
   into memory. Integrity validation still streams across the complete compressed
   artifact once; this is storage I/O, not full-payload memory retention.
   An unusable candidate is logged and the next candidate is tried.

   The private snapshot path pins the returned frame and every derived lazy plan to
   this generation across a later rebuild, mirror, or explicit clear. Repeated access
   to the same artifact generation may share one stable private path, reference-counted
   across concurrent managed executions; the current `ExecutionContext` releases its
   lease only after collection and all execution cleanup finish. The bounded LRU may
   retain its independent verification pin until eviction or explicit cleanup. A
   direct caller without a managed context conservatively pins its paths until orderly
   process exit because a public LazyFrame may outlive its original reference and
   Haute cannot safely infer when all derived plans are gone. A hard-linked current
   generation consumes no duplicate blocks; a replaced generation remains only while
   an execution, process, or bounded-cache pin owns it. The streaming-copy fallback
   uses equivalent temporary disk space. Validation-only probes release their
   transient references immediately after footer validation; an admitted verification
   cache entry may retain the proven generation within the same bounds. An ungraceful
   process termination may leave a
   private snapshot directory for later operational cleanup, but never makes that
   directory part of cache discovery or serving.
4. If no cache can serve, `_iter_records` plus the shared shred walker uses only the
   requested projected specs. The same `_BoundedParquetRowGroupWriter` used by cache
   generation owns one aggregate byte/row-bounded buffer across all requested tables.
   Crossing either bound flushes every non-empty table buffer through the same strict
   `_buffer_to_frame` conversion into a PyArrow `ParquetWriter` row group, then releases those Python rows. The
   resulting `{label: scan_parquet(...)}` bundle preserves schema order, row order,
   carrier-column cardinality, skip accounting, strict bool/date errors, and root
   conservation. JSON root arrays are tokenised one complete top-level value at a
   time; JSON root objects, individual JSONL lines, repeated XML children, and
   one-root XML documents are all subject to the hard structured-input record-byte
   limit. The XML parser releases repeated record elements at each direct-child end.
   Checkpoints run during parsing,
   emission, conversion, and flush. Runtime spill files live outside `working/` and
   `committed/`; this path never writes, refreshes, deletes, or promotes cache state.
   Each spill allocation claims its UUID-named child with an exclusive directory
   create. A name collision fails closed and leaves the pre-existing entry untouched;
   rollback removes a spill child only after this allocation successfully created it.
   Orderly-exit cleanup reports residue through structured warnings; cleanup failures
   during a primary build error are attached as exception notes rather than hidden.
   A managed `ExecutionContext` owns the spill lease until collection/cleanup; an
   unmanaged LazyFrame is conservatively process-pinned until orderly exit.
5. Return a `{label: LazyFrame}` dict in schema order for every requested frame
   (or every eligible frame when `port_columns` is absent) — there is no bare-frame single-table special case, so a
   sole frame routes through the same per-edge `source_port` resolution as
   eight frames (see [execution-engine](../execution-engine/low-level.md)
   `_pick_source_frame`), and adding or removing a sibling frame never changes
   the shape a consumer receives.

**Save-time cache promotion** — `mirror_cache_to_committed(data_path)`
(`src/haute/_json_flatten.py`):
1. No-op if this process has not built `working/` for this data file this session
   (`_session_consulted_hashes`, populated only by a successful build-route call) —
   guards against promoting a stale on-disk `working/` left from a previous process.
2. Acquire the `working/` and `committed/` cache-identity locks in canonical resolved
   path order and hold both for the complete promotion transaction. If `working/` is absent, ensure
   `committed/` is also absent (propagate deletion). If working metadata has a
   non-v2 mode, malformed fingerprint/source identity, or source signature that no
   longer matches `data_path`, or if its artifacts are unsigned, malformed, missing,
   or hash-mismatched, preserve committed state and return without promotion.
   Otherwise no-op only when both manifests agree on
   schema fingerprint, schema mode, source signature, and signed table summaries,
   and both layers' actual parquet bytes match those signatures. A damaged committed
   layer is therefore replaced from healthy working state via
   `copytree` into a `.tmp` sibling. Before publish, the staged `meta.json` must equal
   the captured working manifest and every staged parquet must still match that
   manifest's signature; a concurrently changed/mixed copy is removed and committed
   remains untouched. The rejection warning identifies whether the manifest changed,
   an artifact probe failed, the source identity moved, or the probe itself raised;
   these states are never collapsed into an unexplained generic failure. A valid
   stage is published with `_swap_dir_into_place` (shared with build).

**Staged publish** — `_swap_dir_into_place(tmp_dir, live_dir)`:
renames the current `live_dir` aside to a unique `.build-old-<uuid>` name, renames
`tmp_dir` into `live_dir`, then best-effort removes the old backup; if the second
rename raises, it attempts to rename the backup back before re-raising.
`_rename_dir_with_retry` retries a `PermissionError` with increasing backoff
(`0.01s..0.1s`) before giving up — a Windows-specific transient-handle-lock
accommodation.

**Cross-process publication and recovery.** `_build_lock_for` is re-entrant within a
thread and combines the existing per-process `RLock` with an OS advisory lock on a
stable sibling lock file (`flock` on POSIX, one-byte `msvcrt.locking` on Windows).
An existing lock path must be a plain regular file; symlinks, reparse points, and
file-identity swaps are rejected before the lock is trusted.
Builders, promotion, metadata readers, and snapshot capture hold it across the full
generation selection/publication window. Independent cache identities remain
parallel. On outermost acquisition the owner recovers crash-left siblings: a missing
live directory with a single newest plain `.build-old-*` generation is restored;
superseded plain backups and `.build-tmp-*` stages are removed. Symlinks, junctions,
and other reparse points are never traversed or deleted. Recovery is idempotent and
logged; ambiguous or non-directory state fails loudly.

**Runtime storage budget.** `.runtime-snapshots` and `.runtime-spills` live below the
project cache root and use owner directories named by PID plus a random token. Owner
metadata records a format version and creation time. A global OS-locked budget
(`HAUTE_JSON_RUNTIME_DISK_BUDGET_BYTES`, positive integer) counts unique allocated
file identities so hard links are not double charged. Allocation/flush checks run
under that lock; crossing the budget raises `JsonRuntimeDiskBudgetExceededError` and
cleans the caller's partial spill or snapshot. Startup and first-use recovery remove
only plain owner directories older than the configured grace whose PID is no longer
live; active, young, malformed, symlink, and reparse-point entries are preserved and
logged. Budget accounting is fail-closed: a preserved non-plain or unreadable entry
raises `JsonRuntimeStorageIntegrityError` and blocks new runtime allocation until the
entry can be inspected or removed. A concurrently released plain entry may disappear
during the scan and is treated as a benign reduction in usage, never as zero-sized
evidence for an entry that still exists.

**Admission metadata uses a verified generation.** Per-port JSON metadata used by
materialisation admission never trusts a mutable parquet footer merely because a
matching manifest exists. Under the same cache lock it captures the manifest-named
artifact through the bounded verified-snapshot path, checks the complete declared
schema, reads row/width metadata from that exact snapshot, and releases the transient
lease. A missing, unsigned, corrupt, or schema-mismatched generation makes the
estimate unavailable (or moves to the next layer); runtime never falls back to a
different source generation behind an optimistic estimate.

**Schema inference** — `infer_v2_schema_from_data(data_path, sample_size=None)`:
1. Input dispatch preserves a complete scan by default. JSONL/NDJSON at or above
   `_PARALLEL_MIN_BYTES` is split at newline boundaries with the same exact byte
   tiling used by parallel cache construction. Spawned workers infer compact
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
3. `_InferenceState.merge` unions container/null evidence and applies the same
   associative `_widen_type` operation to scalar and object leaves. States are
   merged in range order and dictionaries keep first-observation order, making
   parallel output byte-for-byte identical to serial output, including column
   and table ordering. Worker failures use the same structured reconstruction
   envelope as parallel cache construction.
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
cache build instead of the shared 30-second default. A hidden head-sample is not
permitted here. A field that first appears after the sample is not a type
widening of a declared column; the subsequent build legitimately ignores that
unknown field, so it cannot act as a completeness backstop. Bounded inference
therefore remains an explicit programmatic opt-in whose caller owns the
incomplete-schema trade-off.

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
- **Conservation is asserted, not assumed**: `build_per_port_cache` cross-checks
  `emitted + skipped == records_read` for every emit-true root table and raises
  `RuntimeError` on any discrepancy — a shred bug that silently lost or duplicated
  rows cannot ship a cache.
- **Bool-into-numeric and int/bool-into-Date are rejected even though Polars'
  "strict" build would accept them** (`bool` is an `int` subclass, so Polars won't
  raise on its own for the first case; a raw JSON int/bool successfully
  reinterprets as a days-since-epoch offset for the second) — both checked
  explicitly in `_buffer_to_frame` before the Polars build.
- **Cache validity remains content-authoritative.** The data file's complete
  SHA-256 is memoised only behind a strong native revision comprising file
  identity, length, last-write value, and an unforgeable-by-normal-write change
  token (`ctime_ns` on POSIX; the file USN read with
  `FSCTL_READ_FILE_USN_DATA` plus `FILE_ID_INFO` on Windows). A Windows volume
  that cannot supply a supported USN record takes the full-hash path; Haute does
  not substitute the weaker `FILE_BASIC_INFO.ChangeTime`. Size/mtime alone never
  authorise reuse, so an
  in-place same-size rewrite followed by an mtime restore and an atomic
  same-stat replacement both force a new hash. If the strong token cannot be
  read, that observation re-hashes instead of falling back to a weaker gate.
  Every manifest-declared parquet generation is completely hashed before its first
  footer schema probe. Reuse of that proof requires the same strong native revision
  and the still-private verified snapshot; otherwise it is re-hashed. A
  footer-readable data-page corruption is therefore rejected rather than masked by
  size/mtime or by a stale retained snapshot.
- **The build lock is a native cross-process file lock** (`fcntl.flock` on POSIX,
  `msvcrt.locking` on Windows), keyed by the normcased resolved cache-dir path and
  reentrant within one process; concurrent builds of *different* caches never block
  each other, and a second process building the *same* cache waits for the first to
  publish or release.
  `_BUILD_LOCKS` weakly retains inactive identities, while the caller strongly owns
  its lock throughout table-spec construction, source signing, validation, staging,
  and publish. Cache directories are resolved from the selected project process CWD,
  not relative to the source data file.
- **Source signatures use bounded process-wide proof reuse**: canonical paths
  key at most 256 immutable signature entries; per-path single-flight prevents a
  concurrent hashing herd. The strong revision is read before and after hashing
  and the result is published only if it held. A cache manifest stores that revision
  as `data_file.native_revision` (`posix_ctime_v1` with device/inode/ctime, or
  `windows_usn_v1` with volume/file ID/USN). After a process restart or fork, an
  exact current-revision match may seed the memo without rereading the source only
  when its `native_revision_proof_sha256` validates and every matching live manifest
  supplies the same strict size/mtime/SHA-256 record. The persisted native-revision
  record is a closed shape: `schema_version` is exactly the integer `1` (not a bool
  or numerically equal float), integer identity/size/time fields use their declared
  bounds, and Windows file IDs are exact non-zero 128-bit hexadecimal values.
  Missing, malformed, or conflicting records, and records for an earlier source
  generation, fall through to a complete hash. Once that hash succeeds,
  `_rebind_persisted_source_proofs` rewrites each live v2 manifest whose recorded
  size/SHA-256 agrees with the fresh hash but whose recorded proof differs (a cache
  built on another volume, or before this host could observe a revision) by an atomic
  `meta.json` replacement carrying the current revision-bound proof; a manifest already
  carrying that proof, or one whose content differs, is not changed, and the rewrite is
  skipped when the source revision moved after the hash. A write failure is logged and
  does not fail or weaken the proven read. Revision movement fails the signature
  operation, loader failure publishes
  nothing, and least-recently-used entries are evicted at the bound.
  Callers receive independent signature mappings so mutation of one result cannot
  poison later validity checks. When strong revision support is unavailable, each
  call hashes and retains no cross-operation proof.
  That conservative path emits a bounded once-per-path structured warning naming
  `full_source_hash_per_operation`, so a platform capability problem remains
  operationally visible instead of presenting only as unexplained preview latency.
- **Inference accepts only expressible keys** through
  `_jsonpath.is_identifier_name`; non-ASCII/non-identifier keys, dots, and the
  reserved `$value` sentinel fail before a schema is returned. Config sidecars use
  duplicate-key-rejecting loading; raw JSON/NDJSON retains the streaming decoder's
  native duplicate-key semantics and is not rescanned.
- **Cache metadata exposes real columns** as label-qualified names with their dtype
  strings. Placeholder names and the constant `"v2"` pseudo-dtype are never returned.
- **`mirror_cache_to_committed`'s consulted-hash gate is intentionally never
  cleared** except by a test-only hook (`_clear_session`) that simulates a process
  restart — the user stays authoritative for a data file for the lifetime of the
  process.
- **`narrow_join_parent_demand` only narrows `inner`/`left`/`semi`/`anti` joins**
  with at least one key and a non-empty suffix; `cross`/`full`/`right`, keyless
  joins, and an ambiguous suffixed-name-that-is-itself-a-real-column all return
  `None` (keep the parent boundary full-width) rather than guess.

## Error handling

- `haute._api_input_schema.ApiInputSchemaError` — raised by the `_json_shred/` package for
  every schema/data-shape problem: malformed v2 config passed to
  `_v2_fingerprint`/`shred_to_buffers`/`build_per_port_cache` (via
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
  selected columns on any emitting table". Missing/stale/corrupt/mismatched cache
  artifacts are rejected as optional fast paths and do not mask the direct raw-file
  result or its native missing/decode/type error.
- `haute._json_shred._cache.SourceChangedDuringCacheBuildError` (a `RuntimeError`
  subclass) — raised when the recorded data-file signature no longer matches after the
  shred and before publication.
- `PermissionError` — allowed to propagate from `_rename_dir_with_retry` once all
  retry delays are exhausted (a persistent, not transient, Windows lock).
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

Shred / inference / cache lifecycle (the `_json_shred/` package, `_json_flatten.py`):

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
- `tests/test_xml_api_input.py` — XML record normalisation, inference, cache
  build/load values, and fail-loud rejection of DTD/entity declarations.
- `tests/test_json_shred_parallel.py` — byte-range splitting (exact tiling, no
  record split, order preserved) and serial-equivalence of parallel inference
  and build: identical inferred schema ordering, late-field discovery and type
  widening, identical frames and row order, identical skip accounting and
  manifest (including per-table row skips crossing chunk boundaries and a
  source without a trailing newline), identical typed failures, staging cleaned
  up on failure, and the build driven from a worker thread as the route drives
  it. Dispatch is witnessed in both directions for inference and build alike:
  an eligible source must actually take the parallel path, and a single-range
  or explicitly sampled source must stay serial.
- `tests/test_json_shred_w1_conservation.py` — fail-loud/accounting regressions:
  reserved-key rejection, `$value`/sibling-column rejection, empty-array type
  non-poisoning.
- `tests/test_json_shred_mut_*.py` (`parser`, `shred`, `validity`, `records`,
  `infer`, `lifecycle`, `rename_retry`, `stragglers`) and
  `tests/test_json_shred_mutation_witnesses.py`,
  `tests/test_json_shred_lock_mutation.py`,
  `tests/test_json_shred_native_revision_mutation.py`,
  `tests/test_json_shred_publication_mutation.py`,
  `tests/test_json_shred_runtime_control_mutation.py`,
  `tests/test_json_shred_signature_mutation.py`,
  `tests/test_json_shred_snapshot_state_mutation.py`, and
  `tests/test_json_shred_stream_mutation.py` — targeted mutation-testing witness
  suites; each pins specific observable branches, boundary values, failure
  evidence, and state transitions so a mutation-testing run cannot silently
  survive a change to them.
- `tests/test_inference_identifier_labels.py` — focused mutation witnesses for
  inferred table-label derivation, symmetric collision qualification,
  deterministic suffixing, case-only collisions, and validation closure.
- `tests/test_load_v2_api_source.py` — direct coverage of the shared runtime entry
  point: emit checks, working→committed→direct resolution, cache corruption and
  exact-schema rejection, stale post-schema changes, scalar/empty arrays, typed
  raw-data failures, no-write direct fallback, and the uniform
  `{label: LazyFrame}` return shape from one eligible frame up. Label invariant B4
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
- `tests/test_json_shred_runtime_snapshots.py` — process-local Parquet snapshot
  ownership and failure-path coverage: inherited-PID isolation, reference and
  process-pin transitions, cleanup-registration rollback, partial-copy cleanup,
  missing-file release, and hard-link signature failure.
- `tests/test_json_cache_cross_process.py` — spawn-process cache-build lock serialisation and crash-stage/backup recovery, including fail-closed non-plain paths; and the HTTP build transaction's parent-owned lifecycle with a real spawned child — a request cancellation or a timeout terminates the live child, discards the staging generation, and only then releases the cross-process build lock, which a second process is shown to be blocked on throughout.
- `tests/test_json_direct_spill.py` — uncached JSON/JSONL direct-spill streaming, validation, disk-budget, and cleanup regressions.
- `tests/test_json_runtime_storage.py` — owned runtime-storage orphan recovery, symlink/reparse preservation, hard-link accounting, and budget-integrity safeguards.
- `tests/test_json_cache_routes.py` — API integration tests for the build/status/
  delete HTTP routes (404/422/504 shapes, progress reporting).
- `tests/test_json_cache_integrity.py` — build/validity/load integration end
  to end: session-consulted gate populating `committed/`, data-file-signature
  invalidation, skip accounting surfaced through build/status responses.
- `tests/test_json_cache_coverage_uplift.py`, `tests/test_multi_frame_end_to_end.py`,
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
  `tests/test_v2_object_nesting_inference.py`, and the JSON-cache integrity/
  error suites own v2 recognition, canonical parse/write behaviour, label/
  column/type/row-ID invariants, structured schema errors, and ancestor-column
  rules.
- `tests/test_output_assembler.py` and
  `tests/test_output_assembler_mutation_witnesses.py` own mapping validation,
  focused mutation boundaries, deterministic cyclic
  cuts, bag fan-out, unmatched partials, sibling-array non-explosion, pruning,
  rendering, exact assembled shapes, one-parse-per-distinct-path validation,
  incomplete editor rows, and multi-frame relation keys absent from a
  non-participating frame; `tests/test_output_nest_example_contract.py`
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

JSON flattening and shredding create, validate, replace, and clean only the
current cache layouts and staging names. They contain no discovery or deletion
code for cache files, temporary directories, backups, or manifests emitted by
an earlier Haute implementation. Current transactional cleanup remains
covered; there are no migration-only cleanup tests.
