# Root cause: results that go stale unseen

## Scope

Stored results, previews and briefs that are used after something they
were computed from has changed.

This file is one pull request. It holds one package, `CCH-06`, which names
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
| `CCH-01` | [caching](caching.md) | A node-output snapshot is tied to the code that computed it | Resolves |
| `CCH-04` | [caching](caching.md) | A preview follows the order of a node's inputs | Resolves |
| `CCH-05` | [caching](caching.md) | A preview follows a utility module imported inside a node's code | Resolves |
| `FEP-03` | [frontend-preview-explore](frontend-preview-explore.md) | A saved utility file makes the previews that use it out of date | Resolves |
| `AST-05` | [assistant](assistant.md) | The assistant's brief of the graph follows an edit to a utility module | Resolves |
| `WB-67` | [workbench](workbench.md) | A change to a params file changes the form's revision | Resolves |
| `WB-69` | [workbench](workbench.md) | Accept is refused when a data file the helper code reads has changed | Resolves |
| `FEM-04` | [frontend-modelling-optimiser-ui](frontend-modelling-optimiser-ui.md) | The optimiser's estimate follows a constant changed by Undo | Resolves |
| `FEN-08` | [frontend-node-editors](frontend-node-editors.md) | The Output editor's preview follows an Undo upstream | Resolves |
| `FEP-01` | [frontend-preview-explore](frontend-preview-explore.md) | A Refresh reads the file as it now is | In part |
| `FEP-02` | [frontend-preview-explore](frontend-preview-explore.md) | A Refresh reaches the data the Explore panes and the editors read | In part |

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| CCH-06 | Planned | P2 | Every stored result records what its run read, and every reader of it reports it old when any of that changes. |

## Planned improvements

### CCH-06 — A stored result is checked against what its run read
**Why:** Read on 16 November 2026. A node snapshot's signature is built
from the inputs the graph declares, through one checked contract. Four
kinds of input escape it. The contract sorts a graph's edges, so the order
of a node's inputs is not in the signature (`CCH-04`). A node's code reads
what the graph does not know: a utility module imported inside the code
(`CCH-05`), and the loaded module is put aside only when the preamble
imports it or the project is bound again, so a result found old could
still be computed again from the old code. What Haute's own code does is
carried by version constants a developer must remember to raise
(`CCH-01`); `src/haute` holds more than forty. And every reader outside
the engine keeps its own list of what makes its answer old: the editor's
previews after a utility file is saved (`FEP-03`), Refresh (`FEP-01`,
`FEP-02`), the assistant's brief (`AST-05`), the Workbench's form revision
and Accept (`WB-67`, `WB-69`), and in the browser the keys of the
optimiser's estimate, which leaves out the constants and the preamble
(`FEM-04`), and of the Output editor's preview, which holds only its own
mapping and the source (`FEN-08`). Each was found as its own defect, and
each list is missing something another list has.

**Plan:** Extend the checked contract; do not build a second one beside it.
It keeps a node's inputs in their order. With every stored result it
records what the run read: the project's modules it imported and the
project's files it opened, each with a hash of its content, gathered in
the process that collects the frame and returned with the result, so a
worker's reads are counted. When a result is found old for a module, the
loaded module and the preamble's stored namespace are put aside before it
is computed again. Derive the engine's version from the content of the
modules that compute a node's output, or fail a test when those modules
change and the constant does not. Give the editor, the assistant and the
Workbench one revision of the project's inputs, taken from the same
record, in place of their own lists, and give the browser one key for a
derived request, built from the document as it stands with its constants
and preamble, that every estimate and preview uses. The pull request first
proves that the reads can be gathered across a lazy plan and a worker;
where they cannot, a read the graph does not declare is refused at save
and lint, naming the node, as `CCH-05` allows.

**Acceptance:** One table-driven test changes each kind of input in turn: a
node's code, a utility module imported at the top and one imported inside a
node, a params file, a data file, the order of a node's inputs, a constant,
the preamble and an engine module. For each it requires every reader to
report its answer old, and the answer computed again to be the one a fresh
process gives: the snapshot, the preview, the Explore panes, the
optimiser's estimate, the Output preview, the assistant's brief and the
form's revision. The readers are found from the code, the contract's
consumers and the browser's users of the key, so one the test does not
drive fails it. The acceptance of the packages marked "Resolves" is met.

**Dependencies:** None. Deliver before the packages its table names.

**Evidence:** `src/haute/_node_snapshots.py`; `src/haute/_cache.py`;
`src/haute/executor.py`; `src/haute/_project_imports.py`;
`frontend/src/panels/OptimiserConfig.tsx`;
`frontend/src/panels/editors/OutputEditor.tsx`.
