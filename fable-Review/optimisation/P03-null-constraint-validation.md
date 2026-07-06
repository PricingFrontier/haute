# P03 — Null constraint/objective values pass validation and silently corrupt results

**Severity:** HIGH (silent wrongness) · **Effort:** S–M

## FO-07 — `routes/_optimiser_service.py:229-249` (`_value_contract_validation_exprs`) + `:1409-1557` (auto-range accumulator)

### Evidence
Input validation null-checks **only** `quote_id`, and checks floats only for NaN/inf — null is
neither:

```python
# _value_contract_validation_exprs (:238-248)
if validate_quote_id_nulls:
    validation_exprs.append(pl.col(quote_id_col).null_count().alias(...))
for index, cname in enumerate(non_finite_check_cols):
    checked = pl.col(cname).cast(pl.Float32) if ... else pl.col(cname)
    validation_exprs.append(checked.is_nan().sum().alias(...))
    validation_exprs.append(checked.is_infinite().sum().alias(...))   # no null_count
```

**Auto-range path** (frontier range estimation): the accumulator aggregates per-quote
`min()/max()` (`:1437-1442`) — Polars min/max skip nulls, so a quote whose constraint is null in
every scenario contributes a null row — then `finish()` reduces buckets with `sum()`
(`:1449-1454`, applied at `:1523-1527`) — Polars sum skips nulls, so that quote **silently
vanishes from the envelope**. An entirely-null constraint column sums to `0.0`, producing
`{min: 0.0, max: 0.0}`, which passes both guards at `:1552-1555` (`np.isfinite` ✓,
`min <= max` ✓) and becomes the frontier bounds for the whole solve.

**Solve path**: `_validate_and_project` (`:4390-4480`) applies the same NaN/inf-only exprs, then
the frame is sunk to parquet and handed to the Rust `build_grid_from_parquet_chunked`
(`:4762`). The engine's own null rejection (`price_contour/solver.py:1639-1674`,
`_reject_nulls_and_nans`) runs only on the **DataFrame** entry path — the pre-built-grid path
Haute always uses skips it (`solver.py:101-121` "Pre-built grid path", `:304-306` "skip
DataFrame validation (already encoded in the grid)"). Null objective/constraint/scenario values
therefore reach the Rust grid builder unvalidated, with engine behaviour undefined from the
Python layer's point of view.

### Impact
Wrong frontier ranges (and downstream, wrong optimisation bounds) with **no error** — the exact
silent-wrongness class CLAUDE.md forbids. Nulls in pricing data are routine (failed joins,
partial scores), so the trigger is realistic. The solve path's exposure depends on opaque Rust
behaviour — which is precisely why the Python boundary must reject nulls loudly.

### Fix design
In `_value_contract_validation_exprs`, alongside the NaN/inf counters add
`pl.col(cname).null_count().alias(f"{_NON_FINITE_COUNT_ALIAS_PREFIX}null_{index}")` for every
checked column, and extend `_non_finite_detail_from_counts` to fold null counts into the same
loud 400 detail ("null rows" alongside "NaN"/"infinite"). Because both
`_validate_and_project` (solve/estimate) and `_validate_and_project_auto_range` funnel through
`_validate_input_value_contracts`, one change covers all entry points. Keep the existing
message style: name each offending column with its per-kind row counts.

Note: `scenario_index` is int-typed (cast to Int32 later) so it is not in
`non_finite_check_cols`; add an explicit null check for it too in the solve path (a null step
index is equally fatal to grid construction). Columns list at `:4458`.

### TDD plan (failing tests first)
1. `tests/test_optimiser_routes.py::test_frontier_auto_range_rejects_null_constraint_values` —
   2-quote scenario frame, quote B's constraint null in every row; POST `/frontier/auto-range`;
   assert 400 naming the constraint column. Fails today (returns a silently-wrong envelope).
2. `tests/test_optimiser_routes.py::test_frontier_auto_range_all_null_constraint_column_fails_loudly`
   — entire constraint column null; assert 400, not a `[0, 0]` range payload.
3. `tests/test_optimiser_service_validation.py::test_solve_rejects_null_objective_rows` and
   `::test_solve_rejects_null_constraint_rows` and `::test_solve_rejects_null_scenario_index`
   — solve setup fails with 400 naming the column before any grid build.
4. Parity check: the detail message format matches the existing NaN/inf wording (single message
   listing all offending columns in one pass — do not add a second validation scan; see P07).

### Acceptance
- Null values in objective, scenario_value, scenario_index, or any constraint column fail the
  solve, estimate, and auto-range endpoints with a 400 naming the column(s) and row counts.
- Validation still happens in the single existing streaming pass (no extra scan).
- Existing NaN/inf tests unchanged; `test_optimiser_service_validation.py` suite green.
