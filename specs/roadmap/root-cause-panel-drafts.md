# Root cause: panel values and Undo

## Scope

A value being edited in a panel that goes on showing, and sending, its own
copy after an Undo.

This file is one pull request. It holds one package, `FES-05`, which names
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
| `FEM-03` | [frontend-modelling-optimiser-ui](frontend-modelling-optimiser-ui.md) | After Undo the search space box shows what training will use | Resolves |
| `FEN-05` | [frontend-node-editors](frontend-node-editors.md) | A Banding cell's edit is one undo step | Resolves |
| `FEN-09` | [frontend-node-editors](frontend-node-editors.md) | A Banding factor's remembered rules stay with it through an Undo | Resolves |
| `FEG-08` | [frontend-graph-canvas](frontend-graph-canvas.md) | "Save node" saves what the panel shows | In part |

`FEG-08` keeps its own change: "Save node" must wait for the node's pending
change before it reads the graph, as the whole pipeline's Save does. The
optimiser's estimate and the Output editor's preview after an Undo
(`FEM-04`, `FEN-08`) are not copies of a value: their keys leave out what
they were computed from, and they belong to
[results that go stale unseen](root-cause-freshness.md).

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| FES-05 | Planned | P2 | Every value being edited in a panel lets go of its copy when an Undo moves the value under it. |

## Planned improvements

### FES-05 — A panel's unsaved value follows the document through Undo
**Why:** Read on 16 November 2026. A shared draft exists: `useCommittedDraft`
holds the keystrokes of a text field, drops them when the stored value
changes under it, and commits once. It was an earlier roadmap's work, and
the fields built on it follow an Undo. It is used by choice. Lines that
mention `useState` number 399 in 98 panel and node files; how many of them
hold a copy of a stored value has not been counted. The findings fall where
a copy is held outside the shared draft: the search space box (`FEM-03`),
the two Banding grids, whose cells reach the graph on every keystroke
(`FEN-05`), and a Banding factor's remembered rules, which are kept by the
factor's position in the list and so pass to another factor when an Undo
replaces the list (`FEN-09`). "Save node" reads the graph without waiting
for a change a panel has still to commit (`FEG-08`). Each was found by
pressing Undo in one panel; the panels nobody pressed it in are built the
same way.

**Plan:** Start with the inventory: every place a panel or node holds a
copy of a stored value, apart from state that is only the page's own (an
open tab, a hover). Extend `useCommittedDraft` to values that are not text
(a list, a grid cell, a remembered set of rules), keyed by what the value
belongs to, a node and a factor's identity, never a position. It lets go
of its copy when an Undo, a Redo or a change from outside moves the value
under it, and commits as one undo step. A commit still pending is known to
the one barrier that every save, of the pipeline or of one node, waits on.
Move the inventory's entries to it. Add a test that finds a component which
seeds its own state from a stored value without the shared draft and fails
for any not on a list of named exceptions, each with its reason.

**Acceptance:** One test drives every entry of the inventory: it types,
undoes a change upstream, and requires the field to show the store's value.
A second types in each and undoes once, and requires the earlier value. A
component written to hold its own copy of a stored value fails the test for
unlisted copies. The journeys of `FEM-03`, `FEN-05` and `FEN-09` pass.

**Dependencies:** None. Deliver before the packages its table names, and
before [late answers](root-cause-late-answers.md) and
[typed numbers](root-cause-typed-numbers.md): the first asks this draft
whether a field is being typed in, and the second builds its number field
on it.

**Evidence:** `frontend/src/components/form/CommittedTextField.tsx`;
`frontend/src/panels/ModellingConfig.tsx`;
`frontend/src/panels/editors/BandingEditor.tsx`;
`frontend/src/hooks/useScopedNodeSave.ts`.
