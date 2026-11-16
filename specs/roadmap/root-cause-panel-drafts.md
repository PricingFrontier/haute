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
| `FEM-04` | [frontend-modelling-optimiser-ui](frontend-modelling-optimiser-ui.md) | The optimiser's estimate follows a constant changed by Undo | Resolves |
| `FEN-08` | [frontend-node-editors](frontend-node-editors.md) | The Output editor's preview follows an Undo upstream | Resolves |
| `FEN-09` | [frontend-node-editors](frontend-node-editors.md) | A Banding factor's remembered rules stay with it through an Undo | Resolves |
| `FEG-08` | [frontend-graph-canvas](frontend-graph-canvas.md) | "Save node" saves what the panel shows | Resolves |

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| FES-05 | Planned | P2 | Every value being edited in a panel lets go of its copy when an Undo moves the value under it. |

## Planned improvements

### FES-05 — A panel's unsaved value follows the document through Undo
**Why:** Read on 16 November 2026. Panels and node components keep their
own state in 399 `useState` calls across 98 files, and no shared hook ties
a value being edited to the store's history. An earlier roadmap put each
editor's draft in step with the document one editor at a time. Undo
restores the store and a panel goes on showing, and sending, its own copy:
the search space box (`FEM-03`), the optimiser's estimate (`FEM-04`), the
Output editor's preview (`FEN-08`), a Banding factor's remembered rules
(`FEN-09`) and "Save node" (`FEG-08`). Each was found by pressing Undo in
one panel; the panels nobody pressed it in are built the same way.

**Plan:** One hook for a value being edited. It is keyed by the node and by
the store's place in its history, lets go of its copy when an Undo, a Redo
or a change from outside moves the value under it, and commits as one undo
step. A request a panel derives, such as an estimate or a preview, is built
from the store's value, never from the copy. Move the editors to the hook,
and add a test that lists the panels holding a copy without it and only
lets the list shrink.

**Acceptance:** One test drives every editor field that holds a copy: it
types, undoes a change upstream, and requires the field to show the store's
value and the next request to carry it. The journeys of `FEM-03`, `FEM-04`,
`FEN-08`, `FEN-09` and `FEG-08` pass.

**Dependencies:** None. Deliver before the packages its acceptance names.

**Evidence:** `frontend/src/panels/ModellingConfig.tsx`;
`frontend/src/panels/OptimiserConfig.tsx`;
`frontend/src/panels/editors/OutputEditor.tsx`;
`frontend/src/components/form/CommittedTextField.tsx`.
