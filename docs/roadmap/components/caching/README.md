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
evidence. Audit C3 was retired after artifact bytes, imported utility modules,
and model contracts became content-addressed cache inputs. Audit C11 was
retired after nanosecond/size freshness and locked atomic cache replacement
were covered by ordinary tests. Re-run the relevant cache repro or write a
failing invariant test before implementation. Retire a package only when key
completeness and replacement/lifetime behaviour are encoded in the
[caching specs](../../../specs/caching/low-level.md) and ordinary tests.
