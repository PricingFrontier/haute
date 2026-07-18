# E09 — Charts tab: server-binned histograms per numeric column (G-01, P1)

**Kind:** feature (fills a dead tab) · **Effort:** M · **Review:** dev/reviewer pair (new numbers rendered to analysts)
Files: `src/haute/routes/_explore_service.py`, `src/haute/schemas.py`,
`frontend/src/panels/ExplorePreview.tsx` (re-add pane), new
`frontend/src/panels/explore/ExploreChartsPane.tsx`, `frontend/src/api/types.ts`, `guards.ts`
Tests: `tests/test_explore_routes.py`, UI-contract fixtures, new
`frontend/src/panels/explore/__tests__/ExploreChartsPane.test.tsx`
Depends on: E03 (join a batched pass, not the single select); E06 (re-introduces the pane WITH content)

## Why (analyst case)

Claims/premium/exposure distributions are heavy-tailed, zero-inflated, and often bimodal —
p25/median/mean/p75 cannot show shape. A histogram is the single most-expected EDA visual, and it
directly informs **Banding node boundaries** (the adjacent workflow consumes exactly these
distributions). `docs/EXPLORE_NODE_SPEC.md` "Future Scope" reserves the Charts tab for this.

## Backend design

- Per numeric column, in an E03 pass-B batch:
  `pl.col(name).hist(bin_count=30, include_breakpoint=True).implode().alias(f"hist::{name}")` —
  verified `Expr.hist` exists in pinned Polars 1.39.2. Structurally identical to the existing
  value-counts implode pattern (`_categorical_value_counts_expr`, `_explore_service.py:353-360`):
  bounded output (30 bins), dtype-gated, one predicate gating both the expression and its parse
  (follow the `_has_categorical_value_counts` discipline — new `_has_numeric_histogram(dtype)`;
  numeric AND not all-null is decided at parse, the gate is dtype-only).
- Verify `hist`'s empirical edge cases in TDD before wiring the UI: all-null column, single-value
  column (degenerate range), NaN presence (E02 lands first — decide NaN exclusion explicitly and
  label it), and integer columns with < 30 distinct values (prefer honest sparse bins over
  interpolated ones; if `hist` output is awkward there, fall back to value-counts-as-bars for
  low-distinct integers — decided by test evidence, not assumption).
- Schema: `ExploreColumnStat.histogram: list[ExploreHistogramBin] | None` with
  `{breakpoint: float, count: int}`; bump `EXPLORE_CACHE_VERSION`; update contract fixtures.
- Guardrail: cap emission on very wide frames (skip histograms when numeric column count > ~150,
  emit a report-level `histograms_skipped: true` flag the UI can explain). Never silently truncate
  without the flag.

## Frontend design

- Re-add the Charts pane (E06 list) rendering: `FeatureBrowser`-style numeric column list
  (`frontend/src/panels/modelling/FeatureBrowser` — searchable left rail, reusable) + a histogram
  panel on the shared SVG primitives (`modelling/ChartScaffold.tsx` — `ChartSvg`, `ChartLegend`,
  `ChartEmptyState`).
- **Adapt `BandingHistogram` (`editors/banding/BandingHistogram.tsx:11`) to accept pre-computed
  `(breakpoint, count)` bins** instead of raw `values: number[]` (client-side binning cannot scale
  to 50M rows). Do it as a new props variant or a small shared `<BinnedHistogram>` both call —
  don't fork the visual style; the Banding editor keeps its raw-values path for its preview-sample
  use case.
- Empty states: no numeric columns; `histograms_skipped`; stale report (E07 strip applies to this
  pane too).

## TDD plan (failing tests first)

1. Backend: `test_frame_stats_histogram_shape` — mixed frame: numeric columns carry ≤30 bins with
   monotonically increasing breakpoints and counts summing to non-null row count; non-numerics
   carry `None`. **Fails today** (field absent).
2. Backend edges: all-null numeric → `histogram is None` (not a crash); single-valued column →
   defined behaviour pinned; NaN column interacts with E02 counts (NaN excluded from bins and
   visible in nan_count).
3. Contract: fixtures + `EXPLORE_CACHE_VERSION` bump test.
4. Frontend: pane renders bars from a fixture report; column search filters; empty states render;
   `histograms_skipped` explains itself. Vitest snapshot-light (assert structure, not pixels).
