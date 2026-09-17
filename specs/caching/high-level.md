# Caching — High-Level Specification

## Purpose

Caching avoids repeated graph hashing, artifact loading, dataframe execution, and JSON
shredding while preserving correctness when configuration or backing files change.
Cache identities are explicit, versioned contracts rather than ad-hoc object hashes.

## Scope

In scope are canonical JSON and checked cache-input contracts, graph and lineage keys,
bounded in-process LRU/stat-gated caches, Parquet-backed dataframe execution artifacts,
JSON-to-Parquet cache routes, and data points: the mapping from a consumer node to the
data it reads and leased reads of that data.

The [IO layer](../io-layer/high-level.md) primarily owns shared source-cache storage;
caching consumes its identity/generation contract. Execution owns runtime-path
fingerprint call sites and the temporary dataframe-cache root lifecycle. JSON shredding
owns the per-port transformation and metadata format.

## Behaviour

Graph fingerprints and preview/trace lineage keys include every execution-relevant graph,
utility-file, runtime switch, upstream lineage, output port, and algorithm-version input
defined by their checked consumer contract. Presentation-only fields are explicitly
classified and excluded. `lineage_cache_key()` is the common preview/trace factory; callers
do not key directly from `graph_fingerprint()` alone.

Nine maintained consumers — graph structure, graph execution, preview/trace,
dataframe execution, runtime graph input, deploy schema, model contract, input
snapshot, and node-snapshot signature — each declare one complete versioned field set.
The node-snapshot signature identifies the data at a pipeline point independently of
any consumer: upstream lineage, runtime inputs, source, execution semantics class,
contract enforcement, preamble presence, and execution semantics version. Every logical
input class is either mapped to named fields or excluded with a rationale;
missing, unknown, or unclassified fields fail before a key is produced. The
low-level inventory is the reviewable source for those exact sets and their
nested record shapes.

A data point is the data at one pipeline location — a producer node and, for an
`apiInput`, a port — independent of the consumer asking for it. A Banding or Rating
Step node reads its single incoming connection's point with a demand of its banded or
raw factor columns; a blank-code Explore node reads its input's point with every
column; any other node reads its own output. A consumer without exactly one incoming
connection is `node_data_point_invalid`. Each point has one kind: a Data Input with
blank post-load code is `data_input`, an `apiInput` port is `api_input_table`, and every
other producer — including a Data Input with post-load code, whose code may sample or
depend on time — is `node_output`. A direct-Parquet Data Input is always `current`; a
snapshot-backed one follows its input snapshot (fresh or unknown freshness is
`current`); an `apiInput` port is `current` when its working or committed JSON table
cache serves the node's full schema; a node output follows its node-output slot, and a
fresh generation that lacks demanded columns is `partial`. A point with a running build
that is not current is `building`. Each current point has a data version: the source
file or snapshot generation, the serving cache metadata and port, or the node-output
generation, together with the producer's lineage fingerprint for source kinds.
A consumer reads a point only when it is `current` for its demand; otherwise it gets
`cache_required` with the state. Source kinds are read by lazily executing that single
source node with input preparation disabled and the API-input loader in cache-only mode,
so selections and renames apply exactly as in a run and a read never builds a snapshot
or shreds JSON. The read holds its lease for the caller's whole operation, including
final collection, and a spawned child reads exactly the generation its parent leased.

`LRUCache` bounds entries, optionally bounds bytes/TTL, and supports pins. Rejecting an
oversized value leaves an existing same-key entry intact. `StatGatedCache` is bounded by an
entry count, uses `(mtime_ns, size)` gates, provides per-key single flight, and evicts least
recently used entries.

The dataframe execution cache stores validated Parquet artifacts. Independent hits validate
the artifact before returning a scan. The writer's first consume uses
`scan_stored_entry()` for the exact entry just validated and stored, deliberately avoiding a
second corruption validation. Live scans pin artifacts; replacement or eviction unlinks
only after the final scan releases them.

An oversized dataframe artifact is rejected without evicting or unlinking an existing
same-key entry. The newly produced oversized path is the caller's responsibility and is
cleaned by the materialization wrapper.

Structured API-input cache build (implemented by the `json_cache` route module) accepts
JSON, JSONL, NDJSON, and XML sources. It selects and validates schema before checking data-file existence, so an
absent schema returns structured 422 before a missing-file 404. Builds expose progress,
status, infer, build, and delete (removing the `working/` layer only while leaving
`committed/` intact); no cancel endpoint is exposed; the build is cancelled
cooperatively by request cancellation through the isolated-worker cancellation gate,
which stops the worker and discards staging.

Source snapshot identities use the same canonical checked-input discipline. Their storage,
lease, and publication behaviour is specified by the IO layer. A published
current generation is durable until the user refreshes or clears that Data
Input, or until an execution's automatic preparation refreshes a stale one
(warned and recorded, never silently); an absent source never retires it, and a host that
cannot install the cap reuses a ready-but-stale generation with warning code
`cap_unavailable_stale_reused` — only a missing generation is refused typed
(`cap_unavailable`). A superseded generation is itself retired only after
`HAUTE_INPUT_CACHE_RETIRE_GRACE_SECONDS` (default 1800) have elapsed since the current
generation was published, because leases are process-local; an explicit clear and quota
pressure reclaim immediately, the latter logged. Quota pressure rejects the incoming build with an actionable error; it
never silently evicts another input's current snapshot. Users must clear an
unused snapshot or raise the configured quota before retrying.

