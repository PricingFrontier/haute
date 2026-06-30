# Dataframe Execution Cache Design

Status: implemented
Owner: backend execution workstream
Last updated: 2026-05-12

## Problem

Backend execution paths can repeat the same expensive lazy pipeline setup when a user moves between closely related actions. For example, an optimiser setup action may materialize the same upstream dataframe that the next solve setup needs. Preview already has fingerprinted caching behaviour, but backend training, sink, deploy, and optimiser preparation did not have a shared materialized-dataframe cache.

The cache must preserve Haute's single execution engine rule: callers should still execute through the same graph executor, with cache reuse acting as an internal optimization rather than a second implementation of pipeline semantics.

## Approach

The execution facade builds explicit `DataFrameExecutionCacheRequest` objects for backend callers that can safely reuse materialized node outputs. Each request carries:

- cache keys derived from the node upstream graph fingerprint, source, execution profile, required columns, runtime input fingerprints, and lazy execution policy;
- a shared parquet-backed `DataFrameExecutionCache`;
- bounded-sink settings used by the normal lazy execution path.

`_execute_lazy` validates cache keys against the current graph and execution policy before reading or writing. A cache hit seeds the requested node with `pl.scan_parquet`, skips covered upstream nodes, and continues the normal downstream lazy execution. A miss builds the graph normally and materializes configured cache nodes through the same bounded sink used for checkpoints.

Runtime input fingerprints include content hashes for local files that affect source-like nodes, model-scoring nodes, optimiser apply nodes, deploy artifact remaps, and in-memory deploy dataframes. This prevents stale reuse when a file changes without changing the pipeline graph.

The cache is disk-backed, LRU by entry count, and process-local. It defaults to no byte cap because the intended value is avoiding repeated work for large dataframes. Operators can opt into a byte cap with `HAUTE_DATAFRAME_EXECUTION_CACHE_MAX_BYTES`.

## Alternatives Considered

### Reuse temporary checkpoint files directly

Lazy execution already writes temporary checkpoints to break large Polars plans. Reusing those files as cache artifacts would avoid an extra sink in some cases, but checkpoint shape is driven by execution safety, projection, and plan structure rather than a stable cache contract. Tying cache identity to checkpoint internals would make the executor harder to reason about and risk stale or too-narrow reuse.

### Keep cached dataframes in memory

An in-memory cache would avoid parquet scan overhead, but it works against the main use case: large setup frames. Disk-backed parquet keeps process memory pressure lower and matches the rest of the lazy engine's checkpointing strategy.

### Add optimiser-specific reuse

The original pain point appeared in optimiser setup, but a route-specific cache would create a parallel execution path and repeat the same invalidation logic in other backend callers. The implemented cache lives in the execution engine and is consumed by optimiser, training, sink, and deploy paths through shared request builders.

## Open Questions

- Cold misses currently materialize the terminal cached frame and then continue downstream from a parquet scan. This is robust, but for some terminal-node callers it can duplicate sink work. A future improvement could let terminal callers consume the freshly written artifact directly when that does not create a second execution path.
- Cache entries are invalidated by key changes and LRU eviction, not by a global disk budget by default. If real projects need stronger disk controls, add explicit operator configuration rather than silently skipping the cache.
- Auto-range can warm solve setup only when the cache request includes the columns the later solve needs. Narrow projected cache requests intentionally do not masquerade as broader reusable artifacts.
