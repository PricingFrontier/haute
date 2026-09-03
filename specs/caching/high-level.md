# Caching — High-Level Specification

## Purpose

Caching avoids repeated graph hashing, artifact loading, dataframe execution, and JSON
shredding while preserving correctness when configuration or backing files change.
Cache identities are explicit, versioned contracts rather than ad-hoc object hashes.

## Scope

In scope are canonical JSON and checked cache-input contracts, graph and lineage keys,
bounded in-process LRU/stat-gated caches, Parquet-backed dataframe execution artifacts,
and JSON-to-Parquet cache routes.

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

Eight maintained consumers — graph structure, graph execution, preview/trace,
dataframe execution, runtime graph input, deploy schema, model contract, and
input snapshot — each declare one complete versioned field set. Every logical
input class is either mapped to named fields or excluded with a rationale;
missing, unknown, or unclassified fields fail before a key is produced. The
low-level inventory is the reviewable source for those exact sets and their
nested record shapes.

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
status, infer, build, and delete; there is no cancel endpoint because the underlying
blocking shred is not cooperatively cancellable.

Source snapshot identities use the same canonical checked-input discipline. Their storage,
lease, and publication behaviour is specified by the IO layer. A published
current generation is durable until the user refreshes or clears that Data
Input, or until an execution's automatic preparation refreshes a stale one
(warned and recorded, never silently). Quota pressure rejects the incoming build with an actionable error; it
never silently evicts another input's current snapshot. Users must clear an
unused snapshot or raise the configured quota before retrying.

Before Studio sends a preview that uses snapshot-backed Data Inputs, it checks
each required snapshot through the existing status endpoint. A missing,
corrupt, failed, or already-building snapshot starts or joins the existing
visible job, waits for completion, and only then sends the preview. The
orchestrator tries the lazy-sink build profile first and retries once with the
admitted eager profile only when the server reports
`snapshot_build_unsupported`. A ready but stale snapshot is refreshed by the execution's
automatic preparation before it runs — warned before and recorded in the terminal
diagnostics, never silently — so Studio no longer prompts for it; the explicit refresh
action remains available.
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
timeouts return 504; unexpected failures are logged and return a generic 500.
