# price-contour on Polars 2

**Status (19 October 2026):** price-contour 0.7.0 declares and tests Polars 2 and bridges to it through pyo3-polars 0.28, and Haute pins it (`price-contour>=0.7.0,<0.8`). What is left is stage 3, Polars' own 2.0 Rust crates, which Polars has not yet published.

This is a handover specification for the price-contour project, written from Haute's side on 18 October 2026. It owns no Haute work.

## Stage 3: Polars' own 2.0 Rust crates

Polars has not yet published Rust crates from its 2.0 tree, and the next pyo3-polars release will probably come with them. When it does, move to it (a patch release, if nothing else changes) and pick up the monorepo changes made since pyo3-polars 0.28.0:
- `Map` dtype support (polars PR #28984), with a new `dtype-map` feature;
- Enum dtype extraction in pyo3-polars (#28598) and preserved `pl.Categories` (#29385). Both affect `PyDataType` only, which price-contour does not use;
- the `arrow` crate renamed to `polars-arrow` (#29356);
- a re-entrance fix in the PyO3 allocator (#29763).

The release is accepted when `cargo build --release`, `cargo clippy -- -D warnings` and `cargo test` pass, the Python suite passes with the golden files unchanged, and `cargo tree -d` shows one `pyo3` and one `polars-core`.

## What Haute calls and sends

Keep all of this unchanged. Haute's guard checks every name and keyword below, and refuses the install by name if one is missing.

**Calls** (`src/haute/_price_contour.py:57-133`):

| Symbol | Keywords Haute passes |
|---|---|
| `ApplyOptimiser`, `.apply`, `.with_explainer_columns` | `lambdas`, `objective`, `constraints`, `quote_id`, `scenario_index`, `scenario_value` |
| `apply_from_grid` | `lambdas`, `constraints`, `cancel` |
| `QuoteGrid`, `build_grid_from_parquet_chunked` | `quote_id`, `scenario_index`, `scenario_value`, `objective`, `n_steps` |
| `build_ratebook_factor_contexts_from_parquet_chunked` | `quote_id`, `expected_quote_ids`, `expected_n_quotes` |
| `OnlineOptimiser`, `.solve`, `.frontier`, `.summary` | `objective`, `constraints`, `max_iter`, `tolerance`, `record_history`; frontier: `threshold_ranges`, `n_points_per_dim`, `initial_lambdas` |
| `RatebookOptimiser`, `.solve`, `.frontier`, `.evaluate`, `.summary` | `objective`, `constraints`, `factor_columns`, `max_iter`, `max_cd_iterations`, `cd_tolerance`, `tolerance`; solve: `factor_columns`, `lambdas`; frontier: `threshold_ranges`, `n_points_per_dim`, `factor_columns`, `initial_lambdas`; evaluate: `cancel` |
| `RatebookResult.quote_results`, `RatebookFactorContexts`, `RatebookFrontierResult.factor_tables`, `frontier_points_schema`, `CancelToken`, `CancelToken.cancel`, `Cancelled` | none |

**Data:**
- **Solver input Parquet** (`src/haute/routes/_optimiser_input.py:813-830`). Python Polars 2.0 writes it through a streaming sink, and `build_grid_from_parquet_chunked` reads it. It holds:
  - `quote_id` cast from String to Categorical (a Categorical or Enum id is kept as it is);
  - `scenario_index` as Int32;
  - `scenario_value`, the objective and the constraint columns as Float32;
  - analysis columns under private names, which the reader does not select.
- **Ratebook factor Parquet:** one row per quote with the factor columns, read by `build_ratebook_factor_contexts_from_parquet_chunked`.
- **The Optimiser Apply frame**, in memory (`src/haute/_builders.py:1695-1756` and `1811-1834`). It holds only the columns the artifact reads (`online_apply_read_columns`): `quote_id` String, `scenario_index` Int32, and the scenario value, objective and constraint columns (a ratio constraint's numerator and denominator) Float32.
- **The explainability trace frame** (`src/haute/_optimiser_apply_explainability.py`): the same columns plus a UInt32 row number, `__haute_explained_row__`, passed to `with_explainer_columns`. Haute joins the frame's other columns back on that number, so `with_explainer_columns` must keep returning every input column it is handed, including ones it does not read.
- **Haute's check frame:** one row with `quote_id` String, `scenario_index` Int32, and `scenario_value` and `objective` Float32. It goes through `ApplyOptimiser(lambdas={}, objective="objective", constraints={}).apply(frame).dataframe`.

## Haute's side of the release

**Before the release, against a local build.** Haute's guard refuses a version outside its pin, so do this on a Haute branch:
1. If the release leaves `>=0.7.0,<0.8`, set the `price-contour` specifier in `pyproject.toml` and `REQUIRED_SPECIFIER` in `src/haute/_price_contour.py` to the new range; `tests/test_dependency_contracts.py` requires the two to be equal. Do not relock: the release is not on PyPI yet.
2. In the price-contour checkout, build the wheel with `uv run maturin build --release`. It lands in `target/wheels/`.
3. In the Haute checkout, install that wheel into Haute's environment: `uv pip install --python .venv/bin/python <wheel>` (`.venv\Scripts\python.exe` on Windows).
4. Check that Haute's guard logs `price_contour_verified` with source `direct URL <wheel path>`. A container build refuses a direct-URL install by design; the tests of container builds opt into the `released_price_contour` fixture (`tests/conftest.py`), which presents the install as a released wheel.
5. Run every Haute test module that drives the library with `uv run --no-sync`: the list is `grep -l price_contour tests/*.py`, and it includes `tests/test_price_contour_guard.py` and `tests/test_optimiser_routes_real_library.py`.

**After the release.** Relock with CI's uv, `uvx --from "uv==0.8.22" uv lock`, then run `uv sync --locked`, which replaces the local wheel with the published one. Deployment needs no change: containers pin `price-contour==<verified version>` from PyPI, and the Linux x86_64 wheel covers them.
