# E11 — Data-quality & profile extensions (G-03…G-05, P2 menu)

**Kind:** feature menu — five independent items, each small; land in any order after E03
**Effort:** M total (items S–M) · **Review:** dev/reviewer pair (new numbers), single PR per item or batched
Files: `src/haute/routes/_explore_service.py`, `src/haute/schemas.py`, cards in
`frontend/src/panels/explore/`, `overviewCardDefinitions.ts` registry
Tests: `tests/test_explore_routes.py`, contract fixtures, `ExploreSummaryCards.test.tsx`
Each report-shape item bumps `EXPLORE_CACHE_VERSION` — batch the bumps if landing together.

## G-03a — Tail quantiles p1/p5/p95/p99 (Numeric card) · S

Heavy tails ARE the pricing signal: p95/p99 locate large losses and capping points; p1/p5 flag
implausible lows. Add `.quantile(0.01/0.05/0.95/0.99, "linear")` to the numeric block — identical
shape to existing p25/p75 (`_explore_service.py:459-465`), joining an E03 pass-B batch. Schema:
`p01_value/p05_value/p95_value/p99_value`. UI: extend the numeric table (E08's shell keeps it
scrollable) — consider a compact "p1 … p99" grouped header so 17 columns stay readable.
**Honest caveat:** an IQR-fence *outlier count* needs the quantiles as inputs and cannot be
computed in the same select; it needs a tiny second collect once fences are known. Ship the
p-values now; add the count as a follow-up second pass only if analysts ask.
**TDD:** hand-computed quantiles on a fixed frame; all-null column renders "-".

## G-03b — Temporal Summary card · M

Pricing data is exposure-period heavy (inception/expiry/earned dates). Temporal columns are
classified (`_column_kind` → "Temporal") but only get null/min/max today. Add, gated on
`dtype.is_temporal()` (and excluding Duration until E01's boundary decides its display):
- future-date count: `(pl.col(c) > <now-lit>).sum()` — pass `now` in as a literal computed once
  per run (determinism within a report);
- month + weekday distributions: `.dt.month().value_counts()` / `.dt.weekday().value_counts()`
  imploded (bounded 12/7; reuse the value-counts parse pattern).
New `ExploreTemporalColumnProfile` in `overview_summary`; new "Temporal Summary" card via the
registry (one definition + one renderer, `ExploreOverviewPane.tsx:38-44`); render the 12/7
distributions as chips or micro-bars (reuse chip styling from the categorical card).
**TDD:** fixed dates → exact month/weekday counts; tz-aware column doesn't crash; future-date
count pinned against the injected `now`.

## G-03c — Text-length stats + empty-string≠null (Categorical card + Quality rule) · S

Blank-but-not-null strings (`""` postcode) pass null checks and break joins/banding silently. Add,
gated on String base: `str.len_chars().min()/mean()/max()` and `(pl.col(c) == "").sum()` empty
count. New quality rule "N text column(s) with empty strings" (warning), alongside
missing/constant. Show min/mean/max length in the categorical card row.
**TDD:** `["ab", "", None]` → empty_count 1, null 1, lengths (0,2); quality issue fires on
empty-heavy column and NOT on merely-nullable ones.

## G-04 — ID-like & high-cardinality flags (Data Quality) · S — near-free, highest value/effort

Zero new aggregation: pure rules over `distinct_count` (post E02/EF-03 null-excluded) already in
hand at `_build_data_quality_summary` (`:222-321`):
- `distinct/rows ≥ 0.99` and rows > small-N → "identifier-like column — exclude from modelling"
  (info/warning);
- non-numeric with `distinct > 200` (constant; tune) → "high-cardinality — band before modelling".
Wire into the existing issues list; keep detail text naming the worst columns
(`_names_text` pattern).
**TDD:** unique-key column flagged id-like; 500-level postcode column flagged high-cardinality;
low-cardinality columns unflagged.

## G-05 — Duplicate-row / key-uniqueness (Data Quality + on-demand) · M

Double-counted exposure silently inflates every downstream number. Two halves:
- **Embedded (cheap):** full-row duplicate count in pass A —
  `pl.len() - pl.struct(pl.all()).n_unique()` — one scalar; quality rule "N exact duplicate rows"
  (danger when > 0 for pricing frames). Caveat: struct-hash over very wide frames is not free —
  place in a pass-B batch and skip above the same width cap as E09's histograms if measurements
  demand.
- **Parameterised (on-demand):** "is the frame unique at policy/exposure grain" needs user-picked
  key columns → reuse the E10 endpoint pattern (`analysis/key-uniqueness`, params = key columns;
  returns duplicate-key count + top-10 duplicated keys with counts). Persist chosen keys in node
  config (display-param strip, as E10's target).
**TDD:** frame with known duplicate rows → exact count; key-uniqueness endpoint on (policy_id)
returns the seeded duplicates; 409 on cold cache.

## Ordering within the package

G-04 first (free), then G-03a, G-03c, G-05-embedded (single version bump), then G-03b, then
G-05-on-demand (after E10 lands the pattern).
