# E01 — A single `Duration` column kills the entire Explore report

**Severity:** HIGH (crash, high reachability) · **Effort:** S · **Review:** dev/reviewer pair
Files: `src/haute/routes/_explore_service.py` · Tests: `tests/test_explore_routes.py`

## EF-01 [HIGH] — Duration is routed into a String cast Polars forbids

### Current behaviour (verified at af3eb2ea, reproduced on Polars 1.39.2)

1. `_supports_min_max` returns True for any temporal dtype (`_explore_service.py:142-151`), so
   `_supports_categorical_value_counts` (`:136-139`) and therefore `_has_categorical_value_counts`
   (`:154-167`) return True for `Duration` columns.
2. Non-numeric columns passing that gate get `_categorical_value_counts_expr` (`:353-360`), whose
   label expression is `pl.col(name).cast(pl.String)` for every non-Binary dtype
   (`_categorical_value_label_expr`, `:336-350`).
3. Polars 1.39.2 forbids that cast. Reproduced:

   ```
   polars.exceptions.InvalidOperationError: casting from Duration('μs') to String not supported
   ```

4. All per-column aggregations live in ONE batched `lf.select(aggregations)` collected once
   (`:476-480`). The `InvalidOperationError` is re-raised by `streaming_collect`
   (`_polars_utils.py:76`; `_is_streaming_compatibility_error` only matches `"stream"` in the
   message, `:42-44`) and lands in `_run_job`'s `except Exception` (`_explore_service.py:771`).
   The job terminates `error` with the raw Polars message.

Empirically checked neighbours: `Date`, `Datetime`, `Datetime(tz)`, `Time` all cast to String
fine. **Only Duration breaks.** Every other exotic dtype (Struct/List/Array/Decimal/Int128) is
safe — see CLEARED.md.

### Impact

A pricing dataset with any derived duration — `policy_end - policy_start`, time-to-claim, tenure —
gets **no report at all**: not the numeric columns, not the schema card, nothing. The analyst sees
an error toast quoting a Polars cast error they didn't write. This is the highest-reachability
defect in the node.

### Fix design

Narrow the gate, keeping the single-source-of-truth shape (the same predicate must keep gating both
the expression build and the parse — that discipline is load-bearing, see CLEARED.md):

- In `_has_categorical_value_counts` (or `_supports_categorical_value_counts`), exclude dtypes whose
  String cast is unsupported. Concretely: exclude `pl.Duration` base type from the value-counts
  branch. Duration columns keep `null_count`, `n_unique`, and min/max (raw Duration min/max works
  and `_format_display_value` str()s it fine — verified).
- Do NOT wrap the collect in try/except and do NOT silently drop the column from the report —
  fail-loud rules apply to *unknown* failures; this is a *known* dtype boundary, expressed in the
  gate like Object already is for `n_unique` (`_UNHASHABLE_DTYPES`, `:67`).
- Update the `_is_unhashable_dtype` docstring's neighbour comment (`:93-101`) — its "allowed
  through" list reasons about `n_unique` only; add a pointer that the value-counts *cast* boundary
  is separate and lives in the categorical gate.
- `ExploreCategoricalColumnProfile.expandable` already handles "has distinct count but no values"
  (`:394-403`) — a Duration column will simply render non-expandable with a distinct count, which
  is correct.

Optional (only if product wants Duration value counts): a Duration-specific label via
`.dt.to_string()`-equivalent formatting or `map_elements(str)` — but that reintroduces a per-row
UDF (see E05); recommend the plain gate now, revisit under E11's temporal card where Duration
display belongs.

### TDD plan (failing tests first)

In `tests/test_explore_routes.py`, driving `_build_frame_stats` via the existing
`explore_execution_context` fixture:

1. `test_frame_stats_duration_column_survives` — frame with a `pl.Duration` column (e.g.
   `pl.date(...) - pl.date(...)` or `pl.duration(days=...)`), some nulls. Assert: report returned;
   one `ExploreColumnStat` per column; the Duration column has `null_count`, `distinct_count`,
   min/max populated; `kind == "Temporal"`; it is present in `categorical_summary` with
   `expandable is False` and `values == []`. **Currently raises `InvalidOperationError`.**
2. `test_frame_stats_temporal_boundary` — siblings for `Date`, `Datetime`, `Datetime("UTC")`,
   `Time`: value counts present (`expandable is True` given low cardinality), locking the boundary
   so a future gate change can't over-exclude.
3. Mixed-frame test: Duration alongside numeric + text columns — all other columns' stats unchanged
   (guards against the fix accidentally reordering/renaming aggregation aliases).
