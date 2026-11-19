# Root cause: values the browser did not edit

## Scope

A value nobody edited that comes back from the browser changed and is then
written to a file or run: the editor sends its whole document with every
request, and the server works out afterwards what was edited.

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

### CGEN-13 — The editor says what it changed; the server reads the rest from the file
**Why:** Read on 19 November 2026. The editor's document goes to the
browser whole and comes back whole with every request: a save, a preview,
a trace, Explore, a training estimate. A browser cannot hold everything
the file holds. It has one kind of number, so `1.0` comes back `1` and an
integer past 2^53 comes back as its nearest double. Whatever else it
cannot carry is not yet known.

Since 17 November 2026 a save keeps this out of its code ("Which steps a
write renders" in the code generation specification): the save rebuilds each stepped node's steps from the stored code, compares
them with the list the request brought, and renders only a node whose list
differs. That is one comparison on one path. Two packages have since asked
for it again. `CGEN-12` is the same loss on the requests that run the
editor's graph, which still build each node from the steps the browser
held (`NodeData._materialise_steps`); the save's request and the
assistant's apply are the only callers that take an unread list off
(`graph_without_unread_steps`). `PCF-09` is the same loss in a settings
file, where a save with nothing edited writes `1.0` back as `1`. Each plan
adds one more comparison of what came back with what the file holds. The
next request that carries the document, and the next kind of value a
browser changes, will need another.

**Plan:** Turn the question round. The server does not infer what was
edited; the request says. The browser names the revision of the document
it loaded and sends, for each node, either that it is untouched or what it
changed. The server builds an untouched node from the file at that
revision: its code, its steps and its settings file, byte for byte on a
save and as the file computes on a run. One function builds the graph a
request means, and the save and every request that runs the editor's graph
call it. The save's comparison, and the ones `CGEN-12` and `PCF-09` plan,
go once nothing they guard against can arrive. Update the
code generation specification's "Which steps a write renders" before the
change.

**Acceptance:** A test passes every shipped example's document through a
number and text round trip as JavaScript performs it, with nothing edited,
and then makes each request that carries the document. A save leaves every
file byte for byte, settings files included. A preview, a trace, an
Explore request and a training estimate give the column types and values
the file computes. A second test changes one field of an untouched node in
transit and requires the file's value used, or the request refused naming
the node. A request type that carries the document without a row in the
test fails it. The acceptance of `CGEN-12` and `PCF-09` is met.

**Dependencies:** None. Deliver before `CGEN-12` and `PCF-09`. `CGEN-09`'s
own plan is still needed for a step the analyst does edit.

**Evidence:** `src/haute/_types.py::NodeData._materialise_steps`;
`src/haute/_step_authority.py::graph_without_unread_steps`;
`src/haute/schemas.py`; `src/haute/_config_io.py::node_config_json`;
`src/haute/routes/_save_pipeline.py::_write_config_files`;
`frontend/src/utils/editedSteps.ts::withEditedSteps`;
`frontend/src/utils/graphSnapshot.ts::toCanonicalGraphPayload`;
`frontend/src/hooks/usePipelineAPI.ts`.
