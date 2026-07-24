# Rating improvement backlog

## Scope

Owns rating-key canonicalisation, Rating Step configuration compaction,
ratebook factor-level agreement, and dtype-stable lookup semantics shared by
rating and optimiser apply. Current behaviour lives in the
[rating specification](../../../specs/rating/high-level.md).

## Work queue

| Package | State | Priority | Candidate outcome | Source |
|---|---|---|---|---|
| AUD-C06 | Reverify | P0 | Make Python and Polars key canonicalisation dtype-faithful and stable across ratebook save/apply. | [Audit cluster C6](../../../review/REMEDIATION-PLAN.md#c6-rating-key-python-mirror-vs-polars-twin-dtype-divergence-silent-neutraldefault-miss) |
| AUD-RATING-01 | Reverify | P0 | Make Rating Step sidecar compaction/expansion preserve every accepted table shape or fail loudly. | [Re-verification Wave 4](../../../review/06-reverification/REPORT.md#wave-4--rating-key--trace-correlation-fidelity--4-items) |

## Dependencies

- [Optimiser](../optimiser/README.md) owns ratebook solve/save/apply workflows
  but consumes this component's canonical key contract.
- [Engineering quality](../engineering-quality/README.md) owns the shared
  property/oracle convention and `ROAD-TEST-03`; this component owns the
  rating semantics that evidence proves.
- Any frontend table/editor change remains owned by
  [Frontend and canvas](../frontend-canvas/README.md) unless it changes rating
  behaviour.

## Evidence and retirement

Reverify the cited Float32, save/apply-dtype, and sidecar cases against real
Polars and current persisted shapes. Retire only after the canonical key and
round-trip invariants are in the rating specs and are exercised across the
runtime, trace, and optimiser consumers.
