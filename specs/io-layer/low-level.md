# IO Layer — Low-Level Specification

## Module map

| File | Responsibility |
|---|---|
| `src/haute/_io.py` | API-input flat-file adapters, source/schema validation, direct CSV/JSON/NDJSON/Parquet reads (a Parquet file with a map column refused), and external-object loading. |
| `src/haute/_polars_io_registry.py` | Canonical Data Input/Output format registry, strict config and argument validation, capability publication, and Polars invocation; `parquet_source_map_columns` finds a Parquet source's map columns in its files' own schema, `refuse_direct_map_columns` refuses a direct Parquet scan whose files hold a map, and `read_parquet_map_entries_for_snapshot` reads such a file for its snapshot through pyarrow and refuses a map in a source of several files (*Map columns* under Edge cases and invariants). |
| `src/haute/_input_providers.py` | Provider dispatch, source identity/signature construction, explicit snapshot build, and leased snapshot resolution. |
| `src/haute/_input_preparation.py` | Automatic snapshot preparation planned before execution: freshness check, cap gate, in-process or worker build, per-process single-flight, preparation records, and staging-token cleanup. |
| `src/haute/_database_io.py` | Credential-free database locator/query validation and bounded read-only SQLite snapshot batches. |
| `src/haute/_credential_security.py` | Shared URI credential detection and provider-diagnostic redaction. |
| `src/haute/_source_cache.py` | Primary owner of source-cache identities, generations, metadata, publication, leases, status, and cleanup. A generation's metadata records `build_seconds`, the wall-clock span from allocating its staging directory to writing that metadata — how long caching it took — absent on a generation published before it was recorded, which reads as unknown rather than instant. |
| `src/haute/_node_snapshots.py` | Node-output snapshots in the shared store: slot and signature identity, class mappings, slot index, column sets and dependencies, cross-process publication and lease locks, lease markers, retention, clear, and pin; also the per-owner inventory (`inventory`, `CacheInventory`, `CacheOwnerUsage`) the cache endpoints serve. |
| `src/haute/_polars_io_schema.py` | Cached index over the committed Polars callable schema, live introspection of the installed Polars, and the intersection of the two. |
| `src/haute/_polars_io_arguments.json` | Generated Polars callable signature data checked against the pinned Polars version. |
| `src/haute/_polars_dtypes.py` | The one dtype vocabulary. `parse_dtype`/`dtype_to_spec` are the struct-capable JSON codec used by registry schema arguments, declared API-input source dtypes and, with renamed keys, rating descriptors; a name it does not define is rejected, never looked up as an arbitrary Polars attribute. Named coarser views serve the consumers that need one: `contract_dtype_name` (a model's feature contract, shared by training and deploy scoring), `contract_mlflow_type_name` (that contract's MLflow projection) and `rendered_dtype_mlflow_type_name` (the MLflow type of a rendered Polars dtype, as a deploy manifest records it). `parse_rendered_dtype` rebuilds a dtype from polars' own rendering (`str(dtype)`, as a preview reports a column's type) by reading it as a Python expression tree whose names must be Polars dtypes and whose arguments are literals, lists, tuples, dicts or nested dtypes; it never evaluates the text. `is_string_or_categorical` tests for text by base type, and `WIDE_INTEGER_DTYPES`, `holds_wide_integer`, `wide_integer_column` and `wide_integer_message` find and refuse a 128-bit integer; *Polars 2 dtypes* under Edge cases and invariants gives the rules. |
| `src/haute/_map_columns.py` | How a map column reaches a pipeline: as a list of `{key, value}` structs holding every entry. `map_entries_table` casts Arrow tables with pyarrow, sharing the map's buffers; `iter_parquet_map_entries` reads a Parquet file through pyarrow one row group at a time; `map_entries_frame` casts a frame Polars read; `parquet_map_column_names` finds a Parquet file's map columns from its footer. |
| `src/haute/_polars_utils.py` | Shared context-aware automatic/streaming collection, bounded/atomic sink, Parquet metadata, chunk-size scope, and allocator trim helpers. |
| `src/haute/_file_ops.py` | The one atomic-write primitive (`atomic_path`) and the byte/text writers built on it, used for pointer and metadata publication and, through `_polars_utils.atomic_write`, for every Parquet/CSV write-then-rename; `publish_staged_file` publishes a caller's staging file, replacing the target or only while none exists; `PathTooLongError` and `path_too_long_error`, which name a path Windows refused for its length. |
| `src/haute/_path_resolution.py` | Shared runtime path containment/resolution owned by [sandbox-security](../sandbox-security/low-level.md) and consumed by I/O. |
| `src/haute/_path_case_audit.py` | Cross-platform case-ambiguity warnings for user-facing paths. |
| `src/haute/discovery.py` | Pipeline-file discovery used by file-facing workflows. |

`src/haute/_source_cache.py` is consumed directly by the caching component; that shared
relationship is recorded in `specs/ownership.toml`.

## Key types and data structures

- `IoFormat` and `FORMATS` describe each format's source kind, Polars reader/scanner and
  writer/sinker, extensions, engine dependencies, bounded-read class, and allowed arguments.
- `DataSourceAdapter` wraps the API-input `flat_file` source surface (the only supported
  API-input source type). It has no Databricks branch.
- `SourceCacheIdentity` is a versioned provider plus canonical descriptor. Construction
  rejects secret-bearing keys and credential-bearing URIs before canonical JSON and SHA-256.
- `SourceCacheBuildContext` carries the execution profile, build class, cancellation,
  deadline, progress, optional `ExecutionContext`, and an optional parent-chosen pair: a
  `generation_id` (a canonical UUID) that names the generation the build publishes and a
  short `staging_token` (eight hex characters, keeping the staging path within Windows'
  traditional limit beneath long temporary roots) that names the staging directory
  (`.staging-<staging_token>`), so a parent supervising the build in a worker can reconcile
  exactly that generation and that staging directory after the worker dies; unset, the
  store chooses both itself. The two are set together or not at all.
- `InputPreparationRecord` is the per-input diagnostic record of automatic preparation
  (node id, identity digest, action `reused`/`built`/`refreshed`, build class, execution
  `in_process`/`worker`, reserved memory limit, elapsed seconds, row count, size bytes,
  generation id, optional warning code). `InputPreparationRequest` and
  `InputPreparationOutcome` are the picklable request/outcome pair of the worker entry
  point.
- `SourceCacheMetadata` records identity, generation, optional freshness signature, the
  ordered part list (`parts`: each part's name, size, xxh64 `digest` (16 lowercase hex),
  and rows; `layout_version` 3), total size and rows, columns, schema, creation time,
  profile, and build class.
- `SourceCacheGeneration` names its immutable `meta.json` and its ordered part files
  (`data_paths`, `part-00000.parquet`, `part-00001.parquet`, …; `lazy_frame` scans exactly
  that list). A generation written in layout 1 or 2 reads as absent
  (`SourceCacheLegacyLayoutError`, a `SourceCacheGenerationMissingError`), never as
  corruption, so a build replaces it. So does a generation the current pointer names whose
  directory is gone (evicted by another process, deleted by hand): `open_generation` and a
  current lease raise `FileNotFoundError` for both, `status` reports `missing`, and
  preparation or an explicit build builds it again. Before a generation is trusted, its
  directory must be a plain directory inside its identity and every artifact a plain,
  single-link file inside the generation; each part's size, xxh64 `digest`, footer row
  count, and schema must match its record; `SourceCacheGenerationMissingError` (a
  `SourceCacheCorruptError`) reports a named generation that does not exist.
- `SourceCacheStore` coordinates same-root handles in-process, publishes generations, tracks
  local leases and verified-generation memos, reclaims provably stale
  staging, and exposes `build`, `lease`, `lease_generation`, `clear`, and `status`. Leases
  protected across processes by lease markers and a shared lock. A
  superseded generation is normally retired after `HAUTE_INPUT_CACHE_RETIRE_GRACE_SECONDS`
  (default 1800) have elapsed since the current generation was published; explicit clear
  bypasses the grace period while preserving live readers. Retirement is the node outputs'
  (*Retirement* under Node-output snapshots): a generation is renamed to `.retired-<hex>`
  under the lease lock and its files are deleted after the lock is released, never removed
  with a bare `rmtree` under it. A reconcile removal that leaves
  its directory behind is logged (`source_cache_reconcile_removal_failed`) and reported as
  `unremovable`.
