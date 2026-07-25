# Rating roadmap

## Scope

Owns rating-key canonicalisation, Rating Step persisted configuration, factor
level agreement, and dtype-stable lookup semantics shared by rating and
optimiser apply. Current behaviour is specified in
[rating](../specs/rating/high-level.md).

## Priorities

| Package | State | Priority | Outcome |
|---|---|---|---|
| `AUD-C06` | Implemented | P0 | Python and Polars key canonicalisation is dtype-faithful across runtime, trace, save, and apply. |
| `AUD-RATING-01` | Implemented | P0 | Rating Step sidecars write one lossless canonical row shape and migrate legacy maps on read. |
| `RATING-PERF-01` | Complete — no rewrite | P2 | The representative gate triggered in only one cell, so the current miss guard remains. |

All three packages are valid improvements. The two P0 packages close
correctness gaps that could otherwise produce silent pricing disagreement or
lossy persisted configuration. The P2 package improves the decision process:
the benchmark and semantic oracle are now repeatable, but their recorded
evidence does not justify a riskier production rewrite.

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
`tests/fixtures/rating_key_cases.py`, `tests/test_rating_dtype_contract.py`,
`tests/test_rating_key_agreement.py`, and
`tests/test_optimiser_ratebook_apply_agreement.py`.

**Outcome:** Implemented. Rating keys are derived through the originating
Polars dtype, ratebook artifacts persist ordered dtype descriptors, and apply
rejects missing, malformed, unsupported, or changed dtype contracts before a
lookup can silently fall through.

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
`src/haute/_config_io.py`, `tests/test_rating_step_config_coverage.py`,
`tests/test_config_io.py`, and the rating editor utility tests.

**Outcome:** Implemented. Canonical writes use ordered entry rows; legacy
nested maps are deterministic read-only compatibility input. Validation occurs
before the generic atomic sidecar writer stages a replacement, and preserves
row metadata, scalar identity, output aliases, defaults, ordering, and valid
factor dtype metadata.

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
`tests/test_rating_miss_fail_loud.py`,
`tests/performance/test_rating_miss_guard_perf.py`, and the recorded
performance evidence below.

**Outcome:** Complete with no production rewrite. On the recorded environment,
one of eight repeated 100,000-row workload cells crossed both the 20% relative
and 10 ms absolute overhead thresholds; the decision gate requires at least
two. The benchmark remains in the performance lane so the decision can be
revisited when the evidence changes.

## RATING-PERF-01 recorded evidence

The control is the identical rating lookup plan with only the miss-guard
expression removed. It is a timing control, not a proposed implementation.

- Recorded: 2026-07-25
- Platform: Windows 10.0.26200
- Python: 3.11.13
- Polars: 1.39.3
- Peak process RSS reported by the lane: 406,921,216 bytes
- Repeats per cell: 5, compared by median
- Rows per cell: 100,000
- Command:
  `uv run python scripts/run_perf_suite.py --pytest-target tests/performance/test_rating_miss_guard_perf.py --output-dir .cache/perf/rating-miss-guard`
- Generated local artifacts:
  `.cache/perf/rating-miss-guard/perf-report.json`,
  `.cache/perf/rating-miss-guard/perf-report.md`, and JUnit XML

| Factors | Miss rate | Entry point / materialisation | Guard median | Control median | Overhead | Relative |
|---:|---:|---|---:|---:|---:|---:|
| 1 | 0% | eager / default | 1.871 ms | 1.784 ms | 0.086 ms | 4.831% |
| 1 | 0% | lazy / streaming | 6.147 ms | 4.951 ms | 1.197 ms | 24.168% |
| 1 | 50% | eager / default | 3.900 ms | 1.502 ms | 2.398 ms | 159.587% |
| 1 | 50% | lazy / streaming | 13.597 ms | 3.693 ms | 9.904 ms | 268.219% |
| 3 | 0% | eager / default | 4.265 ms | 3.072 ms | 1.193 ms | 38.836% |
| 3 | 0% | lazy / streaming | 6.092 ms | 3.765 ms | 2.327 ms | 61.821% |
| 3 | 50% | eager / default | 6.815 ms | 3.392 ms | 3.423 ms | 100.932% |
| 3 | 50% | lazy / streaming | 14.126 ms | 3.565 ms | 10.561 ms | 296.247% |

The three-factor, 50%-miss, lazy/streaming cell is the sole cell to cross both
the 20% relative and 10 ms absolute thresholds. The one-factor equivalent
remained below the absolute threshold at 9.904 ms. The predeclared two-cell
gate therefore resolves to `no_change`.

The performance test also proves that the guarded plan and timing control have
identical output values, input row order, schema, and neutral-miss nulls for
every cell. A separate fail-loud check pins the default policy to
`RatingTableMissError`, including the row count and missing-key diagnosis.
Existing rating miss-policy tests continue to cover defaults, warnings,
non-finite values, and materialisation behaviour.
