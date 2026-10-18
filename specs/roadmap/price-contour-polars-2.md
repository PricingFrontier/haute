# price-contour on Polars 2

This is a handover specification for the price-contour project, written from Haute's side on 18 October 2026. It says what price-contour's next release changes so that it declares and tests Polars 2, and what Haute changes when it moves to that release. It owns no Haute work: `PL2-20` in the [Polars 2 roadmap](polars-2.md) carries Haute's side.

Paths are price-contour's, at v0.6.0 (`cd3aac8`), unless they start with `src/haute/`, `tests/test_` or `scripts/benchmarks/`, which are Haute's.

## Summary

Polars has not published Rust crates for Python Polars 2.0.0. So price-contour cannot yet be rebuilt on Polars 2's Rust code. What a release can do now, as 0.7.0:

1. **Declare and test Polars 2.** price-contour declares `polars>=0.20`, and its CI has only ever tested Polars 1.38. No Rust change is needed.
2. **Keep result row orders.** Four joins should state the row order Polars 2 no longer promises. No Rust change is needed.
3. **Send only the columns a native call reads.** No Rust change is needed.
4. **Move the bridge to pyo3-polars 0.28 (recommended).** This is the newest released bridge between Python Polars and the Rust crates. Its data path is the one Python Polars 2.0.0 ships. It stops rechunking every incoming column and needs no pyarrow. It is not Polars 2's Rust code.
5. **Publish only after CI passes.**

Later, when Polars publishes Rust crates from its 2.0 tree, a further release moves to them.

Haute is not waiting for any of this (see "Haute is not waiting").

## Where things stand (18 October 2026)

**Polars.** Python Polars 2.0.0 was published on 6 October 2026. It is the only 2.x release.

**No Rust release matches Python Polars 2.0.0.**
- The newest Rust `polars` crate is 0.55.2 (6 August 2026). The rs-0.55.1 release notes say: "This Releases' DSL matches Python 1.43.2". The `py-2.0.0` tag's workspace `Cargo.toml` still says `version = "0.55.1"` and `pyo3 = "0.29"`.
- The newest `pyo3-polars` is 0.28.0 (4 August 2026). It requires `polars ^0.55.1` and `pyo3 ^0.29`.
- The pola-rs/pyo3-polars repository is archived. The crate now lives in the Polars monorepo (`pyo3-polars/pyo3-polars`). It is still at 0.28.0 there, with unreleased changes since (stage 3).

**price-contour 0.6.0 works with Polars 2.0.0, but only Haute has tested that.**
- Haute's CI drives the real 0.6.0 wheel on Polars 2.0.0. The optimiser tests that call the library pass on Linux in CI, and on Windows locally.
- Haute checks the boundary the first time it uses the library. It passes a one-row frame through `ApplyOptimiser.apply` and back, and refuses the install with a named error if that fails (`src/haute/_price_contour.py:221-253`). Every Haute test session runs this check first, on Linux, Windows and macOS (`tests/conftest.py:71-81`).
- price-contour's own CI has never run on Polars 2.
  - Its test job installs `polars>=0.20` (`.github/workflows/ci.yml:90`).
  - Then `uv run` (ci.yml:91 and 94) re-syncs the environment to `uv.lock`, which pins polars 1.38.1 (uv.lock:700-725).
  - In the 0.6.0 run (36415887049), the install step put polars 1.44.2 in place, and the `uv run` sync then replaced it with the lock's version.
- 0.6.0 on PyPI declares `polars>=0.20` with no upper bound (pyproject.toml:13). Anyone installing it today gets Polars 2.0.0, and any later major would install too.

## Stage 1: declare and test Polars 2

Stage 1 needs no Rust change. It is the part of the release that matters most.

### Declared range

- pyproject.toml:12-15 should say `polars>=2.0,<3`.
- If stage 2 ships in the same release, drop `pyarrow>=23.0.1`. Otherwise keep it, because the 0.25 bridge needs it.

### Lock and CI

