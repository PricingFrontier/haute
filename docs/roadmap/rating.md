# Rating roadmap

## Scope

Owns rating-key canonicalisation, Rating Step persisted configuration, factor
level agreement, and dtype-stable lookup semantics shared by rating and
optimiser apply. Current behaviour is specified in
[rating](../specs/rating/high-level.md).

## Priorities

| Package | State | Priority | Outcome |
|---|---|---|---|
| `AUD-C06` | Reverify | P0 | Make Python and Polars key canonicalisation dtype-faithful across trace, save, and apply. |
| `AUD-RATING-01` | Reverify | P0 | Make Rating Step sidecar compaction/expansion lossless for every accepted table shape. |
| `RATING-PERF-01` | Decision | P2 | Benchmark the row-miss guard before replacing its callback/struct implementation. |

## Planned improvements

### AUD-C06 — Dtype-faithful rating keys

**Why:** A nominally identical factor can acquire different string keys when
Python widens Float32 or when save and apply observe different column dtypes.
A mismatch silently selects the neutral/default rate.

**Plan:**

- Define the canonical key in terms of the originating Polars dtype and use
  the same primitive for Python/trace values and Polars expressions.
- Carry factor dtype through trace and persisted optimiser artifacts.
- At apply, either canonicalise through the saved dtype or reject a save/apply
  dtype mismatch before lookup.
- Include null, NaN/inf, integer-like float, decimal, categorical, temporal,
  and string cases in the contract.

**Acceptance:**

- A real-Polars differential matrix proves Python and expression keys agree
  for Float32/64, signed/unsigned integers, Boolean, String, date/time, null,
  and non-finite values.
- Ratebook save then apply cannot turn the same nominal factor into a silent
  neutral miss across dtype boundaries.
- Runtime, trace, and optimiser consumers share the same fixtures and failure
  semantics.

**Dependencies:** [Optimiser](optimiser.md) owns artifact/save/apply workflow;
this component owns canonical key semantics.

**Evidence:** `src/haute/_rating.py`, `src/haute/_builders.py`,
`src/haute/routes/_optimiser_service.py`, `tests/test_rating.py`,
`tests/test_rating_key_properties.py`, and `tests/test_optimiser_routes.py`.

### AUD-RATING-01 — Rating Step sidecar round trips

**Why:** Compaction can produce a persisted shape that expansion interprets
differently or cannot reconstruct, losing accepted table configuration.

**Plan:**

- Enumerate every accepted in-memory table form and one canonical sidecar form.
- Make compaction and expansion true inverses for representable input.
- Reject duplicate, incomplete, ambiguous, or non-finite table shapes before
  writing rather than emitting a sidecar that fails later.
- Preserve table identity, factor columns, outputs, defaults, ordering, and
  dtype metadata.

**Acceptance:**

- Property tests prove `expand(compact(config))` is semantically identical for
  the complete accepted-shape matrix.
- Canonical sidecars are deterministic under map insertion order.
- Invalid shapes fail with the table/field named and leave the prior sidecar
  untouched.
- Parser, editor save, codegen, runtime, and optimiser fixtures consume the
  same canonical representation.

**Dependencies:** Pipeline authoring owns generic sidecar I/O; rating owns this
component-specific codec.

**Evidence:** `src/haute/_rating_step_config.py`,
`src/haute/_config_io.py`, `tests/test_rating_step_config.py`,
`tests/test_parser_roundtrip.py`, and `tests/test_serialization_invariants.py`.

### RATING-PERF-01 — Evidence-gated miss guard

**Why:** Replacing the rating lookup miss guard may reduce callback/struct
overhead, but it also touches a fail-loud pricing boundary. The change was
deliberately deferred until representative evidence shows the cost is material.

**Plan:** Benchmark the current guard on representative row counts, factor
widths, hit/miss rates, and lazy/eager execution. Design an alternative only
after the workload and semantic oracle are fixed.

**Acceptance:** The gate records workload, environment, artifact, and an
implement/no-change decision. Any accepted rewrite preserves missing-factor,
missing-entry, explicit-default, null/non-finite, ordering, schema, and
lazy/eager behaviour.

**Dependencies:** `AUD-C06` fixes key semantics before performance comparison.

**Evidence:** `src/haute/_rating.py`, `tests/test_rating.py`,
`tests/test_rating_miss_fail_loud.py`, and `tests/performance/`.
