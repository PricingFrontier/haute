# P01 — Applying one frontier point wipes the quote grid, breaking every subsequent point apply

**Severity:** HIGH (broken core workflow) · **Effort:** S · **Silent-wrongness:** no (fails loudly, but forces a full re-solve)

## FO-01 — `routes/optimiser.py:1288` (`apply_lambdas`, frontier-point branch)

### Evidence
```python
# optimiser.py:1281-1289 — frontier-point apply branch
response = OptimiserApplyResponse(
    status="ok",
    total_objective=result["total_objective"],
    constraints=result["constraints"],
    from_artifact=from_artifact,
    **limited_apply_preview_payload(df),
)
_store.clear_result_data(body.job_id)   # default keys → strips ALL heavy objects incl. quote_grid
return response
```

The sibling non-frontier branch (same endpoint, `:1330`) deliberately uses the session-aware helper:

```python
# optimiser.py:663-670
def _clear_result_data_after_user_action(job_id: str) -> None:
    """Slim result data without ending an active frontier-analysis session."""
    job = _store.get_job(job_id)
    if isinstance(job, dict) and _job_has_frontier_points(job):
        _store.clear_result_data(job_id, keys=("solve_result",))   # keeps quote_grid + solver
        return
    _store.clear_result_data(job_id)
```

`_materialise_frontier_point_apply` (`optimiser.py:986-1090`) needs the live `quote_grid` to
materialise any point that does not already have a cached per-point artifact handle
(`touch_heavy_objects(required_keys=("quote_grid",))` → `False` once wiped → HTTP 400
"…Re-run the solve"). The wipe is currently *pinned* by
`tests/test_optimiser_frontier_materialisation.py:269-270` (`assert "quote_grid" not in job`),
so that assertion must be inverted as part of the fix.

### Impact
The frontier workflow is "sweep the tradeoff → inspect several candidate points". Today:
apply point 1 (works; grid wiped) → apply point 2 → **400**, full re-solve required. Only
re-applies of the *same* point survive (served from the cached artifact handle). The per-point
artifact-handle scheme (`frontier_apply_result:{index}`, invalidated as a plural set at
`optimiser.py:967-983`) was clearly designed for multiple points; the wipe defeats it.

### Fix design
In the frontier-point branch replace `_store.clear_result_data(body.job_id)` with
`_clear_result_data_after_user_action(body.job_id)` — the same helper the non-frontier branch
uses. While frontier points exist the grid and solver survive (only the bulky `solve_result`
is shed); when no frontier session is active behaviour is unchanged. No new code paths.

### TDD plan
1. Failing test first —
   `tests/test_optimiser_frontier_materialisation.py::test_apply_two_distinct_frontier_points_without_resolve`:
   arrange a completed online job with a 3-point frontier and live `quote_grid`; act:
   `POST /apply {point_index: 1}` then `POST /apply {point_index: 2}` (patch
   `price_contour.apply_from_grid` with a stub that records calls); assert both return 200
   with distinct previews and the stub ran twice. Fails today: second call → 400.
2. Update the pinned assertion at `test_optimiser_frontier_materialisation.py:269-270`: after a
   frontier-point apply, assert `solve_result` is gone but `quote_grid` **is retained** while
   `_job_has_frontier_points` is true.
3. Regression: non-frontier apply (no frontier points on the job) still fully clears heavy
   objects (existing behaviour, existing tests).

### Acceptance
- Two different `point_index` applies succeed on one solve without re-solving.
- Same-point re-apply still serves `from_artifact=True` from the cached handle.
- A job with no frontier keeps the current full-wipe behaviour.
- Full `test_optimiser_frontier_materialisation.py` + `test_optimiser_routes.py` pass.
