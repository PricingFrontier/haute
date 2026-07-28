# IO Layer — Low-Level Specification

## Module map

| File | Responsibility |
|---|---|
| `src/haute/_io.py` | API-input flat-file adapters, source/schema validation, direct CSV/JSON/NDJSON/Parquet reads, and external-object loading. |
| `src/haute/_polars_io_registry.py` | Canonical Data Input/Output format registry, strict config and argument validation, capability publication, and Polars invocation. |
| `src/haute/_input_providers.py` | Provider dispatch, source identity/signature construction, explicit snapshot build, and leased snapshot resolution. |
| `src/haute/_database_io.py` | Credential-free database locator/query validation and bounded read-only SQLite snapshot batches. |
| `src/haute/_credential_security.py` | Shared URI credential detection and provider-diagnostic redaction. |
| `src/haute/_source_cache.py` | Primary owner of source-cache identities, generations, metadata, publication, leases, quota, status, and cleanup. |
| `src/haute/_polars_io_schema.py` | Cached index over the committed Polars callable schema. |
| `src/haute/_polars_io_arguments.json` | Generated Polars callable signature data checked against the pinned Polars version. |
| `src/haute/_polars_dtypes.py` | Struct-capable dtype JSON codec used by registry schema arguments. |
| `src/haute/_polars_utils.py` | Streaming and cancellable streaming collect, bounded/atomic sink, Parquet metadata, chunk-size scope, and allocator trim helpers. |
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
  deadline, progress, and optional `ExecutionContext`.
- `SourceCacheMetadata` records identity, generation, optional freshness signature, artifact
  SHA-256/size, rows, columns, schema, creation time, profile, and build class.
- `SourceCacheGeneration` names immutable `data.parquet` and `meta.json` paths.
- `SourceCacheStore` coordinates same-root handles in-process, publishes generations, tracks
  local leases and verified-generation memos, applies quotas, reclaims provably stale
  staging, and exposes `build`, `lease`, `clear`, and `status`.
- `DatabaseSnapshotBuilder` validates a read query and yields Arrow record batches with one
  stable schema from an existing SQLite database.

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
3. File-backed Parquet with effective mode `scan` requires `cacheMode: "direct"`.
   Every other canonical Data Input requires `cacheMode: "snapshot"`. Validation rejects
   mismatches; it does not normalise or migrate them.
4. Direct Parquet resolution anchors the configured path and returns the registry's
   `scan_parquet` lazy scan without creating or consulting a source snapshot.
5. Snapshot creation calls `build_input_snapshot()`, selects a provider builder, creates a
   `SourceCacheBuildContext`, and calls `SourceCacheStore.build()`.
6. Snapshot execution calls `resolve_data_input()`, opens a lease, creates a Parquet scan,
   and attaches lease release to execution cleanup or an explicit callable scan-plan token.
7. `resolve_data_input_from_config()` is the generated-code sidecar entry point.

### Snapshot publication

1. The per-identity process lock serialises same-process builders.
2. A non-refresh build returns the current validated generation when present.
3. The builder writes to `.staging-<nonce>/data.parquet`; Arrow iterables are checked at
   every batch and written against one schema.
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
identity is subtracted only if locally unleased because successful pointer
replacement makes it reclaimable. No current generation for another identity
is an eviction candidate. If the projection still exceeds byte or count limits,
admission raises an actionable quota error and leaves every pointer/generation
unchanged.

Snapshot-mode execution never contacts the configured provider. If no current
snapshot exists, resolution raises `PolarsIoConfigError` with the stable
`input_snapshot_missing:` prefix and an instruction to build the snapshot (or
run a Studio preview, which ensures it before execution).

Direct mode is not a general compatibility path. It is valid only for a file-backed
Parquet scan, which already has the lazy, schema-bearing execution shape that a snapshot
would duplicate.

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

`streaming_collect()` performs one native streaming collect and propagates native failures
unchanged. `cancellable_streaming_collect()` starts one native streaming background query,
polls `InProcessQuery.fetch()` at a validated positive interval, and checkpoints between
polls. A checkpoint failure cancels the native query before propagating; query `fetch()`
failures propagate unchanged, and a best-effort cancellation failure cannot mask the
checkpoint failure. Neither helper has an eager or non-streaming fallback.

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
- Bounded profiles require declared CSV dtypes and reject eager-only plain JSON.

## Error handling

`PolarsIoConfigError`, `DatabaseConfigError`, and `UnsupportedSourceFormatError` identify
invalid input before side effects. `BoundedMemoryUnsupportedError` is raised by the shared
execution discipline for operations that cannot meet the selected profile.

`SourceCacheBuildError` covers cancellation, deadline, unsupported build class, and invalid
builder output. `SourceCacheQuotaExceededError` rejects a publication without replacing the
current pointer. `SourceCacheCorruptError` is reserved for proven structural/integrity
failure. `OSError` subclasses from transient filesystem access are not relabelled corrupt.

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
3. Opening/lease acquisition reads and validates the pointer first, then the
   generation id/metadata identity and counts, byte size/digest, Parquet footer,
   Arrow names, and Polars schema. Proven structural/integrity failures become
   `SourceCacheCorruptError`; a missing pointer remains “not built,” and
   transient `OSError` is propagated rather than mislabeled corruption. No
   provider fallback is attempted for a requested snapshot; the only direct
   execution path is canonical file-backed Parquet.
4. SQLite validates safe locator, existing regular file, and read-only query
   before connection. It begins the read transaction and completes one
   all-column storage-class query before the data cursor emits any batch, so an
   incompatible column mixture cannot leave a partial artifact.
5. Data Output validates the destination/config and, when overwrite is false,
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
  conflict/admission behaviour.