- **Lock:** regenerate `uv.lock` on polars 2.0.0. Today it pins 1.38.1, and it still records price-contour as 0.4.0.
- **Install:** install from the lock only. `uv run` undoes the `uv pip install ... "polars>=0.20"` at ci.yml:90, so drop that line, and keep maturin, pytest, hypothesis and scipy in the lock's dev group.
- **Full suite:** run it on Polars 2.0.0 from the lock. The golden files (`tests/python/golden/*.json`, tolerance 1e-9) must not change. If one does, find out why before regenerating it with `UPDATE_GOLDEN=1`.
- **Optional:** tighten `tests/python/test_input_validation.py:43-171`. Its `pytest.raises((ValueError, Exception))` would hide a change of exception class.

### Row order

Polars 2.0's `DataFrame.join` defaults `maintain_order` to `"none"`. Its documentation says: "The ordering might differ across Polars versions or even between different runs ... Do not rely on any observed ordering without explicitly setting this parameter." Eager joins on 2.0.0 keep the left order in practice (Haute's tests pass), but nothing promises it.

- **`python/price_contour/apply.py:380-388` and `390-398`:** add `maintain_order="left"` to both left joins.
  - `with_explainer_columns` promises every input row and column (apply.py:180-182).
  - `tests/python/test_explainer_columns.py:77` and `592-593` pin row positions.
- **`python/price_contour/_ratio_results.py:98-102`:** add `maintain_order="left"` to the inner join.
  - With ratio constraints, this frame becomes `SolveResult.dataframe`, `ApplyResult.dataframe` and the ratebook `quote_results`. Their rows "follow the grid's quote order" (`docs/DESIGN_DECISIONS.md:1184-1186`).
  - Its ratio totals are Float64 sums taken in row order (_ratio_results.py:49-50), so the order also fixes their last bits.
- **`python/price_contour/solver.py:1504-1509`:** add `maintain_order="left"` to the inner join in the ratio history replay.
  - It feeds `_safe_ratio_from_columns`, whose Float64 sums become the public `history[*]["total_constraints"]` for ratio constraints (solver.py:1456-1458).
  - Their values do not depend on the order, but their last bits do, as above.

### The one lazy query

- apply.py:343-362 builds the explainer baseline with `.lazy() ... .collect()`. In Polars 2, a lazy `collect()` runs on the streaming engine by default.
- The result is joined by key, so its values do not change, but the engine and the memory profile do.
- The input is already an eager frame. Write the lookup eagerly (`modified_df.group_by(...)`) or pass `engine="in-memory"`.

### Columns sent to the native module

`ApplyOptimiser.apply` passes the caller's whole frame to `apply_lambdas_py` (apply.py:150-175). The bridge converts every column, although the native code reads only the named ones. Before each native call, select only what it reads:
- the quote id, scenario index, scenario value, objective and constraint columns;
- a ratio constraint's numerator and denominator.

Where to select them:
- `apply`, `solve` and the other Python wrappers can select the columns before they call the native module.
- `QuoteGridBuilder` is the native class itself (`python/price_contour/builder.py:5`). Its `append` converts the whole frame (`crates/price-contour/src/builder_py.rs:97`) before any Python runs, and the class cannot be subclassed from Python. So:
  - `python/price_contour/_grid_utils.py:34-42`, its internal caller, selects the columns before calling `append`;
  - for other callers, either export a thin Python class that holds the native builder and selects the columns in its own `append`, or document that callers pass only the named columns.