Studio also prepares structured Quote Inputs (JSON/JSONL/NDJSON/XML) before
preview. It checks the existing working/committed cache against the current
in-memory schema, builds a missing or invalid full cache through the existing
JSON-cache build endpoint, and awaits publication before execution. The preview
panel shows cache preparation and elapsed build time. Build or status failures
stop preview with an actionable error; cancellation prevents stale progress or
late execution. A valid cache is reused without rebuilding. Progress reporting
is presentational only: a failed progress poll stops further progress updates
without failing the preparation — the build outcome alone decides it.

Before Studio sends a preview that uses snapshot-backed Data Inputs, it checks
each required snapshot through the existing status endpoint. A missing,
corrupt, failed, or already-building snapshot starts or joins the existing
visible job, waits for completion, and only then sends the preview. The
orchestrator tries the lazy-sink build profile first and retries once with the
admitted eager profile only when the server reports
`snapshot_build_unsupported`. A ready but stale snapshot is refreshed by the execution's
automatic preparation before it runs, warned beforehand and recorded in the terminal
diagnostics. A missing, corrupt, failed, or building snapshot is built (or its running
build joined) before the run, announced by an info toast rather than a prompt, and the
explicit refresh action remains available.
File-backed Parquet inputs do not participate because they scan their Parquet
source directly and expose no cache action. Snapshot execution contacts the
provider only through automatic preparation under an admitted execution
context; schema-only or unadmitted callers receive the IO layer's explicit
`input_snapshot_missing:` error when a required snapshot is missing. Execution
caches key a snapshot-backed input by its generation pointer and its current
source signature, so a rewritten source misses every cache.

## Design rationale

Exact input contracts make omissions reviewable and fail loudly on drift. Versioned keys
allow intentional invalidation. LRU and byte bounds prevent process caches becoming
unbounded. Stat gates avoid hashing/loading unchanged artifacts while accepting the
documented limitation that same-size, same-mtime rewrites are below the gate.

Parquet artifacts move large dataframe values out of Python memory. Separate ordinary-hit
and first-consume paths preserve both corruption detection and the atomic store-to-first-scan
window.

## Interactions

- [Execution engine](../execution-engine/high-level.md), tracing, and executor construct
  lineage requests and own temporary execution-cache roots.
- [IO layer](../io-layer/high-level.md) consumes canonical identity helpers and owns
  `_source_cache.py`.
- Deploy scoring and modelling feature contracts instantiate `StatGatedCache`; `src/haute/_cache.py`
  instantiates the utility-file hash cache.
- Execution currently has a separate `StatGatedCache` instance for runtime-path
  fingerprints; the shared class supplies its bound and single-flight behaviour.
- [JSON shredding](../json-shredding/high-level.md) owns cache content generation.

## Failure model

Unknown/missing checked inputs and unclassified config fields fail key construction.
Stat/loader errors propagate and failed values are not cached; a gate that moves twice
during load raises. Missing/corrupt dataframe artifacts are evicted on ordinary lookup and
reported as misses, while unexpected filesystem unlink failures propagate.

Oversized dataframe artifacts raise `CacheArtifactTooLargeError` without disturbing a
previous same-key entry. JSON schema and parse failures return structured 4xx responses;
source modification during build or stopped workers return 409; memory-limit exhaustion,
unsupported caps, and admission rejections return 507; timeouts return 504; unexpected
failures are logged and return a generic 500.

## Approved change contract — one shared layer for full-data materialisations

- **Current limitation.** Each dataframe-cache consumer puts its own namespace, profile, and
  execution policy in its key, so the same pipeline point is recomputed and stored once per
  consumer, only in the process-local cache; runs also write temporary checkpoints that are deleted
  afterwards. Explore alone persists its data, keyed by the Explore node.
- **Unresolved target.** Every bounded execution,
  explicit build, and admitted preview writes the full-data materialisations it performs into this
  layer and reads from it. Analysis results are stored by point identity and data version, so a
  refreshed or widened generation never serves a previous generation's results.
- **Non-goals.** Stat-gated caches, the preview response cache, and deploy scoring's process-local
  dataframe cache are unchanged. The preview/trace lineage key keeps its field set; only its runtime
  input fingerprint gains the generations of any snapshots a preview or trace seeds from.
- **Failure and compatibility semantics.** The private dataframe-cache namespaces
  for training preparation, training evaluation preview, optimiser setup, and Data Output are
  removed without migration.
- **Acceptance evidence.** Frontend tests for two consumers of one point sharing a job and
  deriving their own availability from one slot entry.
- **Roadmap package.** [CACHE-S05](../roadmap/caching.md#cache-s05--shared-frontend-data-cache-hook-and-button).
