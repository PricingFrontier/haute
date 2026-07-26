# IO Layer — Low-Level Specification

## Module map

| File | Responsibility |
|---|---|
| `src/haute/_io.py` | API-input flat-file adapters, source/schema validation, direct CSV/JSON/NDJSON/Parquet reads, and external-object loading. |
| `src/haute/_polars_io_registry.py` | Canonical Data Input/Output format registry, strict config and argument validation, capability publication, and Polars invocation. |
| `src/haute/_input_providers.py` | Provider dispatch, source identity/signature construction, explicit snapshot build, and leased snapshot resolution. |
| `src/haute/_database_io.py` | Credential-free database locator/query validation and bounded read-only SQLite snapshot batches. |
| `src/haute/_source_cache.py` | Primary owner of source-cache identities, generations, metadata, publication, leases, quota, status, and cleanup. |
| `src/haute/_polars_io_schema.py` | Cached index over the committed Polars callable schema. |
| `src/haute/_polars_io_arguments.json` | Generated Polars callable signature data checked against the pinned Polars version. |
| `src/haute/_polars_dtypes.py` | Struct-capable dtype JSON codec used by registry schema arguments. |
| `src/haute/_polars_utils.py` | Streaming collect, bounded/atomic sink, Parquet metadata, chunk-size scope, and allocator trim helpers. |
| `src/haute/_file_ops.py` | Atomic byte/text writers used for pointer and metadata publication. |
| `src/haute/_path_resolution.py` | Shared runtime path containment/resolution consumed by I/O. |
| `src/haute/_path_case_audit.py` | Cross-platform case-ambiguity warnings for user-facing paths. |
| `src/haute/discovery.py` | Pipeline-file discovery used by file-facing workflows. |

`src/haute/_source_cache.py` is consumed directly by the caching component; that shared
relationship is recorded in `docs/specs/ownership.toml`.

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
  local leases, applies quotas, and exposes `build`, `lease`, `clear`, and `status`.
- `DatabaseSnapshotBuilder` validates a read query and yields Arrow record batches with one
  stable schema from an existing SQLite database.

## Control flow

### Canonical Data Input

1. `validate_data_input_config()` rejects inactive fields, invalid modes, unsafe raw URIs,
   unknown arguments, and missing required provider fields.
2. `source_cache_identity()` canonicalises the logical source. Database named connections
   retain only the environment reference; Databricks retains fixed host/token references and
   excludes `batch_size`.
3. Direct file/lakehouse/inline execution calls `read_polars_input()`.
4. Snapshot creation calls `build_input_snapshot()`, selects a provider builder, creates a
   `SourceCacheBuildContext`, and calls `SourceCacheStore.build()`.
5. Snapshot execution calls `resolve_data_input()`, opens a lease, creates a Parquet scan,
   and attaches lease release to execution cleanup or a scan-plan lease token.
6. `resolve_data_input_from_config()` is the generated-code sidecar entry point.

The dead registry helpers `read_polars_input_from_config()` and
`write_polars_output_from_config()` do not form part of this flow and are not shipped.

### Snapshot publication

1. The per-identity process lock serialises same-process builders.
2. A non-refresh build returns the current validated generation when present.
3. The builder writes to `.staging-<nonce>/data.parquet`; Arrow iterables are checked at
   every batch and written against one schema.
4. Publication computes artifact integrity evidence, reads footer/schema/row counts, writes
   canonical `meta.json`, and validates the staged generation.
5. Quota admission may evict the oldest unleased current generation of another identity.
6. The staging directory is atomically renamed and `current.json` is atomically replaced.
7. Locally leased old generations survive until the final lease release.

Construction of a store handle performs no destructive startup sweep. Process-local locks
and leases define mutation coordination; immutable published generations are safe to read
from another process, but automatic cross-process orphan deletion is deliberately absent.

### SQLite builder

1. `resolve_connection_uri()` resolves either an environment reference or raw safe URI.
2. `resolve_sqlite_path()` validates scheme/authority and returns the existing path.
3. `DatabaseSnapshotBuilder.build()` checkpoints before connection, opens SQLite with
   `mode=ro`, enables query-only behaviour, and begins a transaction.
4. It executes the validated query, derives one Arrow schema from result metadata plus
   SQLite type evidence, and emits every `fetchmany()` batch against that schema.
5. A zero-row query emits one empty schema-bearing batch.

### Data Output

`validate_data_output_config()` and registry mode resolution select the Polars sink.
`write_polars_output()` validates arguments/engines, applies the output target, and uses the
bounded sink discipline. Sidecar loading and parent-directory preparation are owned by the
generated pipeline/runtime seam, not dead registry wrappers.

## Edge cases and invariants

- Raw database URI query keys matching common credential names (`token`, `password`,
  `secret`, `credential`, `access_key`, `apikey`, `api_key`, and common variants) are
  rejected case-insensitively; query values are never inspected or logged.
- Cache identity dictionaries have string keys and canonical-JSON-compatible values.
- Databricks query/table spelling affects identity; fetch `batch_size` does not.
- Generation identifiers are canonical UUID strings and cannot traverse paths.
- Empty snapshots retain schema. Mixed batches use the builder-declared schema rather than
  per-batch inference.
- Missing SQLite paths fail without creating a file.
- Clear removes the pointer first and retires only unleased local generations.
- Startup never removes staging or non-current generation directories.
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

Provider route handlers log both exception type and message, while public job responses use
stable non-sensitive messages and error codes. Unexpected sidecar overwrite is an HTTP 409;
registered data sinks keep their documented overwrite semantics.

## Testing

- `tests/test_source_cache.py` covers canonical/redacted identity, atomic refresh,
  immutable generations, leases, corruption, quota, same-identity single flight,
  non-destructive cross-process startup, and transient OS failures.
- `tests/test_input_providers.py` covers direct/snapshot parity, offline cached reads,
  provider identities, execution-lifetime leases, SQLite empty/mixed batches, and missing
  database rejection.
- `tests/test_polars_io_registry.py`, `tests/test_polars_io_interface_contracts.py`, and
  `tests/test_bounded_sink_contract.py` cover registry/schema drift, validation, engine
  gates, and partitioned sink publication.
- `tests/test_io.py`, `tests/test_io_bounded_profile.py`, and related I/O suites cover
  format dispatch, declared schemas, projections, and bounded-memory policy.
- `tests/test_input_cache_route.py` covers HTTP build/status/cancel/clear lifecycle and
  conflict/admission behaviour.
