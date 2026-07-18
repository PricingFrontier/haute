# Fable Review — EDA ("Explore") node

**Read-only deep review of the Explore node, performed 2026-07-06 at HEAD `af3eb2ea` (branch `code-fixes`).**
Four parallel reviewers (backend performance, correctness/robustness, frontend UX/state, analyst
feature-gap analysis) plus a first-hand synthesis pass that re-verified every load-bearing claim
against the source and, where it mattered, empirically against the pinned Polars 1.39.2
(`.venv`). Benchmarks in E03 were measured on a 3M-row fixture with per-variant isolated-subprocess
RSS sampling; the Duration crash in E01 was reproduced directly.

**Nothing in the source tree was changed by this review.** This folder is the deliverable: the
verdict, the findings, and per-package implementation plans for a follow-up (Opus) agent.

---

## Verdict

The Explore node's core is genuinely well-engineered: one batched, dtype-gated Polars aggregation
pass with bounded outputs; a parquet-backed dataframe cache keyed by upstream lineage; a solid job
lifecycle (admission, supersede, typed terminal states) and a robust frontend poller. The overview
card registry is cleanly extensible, and the code hygiene (single-predicate gating so expression and
parse can't drift, unknown-key round-tripping) is exactly right. **CLEARED.md lists the many things
that were adversarially checked and found correct — do not "fix" anything on that list.**

But the node is **not yet efficient, robust, or complete enough for its job**:

1. **One ordinary column type kills the whole report.** A `Duration` column (`policy_end −
   policy_start`, time-to-claim, tenure) is routed into the categorical value-counts branch, whose
   `cast(pl.String)` raises `InvalidOperationError` in Polars 1.39.2 — reproduced. The single
   batched collect means the analyst gets *nothing*, with a cryptic error. → E01
2. **The tool built to catch bad data is blind to the worst kind.** `NaN`/`inf` columns show 0%
   missing, render literal `nan` strings, and an all-NaN column is mislabelled "constant". Null
   handling has an off-by-one (`n_unique` counts null) that breaks constant-column detection and the
   top-50 truncation flag, and `_percent_text` rounds 99.6% up to a flat "100%" while the adjacent
   table says "99.6%". In a pricing tool these are exactly the silent-wrongness class CLAUDE.md
   forbids. → E02
3. **The stats collect is memory-unbounded and unstoppable.** Exact `n_unique` + exact quantiles +
   full `value_counts` maps for *every* column accumulate in one giant select — peak is the *sum* of
   all accumulators (measured: n_unique alone doubles peak; quantiles alone are 14× the time of all
   cheap aggs combined) — and no RSS checkpoint runs mid-collect, so the typed `memory_limited`
   state the admission system exists to produce is bypassed; the OS kills the worker instead.
   Cancel doesn't interrupt it either. → E03
4. **Every request content-hashes every upstream source file, synchronously — even warm cache
   hits.** The stat-gated memo that preview/trace use for exactly this reason sits unused by
   Explore; a "cached" Explore on a large source appears to hang. → E04
5. **Five dead tabs shipped to users.** Relationships, Charts (preview panel) and Relationships,
   Charts, Export (config panel) render empty panes — placeholders that read as broken. Meanwhile
   the parquet cache the node materialises is **never used again** after the one-shot report: the
   features that would make an analyst *understand* their data (distributions, target-vs-factor
   analysis) are missing even though the spec (`docs/EXPLORE_NODE_SPEC.md` "Future Scope") reserves
   space for them and the components to build them cheaply already exist (`AveChart`,
   `FeatureBrowser`, `ChartSvg`, `BandingHistogram`, `tableClipboard`). → E06, E09, E10, E12

Total: **6 HIGH, ~12 MEDIUM, ~8 LOW** verified findings (EF-01 … EF-25) plus a prioritised feature
programme, organised into 13 packages below.

---

## Packages, in recommended execution order

| # | Package | Kind | Severity | Effort | Review mode |
|---|---------|------|----------|--------|-------------|
| E01 | [Duration kills the report](E01-duration-value-counts-crash.md) | crash fix | HIGH | S | pair |
| E02 | [Silent stats wrongness: NaN/inf, null-distinct, percent text](E02-silent-stats-wrongness.md) | silent wrongness | HIGH | M | pair |
| E03 | [Memory-safe, cancellable stats collect](E03-memory-safe-stats-collect.md) | perf/robustness | HIGH | M–L | pair |
| E04 | [Stat-gated input fingerprint](E04-stat-gated-input-fingerprint.md) | perf (latency) | HIGH | S | pair (cache identity) |
| E05 | [Binary: native value counts, decode survivors](E05-binary-native-value-counts.md) | perf | MEDIUM | S | batch |
| E06 | [Remove dead tabs](E06-dead-tabs.md) | product hygiene | HIGH (UX) | S | batch |
| E07 | [Panel state UX: stale display, failures, defaults](E07-panel-state-ux.md) | UX correctness | MEDIUM | M | pair (state machine) |
| E08 | [Card scalability + a11y](E08-card-scalability-a11y.md) | UX | MEDIUM | M | batch |
| E09 | [Charts tab: server-binned histograms](E09-charts-histograms.md) | feature (P1) | — | M | pair (new numbers) |
| E10 | [Relationships tab: target-aware one-way analysis](E10-relationships-target-analysis.md) | feature (P1) | — | L | pair (new numbers) |
| E11 | [Data-quality & profile extensions](E11-quality-profile-extensions.md) | feature (P2 menu) | — | M | pair (new numbers) |
| E12 | [Export wiring](E12-export-wiring.md) | feature (P2) | — | S | batch |
| E13 | [Cache lifetime & job-path robustness](E13-cache-robustness.md) | perf/robustness | MEDIUM | M | batch |

Rationale for the order: E01/E02 first — they are the fail-loud and silent-wrongness fixes, small
and fully specified. E03 restructures the same function (`_build_frame_stats`) that E01/E02 touch,
so it lands immediately after on a corrected base (its batching also delivers the cancel fix). E04
is a one-function latency HIGH. E05/E06 are small and mechanical. E07/E08 make the existing panel
honest and scalable. E09–E12 are the feature programme in value order (E09 before E10: smaller,
feeds the Banding workflow, and de-risks the chart plumbing E10 reuses). E13 is bounded cleanup.

Dependencies: E03 depends on E01+E02 (same function). E09/E10/E11 SHOULD land after E03 so new
aggregations join batched collects rather than the current single select. E12 fills the Export pane
E06 hides — whichever lands second re-enables/removes accordingly. E10 uses the on-demand endpoint
pattern; E11's key-uniqueness item reuses it once E10 establishes it.

---

## Implementation protocol (binding, per project CLAUDE.md)

1. **Failing test first, always.** Every package has a "TDD plan" listing the failing tests to
   write before touching implementation. Backend entry point: `tests/test_explore_routes.py`
   (drives `_build_frame_stats` directly via the existing `explore_execution_context` fixture) and
   `tests/test_explore_round_trip.py` for config round-trips. Frontend: extend the existing vitest
   files named in each package. For performance, prefer structural assertions (call counts via
   monkeypatch, plan-text checks, per-batch peak bounds with generous margins) over wall-clock.
2. **Review split:** full dev/reviewer pairs where the table says "pair" (silent-wrongness class,
   cache identity, state machines, any package that renders new numbers to analysts); single batch
   reviewer for the mechanical ones.
3. **Fail loud, no fallbacks.** E01 gates a dtype out of a branch rather than wrapping the collect
   in try/except; E02 makes NaN *visible* rather than coercing it away. Do not replace a crash with
   a silent skip.
4. **Line numbers will drift.** All citations are valid at `af3eb2ea`. Locate code by the quoted
   symbol names, not line numbers.
5. **Gates before every commit:** `ruff format --check`, `ruff check`, `mypy`, the focused test
   files for the package, then the full suite before the final commit of a package. Accumulate on
   the existing PR; **do not merge** — Ralph reviews independently.
6. **Report-schema changes** (E02, E09, E11) must bump `EXPLORE_CACHE_VERSION`
   (`_explore_service.py`) and update the UI-contract fixtures
   (`tests/fixtures/ui_contracts/explore_*.json`) plus `frontend/src/api/types.ts` +
   `frontend/src/types/guards.ts` in the same package.

## Finding ID scheme

Backend/frontend defects are numbered **EF-01 … EF-25** across the package files; feature gaps are
**G-01 … G-08** (priority from the gap analysis: P1 = defines the tool, P2 = high value/cheap,
P3 = backlog). Each defect carries severity, file:line (at `af3eb2ea`), evidence, impact, fix
design, and test spec. Severity: HIGH = report-destroying, silently wrong numbers, or unbounded
resource use; MEDIUM = real but bounded cost or misleading-but-recoverable UX; LOW = hygiene.

## Deliberately out of scope (rejected, with reasons — do not scope-creep these in)

- **Compare-two-runs / cross-source drift**: monitoring surface, not single-node EDA; the cache key
  already encodes source so the primitive survives for a future feature.
- **A second "quick filter" UI for cohort EDA**: the Explore node's Polars `code` box already does
  this with full expressiveness; document it + snippet templates instead of a parallel widget (E12
  includes the doc note).
- **Per-column memory footprint card**: engineer-facing, not analyst-facing.
- **Generic-profiler kitchen sink** (N×N scatter matrices, standalone HTML report dumps, duplicate
  "alerts" panes): unreadable at pricing scale or redundant with the Data Quality card. Sweetviz-
  style target association is subsumed by E10 in an exposure-weighted, pricing-native form.

## Backlog (P3 — do not build yet; designs sketched in E10/E11 for when wanted)

- G-06 numeric correlation matrix (Relationships; on-demand, capped ≤~25 columns).
- G-07 column drill-in (full paginated value counts + histogram from the cached parquet).
- G-08 skew/kurtosis, random-N sample card.
