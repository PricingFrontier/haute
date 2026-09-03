# IO Layer — Low-Level Specification

## Module map

| File | Responsibility |
|---|---|
| `src/haute/_io.py` | API-input flat-file adapters, source/schema validation, direct CSV/JSON/NDJSON/Parquet reads, and external-object loading. |
| `src/haute/_polars_io_registry.py` | Canonical Data Input/Output format registry, strict config and argument validation, capability publication, and Polars invocation. |
| `src/haute/_input_providers.py` | Provider dispatch, source identity/signature construction, explicit snapshot build, and leased snapshot resolution. |
| `src/haute/_input_preparation.py` | Automatic snapshot preparation planned before execution: freshness check, cap gate, in-process or worker build, per-process single-flight, preparation records, and staging-token cleanup. |
| `src/haute/_database_io.py` | Credential-free database locator/query validation and bounded read-only SQLite snapshot batches. |
| `src/haute/_credential_security.py` | Shared URI credential detection and provider-diagnostic redaction. |
| `src/haute/_source_cache.py` | Primary owner of source-cache identities, generations, metadata, publication, leases, quota, status, and cleanup. |
| `src/haute/_polars_io_schema.py` | Cached index over the committed Polars callable schema, live introspection of the installed Polars, and the intersection of the two. |
| `src/haute/_polars_io_arguments.json` | Generated Polars callable signature data checked against the pinned Polars version. |
| `src/haute/_polars_dtypes.py` | Struct-capable dtype JSON codec used by registry schema arguments. |
| `src/haute/_polars_utils.py` | Shared context-aware automatic/streaming collection, bounded/atomic sink, Parquet metadata, chunk-size scope, and allocator trim helpers. |
| `src/haute/_file_ops.py` | Atomic byte/text writers used for pointer and metadata publication. |
| `src/haute/_path_resolution.py` | Shared runtime path containment/resolution owned by [sandbox-security](../sandbox-security/low-level.md) and consumed by I/O. |
| `src/haute/_path_case_audit.py` | Cross-platform case-ambiguity warnings for user-facing paths. |
| `src/haute/discovery.py` | Pipeline-file discovery used by file-facing workflows. |

`src/haute/_source_cache.py` is consumed directly by the caching component; that shared
relationship is recorded in `specs/ownership.toml`.

## Key types and data structures

- `IoFormat` and `FORMATS` describe each format's source kind, Polars reader/scanner and
  writer/sinker, extensions, engine dependencies, bounded-read class, and allowed arguments.
- `DataSourceAdapter` wraps the legacy API-input `flat_file` surface only. It has no
  Databricks branch.
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
- `SourceCacheMetadata` records identity, generation, optional freshness signature, artifact
  SHA-256/size, rows, columns, schema, creation time, profile, and build class.
- `SourceCacheGeneration` names immutable `data.parquet` and `meta.json` paths.
- `SourceCacheStore` coordinates same-root handles in-process, publishes generations, tracks
  local leases and verified-generation memos, applies quotas, reclaims provably stale
  staging, and exposes `build`, `lease`, `clear`, and `status`. Leases are process-local. A
  superseded generation is retired only after `HAUTE_INPUT_CACHE_RETIRE_GRACE_SECONDS`
  (default 1800) have elapsed since the current generation was published, so a reader in
  another process finishes its scan; an explicit clear and quota pressure reclaim
  immediately, the latter logged
  (`source_cache_grace_reclaimed_under_quota_pressure`). A reconcile removal that leaves
  its directory behind is logged (`source_cache_reconcile_removal_failed`) and reported as
  `unremovable`.
