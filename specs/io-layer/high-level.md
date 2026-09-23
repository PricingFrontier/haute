# IO Layer — High-Level Specification

## Purpose

The IO layer turns persisted Data Input and Data Output node configuration into
validated Polars operations. It also owns provider-neutral, immutable input snapshots for
sources that are not already Parquet. A file-backed Parquet Data Input is the canonical
direct-read case and is scanned from its configured source.

The component keeps source acquisition a distinct, contained operation. Explicit
snapshot builds acquire non-Parquet provider data and publish a verified Parquet
generation; pipeline execution reads that generation. A bounded execution that finds
that generation missing or stale may schedule the same build before it starts — only
through the explicit build path, inside a hard memory cap, warned and reported — and
then reads the published generation. File-backed Parquet skips the redundant build and
is scanned directly.

## Scope

In scope:

- the format and argument registry for canonical `dataInput` and `dataOutput` nodes;
- API-input flat-file adapters and common Polars collect/sink helpers;
- provider dispatch for file, lakehouse, inline, database, and Databricks inputs;
- source-cache identity, build, publication, lease, clear, and status behaviour;
- node-output snapshots in the same store: signatures, slots, column widening, retention and
  replacement, and cross-process publication and leases;
- bounded SQLite snapshot acquisition.

The [Databricks IO component](../databricks-io/high-level.md) owns Databricks credential
resolution, query validation, Arrow fetching, and Unity Catalog browsing. The
[caching component](../caching/high-level.md) consumes the source-cache identity contract
and owns execution/dataframe/JSON cache behaviour. HTTP job admission and responses belong
to [server API](../server-api/high-level.md).

## Behaviour

**User-managed cache storage.** Input snapshots and node outputs have no
cache-specific byte or entry-count limit. The former 20/40 GiB defaults and
the environment settings for cache bytes and generation counts are removed.
Publishing a dataset never evicts another current dataset to meet a budget.
Users inspect the project cache inventory and clear entries explicitly.
Refresh still replaces previous data, and replacement or clearing preserves
active readers until their leases end. Actual filesystem errors, disk-headroom
checks and execution-memory limits remain enforced.

`dataInput` configurations select exactly one provider. There is no stored cache-mode
field: `data_input_is_direct` derives the execution mode, so a file-backed Parquet scan
reads directly and every other file format and every database, lakehouse, Databricks, or
inline input is snapshot-backed. A config still carrying the removed `cacheMode` field is
rejected as an inactive field, never migrated. Inline records
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
`code` is excluded because it does not change the acquired source bytes; the derived
direct-versus-snapshot execution mode is not part of the config at all.

A snapshot build writes a unique staging directory, validates the Parquet artifact and
metadata, atomically publishes an immutable generation,
and then replaces the current pointer. Cancellation, timeout, connector failure, schema
failure leaves the previous current generation readable.

Automatic preparation is that same build, scheduled by execution. Before an execution
plans its strategy it checks every snapshot-backed Data Input in the executed lineage
against the store with the current source signature: a current, fresh generation (or one
whose freshness is unknown because either side carries no signature) is reused; a missing
generation is built; a stale one is refreshed. An absent local source with a published
generation is reused with warning code `source_unavailable` and never refreshed; without a
generation the preparation is refused as `build_failed` before any worker starts. The build
runs under a hard cap or not at
all: in the current process when that process already runs inside an isolated worker
under a native cap, otherwise in a spawned hard-capped worker admitted from the
execution's own budget. That cap is native and mandatory for preparation whatever the
process-memory enforcement policy says, and is never replaced by RSS sampling: a host that
cannot install the cap reuses a ready-but-stale generation with warning code
`cap_unavailable_stale_reused`; only a missing generation is refused typed
(`cap_unavailable`). A
schema-only execution, or one without an admitted execution context, never builds;
resolution then reports the typed `input_snapshot_missing` rejection as before. Before a
build the engine
emits a structured warning naming the input, its identity digest, the build class, and the
reserved limit; afterwards every execution's terminal diagnostics list each input's
preparation record — reused, built, or refreshed, where it ran, its reserved limit,
elapsed time, rows, bytes, and generation — using digests and counts, never locators or
secrets. Within one process, concurrent executions needing the same identity share one
build. A build that fails for any reason publishes nothing and leaves the previous
generation readable. A spawned build carries a parent-chosen generation identifier and a
short parent-chosen staging token, so after the worker dies the parent reconciles exactly
those: a pointer already naming the generation means the build succeeded and is recorded
as such, an unreferenced renamed generation under that identifier or the staging directory
under that token is removed, nothing else is touched, and a fresh generation published
meanwhile by another process is reused rather than reported as a failure. Execution
and preview caches key a snapshot-backed input by its
generation pointer and its current source signature, so a mutated source misses every
cache and reaches preparation, and a refreshed generation changes the pointer and
therefore every later key.

A snapshot build inspects its source completely. A format that needs a declared schema in
bounded profiles (CSV) is built with a declaration when one is configured and otherwise
by whole-file schema inference before the streaming sink; sample-only inference never
decides a snapshot schema, so a late row cannot be mis-typed. When a format has a scanner
but eager `read` mode was configured, the build scans instead — with a recorded warning —
whenever every configured argument is one the scanner accepts and, where the scanner
narrows an argument's value domain (CSV `encoding`), its configured value, and runs the
eager reader
inside the hard-capped worker only when an argument is reader-only. Every supported
format therefore runs in a bounded profile once a build envelope can be reserved; a source
is refused only when its build cannot be isolated or admitted, or its parsing semantics
are invalid.

