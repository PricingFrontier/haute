# CLEARED — adversarially checked and found CORRECT

Everything below was explicitly hypothesised as a defect and **refuted** against the code at
`af3eb2ea` (empirically against Polars 1.39.2 where marked ⚗). Do not "fix" these; several are
load-bearing design decisions. If a package touches adjacent code, preserve these behaviours and
their tests.

## Execution & caching

- **No double execution on the happy path** — after `materialize_lazy_frame_with_cache`,
  `lazy_outputs[node]` is a `scan_parquet` of the just-written cache entry
  (`_execute_lazy.py:1263-1287`; helper returns a scan on both hit and miss,
  `_dataframe_execution_cache.py:594-621`). `_build_frame_stats` reads the parquet; upstream runs
  exactly once (the sink). Write-then-read is intentional — it populates the reusable cache.
- **Report-cache eviction with a live dataframe cache does NOT re-run the pipeline** — the cache
  seeds `cached_seed_outputs` and skips covered lineage (`_execute_lazy.py:830-845`, `:1217-1224`).
- **Schema is computed once**, from the cheap parquet-footer scan (`_explore_service.py:819`), and
  threaded into `_build_frame_stats` — no second collect.
- **`dataAffectingConfig` strips only `overview`** (`cacheIdentity.ts:15-22`): toggling display
  cards correctly does *not* invalidate the cached report (pinned by
  `ExplorePreview.test.tsx:391-475`). The explore node's `code` correctly *does* invalidate.
- **The single-predicate gate** `_has_categorical_value_counts` (`_explore_service.py:154-167`)
  keeps the aggregation expression and its parse from drifting. E01 narrows the predicate; keep the
  single-source-of-truth shape.

## Polars semantics (⚗ verified on 1.39.2)

- ⚗ **Decimal and Int128 numerics are safe**: `is_numeric()` is true; `quantile/median/mean/std`
  all succeed; values render cleanly. No crash.
- ⚗ **Struct / List / Array are safe**: `n_unique` works for all three; they are correctly excluded
  from min/max and value-counts (not numeric, not min/max-ordered, not text), so `distinct_count`
  is populated and nothing crashes. Of the exotic dtypes only **Duration** breaks (see E01).
- ⚗ **Empty frames are safe**: 0 rows → guards hold (`row_count 0`, aggregates `None`,
  `_percent_text` denominator guard); 0 columns → `pl.len()` alone collects fine.
- ⚗ **`value_counts` field order**: schema is `[(value, String), (count, UInt32)]`; the `name=`
  kwarg names the count field; `struct.rename_fields([VALUE, COUNT])` maps positionally and
  correctly (`_explore_service.py:353-360`).
- ⚗ **Lossy binary decode merges, not splits**: `map_elements(_lossy_decode_binary)` runs *before*
  `value_counts`, so distinct undecodable byte-strings collapsing to U+FFFD are merged into one
  group. (E05 changes the order for performance — its TDD plan must preserve "no split duplicates"
  or consciously change the semantics to per-byte-string groups and say so in the UI.)
- ⚗ **The Binary UDF's justification comment is factually right**: `cast(pl.String, strict=False)`
  on Binary still raises `ComputeError: invalid utf8`. E05 relocates the decode; the reasoning
  stays.
- ⚗ **min/max NaN renders lowercase `nan`** (not "NaN"): `.row()` returns Python floats.

## Job lifecycle & API

- **No completed↔cancelled overwrite**: `JobLifecycle.transition` guards with `expected_status` +
  precedence + an explicit `completed` short-circuit (`_job_lifecycle.py:139-155`).
- **`release()` is idempotent** (`_background_jobs.py:108-115`) — the double call from
  `cancel()` + `_run_job`'s `finally` is safe.
- **`HTTPException` from `_prepare_spec` is raised on the route thread** (inside `start()`), not in
  the daemon thread; FastAPI receives it.
- **`family_key` has a real `source_file`**: `run_explore` flattens and `_ensure_source_file`s the
  graph before `start()` (`explore.py:22-25`).
- **Thread-per-job is memory-backpressured**: `EXPLORE_ANALYSIS` is in `_IN_FLIGHT_PROFILE_SET`
  (`_execution_admission.py:196-206`), so concurrent heavy jobs fail admission with a typed
  `memory_limited` rather than thrashing.
- **Overview config round-trip is validated on both sides**: emit (`_codegen_builders.py:944`) and
  parse (`_config_builder.py:207-213`) share `validate_explore_overview`; `bool` checked before
  `int`; NaN/inf rejected (`_explore_overview.py:24-29`); unknown keys preserved.
- **Explore arity is enforced at three layers**: `maxInputs: 1` (`nodeTypes.ts:62`), runtime parent
  check (`_explore_service.py:650-657`), codegen single-edge guard (`_codegen_builders.py:952-959`).

## Frontend state & polling

- **The full `ExploreCacheReport` is NOT resent on every poll**: running polls carry
  `result: null`; the report rides only the terminal `completed` response, is stored once, and the
  poller stops (`useJobPolling.ts:197-209`, `useNodeResultsStore.ts:1030-1053`,
  `guards.ts:1354-1364`).
- **No per-render hash thrash during canvas drags**: `panelGraph.allNodes/edges` are memoised on
  `panelContextVersion`, deliberately not bumped by position-only churn
  (`usePanelGraphContext.ts:57-66`); the `cacheIdentity`/`configHash` memos stay stable.
- **Synthetic job ids (`startup-failure:`, `cached:`) never reach the poller** — added and resolved
  synchronously in the same handler (`ExplorePreview.tsx:118-148`).
- **App-level polling surviving panel unmount is by design**, with cleanup on terminal states,
  error-only exponential backoff, 30s per-poll timeout, 24h max lifetime, and 404/410 terminal
  handling (`useJobPolling.ts:257-312`).
- **`SchemaTableCard` is the model card** (sticky header, 400px cap, search, pagination,
  null-severity ramp) — E08 copies it outward; don't regress it.
- **Data-quality severity logic uses the true ratio**, not the rounded text
  (`_explore_service.py:238`): only the *display* string is wrong (E02/EF-04).
- **Good a11y already present**: categorical expander (aria-expanded/controls, role=list chips) and
  config toggles (role=checkbox, labelledby/describedby) — extend, don't rebuild (E08).
