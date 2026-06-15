# Price Contour Ratebook Factor Context Plan

## Goal

Allow `price-contour` ratebook solves to consume ratebook factor assignments without requiring Haute to materialise the full per-quote factor table as an in-memory `polars.DataFrame`.

Today Haute can project, stage, and budget the factor source, but the final `price-contour` API still requires:

```python
solver.solve(quote_grid, factors_df)
solver.frontier(quote_grid, factors_df, ...)
```

where `factors_df` is a full in-memory `pl.DataFrame` containing one row per quote and one or more configured factor columns.

The target shape is:

```python
factor_contexts = build_ratebook_factor_contexts_from_parquet_chunked(
    "ratebook_factors.parquet",
    factor_specs=[["age_band"], ["vehicle_age_band"], ["channel_band"]],
    chunk_size=500_000,
    quote_id="quote_id",
    expected_quote_ids=quote_grid.quote_ids,
    expected_n_quotes=quote_grid.n_quotes,
)

solver.solve(quote_grid, factor_contexts)
solver.frontier(quote_grid, factor_contexts, ...)
```

This lets Haute pass a persisted parquet artifact handle to `price-contour` and avoid collecting the factor table back into Python memory.

## Non-Negotiable Decisions

These are firm implementation decisions, not optional design branches.

1. Quote order must be proven, not assumed.

   `QuoteGridBuilder.build()` sorts by `quote_id`. If factor contexts are built in source row order without validation and reorder, factor groups can silently attach to the wrong quote. The new contexts path must validate quote IDs, reorder to the grid quote axis when possible, and fail loudly when alignment cannot be proven.

2. `RatebookFactorContexts` must be opaque.

   It should be a Rust-backed `#[pyclass]` that owns the private factor mappings internally. It should expose read-only metadata, not a public `contexts: list[FactorContext]` field. `FactorContext` should remain private unless it is intentionally promoted in a separate API decision.

3. The public construction surface should stay small.

   Expose `RatebookFactorContexts.from_dataframe(...)` and `build_ratebook_factor_contexts_from_parquet_chunked(...)`. Keep `FactorContextBuilder` internal to Rust. Do not ship a public builder until there is a concrete external consumer.

4. Use one internal factor-context algorithm.

   Both dataframe and parquet construction should route through the same internal builder. Existing `build_factor_contexts_py(...)` can become a compatibility shim around the new builder.

5. Remove `_factor_contexts_override`.

   `frontier(...)` should build a `RatebookFactorContexts` once and pass it through the normal `factors` argument to each `solve(...)` call. This gives one code path and dogfoods the public context object.

6. Solve-time alignment validation must be O(1).

   `QuoteGrid.quote_ids` currently clones the full `Vec<String>` when read from Python. Do not validate by cloning and comparing all quote IDs on every solve. Store a 64-bit quote-id fingerprint on both `QuoteGrid` and `RatebookFactorContexts`; compare the fingerprints in `solve(...)`.

7. Label ordering must match dataframe mode exactly.

   Current `build_group_mapping(...)` assigns group IDs in first-seen order. The chunked path must reproduce that behaviour after quote-order reorder. See "Deterministic Label Remap" below.

## Current Price Contour Shape

Observed current API:

```python
RatebookOptimiser.solve(
    df_or_grid: pl.DataFrame | QuoteGrid,
    factors: pl.DataFrame,
    *,
    factor_columns: list[list[str]] | None = None,
    lambdas: dict[str, float] | None = None,
    _constraints_override: dict[str, dict[str, float]] | None = None,
    _factor_contexts_override: list[FactorContext] | None = None,
) -> RatebookResult
```

```python
RatebookOptimiser.frontier(
    df_or_grid: pl.DataFrame | QuoteGrid,
    factors: pl.DataFrame,
    *,
    threshold_ranges: dict[str, tuple[float, float]],
    n_points_per_dim: int = 5,
    factor_columns: list[list[str]] | None = None,
    initial_lambdas: dict[str, float] | None = None,
    max_total_points: int = 10_000,
    parallel: bool = False,
) -> FrontierResult
```

