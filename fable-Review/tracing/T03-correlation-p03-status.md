# T03 — Correlation hot path & tolerance: P03 re-verified, re-measured, unchanged in design

**Severity:** HIGH (perf) + HIGH silent-wrongness (FR-05, escalated) · **Effort:** M
**Dev/reviewer pair: REQUIRED** (FR-05/FR-07 are silent-wrongness; the rest is mechanical under test cover)
**Files:** `src/haute/_trace_correlation.py`, `src/haute/trace.py`, `src/haute/_trace_enrichment.py`
**Canonical fix designs:** `fable-Review/polars-backend/P03-trace-correlation.md` — **implement as
specified there, in the order FR-05 → FR-03 → FR-04 → FR-06 → FR-07 → FR-08/09/10/11.**
FR-06 is superseded by this review's **T02** (same site, now CRITICAL with an e2e repro).
**Repros:** `repros/repro_trace.py` (FR-05/FR-07), `repros/bench_correlation.py` + `repros/bench_e2e.py`
(fresh measurements), `repros/probe_fr10.py` (FR-10).

## What this doc adds over P03 (do not re-derive the fixes — they still apply verbatim)

**Status: every P03 item FR-03…FR-11 is still open in the current tree.** Commit `b19ff1f4`
(W4-trace) hardened `_find_matching_row`'s ambiguity handling and made `_build_value_match_expr`
dtype-robust, but touched none of the FR sites. Three items were re-confirmed empirically this
review; the perf items were re-measured at larger, more realistic shapes — the picture is *worse*
than P03 recorded:

| FR | Status | Fresh evidence (this review) |
|---|---|---|
| FR-05 float split | **OPEN — escalate to HIGH** | Reproduced: drift 6.17e-07 (rel 5e-10) → `_trace_values_match` True, `_build_value_match_expr` False (`:233` still exact `==`). Which path runs depends on incidental row-count equality ⇒ a contributing step non-deterministically vanishes from trace + waterfall. |
| FR-03 `_shared_key_is_unique` scan | **OPEN — dominates warm clicks** | Measured: 11.9 ms mean @1000×20, **144.8 ms @5000×50** (vectorised equivalent 0.5–0.8 ms → 22–182×). Fires once per edge-join/reordering child per click (edge-join has no `code` ⇒ `_child_transform_may_reorder` returns True). E2e: **68–73 % of every warm click** on a diamond+join graph (28.7 ms @1000×20, 62.6 ms @5000×50 vs the documented 10 ms budget). |
| FR-04 double scan | **OPEN** | Fast path at `:764-780` still precedes `_find_matching_row` at `:783`; e2e run shows both firing. Delete-the-branch design unchanged; precondition FR-05. |
| FR-06 first-duplicate anchor | **OPEN → see T02** | Now CRITICAL, reproduced end-to-end with empty diagnostics. |
| FR-07 `_jsonify_row` stringification | **OPEN** | Reproduced: Datetime `'2020-01-02 03:04:05'` vs preview `'2020-01-02T03:04:05'`; List ships as the *string* `'[1, 2, 3]'` vs JSON array. Note `tests/test_trace.py:120` (`test_non_primitives_stringified`) pins the OLD behaviour and must be updated with the fix. |
| FR-08 per-cell accounting | **OPEN** | Now *half*-vectorised (per-column exprs via `_build_value_match_expr`, `:283-315`) but still materialises every alias column and double-loops rows×cols before the exact check: 2.5 ms @1000×20, 13.0 ms @5000×50. Exact-filter-first design unchanged (~1.6× at 50 cols — predicate build dominates; still worth it, and it removes the Python loop class). |
| FR-09 relaxed-width confidence | **OPEN** | `:418-431` unchanged; no `low_confidence_relaxed_match` diagnostic. |
| FR-10 `visited=set(visited)` | **OPEN** | Reproduced: diamond lineage evaluates the shared subtree twice (4 `evaluate_expression` calls where 3 suffice). Absolute cost small (enrich ≈1–2 ms) — keep at LOW priority. |
| FR-11 comments/lookup idiom | **OPEN, downgraded** | Adversarially checked: the `all_steps.index(current_step)` value-equality collision **cannot** misfire because node_ids are unique per step list. Style/O(n) only. `trace.py:368` "single-entry cache" comment still stale. |

## Design corrections/confirmations for the implementer (vs the P03 text)

- `_build_value_match_expr` is now dtype-robust (numeric-vs-Utf8 degrades to non-match instead of
  raising — a W4 improvement worth pinning with a test). The FR-03/FR-08 vectorisation should reuse
  it exactly as P03 assumed; no new predicate logic is needed. Keep `fill_null(False)`.
- FR-05's tolerance expr goes in the float branch only (`Float32/Float64` column dtypes); keep exact
  equality for ints/strings/temporal — `_trace_values_match` behaviour is the reference semantics.
- The no-shared-columns positional `elif` (`:777`) and the `_child_transform_may_reorder` gate
  (`:478-496`) are correct and must survive FR-04's deletion — pin both with tests before deleting.
- FR-07: route non-primitives through `to_json_safe`; both comparison sides switch together
  (`_trace_values_match` compares jsonified-vs-jsonified). Add temporal + List columns to the
  trace-matches-preview suite (`tests/test_trace_matches_preview.py`).

## Acceptance (supersedes P03's list only where noted)

- P03's package acceptance holds verbatim, plus:
- Warm click ≤10 ms on the diamond+join 1000×20 shape in `repros/bench_e2e.py` (was 28.7 ms);
  the 5000×50 shape ≤20 ms (was 62.6 ms) — assert structurally (zero `iter_rows` on the row-location
  path), not by wall-clock in CI.
- A regression test pins that BOTH matching paths accept the FR-05 repro pair.
- `test_non_primitives_stringified` updated to the preview-identical serialisation.
