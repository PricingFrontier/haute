# Frontend shared roadmap

## Scope

The browser's shared code, as [the frontend shared
specification](../frontend-shared/high-level.md) describes it: the panel values held
outside the shared draft.

## Priorities

**Start with:** `FES-07`

| Package | State | Priority | Outcome |
|---|---|---:|---|
| FES-07 | Planned | P3 | The panel values still held outside the shared draft follow it: a code editor's typing (a decision), the colour picker, the Columns tab's empty selection, and the lists whose fields are keyed by position. |

## Planned improvements

### FES-07 — The last panel values outside the shared draft
**Why:** Read on 18 November 2026, when the shared draft
(`frontend/src/components/form/useCommittedDraft.ts`) became the path for
every typed field of the panels and nodes and the guard
(`frontend/src/components/form/__tests__/draftsByHand.test.ts`) was added.
Four places still hold a value being edited another way. A code editor
(`CodeMirrorEditor.tsx`) keeps its own rule, deliberately and under test:
a change from outside that arrives while it holds typing not yet flushed
is held back, and dropped if the typing is then committed, so a live-sync
frame or the assistant's change can be lost under it, the opposite of
every other field; its unflushed text is also unknown to `isDraftOpen`.
The colour picker (`components/form/ColourSwatches.tsx`) keeps its own
draft so a pick previews tick by tick, and with a preview its draft
survives a change from outside. The Columns tab (`ColumnsTab.tsx`) keeps
an empty selection as a draft tagged with the selection it was made over,
which is correct but its own. And some lists still key their fields by
position: the API Input's tables and columns, the Edge Join's key boxes,
and the field of the selected Banding factor or Rating Step table keyed by
the selected index. A shared field's draft drops when its own value moves,
so only a change from outside while the analyst types that leaves the same
text at the same place could carry one; nobody has seen it.

**Plan:** Decide the code editor's rule: the stored value wins, as for every
field, once its consumers are shown to store exactly what it sends (a
transformed echo would otherwise replace the text being typed); register
its unflushed text with the shared draft either way. Give the colour picker
the shared draft, its previews moving the draft's basis the way a commit
that stays does. Give the Columns tab's empty selection the shared draft.
Key the remaining lists by `useRowKeys`, the selected item's field by its
row's key, and teach the guard to find a shared field keyed by an index.

**Acceptance:** The entries are drivers of
`frontend/src/components/form/__tests__/panelDraftsUndo.test.tsx`; the
guard fails for a shared field given an index as its key or owner.

**Dependencies:** A decision on the code editor's rule.

**Evidence:** `frontend/src/panels/editors/CodeMirrorEditor.tsx`;
`frontend/src/panels/editors/__tests__/_shared.test.tsx`;
`frontend/src/components/form/ColourSwatches.tsx`;
`frontend/src/panels/editors/ColumnsTab.tsx`;
`frontend/src/panels/editors/ApiInputEditor.tsx`;
`frontend/src/panels/editors/EdgeJoinEditor.tsx`.
