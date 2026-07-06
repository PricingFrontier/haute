# E06 — Five blank, clickable tabs shipped to users

**Severity:** HIGH (product hygiene — most user-visible defect in the node) · **Effort:** S · **Review:** batch
Files: `frontend/src/panels/ExplorePreview.tsx`, `frontend/src/panels/NodePanel.tsx`,
`frontend/src/stores/useUIStore.ts` (pane types) · Tests:
`frontend/src/panels/__tests__/ExplorePreview.test.tsx`, `frontend/src/panels/__tests__/NodePanel.test.tsx`

## EF-13 [HIGH]

### Current behaviour (verified at af3eb2ea)

- Preview panel exposes 4 tabs — Preview | Overview | Relationships | Charts
  (`ExplorePreview.tsx:32-37`) — but the body renders only preview and overview; Relationships and
  Charts fall through to `: null` (`:246-255`). Clicking them shows a silent blank pane.
- Config panel exposes 5 tabs — Polars Code | Overview | Relationships | Charts | Export
  (`NodePanel.tsx:88-94`, comment at `:86-87`: "empty scaffolding for upcoming EDA work") — and the
  switch renders only code and overview (`:740-752`); the other three produce an empty
  `role="tabpanel"` div.
- Intent confirmed as placeholder by the comment and by history (`b66ebde2` "Blank EDA node" →
  `ff46025b` "Overview cards").

### Impact

An analyst clicks Relationships/Charts/Export and gets nothing — no explanation, no "coming soon".
It reads as broken software and poisons trust in the parts that do work. Empty `tabpanel`s also
announce nothing to screen readers.

### Fix design

**Hide unshipped panes** (preferred over "coming soon" placeholders — CLAUDE.md: no unnecessary
scaffolding, and a promise-tab is a promise):

1. Filter both pane lists to shipped keys: `EXPLORE_PREVIEW_PANES` → Preview, Overview;
   `EXPLORE_PANES` → Polars Code, Overview. Keep the union types (`ExplorePreviewPane`,
   `ExplorePane` in `useUIStore.ts`) unchanged so E09/E10/E12 re-add entries without churn.
2. Keep the remembered-pane state tolerant: `rememberedPane ?? "preview"` already falls back
   (`ExplorePreview.tsx:99-100`); add the same guard for a remembered-but-now-hidden key (a user
   whose UI-store remembers `charts` must land on Preview, not a blank body).
3. E09 (Charts), E10 (Relationships), E12 (Export) each re-introduce exactly one pane WITH content.
   The pane definition + renderer must land in the same commit — the review gate for those packages
   includes "no pane without a body".

### TDD plan (failing tests first)

1. `ExplorePreview.test.tsx` — `queryByRole("tab", { name: "Relationships" })` and `"Charts"` are
   null; tabs Preview/Overview still present. **Fails today.**
2. `NodePanel.test.tsx` (explore case) — only "Polars Code" and "Overview" tabs render for an
   explore node. **Fails today.**
3. Remembered-pane fallback: seed `useUIStore` with `explorePreviewPanes[node] = "charts"`, mount,
   assert the Preview pane body is shown and the tablist has a valid active tab.