Internally, `RatebookOptimiser.solve(...)` does:

```python
factor_contexts = build_factor_contexts_py(factors, factor_specs, "\x1f")
```

`frontier(...)` currently builds these contexts once and passes `_factor_contexts_override` into each solve. That private override should be removed in favour of the opaque public context path.

## Public API

Add one public opaque context object:

```python
class RatebookFactorContexts:
    @property
    def factor_specs(self) -> list[list[str]]: ...

    @property
    def n_factors(self) -> int: ...

    @property
    def n_quotes(self) -> int: ...

    @property
    def quote_id_fingerprint(self) -> int | None: ...

    @classmethod
    def from_dataframe(
        cls,
        factors: pl.DataFrame,
        factor_specs: list[list[str]],
        *,
        quote_id: str | None = "quote_id",
        separator: str = "\x1f",
        expected_quote_ids: Sequence[str] | None = None,
        expected_n_quotes: int | None = None,
    ) -> RatebookFactorContexts: ...
```

Add one public parquet helper:

```python
def build_ratebook_factor_contexts_from_parquet_chunked(
    path: str,
    factor_specs: list[list[str]],
    chunk_size: int,
    *,
    quote_id: str | None = "quote_id",
    separator: str = "\x1f",
    expected_quote_ids: Sequence[str] | None = None,
    expected_n_quotes: int | None = None,
) -> RatebookFactorContexts:
    ...
```

`expected_quote_ids` and `expected_n_quotes` must be cross-validated at the top of both constructors. If both are supplied and `len(expected_quote_ids) != expected_n_quotes`, raise before reading data.

Update `RatebookOptimiser.solve(...)` and `RatebookOptimiser.frontier(...)` to accept:

```python
pl.DataFrame | RatebookFactorContexts
```

Export only the deliberate public surface from `price_contour.__init__`:

```python
from price_contour.ratebook import RatebookFactorContexts
from price_contour.ratebook import build_ratebook_factor_contexts_from_parquet_chunked
```

Do not export `FactorContext` unless it is intentionally promoted with separate documentation and compatibility guarantees.

## Internal Implementation

Implement an internal Rust/PyO3 builder used by both public constructors.

The builder should own:

- `factor_specs`
- per-factor label-to-group maps
- per-factor group labels
- per-factor per-quote group-index arrays
- quote-id tracking for validation and reorder
- quote-id fingerprint computation
- null and missing-column validation matching dataframe mode

Add `quote_id_fingerprint: u64` to `QuoteGrid`. Compute it at build time after the cycle-following quote-id sort, expose it via a read-only `#[getter]` as `quote_id_fingerprint -> int`, and use the same hash algorithm as the factor-context builder.

The parquet helper should:

- read the parquet source in row-slice chunks
- select only `quote_id` plus the configured factor columns
- avoid `pl.read_parquet(path)` or any equivalent whole-file collection
- use a factor-specific parquet chunk reader, or parameterise the existing reader carefully

Do not reuse `read_parquet_in_aligned_chunks(...)` blindly if it is tied to quote-grid `n_steps` alignment. Factor parquets have one row per quote, not one row per quote-step.

The dataframe constructor should route through the same builder as a single append:

```python
builder.append(factors)
return builder.build(...)
```

Then `build_factor_contexts_py(...)` can become a thin compatibility shim, if needed.

## Quote ID Contract

`QuoteGrid.quote_ids` is `Vec<String>`. The factor-context builder should match the `QuoteGridBuilder` quote-id contract:

- cast the factor source `quote_id` column to Utf8/String per chunk
- reject null quote IDs with a clear error
- reject duplicate quote IDs
- reject missing quote IDs when `expected_quote_ids` is supplied
- reject unexpected quote IDs when `expected_quote_ids` is supplied
- reject empty factor sources unless existing `QuoteGrid` semantics explicitly support zero quotes

When `expected_quote_ids` is supplied, build the contexts in that exact quote order and store the matching quote-id fingerprint.

When `expected_quote_ids` is not supplied:

