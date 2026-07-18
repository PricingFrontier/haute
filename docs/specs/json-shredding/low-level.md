# JSON Shredding — Low-Level Specification

## Module map

| File | Responsibility |
|---|---|
| `src/haute/_json_shred.py` | v2 per-frame JSON shred: single-pass record walk, buffer→parquet build, cache validity/load, schema inference from data. |
| `src/haute/_json_flatten.py` | Dual-layer (`working/`/`committed/`) cache-directory infrastructure for JSON apiInput sources: path resolution, delete, save-time promotion, preview-cache fingerprint contribution. The v1 flattening codec that used to live here has been removed. |
| `src/haute/_flatten.py` | Dissolves submodel placeholder nodes into a flat, executable `PipelineGraph`. |
| `src/haute/_json_safe.py` | Recursively converts Python/pipeline values into JSON-safe representations for API responses and preview rows. |
| `src/haute/_jsonpath.py` | The shared array-outer JSON path grammar: acceptance parsing, canonicality predicate, canonical writer. Used by both INPUT (this component) and OUTPUT (io-layer) path addressing. |
| `src/haute/projection.py` | Shared column-projection planning facade: the `ProjectionPlan` data model, the reverse-topological demand sweep, and the per-node-type rule registry. |
| `src/haute/_edge_join.py` | `edgeJoin` node config validation, Polars join-kwargs construction/execution, and the shared join column-demand-narrowing function used by both static projection and runtime narrowing. |

## Key types and data structures

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

**`projection.py`**

- `ProjectionRequest` — frozen dataclass: `graph`, `target_node_id`, `profile`,
  `required_columns_by_node`, `source`. The public input to `plan()`.
- `ProjectionReason` — `(rule, message, details)`, attached to every node/edge
  decision for diagnostics.
- `ProjectionDiagnostics` — `opaque_reasons` / `node_reasons` / `edge_reasons` maps.
- `ProjectionPlan` — frozen: `needed_by_node: Mapping[str, frozenset[str] | None]`,
  `edge_demands: Mapping[tuple[str, str], frozenset[str] | None]`,
  `materialisation_boundaries`, `opaque_boundaries`, `diagnostics`. `None` for a
  column set means "opaque — read everything". `strategy_summary_payload()` and
  `diagnostics_payload()` render bounded, route-safe summaries.
- `SourceScanProjection` — `columns: frozenset[str] | None`,
  `validate_columns: frozenset[str]` — the physical scan projection for a source
  node plus columns to validate-but-not-read.
- `AllExcept` / `AllExceptColumns` (alias) — `required_columns`, `excluded_columns`;
  a schema-derived "train on everything except X, but always keep Y" demand that is
  resolved against the *materialised* schema rather than converted to a concrete
  column set during static planning.
- `ProjectionRuleCoverage` — registry entry: `node_type`, `rules: frozenset[str]`,
  `opaque: bool`, `note`.
- `ParentDemandResult` — `default: set[str] | None`, `by_parent: dict[str, set[str]
  | None]`, `rule_name`. `.for_parent(id)` falls back to `default`.
- `PreparedGraph` (`NamedTuple`) — `node_map`, `order` (topo), `parents_of`,
  `id_to_name`, `relevant_edges` (post live-switch-pruning, ancestor-filtered).
- Rule dataclasses, each owning one node-type-specific corner of demand routing:
  `OptimiserParentDemandRule`, `SingleParentPolarsExpressionRule`,
  `OpaqueContractRule`, `PolarsFanInRule`, `EdgeJoinFanInRule`. Each is a frozen
  dataclass with a `name` and a `parent_demands(...)` method returning
  `ParentDemandResult | None` (`None` = "this rule doesn't apply, try the next one").
- `_JoinCallInfo` (`NamedTuple`) — `left_parent`, `right_parent`, `how`, `suffix`,
  `key_pairs` — a Polars `.join()` call inferred from a node's Python source by
  AST analysis (`_join_calls_for_parent_inputs`).

**`_jsonpath.py`**

- `_Seg` (`NamedTuple`) — `(name, is_array)`, one output-path segment.
- `_ParsedPath` (frozen dataclass) — `raw`, `segments: tuple[_Seg, ...]`,
  `root_array: bool`.
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

