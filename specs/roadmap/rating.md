# Rating roadmap

## Scope

Banding and rating editors show counts and levels that agree with execution
and cover the whole dataset when its data point is cached. Current behaviour
is specified in [rating](../rating/high-level.md) and
[frontend node editors](../frontend-node-editors/high-level.md). The shared
data-point, lease, and analysis contracts these packages consume are owned by
the [caching roadmap](caching.md).

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| RAT-B02 | Planned | P2 | Serve whole-dataset banding statistics and show them in the Banding editor. |
| RAT-B03 | Planned | P2 | Serve whole-dataset raw factor levels to the Rating Step editor. |

## Planned improvements

Delivery order is `RAT-B02` → `RAT-B03`.

### RAT-B02 — Whole-dataset banding statistics

**Why:** Banding distributions, category values, and match counts come from
preview rows in the browser, so rare categories and tails are missing and
counts disagree with execution.

**Plan:**

- `POST /api/banding/stats` takes `graph`, `node_id` (the Banding node),
  `source`, the in-editor `factor` (so unsaved edits are counted),
  `histogram_bins` (1–200, default 40), and `value_limit` (1–10000,
  default 500). It resolves the node's data point and runs under an
  admitted execution context, as the request-time analysis helper does.
- Response `status: "ok"` carries `total_rows`, `data_version`, `null_count`,
  and, per mode:
  - numeric modes (`continuous`, `breakpoints`): `non_finite_count`, `min` and
    `max` over finite values, and `bins`: equal-width `[lower, upper)` bins over
    finite values with the last bin closed `[lower, upper]`. A constant column
    gives one bin `[v, v]` holding every finite value. No finite values gives
    `bins: []` and null `min`/`max`.
  - `categorical`: `values` as `{value, count}` using the same text cast
    execution matches on, sorted by count descending then value ascending,
    capped at `value_limit`; `distinct_count` over non-null values; and
    `other_count` for non-null rows outside the returned values. Nulls count
    only in `null_count`; `"NaN"` and `"inf"` are ordinary values.
  - `rule_counts` and `unmatched_count` from RAT-B01 when `factor.rules` is
    non-empty.
- Failures: `status: "cache_required"` with the point state when the point is
  not current; HTTP 422 `banding_column_missing` when the column is absent from
  the data point, `banding_column_not_numeric` for a numeric mode on a
  non-numeric column, and `banding_rules_invalid` with execution's message for
  rules execution would reject; HTTP 400 `node_data_point_invalid`; HTTP 507
  for admission or memory-limit failure.
- The Banding editor shows the shared `DataCacheButton` in its header. With a
  current point it requests statistics 250 ms after the last factor edit,
  aborts the previous request, and keeps the last result visible while
  loading. Otherwise it computes the same shapes from preview rows. The basis
  label reads `Sample · N rows`, `All rows · N`, or, for a stale point,
  `Cached data is out of date` with the refresh action; full-data counts are
  never shown for a stale point. `BandingHistogram` renders `bins`.

**Acceptance:**

- Route tests reuse the RAT-B01 vectors through a cached data point and assert
  the numeric bin cases (constant, all-null, all-non-finite, mixed), the
  categorical ordering, cap, `distinct_count`, `other_count`, and null
  accounting, each 422/400/507 case, and `cache_required` for missing and stale
  points.
- A refreshed point with an unchanged signature returns counts from the new
  generation.
- Editor tests cover debounce, abort of a superseded request, the three basis
  labels, and preview fallback shapes matching the server shapes for the same
  rows.

**Dependencies:** RAT-B01; the shared frontend data cache; the caching data-point resolver; the equal-width
binning helper is shared with `EDA-E09`.

**Evidence:** `frontend/src/panels/editors/BandingEditor.tsx`;
`frontend/src/panels/editors/banding/BandingHistogram.tsx`;
`src/haute/routes/_pivot_service.py`; `src/haute/_rating.py`;
`frontend/src/__tests__/editors/BandingEditor.test.tsx`.

### RAT-B03 — Whole-dataset rating factor levels

**Why:** The Rating Step editor lists raw factor levels from preview rows, so
levels absent from the preview cannot be chosen.

**Plan:**

- `POST /api/rating/levels` takes `graph`, `node_id` (the Rating Step node),
  `source`, `columns`, and `value_limit` (1–10000, default 1000). For each
  column it returns `values` as `{value, count}`, `distinct_count`, and
  `null_count`, with keys produced by the lookup's own `_rating_key_expr`, so a
  chosen level matches at lookup time. Only string columns are listed, as the
  preview path does today; nulls and blank strings are excluded from `values`
  and nulls counted in `null_count`.
- Failures match RAT-B02: `cache_required`, HTTP 422
  `rating_level_column_missing` and `rating_level_column_not_string`, HTTP 400
  `node_data_point_invalid`, HTTP 507.
- The Rating Step editor shows the shared `DataCacheButton` and uses these
  levels for raw factor columns when the point is current, keeping preview
  levels otherwise. Banded outputs keep their levels from banding config.

**Acceptance:** Route tests cover key agreement with the rating lookup for a
chosen level, the cap and ordering, blank and null exclusion, each failure
case, and `cache_required` for missing and stale points. Editor tests cover the
switch between preview and whole-dataset levels.

**Dependencies:** RAT-B02; the rating key-normalisation contract.

**Evidence:** `src/haute/_rating.py`;
`frontend/src/panels/editors/RatingStepEditor.tsx`;
`frontend/src/panels/editors/rating/ratingTableUtils.ts`;
`frontend/src/__tests__/editors/RatingStepEditor.test.tsx`.
