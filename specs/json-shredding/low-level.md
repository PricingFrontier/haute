# JSON Shredding — Low-Level Specification

## Module map

| File | Responsibility |
|---|---|
| `src/haute/_api_input_schema.py` | V2 apiInput schema codec: `TypedDict` shapes, extension recognition, canonical table and column path semantics, filesystem label sanitisation, and fail-loud validation. |
| `src/haute/_json_shred.py` | v2 per-frame structured-input shred: JSON/JSONL/XML record decoding, single-pass record walk, buffer→parquet build, cache validity/load, schema inference from data. |
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

**`_json_shred.py`**

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
- `_iter_xml_records(data_path)` — rejects DTD/entity declarations, parses one XML
  document, strips element/attribute namespaces, maps attributes and child elements
  to the common object/list/scalar record shape, and promotes homogeneous object
  children of an attribute-free root to top-level records. An attributed root stays
  one record so its attributes are never discarded. Mixed content and field-name
  collisions raise `ApiInputSchemaError`.

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
  Python-decorator-kwarg ↔ camelCase graph-config-key maps (`base_input` ↔
  `baseInput`, etc.), used by codegen round-tripping.
- `_ALLOWED_HOW = {"inner", "left", "right", "full", "semi", "anti", "cross"}`.
- `_ROLE_HANDLE_TO_CONFIG_KEY = {"base": "baseInput", "join": "joinInput"}`.

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
within one source frame before any frame collection. Frames emitting at the same array prefix are
planned by `_plan_cut` and `_execute_plan`; residual shared fields are full bag-
joined (fan-out is retained), cut/disconnected groups are diagonal-concatenated as
partials, and the prefix-tree builder nests child arrays by ancestor values without
joining siblings. Relation-key guards examine a row only when that row actually
contains the key; an absent column in another mapping frame is not a null. A present
null component raises `OutputNestingKeyError`. `_prune` removes null-valued object fields and empty collection
values from objects, and removes empty-object elements from arrays; null or
empty-list elements already present inside arrays are retained.
`render_output_document` applies that same pruning to the collected Polars shape.

`assemble_output_from_config` uses the same assembler and constructs the final
document frame with `infer_schema_length=None`. The assembled response is already
bounded and materialised, so complete-schema inference preserves late non-null nested
fields without another upstream read.

**Build a structured-input cache** — `build_per_port_cache(data_path, v2_config, cache_dir)`:
1. `validate_v2_schema(v2_config)` up front.
2. Acquire the per-cache-directory lock (`_build_lock_for`) and retain that lock
   strongly for the complete critical section.
3. No-op trapdoor: if `is_per_port_cache_valid` already holds for the current
   in-memory schema and on-disk data file, return the existing `meta.json` payload
   without rebuilding.
4. Still under the lock, record the data-file signature (`_data_file_signature`)
   *before* reading records.
5. Still under the lock, build the shared `_EmittingTableSpec`s once
   (`table_is_emitting` plus parsed table/column paths); the walk and parquet frame
   construction consume the same specs. The signature is shared for this logical
   operation rather than rehashed by each consumer.
6. `shred_to_buffers(_counted_records(), v2_config, stats=skip_stats)` — the shred
   core (below) — consuming `_iter_records` directly (not materialised into a list).
7. Conservation assertion at the root level: for every emit-true root table,
   `emitted + skipped_rows_by_table[label] == record_count`, else `RuntimeError`.
8. Stage output in a unique sibling temp dir: `_buffer_to_frame` per table →
   `to_arrow()` → attach per-frame schema metadata (`_per_frame_metadata`) →
   `pq.write_table(..., compression="zstd")`; after each final write, record its
   derived filename plus `{size, sha256}` `content_signature` in the table summary;
   then write `meta.json`.
9. `_swap_dir_into_place(tmp_dir, cache_dir)` — recoverable two-rename publish
   (below).

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

**Runtime load** — `load_v2_api_source(data_path, config)`:
1. Validate the v2 schema at this public boundary, then require at least one
   emit-true table and at least one selected column (the latter two raise
   `RuntimeError` with an actionable configuration message otherwise).
2. Construct `_EmittingTableSpec`s once: parsed table position plus every selected
   column's name, leaf, declared type, and source array depth. Cache build, direct
   shred, strict frame construction, and ancestor broadcast all consume these specs.
3. Try `working/`, then `committed/`. Read each candidate manifest once. It must
   pass fingerprint/source validity; contain exactly one entry per emitting label;
   derive the expected filename from that label; and carry a strict size/SHA-256
   signature. Missing, duplicate, malformed, or unsigned entries invalidate
   the candidate. Each compressed parquet is then read exactly once; size and
   SHA-256 are verified over that exact payload, and the same bytes seed
   `scan_parquet(BytesIO(payload))`. `LazyFrame.collect_schema()` must expose the
   exact declared name-to-Polars-dtype mapping. Physical parquet column order is
   irrelevant: an accepted lazy frame is projected into the current declared order.
   An unusable candidate is logged and the next candidate is tried.

   The in-memory compressed source pins the returned frame and derived lazy plans to
   this generation across a later rebuild, mirror, or explicit clear. Decode and
   projection remain lazy, but the full compressed file is read/copied up front and
   retained while those plans live. The directory swap keeps disk bounded to one
   generation; active-plan memory scales with their compressed source payloads.