What it buys:
- Unrelated columns then cost nothing.
- A column the extension's Polars cannot import can no longer fail an apply. Examples are Polars 2's `Map` (added after the 0.55 crates, polars PR #28984) and `Struct`, `Decimal` or `Array`, whose Polars features the extension does not enable.
- `with_explainer_columns` keeps returning every input column, because it builds them in Python.

### Python APIs

Apart from the joins and the lazy query above, nothing in `python/price_contour` uses an API Polars 2 removed or renamed. It has no `with_row_count`, `pl.count`, `cumsum`, `apply`, `map_dict`, `StringCache`, `how="outer"` or `melt`. `pl.Utf8` remains an alias of `pl.String` (`python/price_contour/ratebook.py:1743`).

### New tests

1. For each quote-id dtype (String, Categorical, Enum), a round trip through `ApplyOptimiser.apply`, `OnlineOptimiser.solve` and `build_grid_from_parquet_chunked`, asserting the returned schema and values.
2. Parquet written by Python Polars 2.0 the way Haute writes it ("What Haute calls and sends"), read by `build_grid_from_parquet_chunked` and `build_ratebook_factor_contexts_from_parquet_chunked`. The file has a Categorical `quote_id`, Int32 `scenario_index`, Float32 values, and an extra column the reader does not select.
3. An input with extra `pl.Map`, `pl.Struct` and `pl.Decimal` columns, through `ApplyOptimiser.apply` and through `QuoteGridBuilder.append` (or its wrapper), which must not fail.
4. Row order:
   - `with_explainer_columns` on a shuffled input returns the input order;
   - ratio results follow the grid's quote order;
   - a ratio solve's `history` totals equal the sums taken in that order.

### Docs

- **`README.md`:** state the supported Polars range.
- **`docs/architecture.md:621-623`:** it names pyo3 0.23, pyo3-polars 0.20 and polars 0.46. Give the versions in use.
- **`docs/architecture.md:105-106`:** `quote_id` is String, Categorical or Enum (integer ids are refused), and `scenario_index` is Int32.
- **`docs/DESIGN_DECISIONS.md`:** record the Polars range, the column selection and the row-order rule.
- **`docs/ci-setup-guide.md`:** line 217 says CI passes before publishing, which is not enforced. Line 263 mentions `target-cpu=native`, which is gone.
- **`docs/COMMIT_STANDARDS.md:21` and `256`:** they say the only runtime dependencies are numpy and polars.

### Publishing

- `.github/workflows/publish.yml` runs on any push to main that changes the version (publish.yml:3-5 and 19-38), independently of ci.yml. 0.6.0 reached PyPI at 11:31 UTC on 28 September, before its CI finished at 11:37.
- Make the publish job wait for the test jobs, or publish from a tag once CI has passed.
- publish.yml:50-58 builds the four abi3-py310 wheels Haute uses, plus the sdist: Linux x86_64 (manylinux), macOS x86_64, macOS arm64 and Windows x86_64.
- Check the published wheel's metadata: it should say `Requires-Dist: polars<3,>=2.0`.

## Stage 2: the bridge to pyo3-polars 0.28 (recommended)

### What it changes

| | pyo3-polars 0.25 (today) | pyo3-polars 0.28 |
|---|---|---|
| Rust crates | polars 0.52, PyO3 0.26 | polars 0.55, PyO3 0.29 |
| Into Rust | `rechunk`, then `_newest_compat_level`, `CompatLevel._with_version`, `to_arrow(compat_level=...)` and pyarrow's `_export_to_c` (`src/types.rs:173-202` in 0.25.0) | `Series._s._export(ptr)` through Polars' own FFI, `polars_ffi::version_0` (`src/types.rs:176-185` in 0.28.0) |
| Back to Python | `pl.Series._import_arrow_from_c` (`src/types.rs:256-333` in 0.25.0) | `pl.Series._import(ptr)` (`src/types.rs:242-251` in 0.28.0) |
| Copies | Every incoming column is rechunked, so a multi-chunk column is copied | No rechunk: numeric and string columns arrive as they are. Categorical and Enum columns are rebuilt from their strings (`CategoricalChunked::from_str_iter` in polars-core 0.55's dictionary importer, `crates/polars-core/src/series/from.rs`) |
| pyarrow | Required | Not used |
| Enum columns | Arrive in Rust as Categorical (polars issue #25993) | Arrive as `DataType::Enum` (fixed by polars PR #26000, in pyo3-polars 0.26) |

- Both lines go through private Polars Python methods. Python Polars 2.0.0 has all of them, with no deprecation notice (`py-polars/src/polars/series/series.py:511-562` at `py-2.0.0`).
- `Series._import` first appears in Python Polars 1.28.0. The Arrow PyCapsule interface (`__arrow_c_stream__`) exists in Polars 2.0, but pyo3-polars does not use it.
- Two files are byte-identical at rs-0.55.1 and py-2.0.0: `crates/polars-ffi/src/version_0.rs` (the FFI format) and `crates/polars-arrow/src/datatypes/field.rs` (the Categorical and Enum metadata keys). That is why 0.28 works with Python Polars 2.0.0. It was established by comparing sources, not by running them, so stage 2's tests check it.
- Haute's Optimiser Apply sends a whole frame to `ApplyOptimiser.apply`. So with 0.25, every multi-chunk column of a large frame is copied during each apply.

### Cargo

In the workspace `Cargo.toml:11-14`:

```toml
pyo3 = { version = "0.29", features = ["extension-module", "indexmap"] }
pyo3-polars = "0.28"
polars = "0.55"
```

- **Polars features:** keep `parquet` and `dtype-categorical` (`crates/price-contour/Cargo.toml:15`).
- **pyo3-polars features:** none are needed.
  - `lazy` and `derive` are unused.
  - Its `dtype-*` features switch on the matching Polars dtype features, and the `PyDataType` conversions for those dtypes (`src/types.rs:395`, `413` and `442` in 0.28.0).
  - price-contour converts no `PyDataType`, and it enables `dtype-categorical` on its own `polars` dependency.
- **PyO3:** 0.30 is out (0.30.1 on 14 October), but pyo3-polars 0.28 requires 0.29, whose latest release is 0.29.3. A build links only one PyO3, so PyO3 and pyo3-polars move together.
- **Lock:** regenerate `Cargo.lock`, and check that `cargo tree -d` shows a single `pyo3` and a single `polars-core`.
- **Rust:** stable, at least 1.85. Polars uses edition 2024, and PyO3 0.29's MSRV is 1.83.
- **Scope:** `crates/price-contour-core` has no Polars or PyO3 dependency. Only `crates/price-contour`, the bindings, changes.

### Rust: three call sites stop compiling

| file:line | Today | Change | Why |
|---|---|---|---|
| `crates/price-contour/src/solver_py.rs:337` | `DataFrame::new(columns)` | `DataFrame::new(height, columns)` with the row count the function built, or `DataFrame::new_infer_height(columns)` | polars 0.53 made a frame's height explicit (polars PR #25976) |
| `crates/price-contour/src/frontier_py.rs:155` | `DataFrame::new(columns)` | `DataFrame::new(self.inner.points.len(), columns)` | as above |
| `crates/price-contour/src/ratebook_helpers_py.rs:178` | `ca.into_no_null_iter()` on a `StringChunked` | `ca.no_null_iter()` | removed for non-numeric arrays in polars 0.54 (polars PR #27697) |

These still compile:
- the numeric `into_no_null_iter()` calls (builder_py.rs:425 and 439; solver_py.rs:181 and 198);
- `df.sort([..], SortMultipleOptions::default())` (solver_py.rs:99-102), under its new signature;
- the categorical physical types in quote_id.rs (`CategoricalPhysical` still has U8, U16 and U32, and no U64);
- the `Column` accessors;
- `ParquetReader::with_slice` (parquet_grid_py.rs:192-224) and `ParquetWriter::batched` (apply_py.rs:574-584);
- `StringChunkedBuilder` and `Column::new`.

These lists come from comparing signatures between polars 0.52 and 0.55.2. Nothing was compiled.
- Fix whatever `cargo build` and `cargo clippy -- -D warnings` report beyond them.
- Clashes between the `polars::prelude::*` and `pyo3::prelude::*` glob imports, and `Send` bounds in `py.detach` closures, only show at compile time.

### PyO3 0.26 to 0.29

- The code already uses `py.detach`, the 0.26 rename of `allow_threads`.
- It uses none of the APIs removed or deprecated since: `with_gil`, `downcast`, `extract_bound`, the `PyObject` alias, `GILOnceCell` or by-value `#[pyclass]` arguments.
- It has no hand-written `FromPyObject`.
- PyO3 0.29 adds a note to argument-extraction errors (PyO3 #5349). `tests/python/test_apply_chunked.py:584-587` matches a `TypeError` message with a regex, so check that it still matches.

### Tests

1. A multi-chunk input (`pl.concat([a, b], rechunk=False)`) through `apply` and `QuoteGridBuilder.append` gives the same result as the rechunked frame.
2. An Enum quote id reaches the `DataType::Enum` branch of quote_id.rs:50. That branch is not reached today.

### The git-tag alternative

- The alternative is depending on the Polars monorepo at tag `py-2.0.0` for `polars` and `pyo3-polars`. It would match Python Polars 2.0.0 exactly, but it builds from GitHub and ties the sdist to GitHub.
- It buys nothing over the released crates, given the identical FFI files above.
- The same two `DataFrame::new` changes apply there.

## Stage 3, later: Polars' own 2.0 Rust crates

Polars has not yet published Rust crates from its 2.0 tree, and the next pyo3-polars release will probably come with them. When it does, move to it (a patch release, if nothing else changes) and pick up the monorepo changes made since pyo3-polars 0.28.0:
- `Map` dtype support (polars PR #28984), with a new `dtype-map` feature;
- Enum dtype extraction in pyo3-polars (#28598) and preserved `pl.Categories` (#29385). Both affect `PyDataType` only, which price-contour does not use;
- the `arrow` crate renamed to `polars-arrow` (#29356);
- a re-entrance fix in the PyO3 allocator (#29763).

## Decisions for the owner

1. **The range is `polars>=2.0,<3`.**
   - pyo3-polars 0.28 also works with Python Polars 1.28 to 1.4x, but price-contour would not test those versions, and its one consumer is on 2.0.
   - If 1.x support is wanted, declare `>=1.28,<3` and add a CI leg on a 1.x release.
2. **Release 0.7.0, not 0.6.1.**
   - The supported Polars range and the dependency set change. price-contour's `AGENTS.md:83-90` makes a contract change before 1.0 a minor bump.
   - Haute's `<0.7` pin then keeps 0.7.0 out of Haute until Haute moves its pin and its guard test together.
3. **Ship stage 2 in 0.7.0.** It is three code changes and a version bump, and it shares stage 1's testing. Deferring it keeps pyarrow and the rechunk of every incoming column.
4. **Use the released crates, not a git pin.**

## What Haute calls and sends

Keep all of this unchanged in 0.7.0. Haute's guard checks every name and keyword below, and refuses the install by name if one is missing.

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
- **The Optimiser Apply frame**, in memory (`src/haute/_builders.py:1808-1831`). It is the whole input frame: `quote_id` String, `scenario_index` Int32, the value columns Float32, and every other column the analyst's frame has, in any dtype.
- **Haute's check frame:** one row with `quote_id` String, `scenario_index` Int32, and `scenario_value` and `objective` Float32. It goes through `ApplyOptimiser(lambdas={}, objective="objective", constraints={}).apply(frame).dataframe`.

## Acceptance

**price-contour:**
1. `cargo build --release`, `cargo clippy -- -D warnings` and `cargo test` pass.
2. The Python suite passes on Polars 2.0.0 from the regenerated lock, with the golden files unchanged.
3. Every new test in stages 1 and 2 passes.
4. `cargo tree -d` shows one `pyo3` and one `polars-core`.
5. The wheel requires `polars>=2.0,<3`, and does not require pyarrow if stage 2 shipped.
6. Haute's ratebook frontier repro runs no slower than on 0.6.0. It is in `scripts/benchmarks/price_contour_ratebook_frontier`, whose README gives the steps.

**Haute, against a local build before release.** Haute's guard refuses a version outside its pin, so do this on a Haute branch:
1. Set the `price-contour` specifier in `pyproject.toml` and `REQUIRED_SPECIFIER` in `src/haute/_price_contour.py:49` to `>=0.7.0,<0.8`. If stage 2 shipped, also apply step 2 of "Haute's side". Do not relock: 0.7.0 is not on PyPI yet.
2. In the price-contour checkout, build the wheel with `uv run maturin build --release`. It lands in `target/wheels/`.
3. In the Haute checkout, install that wheel into Haute's environment: `uv pip install --python .venv/bin/python <wheel>` (`.venv\Scripts\python.exe` on Windows). Running `uv run maturin develop` from the price-contour checkout would install into price-contour's own environment instead.
4. Check the install.
   - `uv run --no-sync python -c "import importlib.metadata as m; print(m.version('price-contour'))"` prints `0.7.0`.
   - Haute's guard logs `price_contour_verified` with source `direct URL <wheel path>`.
   - A container build refuses a direct-URL install by design. The tests of container builds opt into the `released_price_contour` fixture (`tests/conftest.py:84-98`), which presents the install as a released wheel.
5. Run every Haute test module that drives the library with `uv run --no-sync`. The module list is `grep -l price_contour tests/*.py`. Run at least these:
   - `tests/test_price_contour_guard.py`
   - `tests/test_optimiser_routes_real_library.py`
   - `tests/test_optimiser_apply.py`
   - `tests/test_optimiser_apply_trace_enrichment.py`
   - `tests/test_optimiser_frontier_materialisation.py`
   - `tests/test_optimiser_ratebook_apply_agreement.py`
   - `tests/test_optimiser_ratebook_choices.py`
   - `tests/test_optimiser_quotes.py`
   - `tests/test_optimiser_outcomes.py`
   - `tests/test_optimiser_segments.py`
   - `tests/test_optimiser_solver_session.py`
   - `tests/test_frontier_point_summary.py`

   price-contour's `AGENTS.md:56-81` already asks for `tests/test_optimiser_routes_real_library.py` when the API or a result shape changes. This release changes the boundary itself, so run the whole set.

Once 0.7.0 is on PyPI, relock (step 1 of "Haute's side") and run `uv sync --locked`, which replaces the local wheel with the published one.

## Haute's side after the release

1. **Move the pin.**
   - Set `price-contour>=0.7.0,<0.8` in `pyproject.toml`, and `REQUIRED_SPECIFIER` in `src/haute/_price_contour.py:49`, unless the pre-release check already did. `tests/test_dependency_contracts.py` requires the two to be equal.
   - Once 0.7.0 is on PyPI, relock with CI's uv, `uvx --from "uv==0.8.22" uv lock`, then run `uv sync --locked`. A newer uv rewrites unrelated wheels.
2. **Rewrite the guard's private-method test, if stage 2 shipped** (`tests/test_price_contour_guard.py:453`).
   - Today it deletes `pl.Series._import_arrow_from_c` and expects a `PanicException`. A stage-2 build no longer calls that method, so the round trip would pass and the test would fail.
   - Delete `pl.Series._import` instead, and assert the error the new path raises.
   - pyo3-polars 0.28 calls that method through `call_method1`, so expect a Python `AttributeError` raised through PyO3 rather than a panic. Confirm by running it.
3. **Update the text that names `_import_arrow_from_c`, if stage 2 shipped:**
   - the guard's module docstring (`src/haute/_price_contour.py:9-12`);
   - the guard's description in the optimiser low-level specification;
   - `PL2-20`, which then retires.
4. **Recommended now, independent of the release:** have `_prepare_online_apply_frame` (`src/haute/_builders.py:1808-1831`) pass only the columns the apply reads.
   - Nothing is lost: the native result carries no passthrough columns, and a ratio constraint's numerator and denominator are already among the columns cast there.
   - With 0.6.0, this stops the bridge rechunking and converting every column of the analyst's frame.
5. **Deployment needs no change.** Containers pin `price-contour==<verified version>` from PyPI, and the Linux x86_64 wheel covers them.

## Haute is not waiting

0.6.0 runs Haute's optimiser on Polars 2.0.0 today:
- Haute's CI passes with it on Polars 2.0.0.
- Haute's guard refuses an install whose boundary fails before any solve starts.
- Haute's lock pins Polars 2.0.0 and price-contour 0.6.0, so development, CI and deployments use exactly the pair that was tested.

The exposure until 0.7.0:
- Haute declares `polars>=2.0,<3`. An environment that resolves a later Polars 2.x without one of 0.25's private methods would have Haute's optimiser refuse to start, by name, until price-contour is rebuilt.
- Stage 2 moves to the private methods current pyo3-polars uses, which Polars changes together with pyo3-polars. It does not remove the dependence on private methods.

## Sources

- Polars 2 upgrade guide: https://docs.pola.rs/releases/upgrade/2/
- Polars 2.0.0 release: https://github.com/pola-rs/polars/releases/tag/py-2.0.0
- Rust 0.55.1 release ("DSL matches Python 1.43.2"): https://github.com/pola-rs/polars/releases/tag/rs-0.55.1
- Crate versions: https://crates.io/crates/polars, https://crates.io/crates/pyo3-polars and https://crates.io/crates/pyo3
- pyo3-polars 0.28.0 data path: https://docs.rs/crate/pyo3-polars/0.28.0/source/src/types.rs
- Polars pull requests and issues:
  - #25976 (DataFrame interface cleanup) and #27697 (iterator removal);
  - #26000 and #25993 (`SeriesExport` for pyo3-polars; Enum arriving as Categorical);
  - #28984 (`Map` dtype), #28598, #29385, #29356 and #29763.
- `DataFrame.join` `maintain_order`: https://docs.pola.rs/api/python/stable/reference/dataframe/api/polars.DataFrame.join.html
- PyO3 changelog: https://github.com/PyO3/pyo3/blob/main/CHANGELOG.md
