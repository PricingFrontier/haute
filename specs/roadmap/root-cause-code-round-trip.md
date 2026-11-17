# Root cause: code rewritten on save

## Scope

A save that rewrites hand-written code nobody edited: the editor's steps
take authority over the code they were read from.

This file is one pull request. It holds one package, `CGEN-11`, which names
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
| `CGEN-09` | [codegen](codegen.md) | A load and a save do not rewrite the numbers in hand-written code | In part |
| `CGEN-10` | [codegen](codegen.md) | A Transform that rebinds its input's name keeps its code on save | In part |
| `PCF-06` | [pipeline-config](pipeline-config.md) | A record of unfinished steps ends when the node's code is rewritten by hand | In part |

`CGEN-09` keeps its own work for a step the analyst does edit: a number
must render as it was typed. `CGEN-10` keeps the lift that is given the
node's input names. `PCF-06` keeps its rule for the record in `.haute.json`.

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| CGEN-11 | Planned | P1 | A save keeps, byte for byte, the code of every node whose steps nobody edited, whatever the steps lost in the browser. |

## Planned improvements

### CGEN-11 — A save writes a node's code only when its steps were changed
**Why:** Read on 16 November 2026. When a file is loaded the server rebuilds
steps from each stepped node's code and accepts them only when they render
that code's text exactly; that proof holds. On a save the authority turns
round. A config that carries a step list has its code rendered from the
list, and the code text it also carries is not read. So whatever the list
loses between the load and the save rewrites hand-written code, with no
edit by anyone and no warning. `CGEN-09`, rated P1, is one such loss: a
number that a browser held. `CGEN-10` names the same two functions. The
steps editor does the same when a panel opens: it renders the list and
adopts the answer as the node's code when the text differs. Steps kept in
`.haute.json` for an unfinished node take the same authority over a body
written by hand since (`PCF-06`). Each loss found so far has its own
package and its own remedy; the next thing a step cannot carry through a
browser will be found the same way, in a saved file.

**Plan:** Make the code the authority and the steps a view of it. A step
list reaches the code through one operation only: an edit made in the steps
editor. Whether a node's steps were edited is not a mark kept on the node,
which would need a life of its own through saves, Undo and Redo. It is
worked out in the browser when a save is sent: the node's step list is
compared with the list in the store's saved baseline, two values that
never left the browser. A save names the nodes whose lists differ, and the
server renders code from steps for those alone. Every other node keeps the
code text it carries, byte for byte, and the list it carries is not read.
An acknowledged save moves the baseline as it does today, so the next save
names only what changed since, an edit made while a save ran is still
named, and an Undo or Redo after a save is named when it leaves the list
different from what was saved. The store's history is not changed. A node
that goes unnamed must carry the code that was saved for its steps, and
the browser's own rendering arrives late and separately. So the save's
answer returns the code the server wrote for each node it rendered, and
the acknowledgement puts that code in the baseline, and on the live node
where its steps are still the ones saved. A node whose steps have changed
again keeps its own and is named by the next save.
Comparing the carried list with one rebuilt from the code was set aside: a
field lost in the browser makes the two differ, which is the case that must
not render. The steps editor adopts a rendering as the node's code only
after such an edit, never on opening a panel.

**Acceptance:** A test loads every example pipeline and a set of
hand-written statements, passes the document through a number and text
round trip as JavaScript performs it, saves, and requires each file byte
for byte. A second test removes or changes one field of an unedited step in
transit and requires the node's code unchanged. A browser test edits a
step and saves twice, edits during a save, and saves, undoes, redoes and
saves; each save writes the code of the steps on screen and no other
node's. With the browser's own rendering held back, a step is edited and
the pipeline saved twice: the second save leaves the file as the first
wrote it. A test fails when code is rendered from a step list anywhere but through
the nodes a save names. The journey of `CGEN-09` with no step edited passes
without its own remedy.

**Dependencies:** None. Deliver before `CGEN-09`, `CGEN-10` and `PCF-06`.

**Evidence:** `src/haute/_config_builder.py`;
`src/haute/_codegen_builders.py`; `src/haute/_polars_steps_lift.py`;
`src/haute/_sidecar.py`;
`frontend/src/panels/editors/polarsSteps/useRenderedSteps.ts`.