4. If no cache can serve, `_shred_data_file` streams `_iter_records` into
   `shred_to_buffers`, preserving skip accounting and root conservation, then
   `_buffer_to_frame(...).lazy()` creates each in-memory frame. This path does not
   write, refresh, delete, or promote either cache layer.
5. Return a `{label: LazyFrame}` dict in schema order for every eligible-frame
   count from one up — there is no bare-frame single-table special case, so a
   sole frame routes through the same per-edge `source_port` resolution as
   eight frames (see [execution-engine](../execution-engine/low-level.md)
   `_pick_source_frame`), and adding or removing a sibling frame never changes
   the shape a consumer receives.

**Save-time cache promotion** — `mirror_cache_to_committed(data_path)`
(`_json_flatten.py`):
1. No-op if this process has not built `working/` for this data file this session
   (`_session_consulted_hashes`, populated only by a successful build-route call) —
   guards against promoting a stale on-disk `working/` left from a previous process.
2. Under the shred's own build lock for `working/`: if `working/` is absent, ensure
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
   remains untouched. A valid stage is published with `_swap_dir_into_place` (shared
   with build).

**Staged publish** — `_swap_dir_into_place(tmp_dir, live_dir)`:
renames the current `live_dir` aside to a unique `.build-old-<uuid>` name, renames
`tmp_dir` into `live_dir`, then best-effort removes the old backup; if the second
rename raises, it attempts to rename the backup back before re-raising.
`_rename_dir_with_retry` retries a `PermissionError` with increasing backoff
(`0.01s..0.1s`) before giving up — a Windows-specific transient-handle-lock
accommodation.

**Reader-visibility caveat.** Replacing an existing directory is not atomic to
readers: the live path is absent between the two renames. `_build_lock_for` is a
process-local thread lock that serializes same-process builders (and a promotion
against a build of its working directory), but it does not lock readers or other
processes. A hard interruption after `live_dir` is renamed aside, or a failed
restoration, can leave `live_dir` absent with a UUID `.build-old-<uuid>` backup.
Existing tests exercise same-process build serialization, different-cache
parallelism, staging-write cleanup, transient rename retry, synchronous
restoration after a failed second rename, staged mirror mutation rejection, and
already-returned LazyFrames surviving rebuild, mirror, and clear. A brand-new
concurrent reader can still observe an absent live path and reject that
candidate; cross-process publishers and mid-swap process death are not covered.

**Schema inference** — `infer_v2_schema_from_data(data_path, sample_size=None)`:
1. `_iter_records_for_inference` — full scan; a bounded `islice` for JSONL, NDJSON,
   or XML; or, for a root JSON array, a bounded sample via
   `_iter_sampled_json_array_records`, which hand-parses just enough of the array
   byte-by-byte to avoid materialising the whole file for a sample.
2. Recursive `_walk(value, level, obj_prefix)`: a nested dict stays at the same
   `level`, deepening `obj_prefix` (object folding); a nested list of objects
   descends to a new `level` keyed by the full `(key, is_array)` segment tuple; a
   nested scalar list widens a `scalar_levels[level]` type; a bare scalar widens
   `levels[level][obj_prefix + (k,)]`. `_reject_unexpressible_key` fails loud on a
   `$value`-colliding or dot-containing source key before it can be silently
   mis-addressed later.
3. Table assembly, per observed level, in `(array_depth, len, tuple)` sort order:
   a level only ever seen as a scalar array becomes a one-column `$value` table
   (`_SCALAR_VALUE_COLUMN`); otherwise its object-folded columns are named via
   `_assign_column_names` (bare leaf where unique, else the underscore-joined full
   path, with a final numeric-suffix dedup pass) and typed via the widened
   `levels` map. Only the root level defaults `emit=True`.
4. Label assignment — inferred `label`s are B4-valid identifiers, never raw
   table paths (`path`/`displayPath` still carry the path). The root level is
   labelled `quote_info`; every other level is labelled by its innermost array key
   through `derive_identifier_label(raw)` (`_api_input_schema.py`): the
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

**Edge-join execution** — `execute_edge_join(base, join, config,
collect_eager=False)`: normalises both frames to `LazyFrame`, calls
`base_lf.join(join_lf, **build_edge_join_kwargs(config))`, and only `.collect()`s
if both original inputs were eager *and* `collect_eager` is set.
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
- **Cache validity always re-hashes** the data file's content; there is no
  `mtime_ns`-only short-circuit, so a same-size/same-mtime content rewrite is never
  served as fresh. It also re-hashes every manifest-declared parquet before the
  footer schema probe, so a footer-readable data-page corruption is rejected.
- **The build lock is process-local**, keyed by the normcased resolved cache-dir
  path; concurrent builds of *different* caches never block each other.
  `_BUILD_LOCKS` weakly retains inactive identities, while the caller strongly owns
  its lock throughout table-spec construction, source signing, validation, staging,
  and publish. Cache directories are resolved from the selected project process CWD,
  not relative to the source data file.
