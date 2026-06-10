# Test Suite Hardening Notes

## Purpose

This document captures the testing lesson from the optimiser scenario-expander bug and turns it into a repeatable review method for the rest of the Haute codebase.

The concern is not simply that one bug slipped through. The concern is that a codebase can have a large test suite that still misses important defects when tests assert surface behaviour, such as row counts or successful completion, rather than the deeper contracts that downstream code depends on.

The goal of the hardening pass should be to find these implicit contracts, make them explicit, and add tests that fail loudly when those contracts are broken.

## The Optimiser Expander Incident

The optimiser path depends on quote-level scenario rows being grouped contiguously:

```text
q1 scenario 0
q1 scenario 1
q1 scenario 2
q2 scenario 0
q2 scenario 1
q2 scenario 2
```

That ordering matters because price-contour builds a quote grid from the expanded data. The chunked ingestion path is more sensitive to this contract because it expects each quote's scenario rows to be grouped together when deriving per-quote grid metadata.

The bug was that the scenario expander could produce the correct number of rows and columns, but in an order that was not safe for the chunked optimiser ingestion path. This was not obvious until Haute started using `build_grid_from_parquet_chunked` by default.

## Why The Existing Tests Missed It

The existing tests did cover the scenario expander and the optimiser route, but they did not cover the specific contract that failed.

The expander unit tests checked broad shape:

- number of rows after expansion
- presence of `scenario_index`
- presence of the scenario value column

They did not assert per-quote row order.

The optimiser route tests checked that an optimiser solve completed and produced summary fields:

- job status becomes completed
- `total_objective` exists
- `lambdas` exists
- `n_steps` matches the configured number of scenarios

Before the recent optimiser changes, the route did not default to chunked price-contour ingestion when no chunk size was explicitly persisted. That meant the route test did not exercise the same failure mode as the real memory-efficient path.

The missing test was not another generic "does it run" test. The missing test was a contract test:

```text
After scenario expansion, every quote_id must appear in one contiguous block, and each block must contain exactly the configured scenario indices.
```

## The Broader Risk Pattern

This is a common testing failure mode in data-heavy applications:

1. A node produces data with the right schema and row count.
2. A downstream component depends on an ordering, dtype, null, cardinality, or uniqueness invariant.
3. The invariant is not documented as a contract.
4. Tests check the schema or happy path output, not the invariant.
5. A refactor changes execution mode, streaming behaviour, chunking, or library implementation detail.
6. The bug appears only in an integrated path and is hard to reason about after the fact.

These bugs are usually quiet and expensive. The system may still run, but results can be wrong, stale, incomplete, or subtly mismatched to what the UI shows.

## What Good Tests Should Assert

For critical data paths, tests should assert contracts in addition to outputs.

Useful contract categories:

- **Ordering:** rows are grouped or sorted where downstream code requires it.
- **Cardinality:** expected one row per quote, one block per quote, or fixed scenarios per quote.
- **Uniqueness:** keys are unique at the boundary where uniqueness is assumed.
- **Completeness:** every expected quote, factor, scenario, metric, or constraint is present.
- **Dtypes:** numeric columns use expected precision, IDs use supported string-like dtypes, scenario indices are integer.
- **Null handling:** missing values are rejected, propagated, or filled deliberately.
- **Chunk parity:** chunked and unchunked execution produce equivalent results.
- **Streaming parity:** streamed parquet output preserves the contracts required by downstream consumers.
- **State freshness:** selected UI state does not keep stale diagnostics from a previous solve or frontier point.
- **Artifact correctness:** saved, applied, and logged artifacts correspond to the selected result, not the original baseline result.

## Optimiser Hardening Priorities

The optimiser should be treated as a high-risk area because it combines lazy execution, parquet checkpointing, Rust ingestion, numerical optimisation, and UI state.

Priority contracts to test:

1. **Scenario expansion**
   - Expanded rows are quote-contiguous.
   - Each quote has exactly the configured scenario indices.
   - Scenario values are stable and dtype-compatible with price-contour.
   - Streaming parquet output preserves the same contract as collected output.

2. **Optimiser projection**
   - Only required columns are materialised for price-contour.
   - `quote_id`, objective, constraints, scenario index, and scenario value have expected dtypes.
   - Missing required columns fail loudly with useful errors.
   - Unsupported `quote_id` dtypes fail loudly, while Utf8 and Categorical work.

3. **Chunked grid build**
   - Default path uses chunked ingestion.
   - Explicit valid boundary sizes such as `n_steps`, non-multiples greater than `n_steps`, exactly one quote block, and larger-than-data all work.
   - Too-small chunk sizes fail loudly unless the data has one-step quotes.
   - Chunked and unchunked builds produce equivalent quote grids on deterministic data.
   - Valid chunk sizes that split quote blocks across chunk boundaries still produce correct results.

4. **Solve correctness**
   - Selected scenario indices are valid for every quote.
   - Objective and constraint totals equal sums over selected scenario rows.
   - Re-solving the same deterministic input gives stable results within tolerance.
   - Absolute constraints are interpreted as absolute values, with no hidden baseline multiplier behaviour.

5. **Efficient frontier**
   - Frontier point summaries match the lambdas and totals returned by price-contour.
   - Selecting a point updates only point-specific fields.
   - Missing point-specific diagnostics are cleared rather than inherited from another point.
   - Applying, saving, and logging a selected point materialises artifacts for that point.
   - Ratebook frontier paths either produce complete selected-point artifacts or fail loudly.

6. **Auto range**
   - Calculated min and max values match per-quote scenario extrema for online optimisation.
   - The auto range handles constraints whose extrema occur in middle scenarios.
   - The implementation does not rely on baseline values.
   - The calculation avoids materialising unnecessary columns.

