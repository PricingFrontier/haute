# Frontend node editors roadmap

## Scope

The per-node-type configuration editors. Current behaviour is specified in
[the frontend node-editors specification](../frontend-node-editors/high-level.md).
This package comes from the
[23 September 2026 codebase review](codebase-review-2026-09-23.md).

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| FNE-R01 | Planned | P3 | Editors share components instead of copied blocks, and one dispatcher serves live and read-only views. |

## Planned improvements

### FNE-R01 — Shared editor pieces and one dispatcher
**Why:** The API Input and Output editors share a 175-line copied block; the
Data Input and Data Output editors share two copied blocks; the banding
statistics and rating levels hooks are near copies; and the banding rules
grid repeats its own rows. `ReadOnlyNodeConfig` duplicates the whole per-type
dispatch switch of `NodeConfigEditor` and passes no-op handlers inside an
`inert` wrapper.

**Plan:** Extract the shared blocks into components and one data-fetching
hook, and give `NodeConfigEditor` a read-only mode that the comparison view
uses instead of its own switch.

**Acceptance:** One per-type dispatch switch remains; the copied blocks are
single components; editor and comparison-view tests pass.

**Dependencies:** None.

**Evidence:** `frontend/src/panels/editors/ApiInputEditor.tsx`;
`frontend/src/panels/editors/OutputEditor.tsx`;
`frontend/src/panels/editors/DataInputEditor.tsx`;
`frontend/src/panels/editors/DataOutputEditor.tsx`;
`frontend/src/panels/editors/banding/useBandingStats.ts`;
`frontend/src/panels/editors/rating/useRatingLevels.ts`;
`frontend/src/panels/editors/banding/BandingRulesGrid.tsx`;
`frontend/src/components/ReadOnlyNodeConfig.tsx`;
`frontend/src/panels/NodeConfigEditor.tsx`.