- **Source signatures are operation-scoped**: one logical load/build shares its
  `(size, mtime, SHA-256)` result, while a separately initiated operation hashes
  again. No `(size, mtime)`-only cross-operation shortcut exists.
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

- `haute._api_input_schema.ApiInputSchemaError` — raised by `_json_shred.py` for
  every schema/data-shape problem: malformed v2 config passed to
  `_v2_fingerprint`/`shred_to_buffers`/`build_per_port_cache` (via
  `validate_v2_schema`, including wrong-typed `emit`/`selected` and invalid
  `status` values with exact field paths), a dotted leaf crossing a non-empty array, a `$value`/real-
  column collision, a column value that doesn't match its declared type (including
  the silent-coercion guards), inference's unexpressible-key rejections, invalid XML,
  XML DTD/entity declarations, mixed XML content, and XML field-name collisions.
  Schema/table errors carry their normal `column=`/`table=` context; XML decode errors
  carry a direct safe message.
- `RuntimeError` — raised by the shared file-shred path on a root conservation-
  assertion failure, and by `load_v2_api_source` for "no emitting tables" or "no
  selected columns on any emitting table". Missing/stale/corrupt/mismatched cache
  artifacts are rejected as optional fast paths and do not mask the direct raw-file
  result or its native missing/decode/type error.
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

Shred / inference / cache lifecycle (`_json_shred.py`, `_json_flatten.py`):

- `tests/test_json_shred_properties.py` — Hypothesis property tests: exactly one
  root row per record, one scalar-array child row per element, order-independent
  inference (set-based type widening), full conservation accounting.
- `tests/test_v2_codec_and_shred.py` — canonical schema validation and layered
  per-port shred behaviour, including that an ancestor `$value` distributed into
  a descendant object table does not suppress that object's rows.
- `tests/test_v2_object_nesting_inference.py` — the 2026-06-17 object-nesting
  transparency ruling, end to end through inference/shred/grammar agreement.
- `tests/test_scalar_array_and_inference.py` — scalar-array-as-its-own-child-table
  regression coverage, plus non-mocked exercise of `infer_v2_schema_from_data`.
- `tests/test_xml_api_input.py` — XML record normalisation, inference, cache
  build/load values, and fail-loud rejection of DTD/entity declarations.
- `tests/test_json_shred_w1_conservation.py` — fail-loud/accounting regressions:
  reserved-key rejection, `$value`/sibling-column rejection, empty-array type
  non-poisoning.
- `tests/test_json_shred_mut_*.py` (`parser`, `shred`, `validity`, `records`,
  `infer`, `lifecycle`, `rename_retry`, `stragglers`) and
  `tests/test_json_shred_mutation_witnesses.py` — targeted mutation-testing witness
  suites; each pins one specific branch/condition so a mutation-testing run can't
  silently survive a change to it.
- `tests/test_inference_identifier_labels.py` — focused mutation witnesses for
  inferred table-label derivation, symmetric collision qualification,
  deterministic suffixing, case-only collisions, and validation closure.
- `tests/test_load_v2_api_source.py` — direct coverage of the shared runtime entry
  point: emit checks, working→committed→direct resolution, cache corruption and
  exact-schema rejection, stale post-schema changes, scalar/empty arrays, typed
  raw-data failures, no-write direct fallback, and the uniform
  `{label: LazyFrame}` return shape from one eligible frame up (the former
  bare-frame single-table case is pinned as removed). Label invariant B4
  (ASCII-identifier-only labels; hard keywords rejected; valid *Unicode*
  identifiers such as `café` rejected with the ASCII rule named in the error)
  is pinned alongside the existing blank/duplicate cases in the
  schema-validation suites; the B2 check now compares casefolded stems — a
  case-only pair such as `Items`/`items` is rejected naming both labels and
  the shared stem — and the former Unicode B2 witness labels are pinned as
  B4 rejections instead. Inference label derivation is pinned in the `infer` suites:
  `derive_identifier_label` character/repair cases (spaces, punctuation,
  digit-leading, hard keyword, empty, non-ASCII `_x<hex>_` encoding), root →
  `quote_info`, innermost-key labelling, symmetric collision qualification
  (`a_items`/`b_items`), the numeric-suffix backstop, and the closure
  property that inferred output passes `validate_v2_schema` unchanged.
- `tests/test_json_cache_routes.py` — API integration tests for the build/status/
  delete HTTP routes (404/422/504 shapes, progress reporting).
- `tests/test_json_cache_integrity.py` — the Wave-2 build/validity/load rework end
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
- `tests/test_output_assembler.py` owns mapping validation, deterministic cyclic
  cuts, bag fan-out, unmatched partials, sibling-array non-explosion, pruning,
  rendering, exact assembled shapes, one-parse-per-distinct-path validation,
  incomplete editor rows, and multi-frame relation keys absent from a
  non-participating frame; `tests/test_output_nest_example_contract.py`
  pins the fixture-level nested-document contract, while
  `tests/test_executor_builders.py` and `tests/test_codegen_builders.py` own the
  executor/generated-code integration boundary.
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
