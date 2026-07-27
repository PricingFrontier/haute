# Explore / EDA roadmap

## Scope

Explore reports provide correct, bounded, cached analysis and clear analyst
workflows.

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| EDA-E01 | Active | P0 | Support Duration columns without failing a report. |
| EDA-E02 | Active | P0 | Make statistics and quality signals numerically truthful. |
| EDA-E03 | Active | P0 | Bound and cancel statistics collection. |
| EDA-E04 | Active | P0 | Avoid hashing unchanged large inputs on warm requests. |
| EDA-E05–EDA-E08 | Active | P1 | Improve values, panels, and accessible card browsing. |
| EDA-E09–EDA-E13 | Active | P2 | Add bounded analysis, export, and cache robustness. |

## Planned improvements

### EDA-E01 — Duration value counts
**Why:** A Duration column can make the whole report fail.

**Plan:** Exclude unsupported Duration values from categorical value-count aggregation while retaining their other profile data.

**Acceptance:** Duration-only and mixed frames return reports; supported categorical counts remain present.

**Dependencies:** None.

**Evidence:** `src/haute/routes/_explore_service.py`; `tests/test_explore_routes.py`; `tests/test_explore_round_trip.py`; `docs/specs/explore-eda/high-level.md`.

### EDA-E02 — Truthful statistics
**Why:** NaN, infinity, null distinctness, ties, and percentage rounding can misstate data quality.

**Plan:** Track non-finite values explicitly, use non-null distinctness for constants, render non-finite aggregates honestly, and make percentage and temporal/value labels deterministic.

**Acceptance:** Regression fixtures cover NaN/inf, null-only and null-plus-value columns, boundary percentages, temporal labels, and tied top values.

**Dependencies:** EDA-E03 shares the collection path.

**Evidence:** `src/haute/routes/_explore_service.py`; `src/haute/schemas.py`; `frontend/src/panels/explore`; `tests/test_explore_routes.py`.

### EDA-E03 — Bounded statistics collection
**Why:** One unbounded aggregation can exceed memory limits and cannot be interrupted.

**Plan:** Collect statistics in budgeted batches with approximate distinctness where exactness is unnecessary, progress checkpoints, memory checks, and cancellation between batches.

**Acceptance:** Large-frame tests demonstrate bounded batch construction, cancellation, typed memory-limit results, and correct small-frame profiles.

**Dependencies:** EDA-E01, EDA-E02.

**Evidence:** `src/haute/routes/_explore_service.py`; `src/haute/_execution_context.py`; `tests/test_explore_routes.py`.

### EDA-E04 — Stat-gated input fingerprint
**Why:** Warm requests synchronously reread unchanged inputs merely to compute a cache key.

**Plan:** Use the existing stat-gated fingerprint memo for file-backed Explore inputs, preserving loud missing-path failures and invalidation on file change.

**Acceptance:** Tests prove unchanged inputs do not rehash, changed inputs do, and cache keys remain correct.

**Dependencies:** Caching policy.

**Evidence:** `src/haute/routes/_explore_service.py`; `src/haute/execution.py::dataframe_graph_input_fingerprint`; `tests/test_explore_routes.py`.

### EDA-E05 — Binary value counts
**Why:** Decoding every binary value is slow and one malformed value can poison analysis.

**Plan:** Count binary values natively and decode only displayed survivors with isolated decode failures.

**Acceptance:** Invalid UTF-8 and high-row-count fixtures preserve counts and render safe labels.

**Dependencies:** EDA-E03.

**Evidence:** `src/haute/routes/_explore_service.py`; `tests/test_explore_routes.py`.

### EDA-E06 — Deliberate tabs
**Why:** Empty clickable tabs imply capabilities that do not exist.

**Plan:** Remove placeholders until their backed features land, or provide an explicit intentional state.

**Acceptance:** Every visible tab has content and tests cover the tab set.

**Dependencies:** EDA-E09, EDA-E10, EDA-E12 may reintroduce tabs.