- if a `quote_id` column is supplied and present, store the source-order quote-id fingerprint
- if no quote IDs are available, store `quote_id_fingerprint=None`
- `solve(QuoteGrid, contexts)` must reject contexts with no fingerprint because order cannot be proven
- `solve(QuoteGrid, contexts)` must reject contexts whose fingerprint differs from the grid fingerprint

`from_dataframe(...)` should handle the quote-id matrix explicitly:

| `quote_id` column | `expected_quote_ids` | Behaviour |
| --- | --- | --- |
| present | present | Validate the column against expected IDs, reorder if needed, and set fingerprint to `hash(expected_quote_ids)`. |
| present | absent | Preserve source row order and set fingerprint to `hash(source_order_quote_ids)`. |
| absent | present | Trust positional alignment with `expected_quote_ids`, keep row order, and set fingerprint to `hash(expected_quote_ids)`. |
| absent | absent | Build contexts with `quote_id_fingerprint=None`; `solve(QuoteGrid, contexts)` rejects them. |

The absent-column/present-expected case preserves legacy dataframe callers that already pass factors positionally aligned to the quote grid. It is safe only because the expected quote order is provided by the caller and becomes the contexts' explicit alignment contract.

The fingerprint should include the number of quote IDs and length-delimited string bytes so ambiguous concatenations cannot collide before hashing. Use a stable 64-bit non-cryptographic hash such as xxhash64, or a truncated cryptographic hash if that is already available locally.

## Deterministic Label Remap

Current dataframe mode assigns factor group IDs in first-seen order. The chunked path must reproduce that exactly.

After per-quote reorder to `expected_quote_ids`, walk each factor's `group_of` array from quote index `0` to `n_quotes - 1`. Assign each old label to the next unused final group ID on first encounter, then rewrite `group_of` through that remap and reorder the label table to match. This reproduces dataframe-mode insertion-order semantics for the post-sort quote traversal.

Do not lexicographically sort labels unless existing dataframe mode is changed and pinned separately. The parity target is the current dataframe path.

## Reorder Implementation

When `expected_quote_ids` is supplied, the builder needs to permute quote-level arrays into grid order.

Preferred implementation:

- compute one source-to-grid permutation from quote IDs
- use the cycle-following in-place permutation pattern already used by `QuoteGridBuilder`
- apply the same permutation to every factor's `group_of` array in a batched pass

This avoids doubling the memory peak for per-quote factor indices during reorder.

## Solver And Frontier Logic

Add a small internal resolver in `ratebook.py` with an explicit return type:

```python
@dataclass(frozen=True, slots=True)
class ResolvedFactorContexts:
    factor_specs: list[list[str]]
    factor_contexts: RatebookFactorContexts
    n_quotes: int
```

For dataframe mode:

- preserve existing behaviour, including any current auto-discovery
- construct `RatebookFactorContexts.from_dataframe(...)`
- pass `expected_quote_ids=grid.quote_ids` when a `QuoteGrid` is available

For `RatebookFactorContexts` mode:

- use the specs stored on the context object
- reject conflicting `factor_columns`
- validate `n_quotes` against `QuoteGrid.n_quotes`
- validate `quote_id_fingerprint` against `QuoteGrid.quote_id_fingerprint`
- reject contexts with unknown quote order when solving against a `QuoteGrid`

Auto-discovery is unavailable in `RatebookFactorContexts` mode. The contexts' `factor_specs` are authoritative. If the optimiser instance has `factor_columns=None` and contexts are supplied, use the contexts' specs directly with no scan.

`frontier(...)` should:

1. Resolve or build `RatebookFactorContexts` once.
2. Pass that same object through the normal `factors` argument for each `solve(...)` call.
3. Remove `_factor_contexts_override` and all related branching.

This gives one public and internal path for prebuilt factor contexts.

## Null And Type Semantics

Match existing dataframe mode exactly:

- Missing factor columns should raise a clear error.
- Null factor values should behave identically to `build_factor_contexts_py(...)`.
- Composite factors such as `["age_band", "channel_band"]` should use the same separator semantics as dataframe mode.
- Factor labels should produce identical `factor_tables` keys to current mode.

