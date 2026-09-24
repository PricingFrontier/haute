# Frontend node editors roadmap

## Scope

The per-node-type configuration editors. Current behaviour is specified in
[the frontend node-editors specification](../frontend-node-editors/high-level.md).
This package comes from the
[23 September 2026 codebase review](codebase-review-2026-09-23.md).

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| FNE-R01 | Planned | P3 | The API Input and Output editors share their copied block. |

## Planned improvements

### FNE-R01 — The API Input and Output editors share their copied block
**Delivered so far:** the Data Input and Data Output editors share their
provider block and branch-config helpers (`_ioProvider.ts`,
`_IoProviderPicker.tsx`); the banding statistics and rating levels hooks
share `useWholeDataAnswer`; the banding rules grid renders both rule modes
from one column description; and `NodeConfigEditor` has a read-only mode that
the comparison view uses instead of its own switch.

**Why:** The API Input and Output editors still share a 175-line copied block.

**Plan:** Extract the shared block into one component once the API Input
editor's cache control and the OUTPUT nesting rule have settled.

**Acceptance:** The copied block is a single component; editor tests pass.

**Dependencies:** `CACHE-S08` ([caching](caching.md)) replaces the API Input
editor's cache control, and `JSON-R02` ([JSON shredding](json-shredding.md),
a Decision) settles how OUTPUT nesting is declared, which the Output side of
the block edits.

**Evidence:** `frontend/src/panels/editors/ApiInputEditor.tsx`;
`frontend/src/panels/editors/OutputEditor.tsx`.
