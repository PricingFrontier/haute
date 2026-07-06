# CLEARED — adversarially checked and found CORRECT. Do not "fix" these.

Each item was explicitly probed by a reviewer (main pass or one of the four scoped reviewers)
and found sound. If a fix package seems to touch one, the package text wins only where it
names the item; otherwise treat this list as binding.

## Read path (`_io.py`, adapters, parser)

1. **Bounded-profile rules are principled fail-loud, not fallbacks**: CSV requires declared
   dtypes in bounded profiles (`_io.py:268-281`); plain JSON is rejected *before* eager parse
   with an actionable message naming NDJSON/parquet alternatives (`:518-529`;
   `tests/test_io.py:140` pins that `read_json` is never called). Keep both.
2. **CSV header validation**: duplicate columns rejected loudly with names
   (`_io.py:207-218`); empty CSV → clear `SchemaMismatchError`; BOM tolerated (`utf-8-sig`).
3. **Path safety**: `_validate_source_path` rejects URL schemes and `..` (`_io.py:141-147`);
   `validate_safe_path` guards browse/schema; `config_path_for_node` rejects traversal
   (`_config_io.py:215-243`); sink writes are confined to the project root
   (`resolve_sink_output_path`, `executor.py:1462-1498` + `routes/pipeline.py:156-167`).
4. **Relative-path anchoring**: `_config_with_resolved_data_path` /
   `_resolve_runtime_data_path` anchor GUI-relative paths to the pipeline dir at execute time
   (`_builders.py:328-413`); codegen anchors via `Path(__file__).parent`
   (`_portable_path_expr`). The cwd≠pipeline-dir class is properly closed.
5. **`DataSourceAdapter` / `build_data_source_adapter`** is a clean sourceType seam: strict
   required-field validation, unknown `sourceType` fails loud (`_io.py:379-437`). IO12 gives
   *formats* the same treatment; do not undo this one.
6. **Sidecar config hygiene**: duplicate JSON keys rejected on load
   (`reject_duplicate_keys_hook`, `_config_io.py:82-97`); internal `_*` keys stripped
   recursively; write allowlist warns-and-drops off-spec keys; `selected_columns` on a sink
   survives via `_UNIVERSAL_KEYS` (`_config_validation.py:68-77`) and is validated in
   `execute_sink` (`executor.py:1542-1551`).
7. **Empty source path → empty LazyFrame ONLY in preview/deploy-live profiles**
   (`_allow_empty_source_path`, `_builders.py:97`); bounded batch does not silently continue.
8. **`load_external_object`**: cache keyed by `(path, content_hash, file_type, model_class)`,
   restricted unpickler, project-path validation (`_io.py:552-575`).

## OUTPUT node / assembler

9. **`_prune` + empty-collection semantics are deliberate and documented** (Q1 rule,
   `_output_assembler.py:358-390`) — IO03 fixes the frame construction *on top of* the pruned
   document, not the pruning.
10. **`is_active_mapping_entry`** correctly skips half-built editor rows so a blank
    `source_column` never demands a `""` column (`:568-583`).
11. **`validate_v2_output_mapping`** is loud on grammar, injectivity, and
    prefix-comparability, keyed on typed `OutputMappingSchemaError` (`:586-630`); routes
    discriminate on the type, not message text.
12. **`_resolve_output_node` / `score()` seeding semantics are explicit**: exactly-one
    `@output` or single leaf, ambiguity raises naming nodes; multi-source seeding without a
    marked api_input raises (`pipeline.py:422-455,508-545`).
