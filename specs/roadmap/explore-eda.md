# Explore / EDA roadmap

## Scope

Explore reports provide correct, bounded, cached analysis and clear analyst
workflows. Current behaviour is specified in
[Explore / EDA](../explore-eda/high-level.md) and
[frontend preview/explore](../frontend-preview-explore/high-level.md).

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| EDA-E09 | Planned | P2 | Add bounded server-binned distributions. |
| EDA-E10 | Planned | P2 | Add one cached on-demand relationship/key-analysis service. |

## Planned improvements

### EDA-E09 — Distribution charts
**Why:** Analysts need distributions without client-side raw-data processing.

**Plan:** Emit capped server-binned numeric histograms from the bounded report path and render them with explicit empty/skipped states.

**Acceptance:** Tests cover null, constant, negative, and wide-schema guardrail cases plus chart rendering.

**Dependencies:** Delivered bounded collection and tab/panel contracts (formerly EDA-E03, EDA-E06, EDA-E07).

**Evidence:** `src/haute/routes/_explore_service.py`; `src/haute/schemas.py`; `frontend/src/panels/explore`; `tests/test_explore_routes.py`.

### EDA-E10 — Target relationships
**Why:** A report needs bounded, target-aware signals for feature investigation.

**Plan:** After EDA-E09 establishes the bounded distribution primitives, add
one on-demand analysis job/cache surface with explicit cache-miss and
cancellation behaviour. It owns bounded numeric/categorical target
aggregations, target/weight configuration, and exact user-selected
multi-column key uniqueness checks. Key analysis is not a second synchronous
scan path or a base-report estimate.

**Acceptance:** Tests cover cache miss, cancellation/supersession, target and
weight validation, numeric and categorical results, bounded levels, ranked UI
rendering, exact single-/multi-column key counts, unhashable key rejection, and
cache identity for the selected analysis and columns.

**Dependencies:** EDA-E09 plus delivered bounded collection, dataframe-cache,
job-lifecycle, and tab/panel contracts (formerly EDA-E03, EDA-E06, EDA-E07,
EDA-E13).

**Evidence:** `src/haute/routes/explore.py`; `src/haute/routes/_explore_service.py`; `frontend/src/panels/explore`; `tests/test_explore_routes.py`.

## Delivered outcomes

- `EDA-E11` report schema v5 adds valid-value uniqueness ratios,
  high-cardinality and conservative identifier-candidate cues, min/mean/max
  text length, temporal span, and exact full-row duplicate count/ratio. These
  remain in the existing single cancellable aggregate; an Object column makes
  whole-row duplicates explicitly unknown rather than estimated. The Schema
  card renders, searches, copies, and downloads the factual cues. Backend,
  runtime-guard, and card regressions cover representative/null/boundary
  behaviour. User-selected multi-column key analysis is deliberately folded
  into EDA-E10's on-demand job/cache surface above, where its scan lifecycle
  can be explicit.
- Wide-schema search/pagination, sticky table headers, explicit column counts,
  semantic tables, and a clamped named Explore progressbar complete `EDA-E08`.
  `ExplorePreview.test.tsx` and the focused card suites pin the progress and
  navigation semantics.
- `EDA-E12` adds read-only native-button TSV copy and CSV download actions to
  Schema, Numeric Summary, and Categorical Summary. The actions reuse the
  shared serializers, disable on empty tables, use card-specific accessible
  names, and export every filtered schema row independent of pagination.
- Duration-safe value counts, truthful NaN/null statistics, one batched
  cancellable streaming collect with typed memory-limit outcomes, stat-gated
  input fingerprints, and lossy-decoded binary labels (`EDA-E01`–`EDA-E05`)
  are present-tense contracts in
  [the Explore specification](../explore-eda/high-level.md), with regressions
  in `tests/test_explore_routes.py`.
- The two content-backed Preview/Overview tabs (`EDA-E06`) and the
  hide-stale-reports panel design that superseded `EDA-E07`'s labelled-stale
  plan are specified in
  [the frontend preview/explore specification](../frontend-preview-explore/high-level.md).
- The quantile portion of `EDA-E11` (p25/median/mean/p75/std) remains part of
  the numeric profile.
- `EDA-E13` is covered generically by the shared dataframe execution cache and
  background-job lifecycle components, so no Explore-specific cache/job
  robustness work remains.