**Build a JSON cache** — `build_per_port_cache(data_path, v2_config, cache_dir)`:
1. `validate_v2_schema(v2_config)` up front.
2. Acquire the per-cache-directory lock (`_build_lock_for`).
3. No-op trapdoor: if `is_per_port_cache_valid` already holds for the current
   in-memory schema and on-disk data file, return the existing `meta.json` payload
   without rebuilding.
4. Record the data-file signature (`_data_file_signature`) *before* reading records.
5. Re-derive `(label, col_specs)` per emitting table (`table_is_emitting`).
6. `shred_to_buffers(_counted_records(), v2_config, stats=skip_stats)` — the shred
   core (below) — consuming `_iter_records` directly (not materialised into a list).
7. Conservation assertion at the root level: for every emit-true root table,
   `emitted + skipped_rows_by_table[label] == record_count`, else `RuntimeError`.
8. Stage output in a unique sibling temp dir: `_buffer_to_frame` per table →
   `to_arrow()` → attach per-frame schema metadata (`_per_frame_metadata`) →
   `pq.write_table(..., compression="zstd")`; write `meta.json`.
9. `_swap_dir_into_place(tmp_dir, cache_dir)` — atomic publish (below).

**Shred core** — `shred_to_buffers(records, v2_config, stats=None)`:
1. Validate schema; collect emit-true tables' `(label, segments, col_specs)`, where
   each column spec carries its own `source_depth` (its own table's array depth, or
   a shallower ancestor depth for a W1 "distribute a parent value to every
   descendant row" column).
2. `_reject_reserved_leaf_collision` per table: a `$value` leaf may not coexist with
   another own-depth column.
3. Group tables by their full `(key, is_array)` segment position
   (`tables_by_pos`), and compute the object-hop + array-key "descents" needed to
   reach each child array from its parent position (`descents_by_pos`).
4. Walk: `_emit_at(pos, record, ancestors)` emits a row into every table registered
   at `pos` (skipping — and counting — a shape-mismatched record for that table),
   then descends into each child array via `_walk_array`, which iterates the array
   and recurses into `_emit_at` per element (a `None` element is a real row for a
   scalar table, a counted skip for an object table).
5. Returns `{table_label: [row_dict, ...]}`.

**Runtime load** — `load_v2_api_source(data_path, config)`:
1. Validate at least one emit-true table exists, and at least one has a selected
   column (both raise `RuntimeError` with an actionable message otherwise).
2. Resolve `working/` cache dir; if `is_per_port_cache_valid` fails, fall back to
   `committed/`; if that also fails, raise with `_STALE_CACHE_MESSAGE`.
3. `load_per_port_cache` — `pl.scan_parquet` per emitting table's parquet.
4. Return a bare `LazyFrame` if exactly one emitting label, else a
   `{label: LazyFrame}` dict in schema order.

**Save-time cache promotion** — `mirror_cache_to_committed(data_path)`
(`_json_flatten.py`):
1. Wipe legacy flat-layout artifacts.
2. No-op if this process has not built `working/` for this data file this session
   (`_session_consulted_hashes`, populated only by a successful build-route call) —
   guards against promoting a stale on-disk `working/` left from a previous process.
3. Under the shred's own build lock for `working/`: if `working/` is absent, ensure
   `committed/` is also absent (propagate deletion); else no-op if
   `working/meta.json` and `committed/meta.json` already agree on schema
   fingerprint, schema mode, and data-file signature; else `copytree` into a `.tmp`
   sibling and `_swap_dir_into_place` it into `committed/` (shared with the build's
   own atomic-publish helper).

**Atomic publish** — `_swap_dir_into_place(tmp_dir, live_dir)`:
renames the current `live_dir` aside to a unique `.build-old-<uuid>` name, renames
`tmp_dir` into `live_dir`, then best-effort removes the old backup; if the second
rename fails, the backup is renamed back into place before re-raising, so the live
directory is never left missing. `_rename_dir_with_retry` retries a `PermissionError`
with increasing backoff (`0.01s..0.1s`) before giving up — a Windows-specific
transient-handle-lock accommodation.

**Schema inference** — `infer_v2_schema_from_data(data_path, sample_size=None)`:
1. `_iter_records_for_inference` — full scan, or (for JSONL/root-array files) a
   bounded sample via `_iter_sampled_json_array_records`, which hand-parses just
   enough of a root JSON array byte-by-byte to avoid materialising the whole file
   for a sample.
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

**Graph flatten** — `flatten_graph(graph, target_name=None)`:
1. If `graph.submodels` is empty, or the requested `target_name` isn't in it,
   return the input `graph` object unchanged (identity, not a copy).
2. `build_edge_join_boundary_target_roles` precomputes, for every submodel being
   flattened, a `(submodel_node_id, edge_join_node_id, connected_source_id) →
   "base"|"join"` map from each internal edge-join node's `baseInput`/`joinInput`
   config.
3. Remove the targeted submodel placeholder node(s); inline each targeted
   submodel's internal `nodes`/`edges` (validating dicts through
   `GraphNode.model_validate`/`GraphEdge.model_validate` as needed).
