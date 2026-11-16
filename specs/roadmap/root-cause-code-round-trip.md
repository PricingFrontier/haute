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
| `CGEN-09` | [codegen](codegen.md) | A load and a save do not rewrite the numbers in hand-written code | Resolves |
| `CGEN-10` | [codegen](codegen.md) | A Transform that rebinds its input's name keeps its code on save | Resolves |

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
adopts the answer as the node's code when the text differs. Each loss found
so far has its own package and its own remedy; the next thing a step cannot
carry through a browser will be found the same way, in a saved file.

**Plan:** Make the code the authority and the steps a view of it. On a save,
rebuild the steps from the node's stored code, pass them through the same
JSON round trip the browser applies, and compare them with the list the
request brought. Where they are equal, keep the stored code as it is, byte
for byte; render only a node whose list differs. In the editor, adopt a
rendering as the node's code only after a step has been edited.

**Acceptance:** A test loads every example pipeline and a set of
hand-written statements, passes the document through a number and text
round trip as JavaScript performs it, saves, and requires each file byte
for byte. A second test removes or changes one field of an unedited step in
transit and requires the node's code unchanged, or the save refused naming
the node. The journey of `CGEN-09` passes without its own remedy.

**Dependencies:** None. Deliver before `CGEN-09` and `CGEN-10`: `CGEN-09`'s
own plan is still needed for a step the analyst does edit.

**Evidence:** `src/haute/_config_builder.py`;
`src/haute/_codegen_builders.py`; `src/haute/_polars_steps_lift.py`;
`frontend/src/panels/editors/polarsSteps/useRenderedSteps.ts`.