Explicit builds of the admitted-eager class run in the same hard-capped worker rather than
on a server thread; bounded explicit builds keep their streaming thread path.

Snapshot readers acquire an explicit generation lease. Within an execution request the
lease lasts until execution cleanup. Outside an execution request the returned scan owns a
lease token that is retained by every derived LazyFrame and released only after the scan
plan is no longer reachable. Refresh and clear never delete a live leased generation,
including one held by another process. A superseded input generation is normally retired after
`HAUTE_INPUT_CACHE_RETIRE_GRACE_SECONDS` (default 1800) have elapsed since the current
generation was published. Explicit clear bypasses this grace period while preserving live leases.

Node outputs live in the same store as a `node_output` provider. A
node-output identity is a slot — pipeline source file, node, source, and execution
semantics class — plus the node's
checked data signature. Each slot keeps one current dataset: publishing a new signature
replaces the previous one, so reverting an edit requires recomputation. A per-slot index lists a slot's identities:
for a requested signature the slot is `current` when that identity has a fresh generation,
`stale` when its own generation is stale or only other identities have generations, and
`missing` otherwise. Each generation records the column set it holds (`all` or an explicit
list) and the transitive closure of snapshot generations its rows derive from. A
generation is fresh when no recorded dependency identity now has a different current
generation; a cleared dependency does not make it stale. A writer always
continues from its own completed artifact. Under the identity's publication lock it
publishes only when every dependency it recorded is still current or cleared, its columns
contain the latest generation's columns, and there is no fresh latest generation, it widens
that generation, or it is an explicit refresh; otherwise it keeps its artifact as a
request-owned file and the outcome is `superseded`. A published generation therefore never
narrows its identity's columns, and replacing or widening a generation makes every
descendant recorded against the previous one stale.

A node-output generation is `pinned` when its slot's pin names its identity and `automatic`
otherwise; an explicit build pins, and a pin passes to the slot's newest publication. Its
last-used time is its metadata file's modification time, refreshed on lease at most once a
minute. Both explicit and automatic datasets remain until clear or replacement;
publication does not evict another slot's dataset. Node-output publication, clear, and leases are coordinated across
processes: a per-identity publication lock is held from a writer's re-check to its first
lease, and a store-wide lease lock, always taken after it, makes lease acquisition,
publication through the publisher's first lease, and retirement atomic. Every lease is also
a marker file in its generation naming the owning process's token, whose liveness is an
exclusive lock that process holds on its token file, so retirement never removes a
generation another live process reads, and a dead owner's marker is removed. Retirement
renames a generation out of selection under the lock and deletes its files afterwards.
Clear removes a slot's identities, pointers, and pin but never a generation another
operation still leases; that generation retires when released. A reader may lease a named
generation, current or not, so a spawned worker reads exactly the generation its parent
leased.

Store startup never deletes a published generation. It may reclaim a staging directory only when the
newest filesystem activity beneath that directory is older than the configured stale-build
threshold; recent or unreadable staging state is preserved. Retained staging bytes are
included in the inventory, and physical disk-headroom checks remain enforced.

Registry input capabilities advertise `scan` whenever a format has a scanner;
reader-only formats advertise `read`, and declare the derived execution mode
(`cache_mode`) for that provider/format so the editor knows whether to render the cache
control. Stored `read` configurations remain valid and executable even when the
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

File Data Output destinations are relative to the project root, independent of the
pipeline's directory and process working directory. A bare filename goes in the root's
`outputs/` folder: `output` or `output.parquet` with Parquet selected resolves to
`outputs/output.parquet`. An explicit relative path such as `exports/output.parquet`
is project-relative; an absolute path must remain inside the project. Missing extensions
come from the selected format; explicit extensions are preserved. Destination preview
and writing use the same resolution, and writing creates missing parent directories.
Database and lakehouse locators retain their provider-specific resolution.

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
Connector, cancellation, deadline, and schema failures abort staging and preserve the
previous pointer.

Automatic preparation surfaces its outcome as `InputPreparationError` with a stable reason
code (`cap_unavailable`, `build_failed`, `memory_limited`, `cancelled`, `timed_out`)
and the node's identity digest; a host that cannot install the required
native cap is refused typed rather than built without one. It is a public contract error:
synchronous routes answer HTTP 422 with its payload, and background jobs record the
`memory_limited` terminal state for `memory_limited` and the contract-error fields
otherwise. A corrupt generation is never rebuilt automatically.

When `overwrite=false`, an existing data-output destination raises
`DataOutputDestinationExistsError`; the server maps that explicit refusal to HTTP 409 rather
than treating it as an I/O failure or replacing the destination.

Malformed pointers, digest mismatches, metadata mismatches, invalid generation identifiers,
linked, hard-linked, reparse-point, or escaping generation artifacts, or invalid Parquet
footer/schema evidence raise `SourceCacheCorruptError`; a named generation that does not
exist or was retired raises its `SourceCacheGenerationMissingError` subclass. Callers do not
silently rebuild or fall back. Transient operating-system access errors propagate as
operating-system errors so operators can retry and are not told durable data is corrupt.
