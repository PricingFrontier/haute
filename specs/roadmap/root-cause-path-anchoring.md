# Root cause: path anchoring

## Scope

Which file a relative path in a node's settings means, in the editor, a run
and the deployed bundle.

This file is one pull request. It holds one package, `DPL-01`, which names
a cause that packages in other files share, and it is taken before them.
The pull request makes the shared mechanism the only path and adds the
test that keeps it so. It then checks each package listed below against
that package's own acceptance, removes the ones it has met, and removes
this file.

## Packages this resolves

None beside `DPL-01` itself, which is the finding and its remedy in one.

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| DPL-01 | Planned | P1 | A relative path means the same file in the editor, in a run and in the deployed bundle. |

## Planned improvements

### DPL-01 — One rule anchors a relative path for the editor and the bundle
**Why:** Reproduced. A relative path in a node's settings can exist under
both the project root and the pipeline's folder. The editor, a run and a
snapshot build resolve it with the project root first: the resolver's
docstring says the root's file wins "so the registry, the engine and an
explicit snapshot build always name the same file". The bundler resolves the
same setting with the pipeline folder first: its docstring says a file under
the pipeline folder always wins. With `data/lookup.pkl` under both the
project root and a pipeline folder `rating/`, the engine read
`data/lookup.pkl` and the bundle shipped `rating/data/lookup.pkl`: two
different files, with nothing said. The bundler takes this route for a Load
File's path, an Optimiser Apply's artifact and a Data Input read straight from
Parquet; a model file and its feature contract already use the engine's rule
inside the bundler. The layout is one the product leads to: the bundler's
refusal of data outside the pipeline folder tells the analyst to put the data
in the pipeline folder, and copying a folder there, instead of moving it,
satisfies the deploy while the editor keeps reading the root copy. From then
on the prices the analyst previews and the prices the service returns come
from different files whenever one copy is refreshed.

**Plan:** Decide the one rule (the engine's, since preview is what the
analyst checked) and give the bundler, the served scorer's runtime path
resolution and the schema reader that rule through one function. Where a
relative path exists in both places and the two files differ, refuse the
deploy naming both files; identical copies may pass. Which file a path
means is one question and whether that file may ship is another: the
deployment specification's rule that a Data Input the quote depends on
must lie in the pipeline folder stays, and is applied to the file the one
rule names. The precedence the deployment specification states is brought
into line in the same pull request.

**Acceptance:** With differing copies of one relative path under the project
root and the pipeline folder, `haute deploy` refuses and names both files.
With a single copy, in a place its kind of node may ship from, the bundle
ships the file preview read, asserted by content. With a single copy of a
Data Input's file outside the pipeline folder, the deploy refuses as it
does today, naming the node and the path. A test resolves each
path-bearing node setting through
the engine and through the bundler and requires the same file. A test lists the
functions outside `src/haute/_path_resolution.py` that anchor a relative
path, and only lets the list shrink.

**Dependencies:** None.

**Evidence:** `src/haute/_path_resolution.py::resolve_node_input_path` passes
`prefer="project"`; `src/haute/_path_resolution.py::resolve_graph_source_path`
passes `prefer="pipeline"`; `src/haute/deploy/_bundler.py::_resolve_path`
defaults to the pipeline folder; `src/haute/deploy/_scorer.py::_resolve_runtime_graph_paths`;
`src/haute/deploy/_schema.py::_resolve_pipeline_path`;
`src/haute/deploy/_bundler.py::refuse_data_outside_pipeline_folder`.