13. **Legacy v1 `fields` is properly dead**: rejected at build with a migrate-me error
    (`_builders.py:775-783`), dropped at save, warned on load. (IO06-g is about the *frontend
    default config* still being v1-shaped — that's the bug, not this.)

## Sink / write path

14. **The GUI sink write is streaming AND atomic**: `execute_sink` pre-creates the parent
    (`executor.py:1560`) → `bounded_sink` (no eager broadening; typed
    `BoundedMemoryUnsupportedError`) → `streaming_sink` → `atomic_write` temp+rename.
    IO05-c/g fix the temp *name* and the missing-parent degradation — the architecture stands.
15. **`_gen_data_sink` normalises the path with `_resolve_sink_path` before emitting**
    (`_codegen_builders.py:1024`) — GUI-resolved and generated destinations agree.
16. **Sink admission + metrics**: sink runs under an admitted `ExecutionContext` with
    checkpoints and typed error mapping in the route (`routes/pipeline.py:732-806`).
17. **Sink errors surface verbatim in the editor** (`SinkEditor.tsx:52`) — no
    sanitisation-to-constant on this path; and the Write button gates on committed
    `config.path`, not the local draft (`:32,94`).

## apiInput / JSON shred

18. **The Polars silent-coercion guards in `_buffer_to_frame` are exactly right and
    empirically verified**: bool→Int/Float and int→Date would coerce silently in strict mode;
    the guards intercept precisely those and fail loud (`_json_shred.py:838-860`). IO04-a
    adds scalar→str stringification; it must NOT weaken these.
19. **Conservation accounting is sound**: root tables are always object tables
    (`$[:].$value` rejected), so `emitted + skipped == record_count` holds
    (`:990-1002`); dict intruders in object tables are counted skips (IO04-b is the one
    scalar-side gap).
20. **`_iter_sampled_json_array_records`** byte scanner: string/escape/nesting handling is
    UTF-8-safe, iterative (no recursion blowup), truly early-terminating; documented
    infer-only laxity is a stated tradeoff (`:408-521`).
21. **Reserved-`$value` collisions rejected** both at infer (`:1347`) and hand-edit
    (`_reject_reserved_leaf_collision`, `:602`).
22. **Cache lifecycle**: per-cache-dir build lock, atomic tmp-dir swap with Windows
    rename-retry and restore-on-failure (`:1081`), dual working/committed layers,
    content-hash validity, traversal-safe cache naming (sha256 dir + sanitised labels),
    routes resolve through `resolve_runtime_file_path(enforce_project_root=True)`.
23. **`_v2_fingerprint` canonicalisation**: column/table ordering canonical; same-path
    different-label configs hash distinctly; two emit-tables sharing a path is by-design
    (two projections, single-pass shred).
24. **JSON-vs-flat apiInput dispatch is single-sourced** (`is_json_api_input_path`); a
    `.json` apiInput without `tables[]` fails loud pointing at Infer Tables
    (`_builders.py:485`), never silently falls back to `read_source`.

## Databricks

25. **`fetch_and_cache` is the strongest write path in scope**: Arrow-batch streaming →
    `pyarrow.ParquetWriter(zstd)` → unique `mkstemp` temp → atomic replace;
    `_assert_no_rows_lost_after_retry` guards cursor-advance-on-retry; zero-row schemaless
    refusal. (IO11-e/f are two small burrs on top, not cracks in this.)
26. **SQL guardrails** (`_validate_select_clause`, `_TABLE_NAME_RE`): SELECT-prefix, no `;`,
    no comments, keyword blocklist — defence-in-depth that fails safe.

## Chunked batch input

27. **Chunk-eligibility proving is the right shape**: `{".parquet",".csv"}` allow-list,
    `is_chunk_local_polars_code` whitelisting with per-construct chunked==full property
    tests, loud `ChunkPlanUnsupportedError` for the unprovable (`chunking.py:1512-1542`).
    IO12 adds IPC deliberately; nothing becomes chunkable by omission.
28. **`_estimate_target_row_bytes`** costs the byte budget off the *target* schema with
    loud failure on derivation errors; checkpoint cleanup handles partials and error paths.

## Frontend (input/output editors)

29. **OUTPUT path grammar + conflict detection mirror the backend faithfully**
    (`OutputEditor.tsx:1301-1388`, `outputMappingSchema.ts:173-202`) including the
    `(name, isArray)` comparison and §3 root-array gate.
30. **`CommittedTextInput`** draft-buffering with adjust-on-render prevents dead-row drafts
    leaking into successors (`OutputEditor.tsx:1525-1528`, `ApiInputEditor.tsx:906-910`).
31. **apiInput rename/edge migration** (`apiInputPorts.ts:332-423`): renames migrate edges
    before orphan pruning; sanitised-collision detection matches Python semantics (`/u`).
32. **Stale-response protection** via `outputReqSeq`/`dataReqSeq` refs
    (`OutputEditor.tsx:388,405,1043,1051`).
33. **`ToggleButtonGroup` is a11y-correct internally** (radiogroup, roving tabindex, arrow
    keys); IO06-h/IO09-d are about callers omitting the label, not the component.
34. **DataSource never synthesises load code into the Polars code box** (pinned by
    `DataSourceEditor.test.tsx:169-186`).
35. **Volatile-schema previews**: OUTPUT dry-run and apiInput cache flows send unsaved editor
    state so previews reflect current edits (`OutputEditor.tsx:395-403`,
    `ApiInputEditor.tsx:83-133`).
36. **Frame-column derivation without a preview round-trip** honours per-column
    `selected !== false` to match runtime emit (`OutputEditor.tsx:98-192`).

## Deploy

37. **Bundling is format-agnostic by design** (`collect_artifacts`, `deploy/_bundler.py:113-124`
    copies static sources by path, no extension allow-list) — IO12-R6: keep it that way.
38. **Static-source schema verification at `DEPLOY_BATCH`** (`:157-208`) correctly extends the
    bounded rules to deploy; IO12-R2 asks for a deliberate policy on eager formats, not a
    weakening.
