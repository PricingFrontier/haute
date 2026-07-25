# Caching roadmap

## Scope

Owns generic cache-key composition, invalidation, eviction, artifact lifetime,
and concurrency at shared cache boundaries. JSON source-cache semantics remain
with [I/O](io-layer.md); deploy and tracing consume the shared identity
contracts. Current behaviour is specified in
[caching](../specs/caching/high-level.md).

## Priorities

There are no active caching improvement packages.

## Planned improvements

No further caching improvement is planned from the completed audit and
measurement pass. New work should be added only with a reproduced correctness
gap or a comparative performance candidate.

## Delivered outcomes

- Checked fingerprint contracts and a reflective config-field registry now
  cover graph, preview/trace, dataframe, runtime-input, deploy-schema,
  model-contract, and input-snapshot identities. The present-tense contract and
  regression evidence live in [the caching specification](../specs/caching/high-level.md).
- Repeated node, edge, runtime-source, and selected-switch identity records use
  closed versioned shapes, and stat-gated artifact caches have bounded LRU
  retention without weakening same-key single flight.
- The cache-identity performance lane records its environment, workload,
  artifact, 20% materiality threshold, and decision. It accepted the versioned
  canonical UInt64 row-hash buffer and recorded no-change decisions for
  LRU/stat hot-path operations and cross-request lineage memoisation.
  See [Local Performance Checks](../PERFORMANCE_CHECKS.md).
