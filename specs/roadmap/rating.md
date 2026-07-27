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

| Package | State | Priority | Outcome |
|---|---|---:|---|
| `RATE-01` | Queued | P2 | Reject malformed banding/rating rows consistently at every public boundary. |

## Planned improvements

### RATE-01 — Consistent malformed-config rejection

**Why:** A non-list banding `factors` value silently normalises to an empty
configuration, and the lower rating primitive accepts a populated row with no
factor even though the public config path rejects it. Both paths can turn
corruption into an unchanged frame.

**Plan:** Give public normalisers and lower execution primitives one explicit
malformed-row/type contract while preserving intentionally empty configuration
as a documented no-op.

**Acceptance:** Wrong container types and populated factorless rows raise the
same typed error through generated-code and executor paths; intentional empty
config remains a parity-tested passthrough.

**Dependencies:** Optimiser apply consumes rating-table semantics but does not
own their validation.

**Evidence:** `src/haute/_banding_config.py`, `src/haute/_rating.py`,
`src/haute/_rating_step_config.py`, `tests/test_banding.py`, and
`tests/test_rating_step.py`.

The existing miss-guard benchmark may justify a future package only if
representative evidence crosses its declared materiality threshold.