For composite labels, the first implementation may match dataframe mode by allocating joined strings per row. Reuse/pre-allocate a scratch buffer per factor where practical, but defer more exotic composite-key structures until a benchmark proves the need.

Do not add permissive fallbacks. If the builder cannot reproduce dataframe semantics, fail loudly and fix the mismatch.

## GIL Contract

The new builder should match the existing `build_factor_contexts_py(...)` pattern and release the GIL while processing chunk data after Polars extraction.

Do not hold the GIL across a 500k-row factor append.

## Required Tests

Add failing tests before implementation.

Core parity:

1. `solve(...)` with dataframe factors and chunked parquet contexts produces identical:
   - `total_objective`
   - `baseline_objective`
   - `total_constraints`
   - `baseline_constraints`
   - `factor_tables`
   - `dataframe` where applicable

2. `frontier(...)` with dataframe factors and chunked parquet contexts produces identical frontier points.

3. `frontier(...)` internally builds `RatebookFactorContexts.from_dataframe(...)` and matches the legacy frontier oracle numerics.

4. Explicit prebuilt contexts passed into `frontier(...)` match the internal dataframe-built context path.

5. Chunk sizes:
   - `1`
   - prime
   - exact boundary
   - larger than input

6. Composite factor specs:

```python
[["age_band"], ["vehicle_age_band"], ["age_band", "channel"]]
```

Ordering and validation:

7. Quote grid input is deliberately out of order, `QuoteGridBuilder` sorts it, and chunked factor contexts still solve identically after being built with `expected_quote_ids=quote_grid.quote_ids`.

8. Factor source duplicate quote IDs fail loudly.

9. Factor source missing quote IDs fail loudly.

10. Factor source unexpected quote IDs fail loudly.

11. Nulls in the `quote_id` column fail loudly.

12. Int64 or other non-string `quote_id` dtypes are cast to Utf8/String consistently with `QuoteGridBuilder`.

13. Empty parquet input fails loudly unless zero-quote grids are deliberately supported elsewhere.

14. Contexts built for grid A fail when passed to `solve(grid_B, contexts)` if the quote-id fingerprint differs.

15. Contexts built without provable quote order are rejected by `solve(QuoteGrid, contexts)`.

16. `solve(QuoteGrid_N, RatebookFactorContexts_M)` rejects mismatched quote counts with an error naming both counts.

Schema and semantics:

17. Missing factor column raises a clear error naming:
   - missing column
   - available columns
   - factor spec

18. Null factor values match current dataframe-mode behaviour.

19. Prebuilt contexts cannot conflict with `factor_columns`:

```python
solver.solve(grid, contexts, factor_columns=[["different"]])
```

should fail loudly.

Public API:

20. `RatebookFactorContexts` does not expose `.contexts`.

21. `FactorContext` is not exported in `price_contour.__all__`.

22. The opaque context object exposes read-only metadata only.

Bit stability:

23. Existing dataframe-mode factor label ordering is pinned in a test.

24. Chunked context mode matches dataframe-mode `factor_tables` exactly for single and composite factors.

Performance and memory:

25. The parquet builder reads only required columns: `quote_id` plus factor columns.

26. Replace brittle "does not call `pl.read_parquet`" monkeypatch tests with a memory-bound test or benchmark. A slow-marked synthetic source should assert peak memory is bounded by unique labels plus `n_quotes * n_factors * sizeof(u32)` with a conservative safety factor.

27. Reorder with `expected_quote_ids` does not double per-factor index memory for large inputs.

28. `RatebookFactorContexts.from_dataframe(df_without_quote_id_col, expected_quote_ids=grid.quote_ids)` produces the same contexts and solver output as `from_dataframe(df_with_quote_id_col_in_grid_order, expected_quote_ids=grid.quote_ids)`.

29. `RatebookFactorContexts.from_dataframe(df_without_quote_id_col, expected_quote_ids=None)` returns `quote_id_fingerprint=None`, and `solve(QuoteGrid, contexts)` rejects it because quote order cannot be proven.

