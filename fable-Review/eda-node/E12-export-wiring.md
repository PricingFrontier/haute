# E12 — Export: wire the existing clipboard/CSV machinery into the Explore cards (G-06, P2)

**Kind:** feature (pure reuse — no new backend, no new export code) · **Effort:** S · **Review:** batch
Files: `frontend/src/panels/explore/SchemaTableCard.tsx`, `ExploreSummaryCards.tsx`,
(optionally) `frontend/src/panels/NodePanel.tsx` if the Export pane is revived
Tests: `SchemaTableCard.test.tsx`, `ExploreSummaryCards.test.tsx`

## Why

Analysts share findings — schema tables, quality issues, numeric summaries — by pasting into
spreadsheets and documents. Every table in the Explore panel is currently copy-hostile. The
machinery already exists and is used elsewhere:

- `frontend/src/panels/editors/shared/tableClipboard.ts` — `buildTsv`, `buildCsv` (RFC-4180),
  `writeClipboardText`, `downloadTextFile`, `clipboardWriteAvailable` (secure-context guard).
- `frontend/src/panels/editors/FrameTableActions.tsx` — the wiring precedent (copy/download button
  cluster on frame tables).

## Design

1. Add a compact copy/download action cluster (mirroring `FrameTableActions`) to the card header
   row of Schema, Numeric Summary, and Categorical Summary cards: "Copy TSV", "Download CSV".
   Export the **full filtered set**, not the visible page (search-filtered rows across all pages;
   that's what an analyst means by "copy this table"). Include every stat column present in the
   report even if the on-screen table elides some.
2. Categorical card: exporting a column's expanded value counts exports `value,count` rows for that
   field (per-field action inside the expansion), plus the top-level field summary export.
3. Respect `clipboardWriteAvailable` — hide copy (keep download) in non-secure contexts, exactly as
   the existing component does.
4. Report-level export: "Download report (JSON)" in the panel header — serialise the
   `ExploreCacheReport` already in the store. Zero backend work; versioned by its own
   `generated_at` + the schema the fixtures pin.
5. The config-panel **Export pane** stays hidden (E06) unless this package chooses to host the
   report-level actions there — either revive it WITH this content in the same commit, or keep
   actions on the cards and delete the pane key for good. Decide once, in this package;
   recommendation: card-level actions + panel-header JSON download, no separate Export pane
   (fewer places to look).
6. Documentation note (from the gap analysis's rejected-list): document the Explore `code` box as
   the intended cohort-filter mechanism ("assign to df") with 2–3 snippet examples in
   `docs/EXPLORE_NODE_SPEC.md` — this package is the natural place to close that doc gap since it
   touches analyst-workflow docs anyway.

## TDD plan (failing tests first)

1. `SchemaTableCard.test.tsx` — with a search filter active and 120 columns paginated: "Copy TSV"
   writes ALL filtered rows (not the visible 50) via a mocked clipboard; header row matches the
   table's column labels. **Fails today** (no button).
2. CSV escaping: a column literally named `a,"b"` round-trips per RFC-4180 (the `buildCsv` tests
   exist — assert the integration passes them real names).
3. Non-secure context (mock `clipboardWriteAvailable` false): copy hidden, download rendered.
4. Categorical expansion export: `value,count` rows incl. the "Missing" null row.
5. Report JSON download: blob content parses and deep-equals the store's report.