- `source_signature` is the shared content signature
  (`_json_shred._source_proof.file_signature`, `xxh64:<digest>:<size>`), so an unchanged file
  is hashed once per process whatever asks. A folder or glob pattern is signed by every file
  it names (`_source_proof.source_files`: every file under a folder, every match of a
  pattern with `**` across folders, hidden files included, a pattern's characters literal
  when the `glob` argument is `false`) as `xxh64-files:<digest>:<count>`
  (`_source_proof.files_signature`, the digest over each file's path and its own
  signature), so adding, removing, renaming or rewriting one makes the snapshot stale; a
  path that names no file is `missing`. A proof made under a native revision is
  reused across processes from its durable record
  ([caching](../caching/low-level.md#durable-source-proofs)). Reuse follows the one freshness guarantee in the
  [caching](../caching/low-level.md) specification: a native revision where the platform has
  one, otherwise a stat trusted only for a file modified at least two seconds earlier,
  because a filesystem stamps mtimes at its own granularity and a same-size rewrite inside
  that window would otherwise keep a stale signature (git's racy-index rule).
  `signed_source_files` names the files `source_signature` hashes: the files a `file`
  provider's anchored path names, otherwise none (another provider, or a source that names
  no file and signs as `missing`), from configuration and a listing of the files, never
  their content. The assistant's data check records those files' freshness tokens
  ([assistant](../assistant/low-level.md)).
- `DatabaseSnapshotBuilder` validates a read query and yields Arrow record batches with one
  stable schema from an existing SQLite database.

### Atomic pointer and metadata publication

Every atomic write goes through `_file_ops.atomic_path(target)`: it yields a uniquely named
sibling staging path (`<target stem>.<8 random hex>.tmp`, short so a stage beside a deep
store path stays within Windows' traditional path limit), the caller writes the complete
payload there by any means, and a clean exit renames it onto the target. `atomic_write_bytes`
/ `atomic_write_text` and `_polars_utils.atomic_write` (every Parquet or CSV write-then-rename)
are built on it, so concurrent writers to one target never share a stage and the target ends
as one complete payload (the last rename wins); readers observe either the complete old payload
or the complete new payload. The parent directory must already exist (`atomic_write` creates
it unless its caller passes `ensure_parent=False`). Staged files are not flushed with `fsync`
before the rename. A failed write or exhausted publication
attempt removes the private staging file and preserves the old target. If that exact-file
cleanup also fails, the publication error remains primary, the cleanup failure is attached as
an exception note, and the uniquely named stage remains visible for diagnosis. Windows antivirus,
indexer, and concurrent-reader handles can transiently reject an otherwise valid replace with
`ERROR_ACCESS_DENIED` or `ERROR_SHARING_VIOLATION`; only those two Win32 errors receive the
bounded delays `10 ms, 25 ms, 50 ms, 100 ms`, after which the original error still propagates.
`remove_tree` applies the same two Win32 codes and the same delays to a best-effort directory
removal — discarding a dead worker's staging directory, which must not abort a job's cleanup —
and returns whether the tree is gone so its caller reports a tree that survives instead of
leaking the space silently.
The codes are retried only on Windows; every error on another platform and every other Windows
filesystem error fails immediately. This is retry of the same atomic operation, not
an in-place or non-atomic fallback.

A path Windows refuses for its length is named rather than left a bare "file not found".
While `LongPathsEnabled` is off, Windows takes a file path of at most `MAX_PATH` less its NUL
(259 characters) and a folder path of 12 fewer (247): a folder past that fails with
`ERROR_FILENAME_EXCED_RANGE`, and a file past it fails as missing (`FileNotFoundError`).
`path_too_long_error(exc)` finds such an `OSError` in the chain Python prints for `exc` (its
`__cause__`s, else an unsuppressed `__context__`) and returns a `PathTooLongError`, an
`OSError` with `ENAMETOOLONG` naming the path's length, the limits and the two remedies: a
project folder with a shorter path, or the "Enable Win32 long paths" policy. Any other error on
a long path is that error (a refused permission, a full disk), and a path with the
extended-length `\\?\` or device `\\.\` prefix, which `MAX_PATH` does not limit, is never
named. `atomic_path` raises it, from the original, for a failed write whose cause it is; an
isolated worker reports it as the child's exception (`_worker_error_envelope`), so the parent
can classify the failure. Input preparation's reason code for it is `path_too_long`, including
when the input cache's own folder cannot be created (`_open_cache_store`, which reports it for
the first input that needed the cache), and an input-cache build job's `error_code` is
`path_too_long`; both carry `PATH_TOO_LONG_REMEDIATION`, which names no path. On other
platforms, and on Windows with long paths on, nothing is renamed.

## Control flow

### Canonical Data Input

1. `validate_data_input_config()` rejects inactive fields, invalid modes, unsafe raw URIs,
   unknown arguments, and missing required provider fields. Arguments are judged by the
   callable a build calls: a configured `read` by the scanner when `snapshot_input_plan`
   prefers it (every argument scanner-accepted, so a scanner-only argument such as
   Parquet's `hidden_file_prefix` is valid there), otherwise by the mode's own callable;
   arguments that are not an object are refused by that check before any plan is made. Structural, branch, mode, and
   argument rules are always strict. Presence of required locator values (file/lakehouse
   `path`, Databricks `http_path`/`table`, database locator and `query`) is a separate
   completeness concern: with `require_complete=False` the validator returns those gaps as
   structured completeness entries (field path, code, safe message) instead of raising,
   treating the empty string exactly like absence. `validate_data_output_config()` mirrors
   this for destination locators. Execution, preview, deploy, caching, and RAM callers keep
   the default `require_complete=True` and therefore keep strict presence behaviour.
2. `source_cache_identity()` canonicalises the logical source. Database named connections
   retain only the environment reference; Databricks retains fixed host/token references and
   excludes `batch_size`; inline records contribute only a canonical content digest and row
   count, never raw record values. Relative file/lakehouse locators are resolved through the
   shared runtime path resolver — project root first, configured pipeline directory as
   fallback, an existing file beating a missing candidate, the project-root copy winning when
   both exist — identically for build, execution, execution fingerprinting, and RAM metadata
   inspection, so one locator names one file at every seam. Raw SQLite URIs remain anchored to
   the configured pipeline directory.
3. `data_input_is_direct()` derives the execution mode: file-backed Parquet with
   effective mode `scan` is direct; every other canonical Data Input is snapshot-backed.
   No cache-mode field is stored — validation rejects a config still carrying the removed
   `cacheMode` field as inactive; nothing is normalised or migrated.
4. Direct Parquet resolution anchors the configured path (`anchor_config_source_path`,
   through the shared runtime path resolver: the pipeline directory first, then the
   project root, Windows backslashes read as separators, neither candidate leaving the
   runtime project root, as deploy validates and bundles it; a pipeline directory outside
   the runtime project is its own root, as `_infer_project_root` treats a pipeline outside
   the selected project. A glob pattern is found where it matches a path. A path found in
   neither place keeps the first candidate inside the root, and one with no candidate
   inside raises `RuntimePathOutsideProjectError`)
   and returns the registry's
   `scan_parquet` lazy scan without creating or consulting a source snapshot.
5. Snapshot creation calls `build_input_snapshot()`, selects a provider builder, creates a
   `SourceCacheBuildContext`, and calls `SourceCacheStore.build()`.
6. Snapshot execution calls `resolve_data_input()`, which leases the current generation
   through `lease_input_generation(store, identity, missing_message=...)`: it opens a lease,
   creates a Parquet scan, and attaches lease release to execution cleanup or an explicit
   callable scan-plan token. A structured API Input's tables are read through the same
   helper (see [JSON shredding](../json-shredding/low-level.md)).
7. `resolve_data_input_from_config()` is the standalone-run sidecar entry point: a Data
   Input decorator calls it when a saved pipeline file runs on its own.

### Automatic preparation

1. `prepare_input_snapshots()` runs once per execution, after `_prepare_execution()` has
   pruned the graph to the target lineage and before strategy planning and before runtime
   identity (cache key) computation, so the RAM estimator reads the published generation
   and a refreshed generation's pointer is the one keyed. `executor.execute_graph` calls it
   before its request planning; `haute.trace.execute_trace()` calls it before trace
   preparation; the lazy engine's later call finds the generation fresh and records `reused`.
2. For each snapshot-backed Data Input in order: validate the config, derive the identity
   and source signature, and read `SourceCacheStore.status(identity,
   source_signature=...)`. A `missing` source signature with a published generation reuses it with
   `warning_code="source_unavailable"` and never refreshes; without a generation preparation is
   refused as `build_failed`. Otherwise `ready`/`fresh` and `ready`/`unknown` reuse; `missing` builds;
   `ready`/`stale` refreshes; `corrupt` raises `SourceCacheCorruptError`. So a source with no
   signature (a database query, a Databricks table) is never re-read by preparation once
   published: the preview frame's Import (a forced `POST /api/input-cache/build`) is the one
   action that re-reads it.
3. `schema_only=True` records nothing and never builds; the node builder's
   `input_snapshot_missing` rejection remains the outcome for a missing generation,
   except at the inferred and declared schema tiers described under snapshot resolution
   below.
4. The cap gate: preparation runs only under an admitted `ExecutionContext` (its
   `admission` is present); without one it does nothing and resolution keeps its
   `input_snapshot_missing` rejection. With `current_native_memory_backend()` set the
   build runs in-process through `build_input_snapshot()` with the context's cancellation,
   deadline, and stages; otherwise the build is spawned through `run_isolated_worker` with
   `worker_config_for_memory_policy(memory_limit_bytes=budget.memory_limit_bytes, ...)`
   where `budget = isolated_execution_budget(execution_context)`, with
   `require_memory_limit=True` whatever `HAUTE_WORKER_MEMORY_ENFORCEMENT` says, and the
   child creates `create_isolated_execution_context(budget)` and runs the same
   `build_input_snapshot()`. A host that cannot install the native cap reuses a
   ready-but-stale generation with warning code `cap_unavailable_stale_reused`, and refuses
   with `cap_unavailable` before any provider access only when no generation exists.
   `allow_admitted_eager=True` is the hard-capped-worker-only opt-in for the admitted-eager build class — the explicit
   admitted-eager build sets it inside the child, and the in-thread explicit path keeps
   refusing that class outside `PREVIEW_EAGER`.
5. A per-process single-flight keyed by identity digest makes a concurrent execution wait
   for the in-flight build and re-read status instead of building again; a waiter inherits
   the owner's typed preparation failure instead of rebuilding. The wait is
   polled, not indefinite: each poll checkpoints the waiter's own execution context
   (`input_snapshot_preparation_wait`), so cancellation raises `cancelled`, and the build
   deadline bounds it as `timed_out`. The build deadline is the earlier of the build's own
   budget (the pipeline settings' pipeline time limit, read when the build starts) and the caller's `deadline`
   (`prepare_input_snapshots(..., deadline=)`, a job's), so preparing an input never outlasts
   the run it prepares for. The spawned build itself is cancellable — the worker
   config's `stop_reason` reports `cancelled` while the execution's cancellation token is
   cancelled, terminating the child.
6. The structured warning `input_snapshot_auto_build` is logged once a build actually
   starts — never before a `cap_unavailable` refusal or a stale reuse; the
   record is appended to the context (`ExecutionContext.record_input_preparation`) and
   surfaces as `metrics_payload()["input_preparation"]`.
7. A spawned build sets `defer_retirement=True`; its supervising parent calls
   `SourceCacheStore.retire_unleased(identity)` after success or reconciliation reporting
   `published`. Shared lease markers protect readers in either process. There is no
   retained-generation list on the worker request or build context.
8. Worker failure classification maps the child's own exception type name carried by
   `IsolatedWorkerRemoteError.remote_type` first: `NativeMemoryLimitUnsupportedError` /
   `NativeMemoryLimitCleanupError` are `cap_unavailable`, and `PathTooLongError` is
   `path_too_long`. It then uses
   `isolated_worker_failure_is_memory` and the worker's terminal reason to choose
   `memory_limited`, `cancelled`, `timed_out`, or `build_failed`. An in-process build's
   failure is `path_too_long` when `path_too_long_error` finds one behind it, after a
   cancellation or a `MemoryError`. Each reason code carries
   its own remediation text. After any spawned-build
   failure or abnormal termination the parent calls
   `SourceCacheStore.reconcile_unpublished(identity, generation_id, staging_token)`, which
   under the identity lock returns `published` when the current pointer already names that
   generation (the outcome is then recorded as `built`/`refreshed`, not a failure), removes
   an unreferenced `generations/<generation_id>` directory (never current, never leased)
   and the `.staging-<staging_token>` directory when present, and otherwise reports
   `absent`. After a reconciliation that did not report `published`, the parent re-reads
   `status(identity, source_signature=...)`: a `ready`/`fresh` (or `ready`/`unknown`)
   generation published meanwhile by another process is recorded as `reused` and the
   execution proceeds; only a still-missing or still-stale generation raises the
   classified `InputPreparationError`.
9. A structured (JSON, JSONL, NDJSON, XML) API Input in the lineage is prepared as a unit,
   one record per emitting table (provider `api_input`, one identity per table — see
   [JSON shredding](../json-shredding/low-level.md)). Every emitting table's status is read
   against the source signature. A `corrupt` table raises `SourceCacheCorruptError`. A
   missing source reuses every table when all are published (`source_unavailable`) and is
   refused as `build_failed` otherwise. When every table is `ready` and `fresh`/`unknown`
   they are all `reused`; otherwise one build shreds the source once and writes every
   missing or stale table (tables already fresh are recorded `reused`, the others `built`
   or `refreshed`). Single flight, the cap gate, the in-process/worker choice, the
   deadline, cancellation, cap-unavailable stale reuse, failure classification and the
   successor re-read follow steps 4–8, keyed by the node's group digest; a spawned build
   uses `run_supervised_api_input_build`, which chooses and reconciles every table's
   generation and staging and removes the build's scratch directory. Remediation text
   names the API Input panel.

### Snapshot publication

1. The per-identity process lock serialises same-process builders.
2. A non-refresh build returns the current validated generation when present.
3. The builder writes part files into `.staging-<nonce>/` (`.staging-<staging_token>`, and
   the parent-chosen `generation_id` as the published generation, when the build context
   carries the parent-chosen pair): a LazyFrame through `write_parts`, sliced a chunk at a
   time where the source can be sliced; Arrow iterables are checked at every batch and
   written against one schema into `part-00000.parquet`.
4. Publication computes artifact integrity evidence: a LazyFrame build uses the digests
   recorded while the parts were written (`describe_parts(directory, digests=)` hashes only
   a part with no recorded digest), while an Arrow-batch builder's parts are still hashed
   in a read pass. Publication reads every part's footer and schema but never re-reads a
   part in full to hash it; verification and the verified-generation memo check xxh64.
   It writes canonical `meta.json` and validates the staged generation.
5. The staging directory is atomically renamed and `current.json` is atomically replaced.
6. Old generations with live leases survive until the final lease release. Publication
   has no cache byte/count limit and never evicts another identity's current dataset.

Construction of a store handle inspects staging directories only. It recursively finds the
newest activity timestamp and removes a directory only when that timestamp predates
`HAUTE_INPUT_CACHE_STAGING_MAX_AGE_SECONDS`; stat failures and recent staging are preserved.
Non-current generations are never startup-swept. Retained staging bytes appear in the
inventory and consume physical disk space.

### Snapshot lease lifecycle

1. Snapshot resolution validates the Data Input, derives the redacted identity,
   and enters the store lease under that identity's coordination lock.
   Pointer, metadata, size/digest, footer, row count, and schema validation
   complete before the local `(identity, generation)` lease count is incremented
   and the generation is exposed.
2. The generation supplies a Parquet `LazyFrame`. Inside an
   `ExecutionContext`, one idempotent release callback is registered with
   execution cleanup, so every plan derived during that request remains
   protected through collection and teardown. Outside an execution context,
   an identity batch operation carrying a finalizer is embedded in the lazy
   plan; Polars-derived plans retain that operation and therefore the lease
   until the last reachable plan is collected.
3. Refresh publishes a new immutable generation and changes the current
   pointer. The old generation remains because its lease count is non-zero;
   new resolvers lease the new current generation. Clear removes the pointer
   first, so no new resolver can select either generation, but it likewise
   retains any locally leased generation.
4. The idempotent callback exits the lease once. The count is decremented under
   the identity lock, and when the final holder releases, every non-current
   unleased generation is retired (renamed out of selection under the lease lock,
   its files deleted after it). Thus refresh/clear affect future selection
   immediately without invalidating an already-derived scan. A pointer that names
   a generation whose directory is gone leases nothing: the lease raises
   `FileNotFoundError`, which resolution reports as `input_snapshot_missing`.

### Node-output snapshots

`src/haute/_node_snapshots.py` extends the store as `NodeSnapshotStore`, a subclass
that delegates every non-`node_output` identity to `SourceCacheStore` unchanged.
Input snapshots and explicit builds have no byte or count budget and no constructor
limit arguments; they remain until explicit clear or replacement. Automatic node-output
captures have one budget, applied after each automatic publication:

- `automatic_capture_budget(store)` reads the pipeline settings of the store's project
  (`store.root`): `cache_size_gb` as whole bytes when it is set (an invalid settings file
  raises `PipelineSettingsError`). When it is automatic the budget is
  `min(AUTOMATIC_CAPTURE_BUDGET_CEILING_BYTES, free // 10)`, where the ceiling is 20 GiB
  and `free` is the free disk under the inputs root at that moment. A settings error
  during the enforcement that follows a publication propagates like any other failure of
  that capture, since it is not a store fault.
- `_scan_usage` walks the store once without a lock. It returns the store's total
  bytes (every generation's part files plus in-flight staging) and the generations no
  pin protects: every `node_output` generation except the current generation of a slot
  whose index names a pinned identity, which is exactly what `_describe` reports as
  `pinned`. A generation it cannot classify counts toward the total and is never
  treated as automatic. That covers unreadable `meta.json`, metadata that does not
  reproduce its digest, and a corrupt pointer or slot index.
- `enforce_automatic_budget()` holds `.locks/automatic-budget.lock` from its scan to
  its last eviction, so one pass runs at a time across processes and a second pass
  scans after the first pass's evictions. The scan takes no other lock; the decision
  does. Under the lease lock, which every Clear, pin and pointer commit also takes,
  `_still_automatic_locked` drops the scanned captures that have since gone or become
  a pinned slot's current generation. The excess is computed from what remains, and
  the pass evicts in that same hold. It sorts the captures by last use (`meta.json`'s
  modification time, touched on lease) and evicts the oldest first until the rest
  fit, passing over any a process leases. A capture that left the total after the
  scan is therefore accounted for before anything is evicted. A capture published
  after the scan is not counted, so a race can only leave the captures over budget
  until the next pass. Retired directories are deleted after the lease lock is
  released. The budget lock is taken with no other store lock held. Evicting a current generation removes its pointer, then
  retires the generation. The identity leaves its slot index only once no generation
  remains, so a retirement an open Windows handle defers stays indexed and unpointed,
  where the next publish, Clear or budget check finds it.
- `publish_node_output` runs the enforcement for an automatic capture after the
  pointer commits, while the publisher's lease still protects what it published. An
  `OSError` or `SourceCacheError` from the enforcement is logged
  (`node_snapshot_automatic_budget_failed`) and the capture succeeds. A misconfigured
  budget raises and fails the capture after releasing its lease. An explicit
  publication runs no enforcement.

`usage()` reports the total, the automatic bytes and the budget for
[`GET /api/cache/usage`](../server-api/low-level.md#cache-usage), which the preview
status bar reads. The [cache inventory endpoint](../server-api/low-level.md#cache-usage)
lists the data for user-managed cleanup. Physical disk-headroom checks and
execution-memory limits still apply. Constructing the store can create the inputs root
and sweep retired directories.

`NodeSnapshotStore.inventory()` attributes every generation on disk to the owner its
metadata names, returning a `CacheInventory` of `CacheOwnerUsage` values. A generation's
`meta.json` records the whole identity payload, so a node output names its node and source
and an input snapshot names its provider and descriptor: no graph is needed to say whose
data this is, which is what lets a report name a node that no longer exists. An input
owner is labelled by its descriptor's `path` (or `table`, or `name`); an API-input table
(provider `api_input`) is labelled by its source file and table path together, since one
file holds several tables. A node output groups by node and source; an input
snapshot groups by **identity**, never by its descriptor's label, because the same file read
with different arguments is a different identity with the same path and merging them would
report one owner's bytes for both. Each owner carries the identity digests it covers, so a
caller holding one — a node's resolved input snapshot — can tell whether that owner is its
own. A generation whose `meta.json` is absent
or unreadable, and staging under an identity with no readable generation, are reported as
`unattributed_*` rather than dropped, so the owners' bytes plus the unattributed bytes
account for the stored dataset bytes, skipping symlinked files. A node-output owner is keyed by
pipeline, node and source, because several pipelines can share one project root and therefore
one store. `unmarked_identities` counts identities holding
generations whose provider marker does not classify. Metadata still attributes those
generations to their owners when readable. Inventory takes no lock and mutates nothing.

- **Identity.** `NodeSnapshotSlot(pipeline_source_file, node_id, source, semantics_class)`
  has a SHA-256 slot digest. `slot.identity(signature)` is a `SourceCacheIdentity` with
  provider `node_output` whose descriptor adds the signature under `data_fingerprint`
  (identity redaction treats a key named `signature` as credential material).
  `node_snapshot_signature(graph, node_id, source, semantics_class, enforce_contracts)`
  builds the checked `node_snapshot_signature` consumer from the graph fingerprint of the
  node's source lineage (the node and its upstream subgraph after live-switch pruning for
  the source; see [caching](../caching/low-level.md#source-snapshots)), the runtime-input
  fingerprint of that lineage targeted at the node, and the execution fields; `pipeline_source_file_key(graph)` resolves the slot's
  pipeline file.
- **Class mappings.** `snapshot_write_class(profile, preview_admitted=...)` returns
  `bounded` for every bounded profile (`TRAINING_PREP`, `OPTIMISER_SETUP`,
  `OPTIMISER_SOLVE`, `EXPLORE_ANALYSIS`, `LAZY_SINK`, `NODE_SNAPSHOT`) and
  `None` for deploy profiles; `snapshot_read_classes(profile)` returns `{bounded}` for the
  same profiles. `PREVIEW_EAGER` reads `bounded`, and writes it when `preview_admitted`,
  because `PREVIEW_SHARES_BOUNDED_SEMANTICS` is true; the execution-profile semantics proof
  (`tests/test_execution_profile_semantics.py`) is what that was decided against. Every bounded
  profile materialises the same node identically — schema, values and row order — over a
  direct Parquet input, an input snapshot, an `apiInput` port served from its table cache,
  a declared-dtype CSV, a transform, a join, an aggregation and a Model Score node, under a
  full and a projected column demand, and refuses the sources it cannot read with the same
  error under each. A projected read is the full read restricted to those columns, dtypes
  included.

  Row order, however, belongs to the *query*, not to the profile or the seam. A query that
  defines its order keeps it through every seam: on Polars 2 a join states its order
  (`maintainOrder` is required on the Join step and the Edge Join) and a Sort rows step is
  stable, and a collect, `bounded_sink`, `bounded_collect_batches` and `write_parts`' chunked
  join (whose parts read back as the native join row for row) all return such a query's rows
  in its order. A query that leaves its order undefined (a join stated `none`, an unpivot, a
  join in user code without `maintain_order`) has none: Polars 2's streaming engine returns
  its rows in an order that can change from run to run, as Polars 1 already did for
  unordered joins (a 20,000-row `1:1` join read through `bounded_collect_batches` began at
  `q1820` on one run and `q0` on the next).

  The policy over that is that row order is not part of the snapshot contract: rows carry
  the meaning here and their order does not, so a preview's captures are admitted to the
  `bounded` class and a run may seed from them, with nothing gated on which execution wrote
  a generation.

  A capture must still be *sunk* rather than published from collected batches, whatever
  profile performed it, so that a generation's contents are the ones its writer computed.
  Planned lazy executions are the automatic-capture client of the publication rule below:
  the [execution engine](../execution-engine/low-level.md) sinks each capture into
  `stage_node_output` under its plan's staging token and publishes it with
  `explicit=False`, continuing from the returned artifact whenever it is not published.
- **Layout.** Node-output identities live beside input identities under
  `.haute_cache/inputs/<identity digest>/` (`provider`, `current.json`,
  `generations/<id>/`, `.staging-<token>/`, `.retired-<hex>/`). A `provider`
  file directly in the identity directory holds the provider name as one line,
  written atomically as part of creating that directory before anything is
  staged into it, by both paths that create one (a node-output staging allocation
  and an input-snapshot build). Missing or unreadable provider markers are reported
  by the inventory; their dataset bytes are not silently skipped. Each
  generation's `meta.json` carries a `node_output` block (metadata version 2:
  slot, slot digest, signature, `column_set` of `"all"` or a sorted list, and
  `dependencies` as identity digest → generation id). The per-slot index is
  `.haute_cache/inputs/.node-slots/<slot digest>.json` (identities and the pinned
  identity), rewritten atomically under the lease lock. Locks live in
  `.haute_cache/inputs/.locks/` (`publication-<identity digest>.lock`,
  `leases.lock`, each a `_file_lock.FileLock`) and process tokens in
  `.haute_cache/inputs/.processes/<token>.lock`.
  Lease markers are `.lease-<12-hex token>` files directly in the generation
  directory, and last use is the `meta.json` modification time; both keep the
  deepest path inside the traditional Windows limit beneath long temporary roots.
- **Status.** `latest_generation(identity)` validates the pointer's generation through the
  store's metadata/digest/verified-memo path and reports its columns, dependencies,
  freshness, retention, and last use; a pointer naming a generation whose directory is gone
  has no latest generation (`None`, as a retired layout has), so the node's data is missing
  and the next capture publishes over it. `slot_status(slot, signature)` is `current` or
  `stale` from that generation, `stale` when another indexed identity has a current
  generation, `missing` otherwise, and `corrupt` when validation fails. Because a slot holds
  one signature, the "another indexed identity" case is now the window in which a reader
  still holds the signature a publication superseded, not an indefinitely retained
  alternative version.
- **Leases.** `lease(identity)` reads the pointer, then under the lease lock confirms the
  directory and the pointer, increments the in-process count, and on 0 → 1 creates this
  process's marker; a pointer that moved (or a generation retired meanwhile) re-selects.
  Only then, with the generation protected, is it validated, and a validation failure
  releases the lease before raising. `lease_generation(identity, generation_id)` does the
  same without the pointer check, rejects a malformed generation id, and raises
  `SourceCacheGenerationMissingError` for an unknown or retired generation. Release
  decrements under the lease lock; at zero it removes the marker and
  retires the generation when it is no longer current and no live marker remains. A marker
  is live when it names this process with a non-zero in-process count, or when its token
  file's lock cannot be acquired; a dead marker (and token file) is removed.
- **Publication.** `stage_node_output(identity)` allocates a request-owned
  `NodeSnapshotArtifact` staging directory. `publish_node_output(identity, artifact,
  columns, dependencies, explicit, profile, refresh)` uses the digests recorded while the
  parts were written (`NodeSnapshotArtifact.record_digests`; `describe_parts(directory,
  digests=)` hashes only a part with no recorded digest), reads every part's footer and schema
  but never re-reads a part in full to hash it, validates the artifact (xxh64 `digest`,
  footer, schema; an explicit column set must name existing columns; verification and the
  verified-generation memo check xxh64), and writes `meta.json` outside the locks, then under
  the identity's publication lock applies the publication rule. `explicit` marks an explicit
  cache build: only it may `refresh`, pin the slot, or replace a corrupt latest generation;
  an automatic capture raises the corruption. A superseded outcome returns the artifact.
  Otherwise, under the lease lock, it records the candidate identity and
  retention in the slot index, renames staging to the generation, seeds the verified memo,
  creates the publisher's marker and in-process count, then commits the pointer. Indexing
  before exposure keeps interrupted candidates discoverable; an existing slot pin protects
  the previous current data until commit. It retires the superseded generation when no live marker holds
  it. **One dataset per slot.** It then retires every *other* signature the slot index
  lists, by `clear_slot`'s mechanics — drop the pointer so nothing selects that signature
  again, then retire its generations — and drops each from the index. A node therefore holds
  one dataset, and a re-cache after an edit replaces what was there rather than adding a
  second full copy of that node's output beside it. The consequence is deliberate: an edit
  and a revert recompute, because the earlier signature's data is gone. A generation another
  reader still holds is left alone and retires when that reader releases it, since
  `_release_node_lease` retires a generation whose pointer has gone — a scan in flight never
  has its files deleted underneath it, though a scan that outlives its lease, always a
  contract violation, loses its files at the next re-cache or clear.
  Such a signature stays **indexed and unpointed**: dropping it from the index would strand
  its generation where `clear_slot`, which walks the index, could never see it if the holder
  died without releasing, leaving a node showing two datasets and a Clear that cannot remove
  one. Indexed, the next publish and any `clear_slot` retry it, and `_release_node_lease`
  prunes it when it retires the last generation of an unpointed identity. Any failure after the publisher's lease exists
  releases that lease before raising.
  The returned `NodeSnapshotPublication` holds the lease (or owns the artifact) until closed.
- **Retention.** Neither explicit nor automatic datasets are removed to admit another
  dataset. Pins and last-used metadata remain available, but do not control storage limits.
- **Clear and pin.** `clear_slot(slot)` removes every indexed identity's pointer, retires
  each unheld generation, and deletes the index; `clear(identity)` does the same for one
  identity. `pin(identity)` pins a slot to an identity that has a current generation.
- **Retirement.** Every retirement in the store, an input snapshot's included
  (`SourceCacheStore._retire_generation_locked`), renames the generation directory under
  the lease lock to `.retired-<hex>` beside `generations/`; the files are deleted after the
  lock is released (`_delete_retired`, logging `source_cache_retired_delete_failed` for one
  that survives), and the first store a process opens on a root removes any leftover
  retired directory. A rename refused by an open Windows handle is logged
  (`source_cache_retirement_deferred`) and the generation is left whole, unpointed and
  selectable by id; the next retirement of its identity (a release, refresh, clear or
  publication) renames it.

### Staging reclamation and publication

Store construction examines only real, non-symlink `.staging-*` directories.
For each it walks the tree without following symlinks and finds the newest
activity time across the directory and its contents. A tree is removed only
when that proof is readable and older than the configured threshold; a recent,
racing, or unreadable tree remains. No generation directory is part of startup
cleanup.

Both publication paths atomically write a `provider` marker before staging data.
The marker assists inventory diagnostics; it does not select a quota or eviction policy.
There is no byte/count admission step: no publication is refused for size. Input
snapshots for other identities remain untouched; automatic node captures are subject
only to the automatic-capture budget above, and users clear anything through the
inventory.

Snapshot-mode execution contacts the configured provider only through automatic
preparation, which is the explicit build path scheduled before planning under a hard cap.
Resolution itself never builds: if no current snapshot exists when a node is resolved (a
schema-only execution, or a caller outside an admitted execution context), it raises
`InputSnapshotMissingError` (a `PolarsIoConfigError`) with the stable
`input_snapshot_missing:` prefix and an instruction to build the snapshot or run the
pipeline under an admitted execution, which prepares it.

**Schema tier recorder.** `recording_schema_tiers()` is a context manager yielding a
`RecordedSchemaTiers` it fills: `inferred`, a `{node_id: InferredInputSchema}` mapping,
and `declared`, a `{(node_id, table_label): DeclaredTableSchema}` mapping. Resolvers
reach it only through `schema_tier_recorder(schema_only)`, which returns the active
recorder for a schema-only resolution and `None` otherwise, so the weaker tiers are open
only to a schema-only caller that reports them. A resolver states a schema-only read by
naming the node it records under, `schema_tier_node=<node id>` (`None`, the default, is a
full read), so a schema-only read without a node to record under cannot be expressed.

**Inferred schema tier.** `resolve_data_input(..., schema_tier_node=<node id>)` called
while `recording_schema_tiers()` is active does not raise for a missing generation
when the input can be scanned. Eligibility is `inputType == "file"`, a format with a
scanner, and an empty `scanner_rejected_arguments(fmt, config)`: the registry's one check
of which configured arguments the scanner does not accept by name
(`allowed_arguments` of the scanner) or by value (`_SCANNER_VALUE_DOMAINS`, for example a
CSV `encoding` other than `utf8`/`utf8-lossy`), whatever the configured mode;
`snapshot_input_plan` uses the same check to prefer the scanner for a configured eager
read. An eligible input's file must exist; it is then opened by
`scan_polars_input_for_schema(config)`, which invokes the scanner with the validated
configured arguments. A scanner that infers types (`infer_schema_length` in its argument
surface: CSV, NDJSON) and has no declared `schema` or `infer_schema: false` receives
`infer_schema_length` equal to the configured value capped at `INFERRED_SCHEMA_ROWS`
(10,000), or the cap when the configuration sets none or `None`; that value is the
recorded `inference_rows`. A declared schema is a non-null `schema` mapping, which Polars
uses as the whole schema: `schema: null` declares none, and `schema_overrides` still leaves
the other columns to inference, so both are bounded. Parquet and IPC read file
metadata and a declared schema needs no inference, so they record `inference_rows=None`.
The same non-null test decides whether a direct bounded read has the declared CSV schema
it requires and whether a snapshot build must infer from the whole file. The scan is returned uncollected, and
the source cache is not written. Anything ineligible raises `InputSnapshotMissingError`
naming the reason (`a <provider> input has no local file to scan`, `format '<name>' reads
only eagerly`, or the reader-only argument names) and the remedy "Preview this input
first, which builds its snapshot." Without an active collector, or without a
`schema_tier_node`, a missing generation keeps the plain rejection.

**Declared schema tier.** A structured API Input table read from the store
(`load_v2_api_source(..., read_snapshots=True, schema_tier_node=<node id>)`, see
[JSON shredding](../json-shredding/low-level.md)) whose generation is missing while the
recorder is active resolves as an empty `LazyFrame` under the table's declared frame
schema (`_declared_frame_schema` of its demanded columns), recorded as
`declared[(node_id, label)] = DeclaredTableSchema(column_count=...)`, where
`column_count` is the table's declared selected columns, whatever the demand. The
contract has already passed `validate_v2_schema`, so every selected column declares one
of `int|float|str|bool|date`; an untyped column is refused there with
`ApiInputSchemaError` naming the table and column, the same refusal a preview meets. The
request file is not opened (it need not exist), nothing is collected, and no generation
is written. A table whose generation exists is leased as usual, so a node can mix both.

Direct mode is not a general compatibility path. It is valid only for a file-backed
Parquet scan, which already has the lazy, schema-bearing execution shape that a snapshot
would duplicate.

Flat-file source projection treats an explicit empty `columns` iterable as a
row-cardinality-only request. `_select_columns()` retains the source's first
schema-ordered column as a physical carrier for that empty projection; selecting no
columns would make Polars report zero rows. A non-empty request must name columns the
source schema actually has, and `columns=None` remains full-width. The requested columns
are planner demand alone: a node's `selected_columns` is never passed here and never
checked against the source schema.

### SQLite builder

1. `resolve_connection_uri()` resolves either an environment reference or raw safe URI.
2. `resolve_sqlite_path()` validates scheme/authority and returns the existing path.
3. `DatabaseSnapshotBuilder` rejects `:memory:` and missing/non-file paths before iteration.
4. `build()` checkpoints before connection, opens SQLite with `mode=ro`, enables query-only
   behaviour, and begins a transaction.
5. After obtaining output names, `_sqlite_result_schema()` runs one aggregate wrapper query
   that collects the distinct SQLite storage classes for all columns at once. Runtime
   evidence wins; table declarations supply an empty-result hint.
6. The data cursor emits every `fetchmany()` batch against that schema. Arrow conversion and
   SQLite driver failures are translated to path-free `DatabaseConfigError`.
7. A zero-row query emits one empty schema-bearing batch when its types are provable.

### Data Output

`executor.resolve_data_output_path()` adds `outputs/` to bare filenames and the
registry's default extension when absent. File-provider paths are anchored to the
project root before containment, even for a nested pipeline; explicit relative paths
are also project-relative. The returned display path and filesystem target describe
the same destination. Destination preview and explicit writes share this resolver.

`validate_data_output_config()` and registry mode resolution select the Polars sink.
`write_polars_output()` validates arguments/engines, applies the output target, and uses the
bounded sink discipline. Sidecar loading and parent-directory preparation are owned by the
generated pipeline/runtime seam.

A Parquet sink is written a slice at a time rather than through one native sink whose peak
grows with the input. The seam is inside `write_polars_output()` rather than at its caller,
because that function validates the user's own `arguments` against `sink_parquet`'s signature
and forwards them, while the chunked writer is pyarrow's and takes different names and
different values for the same ideas. Rather than translate them and risk writing a different
file than the user asked for, the bounded path is taken only when there are no arguments to
translate; anything else keeps the native sink and records
`output_arguments_not_translatable:<names>`, naming the arguments that blocked it, as a
non-Parquet destination records `output_format_not_sliceable`. Which arguments are worth
translating is a question about which ones people actually set, and the names are recorded so
that question has something to count.

`write_file()` (`_chunked_writes.py`) is the single-file counterpart of `write_parts()`: it
slices the frame directly where Polars can slice it, slices a write recipe's input where the
node carries one, and otherwise sinks natively and records why. It owns its own atomicity for
every strategy — pyarrow's writer closes its footer on the way out of an exception, so a write
that died half way would otherwise leave a shorter file that reads back perfectly — except
where a caller passes `atomic=False` because it is already writing to a staging path it will
rename or discard itself, as the Data Output's executor does. That flag reaches every
strategy, the native sink included: a temporary beside the caller's staging file is a hidden
sibling in a directory the caller governs and never sweeps.

The frame a Data Output writes is computed under a seed plan (the execution-engine
specification owns it): the node's producer is read from a shared snapshot when a fresh one
covers the output's columns, and otherwise it — and every join, fan-out, and materialisation
above it — is captured, so a second write, a training run, or an optimiser setup on the same
batch upstream starts there. The write-output route's parent prepares inputs before the sink
worker starts, so an automatic snapshot build happens in the parent, within the sink timeout.

### Streaming collection

`execution_collect()` is the one context-aware collection seam. Its engine, `streaming` or
`in-memory`, is a required argument: Polars' `auto` engine resolves to streaming for a lazy
query on 2.0 and follows `POLARS_ENGINE_AFFINITY`, so a collect that left it to Polars
would change engine with the version or the host. Every collect in `src/haute/`, direct or
through the seam, names its engine (`streaming` for a bounded or run-sized
materialisation, which is every site today), held by `tests/test_collect_engines.py`. With
no active context it performs one native collect on that engine. With an
active context it starts one native background query, polls `InProcessQuery.fetch()` at a
validated positive interval, and checkpoints between polls. A checkpoint failure cancels
the native query before propagating; query `fetch()` failures propagate unchanged, and a
best-effort cancellation failure cannot mask the checkpoint failure. `streaming_collect()`
and `cancellable_streaming_collect()` both select the streaming engine through that seam;
the former discovers the current context while the latter requires one explicitly. There
is no eager or engine-fallback retry.

`bounded_sink()` constructs Polars' native sink as a lazy streaming query and materialises
that query through the same context-aware seam. Long Parquet/CSV checkpoint and cache
writes can therefore observe cancellation and RSS limits while native execution is in
flight. The sink still writes to an atomic temporary path; cancellation or any native
failure removes the temporary artifact and never publishes a partial destination. There
is no eager dataframe or collect-then-write fallback.

## Edge cases and invariants

- Raw database URI query keys matching common credential names (`token`, `password`,
  `secret`, `credential`, access/API keys, authentication/password abbreviations, and
  signature/SAS variants) are rejected case-insensitively; query values are never logged.
- Cache identity dictionaries have string keys and canonical-JSON-compatible values.
- Databricks query/table spelling affects identity; fetch `batch_size` does not.
- Generation identifiers are canonical UUID strings and cannot traverse paths.
- Empty snapshots retain schema when declarations prove it. Integer/real runtime mixtures
  widen to float; incompatible text/blob/numeric mixtures fail before the data cursor emits.
- Missing SQLite paths fail without creating a file.
- Clear removes the pointer first and retires only generations without live leases.
- Startup never removes generations; it removes only staging older than the configured
  activity threshold, and inventory includes retained staging bytes.
- Full artifact SHA-256 is verified once per stable generation stat gate per process rather
  than once per lease.
- Partitioned Parquet sinks validate the final layout and preserve the previous published
  target if publication fails.
- Direct bounded reads require declared CSV dtypes and refuse eager-only formats and eager
  `read` mode where a scanner exists. Snapshot builds instead inspect a CSV completely
  (`infer_schema_length=None`, a literal keyword held by
  `tests/test_polars_io_interface_contracts.py`'s `_LITERAL_KEYWORDS`), prefer the scanner
  over a configured eager read whenever every configured argument is scanner-accepted
  (recording `eager_read_mode_scanned`), and run a genuinely reader-only format inside the
  hard-capped worker.
- Automatic preparation is single-flight per identity within a process; across processes
  concurrent builds of one identity both publish valid generations and the last pointer
  replacement wins.
- A spawned build has three kill windows: before the rename only staging exists; between
  the rename and the pointer replacement an unreferenced generation exists; after the
  pointer replacement the build has succeeded. `reconcile_unpublished` distinguishes them
  by the parent-chosen generation id and staging token and never touches any other
  generation or staging. A successor published by another process between the kill and
  the reconciliation is detected by the status re-read and reused, so a successful
  preparation is never reported as a failure.
- An argument is config-expressible only when both the committed interface schema and the
  installed Polars declare it. `haute._polars_io_schema.supported_argument_names()` is that
  intersection and `haute._polars_io_registry.allowed_arguments()` subtracts the excluded
  classes from it. The committed schema records one Polars version while the `polars`
  specifier admits a range, so the two sides disagree in both directions: an argument only
  the installed Polars has would otherwise become expressible without ever being classified
  against the remote-IO, object-valued, and execution-owned exclusions, and an argument only
  the committed schema has would otherwise stay on offer while resting on whatever
  deprecation shim still accepts it. Rejection names the version boundary when
  `haute._polars_io_schema.retired_argument_names()` explains the miss.
- The registry supplies each node's source/target fields positionally — one leading argument
  for a path or inline source and for every output target, two (query, uri) for a database
  input — so a Polars release inside the specifier may append or rename later parameters but
  must not move those leading positions or remove a callable the registry dispatches to.
- `haute._polars_io_registry.registry_capabilities()` publishes only the modes whose Polars
  callable the installed Polars still provides introspectably, tested by
  `haute._polars_io_schema.installed_provides_callable()`. A Polars that has dropped one
  callable therefore costs the editor that one mode rather than the whole format catalogue;
  configuring or executing such a format raises `PolarsIoConfigError` from
  `haute._polars_io_registry.allowed_arguments()` rather than a bare `AttributeError`, and
  the interface contract test fails on the same condition in CI. A format's `unstable`
  flag, which the editor shows as "(unstable)", says Polars marks every callable of the
  format unstable (its `@unstable()` wrapper), held to the installed Polars by a test:
  Avro (Polars 2), Text lines and Iceberg.
- The intersection governs node-config arguments only. Keyword names haute itself writes as
  literals — `haute.executor._output_row_count_scan_kwargs()` for exact artifact row counts,
  `haute._io.read_source()` for declared-dtype CSV scans, and the compression argument in
  `src/haute/_polars_utils.py` — carry no such protection and are held instead by
  `tests/test_polars_io_interface_contracts.py`'s `_LITERAL_KEYWORDS` inventory.
- Header-less inputs and CSV schemas (`_polars_io_registry`). `validate_arguments`
  refuses an input callable's arguments that set `has_header: false` without naming the
  columns (`new_columns`, or a non-null `schema` for a CSV), and a `new_columns` that is
  not a non-empty list of distinct non-empty names. Excel and ODS take `new_columns` as a
  Haute-owned argument (`HAUTE_READ_ARGUMENTS`), published with the format's arguments:
  `_invoke_polars_input` removes it before calling Polars and renames the sheet's columns
  by position, refusing a list whose length is not the sheet's width. For a CSV,
  `_invoke_polars_input` reads the file's first line with the configuration's dialect
  arguments (`_CSV_DIALECT_ARGUMENTS`, `infer_schema=False`): a header-less file whose
  `new_columns` length differs from that line's field count, and a headed file whose
  `schema` names are not exactly its header's, are refused with `PolarsIoConfigError`
  before Polars resolves the scan. The scanner decodes only UTF-8, and the eager reader
  decodes another `encoding` in Python before it parses, so for such a file
  `_csv_first_line_names` decodes the whole file the same way (a `-lossy` codec replacing
  what it cannot decode), a megabyte at a time into one UTF-8 buffer, and has Polars read
  the header from it as the scanner reads a UTF-8 file's, parsing no data row, so the
  read's row options (`truncate_ragged_lines`, `skip_rows_after_header`) never apply to the
  check. No prefix proves where a header ends, which depends on every quote before it; the
  buffer is the text the read decodes anyway and holds beside the file's bytes and frame.
- Argument *defaults* are Polars'. haute forwards what a node config sets and takes the
  installed Polars' behaviour for everything else, so a default that moves inside the
  specifier moves haute's results with it. That is reported by
  `scripts/extract_polars_io.py --diff` rather than gated: pinning every default haute does
  not set would be a promise of result-stability across the specifier that haute has never
  made.

### Polars 2 dtypes

- Polars 2 names a categorical's category pool in its dtype: `Categorical("lexical")`,
  1.x's ordering spelling, makes a pool called `lexical`, and a pooled categorical is not
  equal to `pl.Categorical`. The codec writes the default pool as
  `{"type": "Categorical"}` and any other as
  `{"type": "Categorical", "categories": {"name": ..., "namespace": ..., "physical": ...}}`;
  `parse_rendered_dtype` reads the `Categorical(Categories(...))` rendering; and every
  check for a categorical tests the base type, so a pooled categorical is text wherever a
  categorical is.
- A `Map` or `Extension` column has no spec, and `dtype_to_spec` refuses one: Haute reads
  a map as a list of key-value structs and an unknown extension as its storage type before
  a pipeline sees it.
- `Int128` and `UInt128`, which Polars 2 produces from `Int64` or `Int32` combined with
  `UInt64`, have no model, MLflow or Arrow form, alone or nested in a list, array, struct or
  map's keys or values: `to_numpy` panics on one (a
  `BaseException` that escapes `except Exception`), and pyarrow cannot open a Parquet file
  that holds one. The consumers that convert one refuse it by name with
  `wide_integer_message`, which gives the cast that fixes it (`Int64`, or `Float64` if the
  values can exceed it): the feature contract (`contract_dtype_name` refuses one rather
  than recording it as `Int64`), the training job and the train route before they write or
  convert anything, the predict frame and a deploy's MLflow signature. pyarrow cannot even
  read the footer of a Parquet file holding one (`pq.read_metadata` fails), so it names no
  column, and Haute refuses one first wherever it can name it (`refuse_wide_integers`):
  - at the node whose output holds it, wherever the walk resolves that output's schema: a
    preview, which reports every node's schema, and a node with an output contract, whose
    columns are checked; the refusal names the node, the column and the cast;
  - at every Parquet write another reader opens: Haute's part writer (`write_parts`: every
    snapshot, source cache and chunked write), a streaming Parquet sink (`_polars_utils`),
    a batched Model Score's scored parts (and its Parquet input, before pyarrow opens it),
    and a Data Output writing Parquet, Delta or Iceberg, the formats written through Arrow,
    so Haute never writes a file pandas, Spark or Haute's own metadata read cannot open. A
    staging copy only Polars reads back keeps the column: `bounded_collect_batches` and a
    chunked join's staged input pass `internal=True` to `write_parts`, which hands it on to
    the hashed part sink;
  - at `read_parquet_metadata`, which reads the file's schema through Polars before
    pyarrow reads its footer, for a file another program wrote; a file Polars cannot read
    as Parquet is left to pyarrow, which reports it as before (a `ValueError`, which the
    RAM estimate's metadata reader treats as an unknown source).

  A CSV, JSON or IPC write keeps such a column where no node check met it first (a run
  through nodes without output contracts), since Polars reads it back.
- Under Polars 2, `round`, `floor`, `ceil` and `sign` widen a `Decimal`'s precision by
  one, so the recorded dtype of such a column differs from the one Polars 1 recorded for
  the same pipeline.

### Map columns

- Polars 2 reads an Arrow or Parquet map as its `Map` dtype, and its Parquet reader keeps
  only the last value of a key a map repeats, even when a scan asks for a list of
  key-value structs, and when a `schema` argument names the map one. Haute's dtype
  vocabularies have no `Map`, and Polars 1 read every map as a list of `{key, value}`
  structs holding every entry, so a pipeline receives that shape on every path a source
  takes. A Parquet source's map columns are found in its files' own schema
  (`parquet_source_map_columns`), read from a footer with only the arguments that choose
  the files (`glob`, `hidden_file_prefix`, `storage_options`, `credential_provider`), so
  a configured `schema` cannot hide one:
  - A provider's Arrow batches (a Databricks fetch, a database query) are cast with
    pyarrow before the snapshot part is written. The cast shares the map's buffers and
    keeps the schema's and every field's metadata.
  - A Parquet file input with a map column, found from its footer, is copied into its
    snapshot through pyarrow, one row group at a time, so Polars' Parquet reader never
    reads it. That read applies `columns` (names or positions, in the order given) and
    `n_rows` itself, accepts the arguments that only change how fast a read runs, and
    refuses any other argument that is set, by name. A file that yields no rows still
    writes its schema. A source naming several files (a folder or a glob) whose files hold
    a map is refused by name: pyarrow keeps every entry one file at a time, without the
    file expansion and hive partitioning Polars applies to such a source.
  - A frame Polars read from any other source is cast in Polars before its parts are
    written. An Arrow IPC file keeps every entry this way; a lakehouse table, which
    Polars' Parquet reader reads, keeps the last value of a repeated key, and lakehouse
    writers keep a map's keys unique.
  - A direct Parquet scan whose files hold a map is refused before any row is read,
    whatever `schema` it declares, naming the map columns and the snapshot route (`mode`
    `"read"`). So is a Parquet file `_io.read_source` reads, the flat-file reader behind a
    flat Quote Input, a training file, the deploy input schema and the file routes
    (`SchemaMismatchError`).
  - Schema-only resolution of an input with no snapshot reports the shape its snapshot
    will hold.
- An Arrow extension column Polars does not know loads as its storage type on every path,
  through the Polars settings every process Haute starts; a provider's batches keep the
  extension's field metadata, so the snapshot part loads the same way.

## Error handling

`PolarsIoConfigError`, `DatabaseConfigError`, and `UnsupportedSourceFormatError` identify
invalid input before side effects. `BoundedMemoryUnsupportedError` is raised by the shared
execution discipline for operations that cannot meet the selected profile.

`SourceCacheBuildError` covers cancellation, deadline, unsupported build class, and invalid
builder output. `SourceCacheCorruptError` is reserved for proven structural/integrity
failure. `OSError` subclasses from transient filesystem access are not relabelled corrupt.

`InputPreparationError` (`error_code="input_preparation_failed"`) wraps automatic
preparation outcomes with a stable `reason_code` (`cap_unavailable`, `build_failed`,
`memory_limited`, `cancelled`, `timed_out`, `path_too_long`), the node id, identity
digest, build class, and a remediation; it never carries a locator or a secret. It is a
member of `PUBLIC_CONTRACT_ERROR_TYPES` in `haute.routes._contract_errors`: synchronous
routes answer HTTP 422 with its payload, and background jobs record the `memory_limited`
terminal state when `reason_code == "memory_limited"` and the contract-error fields
otherwise.

Provider route handlers log exception type plus a credential-scrubbed message, while public
job responses use stable non-sensitive messages and error codes. Unexpected sidecar
overwrite is an HTTP 409; registered data sinks keep their documented overwrite semantics.

### Boundary failure ordering

1. Data Input/Output shape, active-branch fields, arguments, mode, engine
   availability, and raw-URI credential safety are validated before identity
   construction or provider access. Identity canonicalisation then rejects
   secret-bearing keys/values before hashing or metadata publication.
2. A snapshot build rejects unsupported/mismatched build class before creating
   staging. Once staged, cancellation/deadline checkpoints bracket provider
   read, artifact write, integrity/footer/schema measurement,
   generation rename, and pointer publication. Metadata is written and the
   staged artifact is self-validated before the pointer changes. Any failure
   removes staging and any unpublished generation; the prior current pointer
   remains authoritative.
3. Automatic preparation checks status before the cap gate, the cap gate before any
   provider access, and single-flight before spawning; a spawned build's failure is
   classified from the worker outcome, the parent reconciles its generation id
   (published, unreferenced generation, or staging), and the previous pointer remains
   authoritative unless the build had already published.
4. Opening/lease acquisition reads and validates the pointer first, then the
   generation id/metadata identity and counts, byte size/digest, Parquet footer,
   Arrow names, and Polars schema. Proven structural/integrity failures become
   `SourceCacheCorruptError`; a missing pointer remains “not built,” and
   transient `OSError` is propagated rather than mislabeled corruption. No
   provider fallback is attempted for a requested snapshot; the only direct
   execution path is canonical file-backed Parquet.
5. SQLite validates safe locator, existing regular file, and read-only query
   before connection. It begins the read transaction and completes one
   all-column storage-class query before the data cursor emits any batch, so an
   incompatible column mixture cannot leave a partial artifact.
6. Data Output validates the destination/config and, when overwrite is false,
   refuses an existing target before sink work. Bounded/partitioned publication
   stages and validates its result before replacement, preserving the previous
   target on compute, validation, or publish failure.

### Depth-review questions

The operational review checks that this specification answers: Which exact
source semantics enter snapshot identity and where are credentials rejected?
What owns a lease from acquisition through derived plans and final release, and
what do refresh and clear do meanwhile? Which staging trees may startup reclaim,
and which datasets does explicit clear remove? At which boundary does each config, provider,
cancellation, schema, integrity, pointer, SQLite, and output failure
surface? The identity, lease, reclamation/publication, SQLite, output, and ordered
failure sections above are the maintained answers.

## Testing

- `tests/test_apiinput_flat_nested_relative_path.py` verifies API-input execution resolves pipeline-relative files from project root and rejects out-of-root absolute paths.
- `tests/test_data_input_nested_relative_path.py` verifies data-input project-root resolution, escape/symlink rejection, selected external pipeline roots, and HTTP external-root denial.
- `tests/test_data_io_config_contract.py` exercises valid/invalid data-input/output discriminated configurations and loud rejection of unknown branches.
- `tests/test_external_file_nested_relative_path.py` verifies project-root relative external-file loading, absolute passthrough, and project-escape rejection.
- `tests/test_pipeline_runtime_path_validation.py` verifies runtime path/graph validation, HTTP status mapping, sidecar/codegen case-collision and reserved-name protections, and safe rename/delete semantics.
- `tests/test_read_user_text.py` verifies robust text/config/pipeline decoding across encodings and malformed inputs.
- `tests/test_map_columns.py` pins the map policy: Polars' Parquet reader drops a repeated key (the reason for it); the Arrow cast keeps every entry, shares the map's buffers, reaches maps nested in structs, lists and maps, and keeps metadata; the Parquet read holds one row group at a time (counted reads at each yield), applies `columns` and `n_rows`, and yields an empty file's schema; a provider's batches, a Polars frame and a Parquet snapshot keep every entry, a Parquet snapshot refuses arguments its read cannot apply, a direct scan of a map-bearing file is refused (also when its configured schema names the map a list of key-value structs, which a test shows reads it the same lossy way), so is a flat Quote Input's map-bearing file and a snapshot of a folder or glob of them (a folder without a map is unaffected), schema-only resolution reports the snapshot's shape, and an unknown extension column loads as its storage type on the direct, frame and Arrow paths in a process started with Haute's Polars settings.
- `tests/test_polars_dtypes.py` pins the one dtype vocabulary: a Polars attribute that is not a dtype is rejected with the column and name, a rating descriptor is the shared codec's spec under rating's key names (and rejects dtypes the codec knows but rating does not support), and the feature-contract view, its MLflow projection and the rendered-dtype MLflow view map representative dtypes exactly, `parse_rendered_dtype` rebuilds scalar, parametric and nested dtypes from their rendering and rejects a name that is not a dtype or an expression that is not a literal; every dtype class Polars exports is covered by one sample at least, each of which round-trips exactly through the spec codec and its rendering, or (`Map`, `Extension`) is refused by `dtype_to_spec`, so a dtype a later Polars adds fails the test until the codec handles it; a categorical in a named pool round-trips with its pool, trains as categorical and is the rating descriptor `{"kind": "Categorical"}`; `tests/test_io.py` pins that declared API-input source dtypes accept the same vocabulary and reject anything else.
- `tests/test_sink.py` verifies sink execution errors, parquet/CSV output, directory creation, scenario handling, compute failures, and response metadata.
- `tests/test_data_output_seeding.py` drives writes through the write-output route with the sink worker in process: a second write seeds the producer the first captured and builds nothing; a write after a batch training run builds its producer, because a training capture holds only the columns training reads and a write reads them all, and captures it in full, which the next training run seeds; an in-process `write_data_output` seeds and captures too; no `haute_sink_` directory is written; a worker stopped, timed out, or killed at its cap leaves no capture staging and publishes nothing; the worker stages under the parent's token; the response metrics keep the parent's input preparation; a superseded capture is read from the run's own staging through the write, with the plan held until the write completes under the process chunk size; preparation that ends past the sink timeout — returning or failing — is a 504 that starts no worker, and otherwise the worker gets the remainder; a request cancelled during preparation stops it, and one cancelled while preparation still succeeds starts no worker; and a parent plan-opening failure (contract, corrupt cache, admission) starts no worker.

- `tests/test_execution_profile_semantics.py` proves what a node's data is under every
  profile that may read or write a snapshot: that the bounded profiles agree with each
  other and repeat themselves, that a projected read is the full read restricted to those
  columns, that they refuse the same sources the same way, and that the interactive preview
  does not promise the bounded row order and so shares no snapshot with them.
- `tests/test_source_cache.py` covers canonical/redacted identity, atomic refresh,
  immutable generations, leases, corruption, no-budget retention, same-identity single flight,
  age-gated staging reclamation, digest memoization, non-destructive
  generation startup, and transient OS failures. A generation whose directory was deleted
  reads as missing, a current lease raises `FileNotFoundError` and holds nothing, and a build
  replaces it. A refresh or clear while a reader holds a part open (Windows' refusals of the
  rename and the delete, simulated) leaves the superseded generation whole and raises nothing,
  and the next retirement removes it; a retired directory left behind is swept when a process
  opens the store.
- `tests/test_input_providers.py` covers direct-Parquet and offline snapshot reads,
  provider identities, execution-lifetime leases, SQLite empty/mixed storage classes,
  typed conversion failures, missing database rejection, and the inferred schema tier:
  a CSV's separator and schema override reach the inferred schema with a bounded
  inference count and no generation written, Parquet records no bound, and Excel, a
  database without a snapshot, and a CSV encoding only the eager reader decodes are
  refused with the preview remedy.
- `tests/test_polars_io_registry.py`, `tests/test_polars_io_interface_contracts.py`, and
  `tests/test_bounded_sink_contract.py` cover registry/schema drift, validation, engine
  gates, and partitioned sink publication.
- `tests/test_io.py`, `tests/test_data_io_nodes.py`, `tests/test_data_io_roundtrips.py`, and
  `tests/test_json_read_documented.py` cover format dispatch, generated-node integration,
  declared schemas, projections, round trips, and bounded-memory policy.
- `tests/test_polars_utils.py` and `tests/test_file_ops.py` cover bounded collect/sink
  behaviour, native-query cancellation, poll validation, unchanged native `fetch()` failures,
  Parquet metadata, allocator dispatch, atomic publication primitives, and a best-effort tree
  removal retrying a transient Windows handle, reporting a tree it cannot remove, and treating
  an absent tree as removed.
- `tests/test_hashing.py` (`test_hashing_writer_digest_equals_file_digest`),
  `tests/test_polars_utils.py` (`test_hashed_streaming_sink_writes_and_hashes_atomically`),
  `tests/test_chunked_writes.py` (`test_parts_carry_write_time_digests`), and
  `tests/test_snapshot_legacy_layout.py` (`test_layout_2_generation_reads_as_absent`,
  `test_xxh64_digest_mismatch_is_corruption`) cover write-time incremental hashing via
  `HashingWriter`, a layout-2 generation reading as absent and being rebuilt, and
  corrupted part detection. `tests/test_polars_utils.py`
  (`test_hashed_streaming_sink_runs_the_native_streaming_sink`,
  `test_bounded_hashed_sink_cancels_native_query_and_publishes_nothing`,
  `test_hashed_streaming_sink_cleans_up_after_a_partial_write`) verifies native streaming
  execution, cancellation without publication, and cleanup after partial writes.
- `tests/test_discovery.py` and `tests/test_path_case_audit.py` cover pipeline discovery,
  deduplication, unreadable files, retained resolver seams, and cross-platform path spelling.
- `tests/test_input_cache_route.py` covers HTTP build/status/cancel/clear lifecycle and
  conflict/admission behaviour, a `read` configuration whose arguments are not an object
  answered 400 `invalid_input_config`, and that an admitted-eager explicit build runs through the
  worker entry point with its `memory_limited`, `cancelled`, `timed_out`, and
  `completed` outcomes mapped onto the job lifecycle (a fake spawn receives the budget, the
  generation id, and the staging token; a memory-limited outcome reconciles both through
  `reconcile_unpublished(identity, generation_id, staging_token)`, removing
  `.staging-<staging_token>`). It also pins build/execution path agreement: with the
  pipeline in `rating/` and the data at the project root, the identity and freshness
  signature the route derives equal the ones execution derives from the same config after
  `canonical_dataframe_execution_graph()`, so a root-relative locator cannot build one
  snapshot while the run reads another file.
- `tests/test_input_preparation.py` covers automatic preparation: (1) parity — for CSV with
  a declared schema, CSV without one, NDJSON, plain JSON, and inline records the prepared
  generation's schema and rows equal a direct whole-file eager read; (2) mixed late types —
  a CSV whose first 2,000 rows hold integers and whose later rows hold text is prepared as
  a `String` column with every row retained, where sample-limited inference would have
  mis-typed it; (3) malformed records — a CSV with the header `id,amount`, the rows
  `1,10` and `2,20`, and a third row `3,30,extra` carrying one field more than the header,
  read with the parser's default arguments (no `truncate_ragged_lines`), fails the build
  with `InputPreparationError(reason_code="build_failed")` wrapping Polars' compute error,
  nothing is published, and a previously current generation stays current and
  leased-readable; (4) source mutation —
  after a build, rewriting the source changes the signature and the next execution records
  `refreshed` and reads the new rows, an untouched source records `reused` with the store's
  build never entered, and `clear` followed by an execution records `built`; (5) concurrent
  preparation — two executions of one graph started on two threads against one store
  perform exactly one build and both read the same generation id; (6) cancellation — a
  cancellation token set during the build yields `reason_code="cancelled"`, no
  publication, and no staging directory; (7) timeout — a build deadline in the past yields
  `timed_out` with the same guarantees; (8) memory-limited worker — a fake spawn reporting
  a memory-limited terminal reason yields `memory_limited`, the `.staging-<staging_token>`
  directory is removed through the three-argument reconciliation, and the previous
  generation stays current; (9) schema-only
  executions never build (the store's `build` is poisoned) and the node still reports
  `input_snapshot_missing`; (10) placement — under `native_memory_backend_scope` the build
  runs in-process with the context's stages recorded, and without it the fake spawn
  receives `isolated_execution_budget(context)` and a config from
  `worker_config_for_memory_policy` whose `require_memory_limit` is `True` even under
  `HAUTE_WORKER_MEMORY_ENFORCEMENT=best_effort`; (11) a call without an admitted execution
  context does nothing and the node still reports `input_snapshot_missing`; (12) diagnostics
  — `metrics_payload()["input_preparation"]` lists one record per snapshot-backed input
  with action, build class, execution, reserved limit, elapsed seconds, rows, bytes, and
  generation id, validates through `ExecutionMetricsPayload`, and contains no configured
  locator; (13) the `input_snapshot_auto_build` warning is logged once per build with the
  identity digest and build class; (14) kill windows — a fake spawn that performs the
  store's steps and dies after the rename but before the pointer replacement leaves the
  parent reconciling an unreferenced generation (removed, previous pointer intact,
  `build_failed`), one that dies after the pointer replacement is reconciled as
  `published` and recorded `built`, and one whose generation was superseded before
  reconciliation by a fresh generation another process published (simulated by publishing
  through a second store handle) is recorded `reused` with the successor's generation id
  and no failure; (15) a host without a native cap under
  `HAUTE_WORKER_MEMORY_ENFORCEMENT=best_effort` yields `cap_unavailable` before any
  provider access; (16) cache invalidation — a warmed preview (`execute_graph` preview
  cache) returns the new rows and records `refreshed`
  after the source file is rewritten, because the runtime identity includes the source
  signature.
- `tests/test_polars_io_registry.py` additionally covers the build-only complete-inspection
  read (a CSV without a declared schema scans with `infer_schema_length=None` and a direct
  bounded read still refuses it), the scanner preference for a configured eager `read` when
  every argument is scanner-accepted (build class `bounded`, warning
  `eager_read_mode_scanned`) versus a reader-only argument (build class stays
  `admitted_eager`), and `input_snapshot_build_class` reporting the effective class.
- `tests/test_source_cache.py` additionally covers the generation hardening ported from the
  removed Explore store (hard-linked, symbolic-link, reparse-point, escaping, non-regular,
  and partial generations, simulated through `lstat` so no platform skips) and `lease_generation` of a named non-current generation and of an
  unknown one.
- `tests/test_node_snapshot_signature.py` covers the checked signature field set, upstream,
  runtime-file, utility-module, preamble, source, class, and contract-enforcement
  invalidation, and slot and signature stability for downstream edits and Explore
  presentation fields.
- `tests/test_node_snapshot_retention.py` covers the class mappings, a publication
  replacing the slot's previous signature (including that the bytes leave the store, and
  that a signature a reader still holds survives until it releases),
  widening with descendant staleness, the publication rule (fresh equal width, stale
  replacement, never narrowing a stale generation, refresh only by explicit builds, a writer
  bound to a replaced dependency, cleared dependencies, a corrupt latest generation surfaced
  by an automatic capture and replaced by an explicit build),
  the automatic-capture budget (the least recently leased evicted first; pinned, leased
  and input generations never; an explicit build evicting nothing; the re-checks under
  the lease lock; a deferred eviction staying indexed until a later check retires it;
  unclassifiable generations counted and kept; the default and configured budget; a
  store fault never failing the capture, and a misconfigured budget failing it with its
  lease released) and `usage()`,
  staged-artifact handover on supersession, pin inheritance and explicit pinning, no rehash
  on a second lease, named-generation leases through refresh and clear, validation only
  after the lease marker exists, malformed generation ids, the publisher's lease released
  when its handoff fails, lease markers, and recorded metadata; a generation whose directory
  was deleted is `missing`, never `corrupt`, and an automatic capture replaces it.
  `tests/test_seed_plans.py` resolves such a node as recomputed rather than raising
  `SnapshotCorruptError`, `tests/test_input_preparation.py` builds such an input again, and
  `tests/test_cache_nodes_routes.py` reports its row `missing` and `current` after a build.
  `test_the_budget_never_evicts_a_pinned_leased_or_input_generation` and the source-store
  `test_input_cache_keeps_datasets_without_byte_or_entry_budgets` prove that input
  snapshots and explicit builds are never evicted or refused for size.
- `tests/test_node_snapshot_cross_process.py` covers two worker processes publishing one
  identity once, a paused reader in another process keeping its generation through
  clear, a killed reader's dead marker allowing its generation to be cleared, a
  writer whose columns miss a concurrently widened generation keeping its own artifact, and
  lease/clear interleavings paused at their fault points.
- `tests/test_source_cache.py` additionally covers a build context with the parent-chosen
  pair staging under `.staging-<staging_token>` and publishing `generation_id`, beneath a
  temporary root long enough that a full-UUID staging name would exceed Windows'
  traditional path limit; a context carrying only one of the pair being rejected;
  `reconcile_unpublished` returning `published` for a current generation (nothing removed),
  removing an unreferenced renamed generation without touching the current one or another
  build's staging, removing the token's staging directory, and reporting `absent`
  otherwise.
# Publication and part naming corrective contracts (PR #227)

Ordered generation parts follow the numeric, unbounded-index naming contract in
the execution-engine specification, including indices above 99,999.

Every node-output identity is recorded in its existing slot index before a new
generation or current pointer becomes visible. A failure before pointer commit
preserves the old pointer and pin; an indexed identity without a pointer is a legal,
discoverable recovery state. Publication commits retention together with the
pointer under the existing lease lock: a failure while preparing retention must
not replace the old pointer. After pointer commit, cleanup failure must leave the
new generation indexed and retain its intended pin; Clear must discover both old
and new identities and readers must retain valid data. No additional metadata
store or journal is introduced.

The existing non-null `pinned_identity` denotes retention of the slot's current
data, including any older current pointer left during interrupted publication.
The candidate and intended pin are indexed before rename/pointer replacement;
pre-commit exceptions restore the previous pin. If storage also refuses that
rollback, the original error includes the rollback failure and the conservative
pin remains until Clear or another publication. Process death at this boundary
can likewise retain extra data, but cannot weaken the previous slot pin.

Replacement preserves the previous dataset until commit, then retires superseded
generations when no live reader holds them. Replacement leaves other slots untouched,
and there is no byte/count admission step. Only the automatic-capture budget, applied
after an automatic publication, may evict other slots' unpinned, unleased captures.
### Input leases shared between processes (PR #227 correction)

Input snapshots use the same store-wide lease lock, process-owner token and
generation markers as node outputs. Both `SourceCacheStore` and
`NodeSnapshotStore` handles participate. Acquisition records the marker before
metadata/content validation, and nested local readers retain it until the last
reader releases. Clear, replacement and reconciliation check
live holders under the shared lock before retiring or discarding a generation.
Dead owner markers can be reclaimed; age alone never overrides a live marker.

A current-generation lease preserves the ordinary open contract for retired
layouts: they are absent (`FileNotFoundError`), so preparation can rebuild them.
Failed validation releases its marker and local reference count before raising.
Named-generation leases retain their existing generation-missing error contract.

Keep the input retirement grace as an additional retention policy and preserve
supervised parent retirement after a successful worker build. A held superseded generation
can survive beyond grace or explicit Clear and stays readable until release;
once unpointed, the last release permits retirement. Input build publication
takes the shared lease lock before the in-process count lock. Do not
hold the global lease lock while building or validating a leased input. The
existing file-lock implementation and token directory are shared, not duplicated.
## Scratch filesystem headroom

Snapshot staging and bounded sinks check the destination filesystem before
starting a write. Known in-memory Arrow/DataFrame batches additionally check
their decoded byte size before appending. Reserve 64 KiB beyond the proposed
write for a footer and ownership metadata. Multipart sinks use the preceding
part's actual size as the next-part estimate when a decoded batch is unavailable.

These are local headroom checks, not reservations or predictions of total query
output. Concurrent filesystem users and variable compression can still cause
an OS write failure. Fail with `OSError(ENOSPC)` naming required/available bytes;
the existing staging owner removes the partial artifact on failure/cancellation.
Disk checks use actual free space: old generations and already-written staging
consume space until deleted. No old generation is removed to make a speculative write fit.
