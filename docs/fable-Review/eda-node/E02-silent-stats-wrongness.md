# E02 — Silent stats wrongness: NaN/inf blindness, null-in-distinct, percent rounding

**Severity:** HIGH (silent wrongness — the class CLAUDE.md forbids) · **Effort:** M · **Review:** dev/reviewer pair
Files: `src/haute/routes/_explore_service.py`, `src/haute/schemas.py`,
`frontend/src/utils/formatValue.ts`, `frontend/src/api/types.ts`, `frontend/src/types/guards.ts`
Tests: `tests/test_explore_routes.py`, `tests/fixtures/ui_contracts/*`, frontend vitest

All four findings put a wrong or misleading number in front of an analyst with no error. All were
reproduced empirically on Polars 1.39.2. This package bumps `EXPLORE_CACHE_VERSION` (2 → 3) once
(EF-02 adds report fields).

## EF-02 [HIGH] — NaN/±inf columns show 0% missing, render literal `nan`, and get mislabelled "constant"

### Current behaviour (reproduced)

`null_count()` (`_explore_service.py:449`) does not count NaN. For an all-NaN float column:

```
{'null': 0, 'uniq': 1, 'min': nan, 'max': nan, 'mean': nan, 'median': nan,
 'std': nan, 'p25': nan, 'zero': 0, 'neg': 0}
```

- The Data Quality card never flags it — missing detection keys off `null_count > 0` (`:228-234`).
- It IS flagged, wrongly, as **"1 constant / single-value column"** — `n_unique` returns 1 (NaN is
  one distinct bucket) and `null_count(0) < row_count` passes the guard (`:265-276`).
- Stats render the literal strings `nan` / `inf`: `_format_numeric_profile_value` (`:126-133`) does
  `f"{value:.6g}"`; `StatValueCell` prints the non-null string as-is while Null % reads `0.0%`.
- Partial NaN poisons mean/std but not median/min/max (`[1,2,3,NaN]` → `mean=nan, median=2.5`), so
  a column looks complete with a "broken" average and no explanation.

### Impact

An unnoticed NaN/inf in a premium or exposure column is exactly what this card exists to catch. It
is invisible, and the column is actively mislabelled.

### Fix design

- Add to the numeric aggregation block (`:456-468`): `pl.col(name).is_nan().sum().alias(f"nan::{name}")`
  and `pl.col(name).is_infinite().sum().alias(f"inf::{name}")` — float dtypes only (`is_nan` raises
  on integers; gate with `dtype in (pl.Float32, pl.Float64)` or `dtype.is_float()`).
- Extend `ExploreColumnStat` (`schemas.py:419-439`) with `nan_count: int | None = None`,
  `inf_count: int | None = None`; mirror in `frontend/src/api/types.ts` + `guards.ts`; update
  `tests/fixtures/ui_contracts/*`.
- New Data Quality rule in `_build_data_quality_summary` (`:222-321`): "N numeric column(s) with
  non-finite values", severity `danger` when a column is majority-non-finite, else `warning`,
  detail naming the worst column. Keep it a *visible* issue — do not coerce NaN to null anywhere.
- Fix the constant rule: a column only counts as constant when its single distinct value is a real
  value — require `distinct_count(excluding null, see EF-03) <= 1` AND `nan_count == 0` (or treat
  all-NaN as its own non-finite issue, never "constant").
- Numeric display: render non-finite aggregates distinctly (e.g. `NaN` styled as a warning in
  `StatValueCell` via a new severity prop, or keep text but pair with the quality issue). Minimum
  bar: the quality card names the columns so `nan` cells stop being unexplained.

## EF-03 [MEDIUM] — `n_unique()` counts null as a distinct value; three downstream wrongs

### Current behaviour (reproduced)

```
['a','a',None,None]   n_unique = 2    (pandas nunique = 1)
['a','a','a',None]    n_unique = 2 → constant rule (distinct<=1): NOT flagged
50 distinct + 1 null  n_unique = 51 → values_truncated (>50): True (wrongly)
```

1. Constant-column detection (`:265-276`) misses "one real value + nulls" — an effectively
   degenerate column analysts must know about.
2. `values_truncated` (`:399-402`) goes off-by-one: 50 real groups + nulls reports "Top 50 groups"
   truncation and `head(50)` genuinely drops a real group from the chips.
3. The Distinct column disagrees with pandas' `nunique` by one whenever nulls exist.

### Fix design

