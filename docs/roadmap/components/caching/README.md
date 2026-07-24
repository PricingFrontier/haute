# Caching improvement backlog

## Scope

Owns generic fingerprint completeness, cache invalidation and eviction,
artifact lifetime, preview/trace cache identity, and concurrency at shared
cache boundaries. JSON input-cache semantics remain owned by
[I/O layer](../io-layer/README.md). Current contracts live in the
[caching specification](../../../specs/caching/high-level.md).

## Work queue

| Package | State | Priority | Candidate outcome | Source |
|---|---|---|---|---|
| AUD-C03 | Reverify | P0 | Make artifact, utility-module, and model-contract identity explicit inputs to every output-affecting cache key. | [Audit cluster C3](../../../review/REMEDIATION-PLAN.md#c3-cache-key-completeness-model-artifact--preamble-module-identity-excluded-from-fingerprints) |
| AUD-C11 | Reverify | P0 | Remove coarse mtime and mirror-copy races from committed cache freshness and replacement. | [Audit cluster C11](../../../review/REMEDIATION-PLAN.md#c11-cache-mtime-bucket-coarseness--mirror-lockrename-concurrency-committed-cache-layer) |
| AUD-CACHE-01 | Reverify | P1 | Replace ad-hoc key audits with one checked fingerprint-completeness registry and injectivity suite. | [Highest-standard invariant A2](../../../review/PATH_TO_HIGHEST_STANDARD.md#a2-one-source-of-truth-for-cache-key-completeness) |

## Dependencies

- [Deploy and platform](../deploy-platform/README.md) consumes model-artifact
  identity; it must not define a competing fingerprint.
- [I/O layer](../io-layer/README.md) owns JSON-shred conservation and
  per-port cache validation.
- [Tracing](../tracing-explainability/README.md) and the preview path consume
  cache identity but do not own eviction or key composition.

## Evidence and retirement

The component page owns package state; the audit plans are point-in-time
evidence. Re-run the relevant cache repro or write a failing invariant test
before implementation. Retire a package only when key completeness and
replacement/lifetime behaviour are encoded in the
[caching specs](../../../specs/caching/low-level.md) and ordinary tests.
