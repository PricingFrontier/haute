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
| `FEP-01` | [frontend-preview-explore](frontend-preview-explore.md) | A Refresh reads the file as it now is | In part |
| `FEP-02` | [frontend-preview-explore](frontend-preview-explore.md) | A Refresh reaches the data the Explore panes and the editors read | In part |

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| CCH-06 | Planned | P2 | Every stored result records what its run read, and every reader of it reports it old when any of that changes. |

## Planned improvements

### CCH-06 — A stored result is checked against what its run read
**Why:** Read on 16 November 2026. A node snapshot's signature is built
from the inputs the graph declares, through one checked contract, and that
part holds. Two kinds of input stay outside it. The first is what a node's
code reads without the graph knowing: a utility module imported inside the
code is `CCH-05`. The second is what Haute's own code does, which is
carried by version constants a developer must remember to raise: `CCH-01`.
`src/haute` holds more than forty such constants. Every reader outside the
engine then keeps its own list of what makes its answer old: the editor's
previews after a utility file is saved (`FEP-03`), Refresh (`FEP-01`,
`FEP-02`), the assistant's brief (`AST-05`), and the Workbench's form
revision and Accept (`WB-67`, `WB-69`). Each was found as its own defect,
and each list is missing something another list has.

**Plan:** Record with every stored result what its run read: the project's
modules it imported and the project's files it opened, each with a hash of
its content, and check the record when the result is used again. Derive the
engine's version from the content of the modules that compute a node's
output, or fail a test when those modules change and the constant does not.
Give the editor, the assistant and the Workbench one revision of the
project's inputs, taken from the same record, in place of their own lists.

**Acceptance:** One table-driven test changes each kind of input in turn: a
node's code, a utility module imported at the top and one imported inside a
node, a params file, a data file, the order of a node's inputs, and an
engine module. For each it requires every reader to report its answer old:
the snapshot, the preview, the Explore panes, the assistant's brief and the
form's revision. A new reader or a new kind of input is one more row.

**Dependencies:** None. Deliver before `CCH-01`, `CCH-04`, `CCH-05`,
`FEP-03`, `AST-05`, `WB-67` and `WB-69`, whose acceptance it should meet.

**Evidence:** `src/haute/_node_snapshots.py`; `src/haute/_cache.py`;
`src/haute/_project_imports.py`.
