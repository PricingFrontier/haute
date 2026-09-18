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
| RAT-B03 | Planned | P2 | Serve whole-dataset raw factor levels to the Rating Step editor. |

## Planned improvements

Delivery order is `RAT-B03`.

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
