# Rating roadmap

## Scope

Owns rating-key canonicalisation, persisted Rating Step round trips, runtime
lookup behaviour, optimiser apply agreement, and evidence-gated performance
decisions.

The dtype-faithful key and lossless sidecar packages are delivered. The rating
miss-guard performance gate also completed with a no-change decision. Their
current contracts and evidence live in the rating specification, ordinary
regression tests, and the maintained performance test.

## Priorities

No active implementation packages.

## Planned improvements

There are no active rating roadmap packages.

## Delivered outcomes

- `RATE-01` gives the banding normaliser and lower rating-table boundary one
  fail-loud collection-shape contract. Non-list containers and populated
  factorless rows raise consistently through generated and executor paths,
  while genuinely empty drafts retain parity-tested passthrough semantics.

The existing miss-guard benchmark may justify a future package only if
representative evidence crosses its declared materiality threshold.
