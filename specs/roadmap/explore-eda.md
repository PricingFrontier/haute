# Explore / EDA roadmap

## Scope

Explore reports provide correct, bounded, cached analysis and clear analyst
workflows. Current behaviour is specified in
[Explore / EDA](../explore-eda/high-level.md) and
[frontend preview/explore](../frontend-preview-explore/high-level.md).

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| EDA-E08 | Active | P1 | Complete accessible progress semantics on Explore cards. |
| EDA-E09–EDA-E12 | Active | P2 | Add bounded analysis, target insight, deeper profiles, and export. |

## Planned improvements

### EDA-E08 — Scalable accessible cards
**Why:** Wide schemas make cards hard to browse and expose small accessibility gaps.

**Plan:** Add bounded card browsing with search/pagination and sticky headers; expose column count and semantic progress/table markup.

**Acceptance:** Wide-schema and accessibility tests verify navigation, headers, progress roles, and snapshot counts.

**Dependencies:** Delivered panel-state semantics (formerly EDA-E07).

**Evidence:** `frontend/src/panels/explore`; `frontend/src/panels/explore/__tests__/*.test.tsx`.

### EDA-E09 — Distribution charts
**Why:** Analysts need distributions without client-side raw-data processing.

**Plan:** Emit capped server-binned numeric histograms from the bounded report path and render them with explicit empty/skipped states.

**Acceptance:** Tests cover null, constant, negative, and wide-schema guardrail cases plus chart rendering.

**Dependencies:** Delivered bounded collection and tab/panel contracts (formerly EDA-E03, EDA-E06, EDA-E07).

**Evidence:** `src/haute/routes/_explore_service.py`; `src/haute/schemas.py`; `frontend/src/panels/explore`; `tests/test_explore_routes.py`.

### EDA-E10 — Target relationships
**Why:** A report needs bounded, target-aware signals for feature investigation.

**Plan:** Add an on-demand cached analysis endpoint with explicit cache-miss behaviour, bounded numeric/categorical aggregations, and target/weight configuration.

**Acceptance:** Tests cover cache miss, target validation, numeric and categorical results, bounded levels, and ranked UI rendering.

**Dependencies:** Delivered bounded collection and tab/panel contracts (formerly EDA-E03, EDA-E06, EDA-E07).

**Evidence:** `src/haute/routes/explore.py`; `src/haute/routes/_explore_service.py`; `frontend/src/panels/explore`; `tests/test_explore_routes.py`.

### EDA-E11 — Quality profile extensions
**Why:** Current profiles omit useful tails, temporal/text cues, and key-quality signals.

**Plan:** Add bounded quantiles, temporal and text summaries, ID/high-cardinality flags, duplicate counts, and on-demand key uniqueness checks.

**Acceptance:** Each new signal has null, boundary, and representative-frame regression coverage.

**Dependencies:** Delivered bounded collection (formerly EDA-E03).

**Evidence:** `src/haute/routes/_explore_service.py`; `src/haute/schemas.py`; `tests/test_explore_routes.py`.

### EDA-E12 — Export workflow
**Why:** Existing table export utilities are not available from Explore results.

**Plan:** Wire accessible copy and CSV export actions to supported cards using shared escaping and download utilities.

**Acceptance:** Tests cover TSV/CSV quoting, headers, disabled/empty states, and keyboard-accessible actions.

**Dependencies:** Delivered tab contract (formerly EDA-E06).

**Evidence:** `frontend/src/panels/editors/shared/tableClipboard.ts`; `frontend/src/panels/editors/FrameTableActions.tsx`; `frontend/src/panels/explore`.

## Delivered outcomes

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
- The quantile portion of `EDA-E11` (p25/median/mean/p75/std) already ships in
  the numeric profile; only the remaining signals stay active above.
- `EDA-E13` is covered generically by the shared dataframe execution cache and
  background-job lifecycle components, so no Explore-specific cache/job
  robustness work remains.
