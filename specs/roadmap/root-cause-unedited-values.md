# Root cause: values the browser did not edit

## Scope

A value nobody edited that comes back from the browser changed and is then
written to a file or run. The rule that keeps this out is applied by each
writer for a step list alone; the requests that run the editor's graph,
and a node's settings, are outside it.

This file is one pull request. It holds one package, `CGEN-13`, which names
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
| `CGEN-12` | [codegen](codegen.md) | A preview, a trace and every other request that runs the editor's graph run the code of a node nobody edited | Resolves |
| `PCF-09` | [pipeline-config](pipeline-config.md) | A save keeps the spelling of the numbers in a settings file nobody edited | Resolves |
| `CGEN-09` | [codegen](codegen.md) | A step list rendered after a browser held it keeps its numbers | In part: a step the analyst edits still needs its number carried as its kind and its digits |

## Planned improvements

### CGEN-13 — One rule for what the editor edited, on every request and for every value
**Why:** Read on 19 November 2026. The editor's document goes to the
browser whole and comes back whole with every request. A browser cannot
hold everything the file holds: it has one kind of number, so `1.0` comes
back `1` and an integer past 2^53 comes back as its nearest double.

The code generation specification already has the right rule for this,
"Which steps a write renders": the writer names the nodes whose steps were
edited, and every other stepped node is written from the code it carries,
its list unread. The editor decides by comparing each node's steps with
its saved baseline's, sends the baseline's code for the ones that match
and names the rest (`withEditedSteps`); the server takes the unread lists
off before a node is built (`graph_without_unread_steps`).

The rule is held in three places and covers one kind of value. The save
carries `edited_steps`, the node save carries `steps_edited`, and the
assistant's apply computes its own; each request model applies the rule
for itself. A request model that carries the graph and does not apply it
builds every node from the steps the browser held, because building a
node renders its steps into its code (`NodeData._materialise_steps`). That
is `CGEN-12`: a preview, a trace, Explore and a training estimate compute
an integer column where the file computes a float. And the rule speaks
only of steps. A node's settings travel as JSON numbers with no word on
whether anyone edited them, so a save with nothing edited writes a
settings file's `1.0` back as `1` (`PCF-09`). `CGEN-12` plans to carry the
naming on four more requests; `PCF-09` plans a comparison of values at the
settings writer, a second mechanism beside the first. The next request
that carries the document, and the next kind of value, would each need
their own again.

**Plan:** Keep the specification's rule and give it one home on each side
and its full reach.

- Name what was edited part by part, not node by node. A node's steps and
  its settings are named separately, each by comparison with the
  baseline's, as steps are now. A part that is not named is taken as the
  file holds it; only a named part is read from the request. So a node
  whose label or one setting changed still has its steps unread and its
  code kept, and a node whose step changed still has its settings file
  kept. A node the baseline does not hold has every part named. A node
  inside a submodel is named by its scope and id, as now.
- One function in the browser builds what is sent for every request that
  carries the document, the saves and the requests that run the graph
  alike, from the document and its baseline.
- One base model on the server carries the document and the named parts
  and applies the rule before any node is built. The save, the node save,
  the assistant's apply and each request that runs the editor's graph use
  it; none applies the rule for itself.
- A repeated save is still answered from its receipt before anything is
  rebuilt: its base revision is one its first sending already replaced.

Update "Which steps a write renders" in the code generation specification
before the change.

**Acceptance:** A test passes every shipped example's document through a
number and text round trip as JavaScript performs it and makes each
request that carries the document, in three states. With nothing edited, a
save leaves every file byte for byte, settings files included, and a
preview, a trace, an Explore request and a training estimate give the
column types and values the file computes. With one node edited, the edit
reaches the saved file and the run, every neighbouring node's values keep
their exact spelling and meaning, and so do the parts of the edited node
that were not edited: after a change of its label or of one setting its
code is byte for byte and `pl.lit(1.0)` is still a decimal, and after a
change of one step its settings file is byte for byte. With a new node the baseline does not hold, at the root and
inside a submodel, the node is saved and run as sent. A further test
changes one field of an untouched node in transit and requires the file's
value used. A request model that carries the document and does not use
the shared model fails a test. The acceptance of `CGEN-12` and `PCF-09` is
met.

**Dependencies:** None. Deliver before `CGEN-12` and `PCF-09`. `CGEN-09`'s
own plan is still needed for a step the analyst does edit.

**Evidence:** `specs/codegen/high-level.md`;
`src/haute/_types.py::NodeData._materialise_steps`;
`src/haute/_step_authority.py::graph_without_unread_steps`;
`src/haute/schemas.py::SavePipelineRequest`;
`src/haute/_pipeline_repair_actions.py`;
`src/haute/assistant/_application.py`;
`src/haute/_config_io.py::node_config_json`;
`src/haute/routes/_save_pipeline.py::_write_config_files`;
`frontend/src/utils/editedSteps.ts::withEditedSteps`;
`frontend/src/utils/graphSnapshot.ts::toCanonicalGraphPayload`;
`frontend/src/hooks/usePipelineAPI.ts`.