4. Rewire: an edge whose `source` was a submodel placeholder is rewritten to the
   real node named by stripping the `out__` prefix off its `sourceHandle`; an edge
   whose `target` was a placeholder is rewritten similarly via `in__` and,
   for the target side, its `targetHandle` is replaced by the precomputed
   base/join role (or `None` if the target isn't an edge-join boundary). Any edge
   still referencing a placeholder afterwards is dropped (should not happen).
5. Deduplicate by `(source, target, sourceHandle, targetHandle)`.
6. Return `graph.model_copy(update={...})` with the remaining (non-flattened)
   submodels preserved.

**Projection planning** — `plan(request) = compute_prepared_plan(prepare_graph(...))`:
1. `prepare_graph`: resolve `node_map`; prune live-switch edges inactive for the
   requested `source` (`prune_live_switch_edges`); restrict to the ancestor set of
   `target_node_id` if given, else the whole graph; topo-sort; build `parents_of`.
2. `compute_prepared_plan` walks `order` in **reverse** (children before parents):
   - A childless node's demand is its `outputMapping`'s enabled source columns
     (`OUTPUT` node type) or `None` (opaque terminal) otherwise.
   - A node with children accumulates the union of each child's
     per-parent contribution (`contribution[child].for_parent(node_id)`); any
     opaque child collapses the whole accumulation to `None`.
   - A caller-seeded `required_columns_by_node` entry is merged in (`AllExcept`
     merges its `keep` set; a plain set replaces `None` demand for single-child
     nodes, or is unioned into existing concrete demand; replacing opaque demand
     from *multiple* children raises `ProjectionImpossibleError` under strict
     profiles).
   - Strict profiles force certain source-type nodes with unprovable post-load code
     to `None` demand (`_must_run_source_user_code_unprojected`) — the physical
     scan runs full-width, and downstream edges narrow again afterward.
   - Node-specific routing (`parent_demands_for_node` → optimiser/ratebook rule,
     then single-parent Polars AST rule) is tried first; if it doesn't apply, and
     the node's own `Contract` is concrete (`produced`/`referenced` both non-`None`),
     `base_contribution = (needed - produced) | referenced` is computed and, for a
     multi-parent Polars fan-in, routed through `PolarsFanInRule` (declared
     `inputs_by_parent`, inferred simple joins, or an unambiguous passthrough
     parent); otherwise the node is opaque and routed through
     `EdgeJoinFanInRule` (concrete-schema, mechanical join-key routing) or
     `OpaqueContractRule` (forces parents opaque, or an explicit bounded
     full-width boundary under strict profiles for a multi-parent Polars node or
     unprovable user code).
3. Every node/edge decision is frozen into `ProjectionReason`s for `explain()`.
4. `validate_projection_rule_coverage()` runs once at **module import time** — every
   `NodeType` must appear exactly once in `_PROJECTION_RULE_COVERAGE_BY_NODE_TYPE`,
   an opaque entry must include the opaque-contract rule name, and a
   non-opaque entry must not be *implicitly* opaque (rules == just
   `{"opaque_contract"}`) — so registry drift crashes on import, not on first
   planner call.

**Edge-join execution** — `execute_edge_join(base, join, config,
collect_eager=False)`: normalises both frames to `LazyFrame`, calls
`base_lf.join(join_lf, **build_edge_join_kwargs(config))`, and only `.collect()`s
if both original inputs were eager *and* `collect_eager` is set.

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
  served as fresh.
- **The build lock is process-local**, keyed by the normcased resolved cache-dir
  path; concurrent builds of *different* caches never block each other.
- **`mirror_cache_to_committed`'s consulted-hash gate is intentionally never
  cleared** except by a test-only hook (`_clear_session`) that simulates a process
  restart — the user stays authoritative for a data file for the lifetime of the
  process.
- **`narrow_join_parent_demand` only narrows `inner`/`left`/`semi`/`anti` joins**
  with at least one key and a non-empty suffix; `cross`/`full`/`right`, keyless
  joins, and an ambiguous suffixed-name-that-is-itself-a-real-column all return
  `None` (keep the parent boundary full-width) rather than guess.
- **AST-based Polars demand inference is order-sensitive when it must be**: plain
  rename-free code uses an unordered union walk (safe over-approximation); code
  containing a `rename`, a `select`/`select_seq`, or a reference to a
  column the same node derives is routed through an *ordered* backward
  propagation instead, because the unordered walk would either re-add a
  post-rename/derived name to the parent demand (over-demand a nonexistent parent
  column) or under-demand a `select`'s unused-downstream inputs (the `select`
  still executes every output expression regardless of what's needed downstream).
- **`flatten_graph` returns the identity object**, not a copy, when there is
  nothing to flatten — callers relying on `is` for a no-op check are honoring an
  explicit contract (see `TestNoSubmodels.test_no_submodels_returns_same_graph`).

## Error handling

- `haute._api_input_schema.ApiInputSchemaError` — raised by `_json_shred.py` for
  every schema/data-shape problem: malformed v2 config passed to
  `_v2_fingerprint`/`shred_to_buffers`/`build_per_port_cache` (via
  `validate_v2_schema`), a dotted leaf crossing a non-empty array, a `$value`/real-
  column collision, a column value that doesn't match its declared type (including
  the silent-coercion guards), and inference's unexpressible-key rejections. Always
  carries `column=`/`table=` context.
