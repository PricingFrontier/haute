# P03 — Trace correlation: warm-click hot path, float tolerance, row anchoring, value fidelity

**Severity:** HIGH (latency) + MEDIUM silent-wrongness · **Effort:** M · **Dev/reviewer pair: REQUIRED** (silent-wrongness class)

Files: `src/haute/_trace_correlation.py`, `src/haute/trace.py`, `src/haute/_trace_enrichment.py`

These findings interlock: FR-04 (delete the duplicated fast path) is only safe after FR-05 (unify float
tolerance), and FR-03's vectorisation is what makes the deletion free. Implement in the order
FR-05 → FR-03 → FR-04 → FR-06 → FR-07, then the smaller items.

---

## FR-05 [MEDIUM, silent wrongness] — the two correlation paths disagree on float equality
**`_trace_correlation.py:233` vs `:118-120`**

- The fast path and `_shared_key_is_unique` compare via `_trace_values_match` →
  `math.isclose(rel_tol=1e-9, abs_tol=1e-12)` (:118-120).
- The value-match path's `_build_value_match_expr` float branch is **exact**: `pl.col(column) == value`
  (:233), no tolerance.
- Reproduced on the same pair (child `1234.5678`, parent `1234.567800617284`, drift 5e-10, inside the
  fast path's window): `_trace_values_match` → True; `_find_matching_row` → `(None, -1)` miss.
- The `_TRACE_REL_TOL` docstring (:36-42) says the tolerance exists "to absorb the float noise of values
  carried verbatim" — the primary vectorised matcher ignores it. Which behaviour a user gets depends on
  incidental row-count equality (the fast-path gate), not on the data. Result: a trace step silently
  drops from the waterfall.

**Fix:** give `_build_value_match_expr`'s float branch the same tolerance:
`(pl.col(column) - value).abs() <= (_TRACE_ABS_TOL + _TRACE_REL_TOL * abs(value))`, with the existing
null handling (`fill_null(False)`) preserved. Keep exact equality for non-float dtypes.

**Failing test first:** parametrised test feeding the reproduced pair through BOTH paths (force each by
constructing frames with equal/unequal row counts) and asserting both find the row.

---

## FR-03 [HIGH] — `_shared_key_is_unique` full-frame Python scan on the warm-click path
**`_trace_correlation.py:499-529`, called at `:770`**

- `for raw_row in df.iter_rows(named=True): candidate = _jsonify_row(raw_row)` jsonifies **all**
  columns of **every** row while comparing only `shared_cols`; for a unique key (the common, correct
  case) it cannot short-circuit — it must scan the whole frame to prove `count == 1`.
- Measured by the reviewing agent: 1000×20 frame with a unique key → **8.94 ms/call** vs **1.04 ms**
  for a vectorised filter+count (8.5×). A 12-node path ≈ 107 ms/click on this gate alone; the module
  docstring promises "subsequent clicks <10 ms".

**Fix:** vectorise — reuse `_build_value_match_expr` (post-FR-05, tolerance-aware):
`df.lazy().filter(reduce(and_, exprs)).head(2).collect().height == 1` or the sum-of-bools variant.
Then see FR-04: the gate mostly disappears.

**Failing test first (structural, not wall-clock):** monkeypatch `pl.DataFrame.iter_rows` with a spy;
run the shared-columns warm-click path; assert `iter_rows` is not called on the parent frame (today it
is). Plus a correctness test: unique/duplicate/no-match keys give the same booleans as before.

---

## FR-04 [HIGH] — the shared-columns fast path duplicates `_find_matching_row` and double-scans
**`_trace_correlation.py:764-779` vs `:362-453`**

- When shared columns exist and the key is unique, the value-matched index equals what
  `_find_matching_row`'s exact branch (:401-416) returns — the fast path adds only the
  `_shared_key_is_unique` scan on top.
- When the key is **not** unique: the fast path scans in Python, fails its gate, then falls through to
  `_find_matching_row` at :782 which scans **again**. Two full passes.
- The only capability unique to the fast path is the **no-shared-columns positional fallback**
  (:774 `elif`).

**Fix:** delete the shared-columns branch; keep only the no-shared-columns `elif`; let
`_find_matching_row` handle everything else. **Precondition: FR-05 merged first** — otherwise
verbatim-carried floats that the fast path accepted would start missing.

**Tests:** the existing correlation suite is the net here; add one test pinning the no-shared-columns
positional fallback survives, and one asserting a non-unique shared key produces the same
diagnostics-recorded outcome as before with only one scan (spy on the filter/collect count if
practical).

---

## FR-06 [MEDIUM, silent wrongness] — `_find_target_row_index` silently returns the first duplicate
**`trace.py:262-271`**

- `for idx, row in enumerate(df.select(shared).iter_rows(named=True)): if all(_trace_values_match(...)): return idx`
  — first match wins, no ambiguity check. This is the relocation path taken at `trace.py:489` when the
  cached preview was evicted and row order changed.
- If the clicked row's visible columns aren't unique (two policies with identical displayed attributes),
  the whole trace re-anchors to the wrong row: every upstream value is correct-for-the-wrong-row.
  One call deeper, `_find_matching_row` refuses exactly this (records `duplicate_exact_match`, returns
  `(None, -1)`); the entry point is the weakest link. Note `shared` also drops `row_values` columns
  absent from the target frame, further weakening uniqueness.

**Fix:** collect matching indices with a short-circuit at 2; if >1, raise the same
"Trace data does not match … please click the node to refresh" `ValueError` shape used at
`trace.py:493` (routes already map that message to HTTP 409). Never return index 0 on ambiguity.
While here, vectorise the scan with the same tolerance expression as FR-05 (this is also an
`iter_rows` full scan).

**Failing test first:** frame with two identical visible rows; assert ValueError (currently returns 0).

---

## FR-07 [MEDIUM, fidelity] — `_jsonify_row` renders values unlike the preview table
**`_trace_correlation.py:84-98`**

- `else: clean[k] = str(v)` for non-primitives. Reproduced: `Datetime` → `"2020-01-02 03:04:05"`
  (space) in the trace vs `"2020-01-02T03:04:05"` (ISO T) via `row_to_json_safe`; `List(Int64)` →
  the *string* `"[1, 2, 3]"` in the trace vs the JSON *array* `[1, 2, 3]` in the preview.
- Module docstring guarantees the trace "always shows exactly the data the user sees in the preview
  table" — violated at the type level for List/Struct/Array columns.

**Fix:** route non-primitives through the canonical `to_json_safe` (`haute._json_safe`) —
`{k: to_json_safe(v) for k, v in row.items()}` keeping the fast primitive short-circuits. The comment
at :86-88 explicitly frames the `str()` path as "older trace behavior"; this closes that seam.
Check the impact on `_trace_values_match` comparisons of jsonified values (they compare
jsonified-vs-jsonified, so switching both sides together is safe — verify with tests for temporal and
list columns).

---

## FR-08 [MEDIUM, perf] — `_match_columns_by_row_index` O(rows×cols) Python double-loop
**`_trace_correlation.py:283-315`**

Materialises every alias column with `.to_list()` and double-loops per cell, even when the exact-match
case (:401-416) could be answered by one vectorised filter returning ≤2 indices. **Fix:** try the exact
match vectorised first (`indexed.filter(reduce(and_, exprs)).select("__tmp_idx").head(2)`); build the
per-cell `matched_by_row` accounting **only** when exact matching found nothing and `allow_relaxed` is
set. Behaviour must be identical — the relaxed path keeps its current semantics.

## FR-09 [LOW] — relaxed match accepts width-1 with no confidence signal
**`_trace_correlation.py:418-431`** — a unique best-subset match of width 1 (out of 5 shared columns)
is accepted with empty diagnostics. **Fix:** when `best_relaxed_width` is much smaller than
`len(original_shared)` append a non-fatal `low_confidence_relaxed_match` diagnostic (mirror the
existing tie diagnostic).

## FR-10 [MEDIUM, perf] — `_build_input_sources` copies `visited` per branch, defeating memoisation
**`_trace_enrichment.py:1218-1231`** — `visited=set(visited)` per recursive call means a
`(node_id, ref_col)` resolved under one branch is re-derived (re-running `evaluate_expression`) under
every sibling branch. Diamond dependencies re-walk shared subtrees once per path (bounded by
`max_depth=3`). **Fix:** share one visited/memo dict across the invocation; the key already includes
node and column so cross-branch reuse is sound. Failing test: counter on `evaluate_expression` calls
for a diamond dependency (expect 1, currently 2).

## FR-11 [LOW] — inconsistencies and stale comments
- `_trace_enrichment.py:1092` locates the current step via `all_steps.index(current_step)` (value
  equality on a mutable dataclass, O(n)) while `:1347` matches `s.node_id == current_step.node_id`.
  Unify on node_id; a `{node_id: index}` map built once per `enrich_steps` removes the O(n) lookups.
- `trace.py:368` says "single-entry cache"; the cache is an 8-entry byte-bounded LRU (the header
  comment at :211-226 is correct). Reword.

---

## Acceptance for the package
- Warm-click correlation does zero `iter_rows` full scans on the row-location path.
- One scan (not two) for non-unique shared keys.
- Both matching paths share one tolerance definition (a single constant/expr helper — add a unit test
  that greps/asserts `_build_value_match_expr` uses `_TRACE_REL_TOL`).
- Ambiguous target rows raise (409 at the route), never silently anchor to row 0.
- Trace step values for temporal/list/struct columns are byte-identical to the preview serialisation.
- Full trace test suite green.