7. **UI workflow**
   - Add constraint, choose individual point, fill min/max, solve.
   - Add constraint, choose efficient frontier, auto range, solve frontier.
   - Select frontier point and verify visible summary, charts, apply, save, and MLflow actions use selected point data.
   - Missing required values are highlighted and prevent invalid solve requests.

## Whole-Codebase Hardening Method

Use the optimiser incident as the template for reviewing other areas.

For each subsystem, answer these questions:

1. What data leaves this boundary?
2. What does the next component silently assume about that data?
3. Are those assumptions documented in code, types, validation, or tests?
4. Does the test suite exercise the same execution mode users rely on?
5. Does it test streaming/chunked/lazy paths, or only collected in-memory data?
6. Does it test failure cases and edge cases, or mostly happy paths?
7. Could stale state, cached artifacts, or partial results be reused incorrectly?
8. Are tests using mocks where a real integration test is needed?

The output of the review should be a list of explicit contracts and the tests that enforce them.

## Suggested Test Types

### Contract Tests

Small deterministic tests that assert invariants at module boundaries.

Example:

```python
def assert_quote_contiguous(df: pl.DataFrame, quote_col: str, scenario_col: str) -> None:
    seen: set[str] = set()
    active_quote: str | None = None
    active_indices: list[int] = []

    for quote_id, scenario_index in df.select(quote_col, scenario_col).iter_rows():
        if quote_id != active_quote:
            if active_quote is not None:
                assert active_indices == list(range(len(active_indices)))
                seen.add(active_quote)
            assert quote_id not in seen
            active_quote = quote_id
            active_indices = []
        active_indices.append(scenario_index)

    if active_quote is not None:
        assert active_indices == list(range(len(active_indices)))
```

### Parity Tests

Run the same deterministic input through two paths and compare the business result.

Useful pairs:

- collected vs streamed
- chunked vs unchunked
- valid boundary chunk sizes such as `n_steps`, non-multiples greater than `n_steps`, one quote block, and larger-than-data
- Utf8 `quote_id` vs Categorical `quote_id`
- API route result vs direct service result
- selected frontier point vs materialised selected-point artifact

### Property-Style Tests

Generate many small valid datasets and assert invariants that should always hold.

Useful optimiser variations:

- quote count from 1 to 50
- scenario count from 1 to 20
- repeated and unusual quote IDs
- constraints with min only, max only, and both
- objective and constraint values whose extrema occur at middle scenarios
- valid chunk sizes (`>= n_steps`) that split quote blocks in awkward places, plus undersized chunks as failure-mode cases

These do not need to be huge. Small random datasets often find the bug class more effectively than one large fixture.

### Failure-Mode Tests

Tests should prove the system fails loudly when contracts are violated.

Examples:

- missing objective column
- unsupported `quote_id` dtype
- duplicate quote IDs in factor tables
- invalid chunk size
- missing frontier ranges
- selected frontier point without lambdas where lambdas are required for materialisation

Avoid hidden fallbacks in these areas. A clear error is much better than a plausible but incorrect output.

### End-to-End Workflow Tests

Use sparingly, but they are important for high-risk workflows.

For optimiser:

```text
configure input -> expand scenarios -> solve -> generate frontier -> select point -> apply -> save/log artifact
```

The assertions should check the data attached to each action, not just HTTP status codes.

## Signals That A Test Is Too Weak

A test may be too weak if it only asserts:

- response status is 200
- a job reached completed
- a dataframe has the expected number of rows
- a column exists
- a value is not null
- a mocked function was called
- a component renders without throwing

Those checks are still useful, but they should usually be paired with contract assertions when the path is important.

## Review Checklist For Each Module

Use this checklist during the wider codebase review:

- Are boundary contracts named and tested?
- Are dtypes tested where external libraries or Rust paths depend on them?
- Are row ordering assumptions tested?
- Are uniqueness assumptions tested?
- Are null and missing-column behaviours tested?
- Are lazy, streaming, and parquet checkpoint paths tested?
- Are cache invalidation and stale state behaviours tested?
- Are artifact outputs tested for content, not only existence?
- Are mocks hiding integration risks?
- Are failure messages specific enough for a user or developer to fix the issue?
- Are there old defaults or compatibility behaviours that now conflict with current product rules?

## Practical Roadmap

### Phase 1: Optimiser

Add contract tests around scenario expansion, optimiser projection, chunked grid build, selected frontier points, and artifacts. This should be completed before expanding the review because optimiser bugs are high impact and can be hard to detect visually.

### Phase 2: Execution Engine

Review lazy execution, checkpointing, preview caching, graph fingerprints, and node boundary contracts. Focus on whether collected and checkpointed paths behave equivalently where they need to.

### Phase 3: Data Transformation Nodes

Review joins, banding, rating steps, live switches, model scoring, and custom Polars nodes. Focus on ordering, cardinality, dtype, and null contracts.

### Phase 4: Persistence And Artifacts

Review config schema mappings, save/load round trips, MLflow logging, deployed pipeline outputs, and parquet/json artifacts. Focus on whether saved artifacts can be trusted as complete representations of the selected state.

### Phase 5: Frontend State

Review stores, panels, charts, and long-running job state. Focus on stale state, partial updates, selected-item consistency, and workflows that change mode.

## Standard For New Fixes

For each hardening item:

1. Write the failing test first.
2. Make the smallest implementation change that satisfies the contract.
3. Add edge-case coverage around the bug class, not just the exact reproduction.
4. Run focused tests and the relevant integration slice.
5. Review whether the test would fail for the original defect.

The most important question after every fix is:

```text
Would this test have caught the bug before we knew what the bug was?
```

If the answer is no, the test is probably asserting too close to the implementation and not close enough to the contract.