- `RuntimeError` — raised by `build_per_port_cache` on a conservation-assertion
  failure, and by `load_v2_api_source` for "no emitting tables", "no selected
  columns on any emitting table", "cache changed mid-load" (a parquet vanished
  between validity check and load), and the generic stale-cache message
  (`_STALE_CACHE_MESSAGE`).
- `PermissionError` — allowed to propagate from `_rename_dir_with_retry` once all
  retry delays are exhausted (a persistent, not transient, Windows lock).
- `haute.errors.ConfigError` — raised by `_edge_join.py` for any malformed
  `edgeJoin` config: wrong connected-input count/distinctness, unresolved or
  ambiguous base/join role, unsupported `how`, missing/conflicting join keys,
  mismatched key counts, non-string suffix, malformed `on`/`leftOn`/`rightOn`.
- `haute.errors.ContractMismatchError` — raised by `projection.py` when a declared
  fan-in contract references unknown/missing parents, is malformed, or (for
  `PolarsFanInRule`) still leaves demanded columns uncovered after join-key and
  passthrough-parent inference.
- `haute.errors.ProjectionImpossibleError` (subclass of `ContractMismatchError`
  and `BoundedMemoryUnsupportedError`) — raised under strict profiles when a
  projection seed can't replace opaque multi-child demand, or when fan-in join
  code can't be parsed / uses a non-literal `how`/`suffix`.
- `ValueError` — used internally and locally by `_jsonpath.is_canonical`'s
  ephemeral error-adapter (never escapes the function) and by the small config
  validators in `projection.py`/`_edge_join.py` (`_strict_string_list`,
  `_strict_renames`, `ratebook_factor_required_columns`) for malformed
  non-graph-sourced inputs.
- Grammar rejections in `_jsonpath.parse_path` / `parse_data_path` raise whatever
  the caller injected as `error` — `ApiInputSchemaError` from the INPUT side,
  `OutputMappingSchemaError` from the OUTPUT side — carrying the offending
  `output_path`.

## Testing

Shred / inference / cache lifecycle (`_json_shred.py`, `_json_flatten.py`):