Both inputs are already computed — derive `distinct_non_null = n_unique - (1 if null_count > 0 else 0)`
in `_build_frame_stats` and use it as `ExploreColumnStat.distinct_count`. The null presence is
already shown separately (Null % column; "Missing" chip in the expanded values). Fix the constant
rule and `values_truncated` in the same move. Document the semantics in the `ExploreColumnStat`
docstring ("distinct excludes null; nulls are reported via null_count"). Note: NaN still counts as
a distinct float value — that interaction is handled by EF-02's constant-rule guard.

## EF-04 [MEDIUM] — `_percent_text` rounds 99.5–99.9% up to "100%"

### Current behaviour (reproduced)

`f"{ratio:.0%}"` (`:201-207`): `996/1000 → "100%"`. The Data Quality detail reads "worst at 100%"
while rows still carry data — and the adjacent Schema/Numeric tables show the same column as
"99.6%" (frontend `formatNullPct`, `formatValue.ts:54-57`). One card contradicts the other.

### Fix design

Reserve `"100%"` for `numerator == denominator`; render `0.995 ≤ ratio < 1` as `">99%"`; keep
`"<1%"` for `0 < ratio < 0.01`. Severity logic already uses the true ratio (`:238`) — display only.

## EF-05 [LOW] — frontend `formatNullPct` renders tiny-but-real null share as "0.0%"

`formatValue.ts:54-57` uses `toFixed(1)`: a 0.04% null share reads "0.0%" while styled as non-zero
severity (`SchemaTableCard.tsx:39-63` severity "normal"). Mirror EF-04: show `"<0.1%"` for
`0 < share < 0.001`, and `">99.9%"` for the top edge. Keep backend and frontend phrasings
consistent (they will still differ in precision — 1dp tables vs coarse quality text — but must
never *contradict* on the 0%/100% edges).

## EF-06 [LOW] — temporal min/max and value-count labels use different formats

Datetime min/max flow through Python `str()` (`_format_display_value`, `:112-123`) →
`"2024-01-01 00:00:00"`, while the same column's value-count labels come from Polars
`cast(String)` → `"2024-01-01 00:00:00.000000"`. Same column, two formats. Fix by routing temporal
min/max display through the same cast-to-String expression min/max already uses for text
(`_STRING_MIN_MAX_DTYPE_BASES`, `:78-83`) — i.e. add temporal bases to the cast set — or format
both via one Python formatter. Cosmetic; do last.

## EF-22 [LOW] — top-50 tie membership at the cut boundary is nondeterministic

`value_counts(sort=True).head(50)` (`:353-360`) cuts before the deterministic Python re-sort
(`:377-380`), so *which* tied groups survive rank 50 can vary run-to-run. Fix: sort by
`(count desc, value asc)` inside the expression before `head` (`value_counts(sort=True)` then
`.list.sort()` is not directly available pre-implode — implement by sorting the struct list after
implode via `.list.eval(...)`, or accept and document). If the expression-level tiebreak is
awkward in 1.39.2, document the behaviour in the card's "Top 50 groups" tooltip instead. Low
priority; don't spend more than an hour.

## TDD plan (failing tests first)

`tests/test_explore_routes.py` (the `all_null` test at `:737-746` is the pattern; add siblings):

1. `test_frame_stats_all_nan_column` — all-NaN float col: assert `nan_count == n`, a non-finite
   quality issue exists, and NO "constant" issue. **Fails today** (no field, no issue, wrong
   constant label).
2. `test_frame_stats_partial_nan_inf` — `[1.0, 2.0, float("nan"), float("inf")]`: `nan_count == 1`,
   `inf_count == 1`, quality issue present.
3. `test_frame_stats_integer_columns_skip_nan_exprs` — integer col: `nan_count is None`, no crash
   (guards the `is_nan`-on-int trap).
4. `test_distinct_count_excludes_null` — `['a','a','a',None]`: `distinct_count == 1`, constant
   issue present. `['a',None]` vs `['a']` distinguishes only via null_count.
5. `test_values_truncated_ignores_null_group` — 50 real groups + nulls: `values_truncated is False`
   and all 50 real values present in chips.
6. `test_percent_text_edges` — unit test: `(996,1000) == ">99%"`, `(1000,1000) == "100%"`,
   `(5,1000) == "<1%"`, `(0,0) == "0%"`.
7. Frontend vitest (`formatValue` unit + `SchemaTableCard.test.tsx`): `formatNullPct(4, 10000) ===
   "<0.1%"`; contract-fixture update test for the two new `ExploreColumnStat` fields.
8. Bump `EXPLORE_CACHE_VERSION` and assert old-version report-cache keys miss (existing pattern in
   `test_explore_routes.py` cache-key tests).