## Haute Integration After Price Contour Change

Once `price-contour` exposes this API, Haute should change ratebook solve from:

```python
factors_df = streaming_collect(pl.scan_parquet(factor_path), ...)
solver.solve(quote_grid, factors_df)
```

to:

```python
from price_contour import build_ratebook_factor_contexts_from_parquet_chunked

factor_contexts = build_ratebook_factor_contexts_from_parquet_chunked(
    str(factor_path),
    factor_specs=config["factor_columns"],
    chunk_size=config.get("chunk_size", 500_000),
    quote_id="quote_id",
    expected_quote_ids=quote_grid.quote_ids,
    expected_n_quotes=quote_grid.n_quotes,
)

solver.solve(quote_grid, factor_contexts)
```

Build the `QuoteGrid` first, then build factor contexts against `quote_grid.quote_ids`. That keeps the final solve path order-safe even when the grid builder sorts input quotes.

Haute can continue persisting the factor parquet artifact for later frontier, select, and apply workflows.

## Haute-Side Requirements

This `price-contour` change only removes the memory peak if Haute also stops collecting the projected factor source into `factors_df`.

The current Haute ratebook path:

1. projects the banding source to `quote_id` plus configured factor columns
2. sinks that projection to a temporary parquet
3. immediately collects the parquet back into an in-memory `pl.DataFrame`
4. aligns the dataframe to `quote_grid.quote_ids`
5. passes the dataframe to `solver.solve(...)`
6. persists the dataframe again for frontier point materialisation

After this API lands, Haute should instead:

1. project the banding source to `quote_id` plus configured factor columns
2. sink it once to a server-owned parquet artifact that lives for the optimiser job
3. build the `QuoteGrid`
4. build `RatebookFactorContexts` from the factor parquet with `expected_quote_ids=quote_grid.quote_ids`
5. call `solver.solve(quote_grid, factor_contexts)`
6. store or lazily rebuild `factor_contexts` for frontier recompute and selected-point materialisation
7. compute `factor_level_counts` from the factor parquet with lazy `group_by(...).len()` aggregations, collecting only the small aggregate result

Haute routes that currently carry `factors_df` should move to a factor-artifact/context model:

- optimiser setup should return a factor artifact handle, not a collected dataframe
- `_solve_ratebook(...)` should accept the factor artifact handle or built contexts
- `_compute_frontier(...)` should pass contexts to `solver.frontier(...)`
- `_ratebook_runtime_state_or_raise(...)` should not call `pl.read_parquet(...)` for the full factor table
- `_materialise_ratebook_frontier_point(...)` should call `solver.solve(quote_grid, factor_contexts, ...)`
- the job store heavy-object set should replace `factors_df` with the cheaper context object or rebuild contexts from the artifact when needed

The existing factor parquet artifact is still useful. The important change is that it becomes the primary factor source for solve/frontier/materialisation, not a temporary staging file before a full dataframe collect.

## Acceptance Criteria

This work is complete when:

- `RatebookOptimiser.solve(...)` accepts prebuilt opaque factor contexts.
- `RatebookOptimiser.frontier(...)` accepts prebuilt opaque factor contexts.
- `_factor_contexts_override` is removed.
- A chunked parquet builder can build factor contexts without materialising a full `pl.DataFrame`.
- Dataframe and parquet construction share one internal builder algorithm.
- The new contexts path validates quote order with quote-id fingerprints and fails loudly when order cannot be proven.
- `FactorContext` remains private unless explicitly promoted in a separate API decision.
- Dataframe mode remains backwards-compatible.
- Existing ratebook results are bit-stable, including `factor_tables`.
- The new path fails loudly on missing columns, duplicate quote IDs, missing quote IDs, unexpected quote IDs, null quote IDs, row-count mismatch, factor spec conflicts, and quote-order uncertainty.
- Haute can remove its final `factors_df = streaming_collect(...)` step for ratebook solve.
- Haute can recompute frontier points and materialise selected ratebook tables without reloading the full factor parquet into a dataframe.
