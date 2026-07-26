# IO Layer — High-Level Specification

## Purpose

The IO layer turns persisted Data Input and Data Output node configuration into
validated Polars operations. It also owns provider-neutral, immutable input snapshots so
database, Databricks, and selected file inputs can be built explicitly and then executed
without contacting the source.

The component keeps source acquisition separate from pipeline execution. Direct inputs
remain lazy where Polars supports them; snapshot inputs publish a verified Parquet
generation and execute only from that generation.

## Scope

In scope:

- the format and argument registry for canonical `dataInput` and `dataOutput` nodes;
- API-input flat-file adapters and common Polars collect/sink helpers;
- provider dispatch for file, lakehouse, inline, database, and Databricks inputs;
- source-cache identity, build, publication, lease, quota, clear, and status behaviour;
- bounded SQLite snapshot acquisition.

The [Databricks IO component](../databricks-io/high-level.md) owns Databricks credential
resolution, query validation, Arrow fetching, and Unity Catalog browsing. The
[caching component](../caching/high-level.md) consumes the source-cache identity contract
and owns execution/dataframe/JSON cache behaviour. HTTP job admission and responses belong
to [server API](../server-api/high-level.md).

## Behaviour

`dataInput` configurations select exactly one provider. File and lakehouse inputs may run
directly or through a snapshot; inline records run directly only; database and Databricks
inputs require snapshots. `dataOutput` configurations select a registered file,
lakehouse, or database sink. Unknown fields, unsupported arguments, unavailable engines,
ambiguous locators, and invalid mode/provider combinations fail before provider access.

Raw database URIs are permitted only when credential-free. URI userinfo and recognised
secret-bearing query parameters are rejected by one shared validator before a sidecar,
cache identity, metadata document, or connector can receive the value. Named connection
references may resolve credentials from the environment, but the resolved URI never
enters cache identity or metadata.

Snapshot identity includes source semantics: provider, canonical locator, validated query,
format/mode, and source arguments that can affect returned rows or schema. Databricks
`batch_size` is excluded because it changes fetch partitioning, not logical source data.
`code` and `cacheMode` are also excluded because they are post-read execution choices.

A snapshot build writes a unique staging directory, validates the Parquet artifact and
metadata, admits it against byte/count quotas, atomically publishes an immutable generation,
and then replaces the current pointer. Cancellation, timeout, connector failure, schema
failure, or quota rejection leaves the previous current generation readable.

Snapshot readers acquire an explicit generation lease. Within an execution request the
lease lasts until execution cleanup. Outside an execution request the returned scan owns a
lease token that is retained by every derived LazyFrame and released only after the scan
plan is no longer reachable. Refresh and clear never delete a locally leased generation.

Store startup is non-destructive: it does not infer that another process's staging
directory or non-current generation is abandoned. Publication and leases are coordinated
within one process. Cross-process processes may share already-published immutable
generations, but destructive orphan reclamation requires an explicit maintenance operation;
startup never performs it.

SQLite snapshots open an existing database in read-only URI mode, start a read transaction,
derive one Arrow schema before writing, and apply that schema to every batch. Missing
database files are rejected rather than created. Empty results retain declared column
dtypes, and values that cannot be represented by the derived schema fail the build.

Outputs overwrite existing targets when the registered Polars sink has overwrite
semantics. Authoring-time publication of a new output sidecar is conflict-safe: an
unexpected existing sidecar is reported as a conflict rather than silently replaced.

## Design rationale

The registry is the single capability source for validation, editor metadata, and Polars
dispatch, preventing code generation from inventing a second format matrix. Snapshot
generation directories and a tiny atomic pointer make refresh safe for concurrent readers.
Strict identity validation prevents durable cache metadata becoming a credential leak.

Parquet is the shared snapshot boundary because it is lazy-scannable, schema-bearing, and
can be written in bounded batches. Existing-generation integrity uses metadata, file size,
and Parquet footer/schema checks on ordinary opens; full SHA-256 verification is performed
at publication and retained for explicit integrity evidence, not recomputed on every lease.

## Interactions

- [Databricks IO](../databricks-io/high-level.md) supplies bounded Arrow batches.
- [Caching](../caching/high-level.md) defines checked identity inputs and consumes snapshot
  generations alongside execution caches.
- [Execution engine](../execution-engine/high-level.md) supplies profiles, cancellation,
  stages, and lifecycle cleanup.
- [Server API](../server-api/high-level.md) owns explicit snapshot build/status/cancel/clear
  routes and conflict responses.
- [Sandbox security](../sandbox-security/high-level.md) owns project-root path containment.

## Failure model

Configuration and credential-safety errors are loud `ValueError` subclasses before I/O.
Unsupported bounded-memory operations fail rather than falling back to eager collection.
Connector, cancellation, deadline, quota, and schema failures abort staging and preserve the
previous pointer.

Malformed pointers, metadata mismatches, invalid generation identifiers, or invalid Parquet
footer/schema evidence raise `SourceCacheCorruptError`; callers do not silently rebuild or
fall back. Transient operating-system access errors propagate as operating-system errors so
operators can retry and are not told durable data is corrupt.