**Evidence:** `frontend/src/panels/explore`; `frontend/src/panels/explore/__tests__/*.test.tsx`.

### EDA-E07 — Panel state semantics
**Why:** Stale, failed, superseded, multi-source, and default states currently mislead users.

**Plan:** Model explicit report states per node and source; retain labelled stale data, show inline failures, treat supersession as non-error, and seed useful defaults.

**Acceptance:** UI tests cover first run, stale data, failure, supersession, and source switching.

**Dependencies:** Shared panel conventions.

**Evidence:** `frontend/src/panels/explore`; `frontend/src/stores`; `frontend/src/panels/explore/__tests__/*.test.tsx`.

### EDA-E08 — Scalable accessible cards
**Why:** Wide schemas make cards hard to browse and expose small accessibility gaps.

**Plan:** Add bounded card browsing with search/pagination and sticky headers; expose column count and semantic progress/table markup.

**Acceptance:** Wide-schema and accessibility tests verify navigation, headers, progress roles, and snapshot counts.

**Dependencies:** EDA-E07.

**Evidence:** `frontend/src/panels/explore`; `frontend/src/panels/explore/__tests__/*.test.tsx`.

### EDA-E09 — Distribution charts
**Why:** Analysts need distributions without client-side raw-data processing.

**Plan:** Emit capped server-binned numeric histograms from the bounded report path and render them with explicit empty/skipped states.

**Acceptance:** Tests cover null, constant, negative, and wide-schema guardrail cases plus chart rendering.

**Dependencies:** EDA-E03, EDA-E06, EDA-E07.

**Evidence:** `src/haute/routes/_explore_service.py`; `src/haute/schemas.py`; `frontend/src/panels/explore`; `tests/test_explore_routes.py`.

### EDA-E10 — Target relationships
**Why:** A report needs bounded, target-aware signals for feature investigation.

**Plan:** Add an on-demand cached analysis endpoint with explicit cache-miss behaviour, bounded numeric/categorical aggregations, and target/weight configuration.

**Acceptance:** Tests cover cache miss, target validation, numeric and categorical results, bounded levels, and ranked UI rendering.

**Dependencies:** EDA-E03, EDA-E06, EDA-E07.

**Evidence:** `src/haute/routes/explore.py`; `src/haute/routes/_explore_service.py`; `frontend/src/panels/explore`; `tests/test_explore_routes.py`.

### EDA-E11 — Quality profile extensions
**Why:** Current profiles omit useful tails, temporal/text cues, and key-quality signals.

**Plan:** Add bounded quantiles, temporal and text summaries, ID/high-cardinality flags, duplicate counts, and on-demand key uniqueness checks.

**Acceptance:** Each new signal has null, boundary, and representative-frame regression coverage.

**Dependencies:** EDA-E03.

**Evidence:** `src/haute/routes/_explore_service.py`; `src/haute/schemas.py`; `tests/test_explore_routes.py`.

### EDA-E12 — Export workflow
**Why:** Existing table export utilities are not available from Explore results.

**Plan:** Wire accessible copy and CSV export actions to supported cards using shared escaping and download utilities.

**Acceptance:** Tests cover TSV/CSV quoting, headers, disabled/empty states, and keyboard-accessible actions.

**Dependencies:** EDA-E06.

**Evidence:** `frontend/src/panels/editors/shared/tableClipboard.ts`; `frontend/src/panels/editors/FrameTableActions.tsx`; `frontend/src/panels/explore`.

### EDA-E13 — Cache and job robustness
**Why:** Restarts, oversized artifacts, and long-lived service paths waste work or accumulate resources.

**Plan:** Add a bounded durable report-cache strategy where justified, avoid duplicate pipeline execution on skipped artifacts, and bound eviction/job resources.

**Acceptance:** Tests cover restart/cache reuse policy, single execution on oversized output, eviction, and terminal job behaviour.

**Dependencies:** EDA-E03; caching and job lifecycle policy.

**Evidence:** `src/haute/routes/_explore_service.py`; `src/haute/routes/_job_store.py`; `tests/test_explore_routes.py`.
