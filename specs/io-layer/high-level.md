# IO Layer — High-Level Specification

## Purpose

The IO layer turns persisted Data Input and Data Output node configuration into
validated Polars operations. It also owns provider-neutral, immutable input snapshots for
sources that are not already Parquet. A file-backed Parquet Data Input is the canonical
direct-read case and is scanned from its configured source.

The component keeps source acquisition separate from pipeline execution. Explicit
snapshot builds acquire non-Parquet provider data and publish a verified Parquet
generation; pipeline execution reads that generation. File-backed Parquet skips the
redundant build and is scanned directly.

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

`dataInput` configurations select exactly one provider. File-backed Parquet in scan mode
requires `cacheMode: "direct"`; every other file format and every database, lakehouse,
Databricks, or inline input requires `cacheMode: "snapshot"`. These are strict canonical
combinations, not migration aliases: a mismatched cache mode is invalid. Inline records
use a bounded snapshot build whose identity contains a digest of the logical records
rather than their raw values. `dataOutput` configurations select a registered file,
lakehouse, or database sink. Unknown fields, unsupported arguments, unavailable engines,
ambiguous locators, and invalid mode/provider combinations fail before provider access.

Raw database URIs are permitted only when credential-free. URI userinfo and recognised
secret-bearing query parameters are rejected by one shared validator before a sidecar,
cache identity, metadata document, or connector can receive the value. Named connection
references may resolve credentials from the environment, but the resolved URI never
enters cache identity or metadata. Provider diagnostics are scrubbed with the same
credential-name policy plus resolved in-process secret values before logging.

Snapshot identity includes source semantics: provider, canonical locator, validated query,
format/mode, and source arguments that can affect returned rows or schema. Databricks
`batch_size` is excluded because it changes fetch partitioning, not logical source data.
`code` and `cacheMode` are excluded because they do not change the acquired source bytes.

A snapshot build writes a unique staging directory, validates the Parquet artifact and
metadata, admits it against byte/count quotas, atomically publishes an immutable generation,
and then replaces the current pointer. Cancellation, timeout, connector failure, schema
failure, or quota rejection leaves the previous current generation readable.

Snapshot readers acquire an explicit generation lease. Within an execution request the
lease lasts until execution cleanup. Outside an execution request the returned scan owns a
lease token that is retained by every derived LazyFrame and released only after the scan
plan is no longer reachable. Refresh and clear never delete a locally leased generation.

Store startup never deletes a generation. It may reclaim a staging directory only when the
newest filesystem activity beneath that directory is older than the configured stale-build
threshold; recent or unreadable staging state is preserved. Unreclaimed staging bytes count
against the store byte quota. Publication and leases are coordinated within one process,
while published immutable generations may be read by another process.

Registry input capabilities advertise `scan` whenever a format has a scanner;
reader-only formats advertise `read`, and declare the one canonical cache mode for that
provider/format. Stored `read` configurations remain valid and executable even when the
current capability payload advertises only `scan`.

SQLite snapshots open an existing database in read-only URI mode and start a read
transaction. One aggregate query determines every output column's observed SQLite storage
classes before the data cursor emits a batch; declared table types are hints only for empty
columns. Integer/real observations widen to a float, incompatible storage-class mixtures
fail before artifact output, and one Arrow schema is applied to every data batch. Missing
database files are rejected rather than created.

Outputs overwrite existing targets when the registered Polars sink has overwrite
semantics. Authoring-time publication of a new output sidecar is conflict-safe: an
unexpected existing sidecar is reported as a conflict rather than silently replaced.

## Design rationale

The registry is the single capability source for validation, editor metadata, and Polars
dispatch, preventing code generation from inventing a second format matrix. Snapshot
generation directories and a tiny atomic pointer make refresh safe for concurrent readers.
Strict identity validation prevents durable cache metadata becoming a credential leak.

Parquet is the shared snapshot boundary because it is lazy-scannable, schema-bearing, and
can be written in bounded batches. Publication computes SHA-256 and seeds a process-local
verification memo. The first open of a generation not already in that memo rechecks SHA-256;
later opens reuse the verification while `(mtime_ns, size, recorded digest)` is unchanged.
Footer, schema, row count, and metadata checks still run on every open.

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

When `overwrite=false`, an existing data-output destination raises
`DataOutputDestinationExistsError`; the server maps that explicit refusal to HTTP 409 rather
than treating it as an I/O failure or replacing the destination.

Malformed pointers, digest mismatches, metadata mismatches, invalid generation identifiers,
or invalid Parquet footer/schema evidence raise `SourceCacheCorruptError`; callers do not
silently rebuild or fall back. Transient operating-system access errors propagate as
operating-system errors so operators can retry and are not told durable data is corrupt.
