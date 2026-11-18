# Root cause: late answers

## Scope

An answer from the server that arrives after what asked for it has
changed, and is applied all the same.

This file is one pull request. It holds one package, `FES-04`, which names
a cause that packages in other files share, and it is taken before them.
The pull request makes the shared mechanism the only path and adds the
test that keeps it so. It then checks each package listed below against
that package's own acceptance, removes the ones it has met, and removes
this file.

## Packages this resolves

"Resolves" means the package's acceptance should be met by this work
alone. "In part" means this work removes the shared cause and the package
keeps something of its own to do.

| Package | Roadmap | What it records | Expected |
|---|---|---|---|
| `FEG-10` | [frontend-graph-canvas](frontend-graph-canvas.md) | A late answer to a repair leaves the canvas on the current document | Resolves |
| `VCS-10` | [git-integration](git-integration.md) | An edit made while Catch up runs is kept, or cannot be made | Resolves |
| `FEP-04` | [frontend-preview-explore](frontend-preview-explore.md) | A utility file edited while another is being deleted keeps its edit | Resolves |
| `FEN-01` | [frontend-node-editors](frontend-node-editors.md) | A late answer does not replace the chosen catalog's schemas | Resolves |
| `FEN-10` | [frontend-node-editors](frontend-node-editors.md) | Imported values that arrive after their factor was removed are dropped | Resolves |
| `WB-61` | [workbench](workbench.md) | Text being typed when a test quote opens is kept | In part |
| `WB-41` | [workbench](workbench.md) | Fence `loadingMore` in `useQuoteListStore`, resolve submodel `WORKBENCH_OUTPUT` runtime targets in `priceSample`, and remove stale blank-form rationale | In part |

`WB-61` needs the helper to see text typed and not yet committed, which
the shared draft answers: `isDraftOpen` in
`frontend/src/components/form/useCommittedDraft.ts`, which every typed
field of the editor and the Workbench holds its text in. Of `WB-41`
the quote list's `loadingMore`, its reset and its guard, is done here; the
rest of that package is not this cause. Four packages
that look alike are not: `MLR-10` (a retry that reuses an operation's id
with other settings), `AST-06` (an event the server does not send) and
`FEG-04` and `FEG-05` (a save whose outcome is not known). No answer
arrives late in them, and they keep their own plans.

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| FES-04 | Planned | P2 | Every answer from the server is applied through one helper that drops it when what asked for it has changed. |

## Planned improvements

### FES-04 — An answer is applied only to what asked for it
**Why:** Read on 16 November 2026. The editor has a fence for answers that
arrive late: a caller captures the document's state before a request and
checks it after. It was an earlier roadmap's work, and where it is used no
finding of this review falls. It is used by choice. Of 105 source files
that wait for an answer, 12 use the fence, 8 use the optimiser's
latest-request hook, and 22 keep a counter or a cancelled flag of their
own. The files behind this review's late-answer findings use none, or a
guard of their own that misses a case: `App.tsx`, the utility panel, the
repair dialog and the catalog selector (`FEG-10`, `FEP-04`, `FEN-01`). The
test-quotes store keeps a counter and compares the form, and so cannot see
text typed and not yet committed (`WB-61`); the quote list guards its
answer and not the flag it clears afterwards (`WB-41`). The fence was also
built for execution answers; an answer that writes to the document or to a
panel, such as a repair, a catch up, a delete or imported values
(`VCS-10`, `FEN-10`), has no shared equivalent. A new panel is correct only
if its author remembers.

**Plan:** One request helper that every call goes through when its answer
will be written to state. It records what the answer belongs to: the
document, the node, the panel, the edit it was asked under, and whether a
field it would write to is being typed in, read from the shared draft. A
late answer is dropped, or handed back marked stale for the caller to
settle, and what runs after the answer (a flag cleared, a list reloaded) is
under the same check. Whatever a request set while it waited, such as a
loading flag, is reset at the moment the request stops being current, not
when its answer comes: otherwise dropping the late answer would leave the
flag set for good, and the quote list could not load a further page. The
quote list's reset on a change of list is part of this work. Replace
the 22 hand-kept guards with it. Add a test
that finds each function which writes state after waiting without the
helper, and fails for any; the pull request shows it failing on a guard
written by hand.

**Acceptance:** One test drives every surface that calls the server: it
holds the answer, changes what it belongs to (another document, the node
deleted, an Undo, text typed and not committed), lets the answer go, and
requires the page to show the current state. With a further page of one
quote list still awaited, another list is opened: it loads its own further
page before the first answer comes, and is unchanged when it does. No
function writes state
after waiting outside the helper. The journeys of `FEG-10`, `VCS-10`,
`FEP-04`, `FEN-01` and `FEN-10` pass.

**Dependencies:** None: the shared draft it asks is built
(`frontend/src/components/form/useCommittedDraft.ts`, `isDraftOpen`; a
code editor's unflushed text is not registered there yet). Deliver before
the packages its table names.

**Evidence:** `frontend/src/stores/useDocumentStatusStore.ts`;
`frontend/src/panels/optimiser/useLatestRequest.ts`; `frontend/src/App.tsx`;
`frontend/src/panels/UtilityPanel.tsx`;
`frontend/src/stores/useWorkbenchTestQuotesStore.ts`;
`frontend/src/app/useQuoteListStore.ts`.