- `source_signature` memoises by path, size, and mtime, so an unchanged file is hashed once
  per process; a file modified within the last two seconds is hashed every time, because a
  filesystem stamps mtimes at its own granularity and a same-size rewrite inside that window
  would otherwise keep a stale signature (git's racy-index rule).
- `DatabaseSnapshotBuilder` validates a read query and yields Arrow record batches with one
  stable schema from an existing SQLite database.

### Atomic pointer and metadata publication

`atomic_write_bytes` / `atomic_write_text` stage a uniquely named sibling file and atomically
replace the target, so readers observe either the complete old payload or the complete new
payload. The parent directory must already exist. A failed write or exhausted publication
attempt removes the private staging file and preserves the old target. If that exact-file
cleanup also fails, the publication error remains primary, the cleanup failure is attached as
an exception note, and the uniquely named stage remains visible for diagnosis. Windows antivirus,
indexer, and concurrent-reader handles can transiently reject an otherwise valid replace with
`ERROR_ACCESS_DENIED` or `ERROR_SHARING_VIOLATION`; only those two Win32 errors receive the
bounded delays `10 ms, 25 ms, 50 ms, 100 ms`, after which the original error still propagates.
The codes are retried only on Windows; every error on another platform and every other Windows
filesystem error fails immediately. This is retry of the same atomic operation, not
an in-place or non-atomic fallback.

## Control flow

### Canonical Data Input

1. `validate_data_input_config()` rejects inactive fields, invalid modes, unsafe raw URIs,
   unknown arguments, and missing required provider fields.
2. `source_cache_identity()` canonicalises the logical source. Database named connections
   retain only the environment reference; Databricks retains fixed host/token references and
   excludes `batch_size`; inline records contribute only a canonical content digest and row
   count, never raw record values. Relative file/lakehouse locators and raw SQLite URIs are
   anchored to the configured pipeline directory consistently for build, execution
   fingerprinting, and RAM metadata inspection.
3. `data_input_is_direct()` derives the execution mode: file-backed Parquet with
   effective mode `scan` is direct; every other canonical Data Input is snapshot-backed.
   No cache-mode field is stored — validation rejects a config still carrying the removed
   `cacheMode` field as inactive; nothing is normalised or migrated.
4. Direct Parquet resolution anchors the configured path and returns the registry's
   `scan_parquet` lazy scan without creating or consulting a source snapshot.
5. Snapshot creation calls `build_input_snapshot()`, selects a provider builder, creates a
   `SourceCacheBuildContext`, and calls `SourceCacheStore.build()`.
6. Snapshot execution calls `resolve_data_input()`, opens a lease, creates a Parquet scan,
   and attaches lease release to execution cleanup or an explicit callable scan-plan token.
7. `resolve_data_input_from_config()` is the generated-code sidecar entry point.

### Automatic preparation

1. `prepare_input_snapshots()` runs once per execution, after `_prepare_execution()` has
   pruned the graph to the target lineage and before strategy planning and before runtime
   identity (cache key) computation, so the RAM estimator reads the published generation
   and a refreshed generation's pointer is the one keyed. `executor.execute_graph` calls it
   before its request planning; the lazy engine's later call finds the generation fresh
   and records `reused`.
2. For each snapshot-backed Data Input in order: validate the config, derive the identity
   and source signature, and read `SourceCacheStore.status(identity,
   source_signature=...)`. `ready`/`fresh` and `ready`/`unknown` reuse; `missing` builds;
   `ready`/`stale` refreshes; `corrupt` raises `SourceCacheCorruptError`.
3. `schema_only=True` records nothing and never builds; the node builder's
   `input_snapshot_missing` rejection remains the outcome for a missing generation.
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
   deadline bounds it as `timed_out`. The spawned build itself is cancellable — the worker
   config's `stop_reason` reports `cancelled` while the execution's cancellation token is
   cancelled, terminating the child.
6. The structured warning `input_snapshot_auto_build` is logged once a build actually
   starts — never before a `cap_unavailable` refusal or a stale reuse; the
   record is appended to the context (`ExecutionContext.record_input_preparation`) and
   surfaces as `metrics_payload()["input_preparation"]`.
7. A spawned build never retires superseded generations: the child's lease counts are
   process-local, so a generation the parent still leases would look unreferenced there.
   The child's build context sets `defer_retirement=True`, which skips both retirement
   passes in `SourceCacheStore.build`, and the supervising parent calls
   `SourceCacheStore.retire_unleased(identity)` — with its own lease counts — after a
   successful spawned build and after a reconciliation that reported `published`.
   Because the child's lease table is empty, the supervising parent also passes the
   generation ids it leases at spawn time (`SourceCacheStore.leased_generation_ids`,
   carried on the request as `retained_generation_ids` and set on the child's build
   context), and the child's quota projection treats them as retained rather than
   reclaimable. A lease the parent acquires after that snapshot is the accepted
   cross-process window: the generation is kept until its release, and the release
   path retires it.
8. Worker failure classification maps the child's own exception type name carried by
   `IsolatedWorkerRemoteError.remote_type` first: `SourceCacheQuotaExceededError` (or the
   direct instance) is `quota_exceeded`, and `NativeMemoryLimitUnsupportedError` /
   `NativeMemoryLimitCleanupError` are `cap_unavailable`. It then uses
   `isolated_worker_failure_is_memory` and the worker's terminal reason to choose
   `memory_limited`, `cancelled`, `timed_out`, or `build_failed`. Each reason code carries
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

### Snapshot publication

1. The per-identity process lock serialises same-process builders.
2. A non-refresh build returns the current validated generation when present.
3. The builder writes to `.staging-<nonce>/data.parquet` (`.staging-<staging_token>`, and
   the parent-chosen `generation_id` as the published generation, when the build context
   carries the parent-chosen pair); Arrow iterables are checked at every batch and written
   against one schema.
4. Publication computes artifact integrity evidence, reads footer/schema/row counts, writes
   canonical `meta.json`, and validates the staged generation.
5. Quota admission rejects the incoming publication when projected byte/count limits would
   be exceeded; it never evicts another identity's current generation.
6. The staging directory is atomically renamed and `current.json` is atomically replaced.
7. Locally leased old generations survive until the final lease release.

Construction of a store handle inspects staging directories only. It recursively finds the
newest activity timestamp and removes a directory only when that timestamp predates
`HAUTE_INPUT_CACHE_STAGING_MAX_AGE_SECONDS`; stat failures and recent staging are preserved.
Non-current generations are never startup-swept. Staging bytes are included in quota
projection, excluding only the build currently being admitted.

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
   unleased generation is retired. Thus refresh/clear affect future selection
   immediately without invalidating an already-derived scan.

### Staging reclamation and quota admission

Store construction examines only real, non-symlink `.staging-*` directories.
For each it walks the tree without following symlinks and finds the newest
activity time across the directory and its contents. A tree is removed only
when that proof is readable and older than the configured threshold; a recent,
racing, or unreadable tree remains. No generation directory is part of startup
cleanup.

Before publication, quota accounting totals every published Parquet plus every
retained staging byte except the staging tree being admitted, then adds the
new artifact and generation count. The old current generation for the same
identity is subtracted only if locally unleased and not named in the build
context's `retained_generation_ids`, because successful pointer replacement
makes it reclaimable; a retained id is a lease held by the supervising parent of
a spawned build, which the child cannot see. No current generation for another identity
is an eviction candidate. If the projection still exceeds byte or count limits,
admission raises an actionable quota error and leaves every pointer/generation
unchanged.

Snapshot-mode execution contacts the configured provider only through automatic
preparation, which is the explicit build path scheduled before planning under a hard cap.
Resolution itself never builds: if no current snapshot exists when a node is resolved (a
schema-only execution, or a caller outside an admitted execution context), it raises
`PolarsIoConfigError` with the stable `input_snapshot_missing:` prefix and an instruction
to build the snapshot or run the pipeline under an admitted execution, which prepares it.

Direct mode is not a general compatibility path. It is valid only for a file-backed
Parquet scan, which already has the lazy, schema-bearing execution shape that a snapshot
would duplicate.

Legacy flat-file source projection treats an explicit empty `columns` iterable as a
row-cardinality-only request. `_select_columns()` validates against the complete lazy
schema and retains its first schema-ordered column as a physical carrier; selecting no
columns would make Polars report zero rows. Non-empty requests retain their exact existing
behaviour, and `columns=None` remains full-width.

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

`validate_data_output_config()` and registry mode resolution select the Polars sink.
`write_polars_output()` validates arguments/engines, applies the output target, and uses the
bounded sink discipline. Sidecar loading and parent-directory preparation are owned by the
generated pipeline/runtime seam.

### Streaming collection

`execution_collect()` is the one context-aware collection seam. With no active context it
performs one native collect using the requested automatic or streaming engine. With an
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
- Clear removes the pointer first and retires only unleased local generations.
- Startup never removes generations; it removes only staging older than the configured
  activity threshold, and quota accounting includes retained staging bytes.
- Full artifact SHA-256 is verified once per stable generation stat gate per process rather
  than once per lease.
- Partitioned Parquet sinks validate the final layout and preserve the previous published
  target if publication fails.
- Direct bounded reads still require declared CSV dtypes and refuse eager-only formats and
  eager `read` mode where a scanner exists; those rules no longer bound what a pipeline can
  run, because snapshot builds inspect a CSV completely (`infer_schema_length=None`, a
  literal keyword held by `tests/test_polars_io_interface_contracts.py`'s
  `_LITERAL_KEYWORDS`), prefer the scanner over a configured eager read whenever every
  configured argument is scanner-accepted (recording `eager_read_mode_scanned`), and run a
  genuinely reader-only format inside the hard-capped worker.
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
  the interface contract test fails on the same condition in CI.
- The intersection governs node-config arguments only. Keyword names haute itself writes as
  literals — `haute.executor._output_row_count_scan_kwargs()` for exact artifact row counts,
  `haute._io.read_source()` for declared-dtype CSV scans, and the compression argument in
  `src/haute/_polars_utils.py` — carry no such protection and are held instead by
  `tests/test_polars_io_interface_contracts.py`'s `_LITERAL_KEYWORDS` inventory.
- Argument *defaults* are Polars'. haute forwards what a node config sets and takes the
  installed Polars' behaviour for everything else, so a default that moves inside the
  specifier moves haute's results with it. That is reported by
  `scripts/extract_polars_io.py --diff` rather than gated: pinning every default haute does
  not set would be a promise of result-stability across the specifier that haute has never
  made.

## Error handling

`PolarsIoConfigError`, `DatabaseConfigError`, and `UnsupportedSourceFormatError` identify
invalid input before side effects. `BoundedMemoryUnsupportedError` is raised by the shared
execution discipline for operations that cannot meet the selected profile.

`SourceCacheBuildError` covers cancellation, deadline, unsupported build class, and invalid
builder output. `SourceCacheQuotaExceededError` rejects a publication without replacing the
current pointer. `SourceCacheCorruptError` is reserved for proven structural/integrity
failure. `OSError` subclasses from transient filesystem access are not relabelled corrupt.

`InputPreparationError` (`error_code="input_preparation_failed"`) wraps automatic
preparation outcomes with a stable `reason_code` (`cap_unavailable`, `build_failed`,
`memory_limited`, `cancelled`, `timed_out`, `quota_exceeded`), the node id, identity
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
   read, artifact write, integrity/footer/schema measurement, quota admission,
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
which bytes/counts enter quota projection, and when may another current
generation be evicted? At which boundary does each config, provider,
cancellation, schema, integrity, quota, pointer, SQLite, and output failure
surface? The identity, lease, reclamation/quota, SQLite, output, and ordered
failure sections above are the maintained answers.

## Testing

- `tests/test_apiinput_flat_nested_relative_path.py` verifies API-input execution resolves pipeline-relative files from project root and rejects out-of-root absolute paths.
- `tests/test_data_input_nested_relative_path.py` verifies data-input project-root resolution, escape/symlink rejection, selected external pipeline roots, and HTTP external-root denial.
- `tests/test_data_io_config_contract.py` exercises valid/invalid data-input/output discriminated configurations and loud rejection of unknown branches.
- `tests/test_external_file_nested_relative_path.py` verifies project-root relative external-file loading, absolute passthrough, and project-escape rejection.
- `tests/test_pipeline_runtime_path_validation.py` verifies runtime path/graph validation, HTTP status mapping, sidecar/codegen case-collision and reserved-name protections, and safe rename/delete semantics.
- `tests/test_read_user_text.py` verifies robust text/config/pipeline decoding across encodings and malformed inputs.
- `tests/test_sink.py` verifies sink execution errors, parquet/CSV output, directory creation, scenario handling, compute failures, and response metadata.

- `tests/test_source_cache.py` covers canonical/redacted identity, atomic refresh,
  immutable generations, leases, corruption, quota, same-identity single flight,
  age-gated staging reclamation, quota-visible staging, digest memoization, non-destructive
  generation startup, and transient OS failures.
- `tests/test_input_providers.py` covers direct-Parquet and offline snapshot reads,
  provider identities, execution-lifetime leases, SQLite empty/mixed storage classes,
  typed conversion failures, and missing database rejection.
- `tests/test_polars_io_registry.py`, `tests/test_polars_io_interface_contracts.py`, and
  `tests/test_bounded_sink_contract.py` cover registry/schema drift, validation, engine
  gates, and partitioned sink publication.
- `tests/test_io.py`, `tests/test_data_io_nodes.py`, `tests/test_data_io_roundtrips.py`, and
  `tests/test_json_read_documented.py` cover format dispatch, generated-node integration,
  declared schemas, projections, round trips, and bounded-memory policy.
- `tests/test_polars_utils.py` and `tests/test_file_ops.py` cover bounded collect/sink
  behaviour, native-query cancellation, poll validation, unchanged native `fetch()` failures,
  Parquet metadata, allocator dispatch, and atomic publication primitives.
- `tests/test_discovery.py` and `tests/test_path_case_audit.py` cover pipeline discovery,
  deduplication, unreadable files, retained resolver seams, and cross-platform path spelling.
- `tests/test_input_cache_route.py` covers HTTP build/status/cancel/clear lifecycle and
  conflict/admission behaviour, and that an admitted-eager explicit build runs through the
  worker entry point with its `memory_limited`, `cancelled`, `timed_out`, quota, and
  `completed` outcomes mapped onto the job lifecycle (a fake spawn receives the budget, the
  generation id, and the staging token; a memory-limited outcome reconciles both through
  `reconcile_unpublished(identity, generation_id, staging_token)`, removing
  `.staging-<staging_token>`).
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
  cache) and a warmed dataframe cache both return the new rows and record `refreshed`
  after the source file is rewritten, because the runtime identity includes the source
  signature.
- `tests/test_polars_io_registry.py` additionally covers the build-only complete-inspection
  read (a CSV without a declared schema scans with `infer_schema_length=None` and a direct
  bounded read still refuses it), the scanner preference for a configured eager `read` when
  every argument is scanner-accepted (build class `bounded`, warning
  `eager_read_mode_scanned`) versus a reader-only argument (build class stays
  `admitted_eager`), and `input_snapshot_build_class` reporting the effective class.
- `tests/test_source_cache.py` additionally covers a build context with the parent-chosen
  pair staging under `.staging-<staging_token>` and publishing `generation_id`, beneath a
  temporary root long enough that a full-UUID staging name would exceed Windows'
  traditional path limit; a context carrying only one of the pair being rejected; and
  `reconcile_unpublished` returning `published` for a current generation (nothing removed),
  removing an unreferenced renamed generation without touching the current one or another
  build's staging, removing the token's staging directory, and reporting `absent`
  otherwise.