- `tests/test_json_shred_properties.py` — Hypothesis property tests: exactly one
  root row per record, one scalar-array child row per element, order-independent
  inference (set-based type widening), full conservation accounting.
- `tests/test_v2_codec_and_shred.py` — v2 shape recognition (`is_v2_shape`) and
  layered per-port shred behaviour; positively asserts the deleted v1
  `legacy_to_v2`/`v2_to_legacy` symbols no longer import.
- `tests/test_v2_object_nesting_inference.py` — the 2026-06-17 object-nesting
  transparency ruling, end to end through inference/shred/grammar agreement.
- `tests/test_scalar_array_and_inference.py` — scalar-array-as-its-own-child-table
  regression coverage, plus non-mocked exercise of `infer_v2_schema_from_data`.
- `tests/test_json_shred_w1_conservation.py` — fail-loud/accounting regressions:
  reserved-key rejection, `$value`/sibling-column rejection, empty-array type
  non-poisoning.
- `tests/test_json_shred_mut_*.py` (`parser`, `shred`, `validity`, `records`,
  `infer`, `lifecycle`, `rename_retry`, `stragglers`) and
  `tests/test_json_shred_mutation_witnesses.py` — targeted mutation-testing witness
  suites; each pins one specific branch/condition so a mutation-testing run can't
  silently survive a change to it.
- `tests/test_load_v2_api_source.py` — direct coverage of the shared runtime entry
  point: emit checks, working→committed fallback, single- vs multi-port return
  shape.
- `tests/test_json_cache_routes.py` — API integration tests for the build/status/
  delete HTTP routes (404/422/504 shapes, progress reporting).
- `tests/test_json_cache_integrity.py` — the Wave-2 build/validity/load rework end
  to end: session-consulted gate populating `committed/`, data-file-signature
  invalidation, skip accounting surfaced through build/status responses.
- `tests/test_json_cache_coverage_uplift.py`, `tests/test_multi_frame_end_to_end.py`,
  `tests/test_apiinput_multi_port_runtime.py`,
  `tests/test_apiinput_nested_relative_path.py` — broader integration coverage
  (multi-frame ports, relative data paths, nested apiInput contexts).

Graph flattening (`_flatten.py`):

- `tests/test_flatten.py` — direct unit tests: no-submodels/empty/target-not-found
  identity returns, single- and all-submodel flattening, boundary edge rewiring.
- `tests/test_flattening_dedup.py` — regression gate ensuring the pipeline parser's
  submodel-merge path routes through this shared flattener rather than
  re-implementing its own inlining loop.

Path grammar (`_jsonpath.py`):

- `tests/test_jsonpath_canonical.py` — direct grammar unit coverage: the canonical
  writer, the canonicality predicate, and the INPUT-mode `parse_data_path`
  (`allow_root`, the `$value` reserved leaf). The OUTPUT-mode `parse_path` is
  additionally exercised heavily and indirectly through the assembler's own test
  suite (io-layer).

Projection (`projection.py`) and edge join (`_edge_join.py`):

- `tests/test_projection_planner.py` — the public planner API: rule-coverage
  registry completeness/immutability, `plan()`/`explain()`, `source_scan_projection`,
  `model_score_required_output_columns`, and dispatch through the various
  node-type-specific rules.
- `tests/test_edge_join.py` — backend contracts for `edgeJoin` node config
  validation and codegen decorator round-tripping.
- `tests/test_trace_edge_join.py` — lineage/trace correlation specifically for
  join-role columns (base vs. join, suffix-renamed duplicates).
- `tests/test_preview_json_serialization.py` — regression coverage for
  `to_json_safe`/preview payload shaping (dates, non-finite floats, etc.).

> NOTE: `tests/test_compute_needed_columns.py` and `tests/test_checkpoint_projection.py`
> exercise `_execute_lazy._compute_needed_columns` / `_compute_projection_plan`, a
> related but separately-implemented backward column-analysis inside the lazy
> execution engine, not the `projection.py` functions documented here. See
> [execution-engine](../execution-engine/low-level.md) for that analysis; this spec
> does not assert whether or how the two implementations' results are expected to
> agree.

No coverage gaps were identified beyond the execution-engine boundary noted above.
