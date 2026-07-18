# P07 — Rating step: combine null gap, dead NaN mask, miss ergonomics, miss-guard cost

**Severity:** MEDIUM (one silent-wrongness) · **Effort:** M · **Dev/reviewer pair: REQUIRED** (FR-19/FR-20 are the silent-wrongness class)

File: `src/haute/_rating.py`

Context the implementer needs: rating tables support `defaultValue` and `onMissing` ∈
{"error" (default), "neutral"}. "Neutral" misses leave **null** in the table's output column; combined
outputs then merge several table columns with one of multiply/add/min/max.

---

## FR-19 [MEDIUM, silent wrongness] — `min`/`max` combine leaves all-null rows null; `multiply`/`add` fill neutral
**`_rating.py:717-727` (combine expressions)**

### Evidence
- multiply: `pl.col(c).fill_null(1.0)` per column; add: `.fill_null(0.0)`.
- min/max: bare `pl.min_horizontal(...)` / `pl.max_horizontal(...)` — horizontal min/max skip nulls,
  so partial-null rows are fine, but a row where EVERY combined table missed yields **null**.
- Reproduced by the reviewing agent: two neutral-missed inputs → `min` gives `[None, 1.0]` where
  `multiply` gives `[1.0, 2.0]`.
- Reachable through the public `apply_rating_step_from_config` path with `onMissing: "neutral"` +
  a min/max combined output. The null then propagates into downstream price arithmetic silently.

### Fix design (fail loud, not fill)
+inf/-inf are not valid neutral elements for pricing, so do NOT just fill. Choose explicitly:
- (a) **Raise** when a min/max combined output would produce an all-null row (a
  `map_batches`-free check: `pl.all_horizontal(pl.col(cols).is_null())` any-true → raise a
  `RatingTable*`-family error naming the output and the row condition), or
- (b) **Config-time guard:** require every table feeding a min/max combined output to have a
  `defaultValue` or `onMissing: "error"`, rejected at config validation with a clear message.
Option (b) is cheaper at runtime and surfaces at edit time — preferred unless fixtures show legitimate
all-null-min use. Whichever is chosen, document the rule in the rating config docs/sidecar schema.

**Failing test first:** config with two neutral tables + min combine; feed a row missing in both;
expect the loud error (today: silent null).

---

## FR-20 [LOW, masking + dead code] — `fill_nan(1.0/0.0)` on multiply/add is unreachable and would mask
**`_rating.py:717, 727`**

`_apply_rating_table` already rejects NaN/Inf entries (:581), null entries (:591), and non-finite
defaults (:565), so no NaN can reach the combine. If one ever did (regression elsewhere), `fill_nan`
would silently turn it into a neutral factor — a wrong price that looks fine. **Fix:** delete the
`fill_nan` calls; add a comment pointing at the upstream rejection sites. (This also removes the
asymmetry that produced FR-19's confusion.)

**Test:** none needed beyond existing suite; optionally an integration test asserting a NaN smuggled
past validation propagates loudly (xfail-style documentation test).

---

## FR-21 [LOW-MEDIUM, ergonomics] — factor present in entries but absent from the input frame → raw Polars error
**`_rating.py:643-655`**

`original_dtypes`/`cast_exprs` are built only `for f in factors if f in existing_cols` (:643, :649),
but the join runs `on=factors` with the full list (:655). A factor column missing from the input frame
is never validated → bare `ColumnNotFoundError` at collect, naming a raw column with no table context
(contrast the friendly `RatingTableMissError`). **Fix:** before the join,
`missing = [f for f in factors if f not in existing_cols]`; raise a rating-specific error naming the
table and the missing factor(s).

**Failing test first:** rating config whose table names a factor not in the frame → assert the typed
error message contains the table name (today: `ColumnNotFoundError`).

---

## FR-22 [MEDIUM, perf — optional, measure first] — miss guard adds a per-morsel Python callback + full-width factor struct on every default-less table
**`_rating.py:453-507` (guard construction), `:661-670` (wiring when `default_val is None`)**

### Evidence
`pl.struct([*factors, _LOOKUP_VAL]).map_batches(_check, return_dtype=pl.Float64, is_elementwise=True)`
is attached whenever a table has no `defaultValue` and `onMissing="error"` — the common case. The
struct materialises a copy of ALL factor columns even when nothing misses; `_check` runs
`struct.unnest()` + null-count per morsel. N default-less tables ⇒ N Python callbacks + N full-width
struct copies per morsel, on the hottest data path. `is_elementwise=True` keeps it
streaming-compatible, so this is cost, not a correctness barrier.

### Fix design
Keep fail-loud, drop the constant cost: detect misses with a pure-expression guard on `_LOOKUP_VAL`
only, and only re-derive the offending factor values on the (rare) miss branch:
- Cheap path: after the join, `null_count = pl.col(_LOOKUP_VAL).null_count()` folded into the plan;
  raise from a `map_batches` that receives ONLY `_LOOKUP_VAL` (1 column, still elementwise) and, on
  miss, raises a first-pass error with the table name and row count; a follow-up eager query on the
  failed batch (or a documented second pass) reconstructs the offending factor keys for the message.
- The error message may legitimately become two-stage (count first, keys on demand) — preserve the
  error TYPE and the table-identifying fields; tests pin the type + table name, not the exact prose.

**Tests:** existing miss-error tests keep passing (type + table name); new structural test asserting
the guard expression no longer structs all factor columns (inspect the expression tree or spy on
`pl.struct` arity for the guard construction).

---

## FR-23 [LOW] — `_combine_rating_output` resolves schema per output on the growing plan
**`_rating.py:824-826`** — calls `lf.collect_schema().names()` (when `base_value is not None`) once
per combined output, while the table loop deliberately threads one `schema` dict (:887-905) to avoid
O(N²) resolution. **Fix:** thread the same local schema view into `_combine_rating_output`.
