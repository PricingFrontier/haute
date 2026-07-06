# E08 — Overview cards don't scale to wide frames; small UX/a11y gaps

**Severity:** MEDIUM · **Effort:** M · **Review:** batch
Files: `frontend/src/panels/explore/ExploreSummaryCards.tsx`,
`frontend/src/panels/explore/SchemaTableCard.tsx` (extract shared shell),
`frontend/src/panels/ExplorePreview.tsx` (aria) · Tests:
`frontend/src/panels/explore/__tests__/ExploreSummaryCards.test.tsx`,
`SchemaTableCard.test.tsx`, `ExplorePreview.test.tsx`

## EF-19 [MEDIUM] — Numeric & Categorical cards lack search/pagination/sticky header/height cap

**Evidence:** `NumericSummaryCard` maps every numeric column into a 13-column table inside a plain
`overflow-x-auto` wrapper (`ExploreSummaryCards.tsx:174-235`) — no `maxHeight`, no sticky `thead`,
no search, no pagination, no sort. `CategoricalSummaryCard` likewise (`:284-396`). The Schema card
was already hardened for exactly this (sticky header `SchemaTableCard.tsx:27-35`, 400px scroll cap
`:218`, search + 50-row pagination `:111-216`) — the hardening never reached the other two cards.

**Impact:** a 500-numeric-column frame renders a 500-row × 13-col table whose header scrolls away
and whose fields can't be found except by eye. This is the standard wide-pricing-frame case.

**Fix:** extract the Schema card's controls into a shared `OverviewTableCard` shell (search box,
pagination, range summary, sticky header, height cap — the pieces at `SchemaTableCard.tsx:111-216`
nearly verbatim) and adopt it in Numeric and Categorical cards. Add client-side column sort to the
numeric card (clickable `th`, numeric-aware comparator on the raw stat values — sort on the
underlying numbers where available, not the display strings). Keep `StatValueCell` and the existing
row renderers; this is lifting controls up, not rebuilding tables.

## EF-20 [LOW] — Dataset Snapshot omits column count

**Evidence:** the card shows Rows / Source / Upstream / Cached (`ExploreSummaryCards.tsx:78-95`);
`report.column_count` exists and is only visible inside the Schema card header. Add a "Columns"
`Metric`.

## EF-21 [LOW] — a11y gaps

- Busy progress bar is a styled div with no `role="progressbar"` / `aria-valuenow`
  (`ExplorePreview.tsx:219-226`).
- Numeric and Schema `<th>` lack `scope="col"` (Categorical already sets it,
  `ExploreSummaryCards.tsx:291`).
- (Empty-tabpanel announcement is resolved by E06.)

## TDD plan (failing tests first)

1. `ExploreSummaryCards.test.tsx` — 120-numeric-column report: numeric card shows 50 rows + range
   text + working next/prev; search narrows rows. Mirror the existing
   `SchemaTableCard.test.tsx:97-142` patterns. **Fails today.**
2. Same file, categorical variant — 120 non-numeric columns: pagination + search present.
3. Sort test — click "Mean" header: rows ordered by numeric mean (assert against a fixture where
   display-string ordering would differ from numeric ordering, e.g. `9.5` vs `10`).
4. Snapshot card shows "Columns" with `column_count`. **Fails today.**
5. A11y assertions: progressbar role present while busy; every `th` in all three cards has
   `scope="col"`.
