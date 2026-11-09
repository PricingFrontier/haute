# Deploy roadmap

## Scope

Bundling a pipeline and serving it, as [the deploy
specification](../deploy/high-level.md) describes it: which files a deploy
ships for the paths a pipeline names, and how the served `/quote` route
answers a quote it cannot price. The packages below come from the 9 November
2026 codebase review. `DPL-01` was reproduced against `HEAD` on that day;
`DPL-02` is read from the source and was not run against a built image.

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| DPL-01 | Planned | P1 | A relative path means the same file in the editor, in a run and in the deployed bundle. |
| DPL-02 | Planned | P3 | An output value that is not a finite number is refused naming the output column and the quote. |

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
deploy naming both files; identical copies may pass.

**Acceptance:** With differing copies of one relative path under the project
root and the pipeline folder, `haute deploy` refuses and names both files.
With a single copy in either place, the bundle ships the file preview read,
asserted by content. A test resolves each path-bearing node setting through
the engine and through the bundler and requires the same file.

**Dependencies:** None.

**Evidence:** `src/haute/_path_resolution.py::resolve_node_input_path` passes
`prefer="project"`; `src/haute/_path_resolution.py::resolve_graph_source_path`
passes `prefer="pipeline"`; `src/haute/deploy/_bundler.py::_resolve_path`
defaults to the pipeline folder; `src/haute/deploy/_scorer.py::_resolve_runtime_graph_paths`;
`src/haute/deploy/_schema.py::_resolve_pipeline_path`;
`src/haute/deploy/_bundler.py::refuse_data_outside_pipeline_folder`.

### DPL-02 — A non-finite output value is refused by name
**Why:** Read from the source. The served route encodes its answer with
`allow_nan=False`, so a NaN or an infinity is never sent, but nothing checks
the output frame before encoding. The envelope is rendered inside the route's
`try`, so the encoder's `ValueError` ("Out of range float values are not JSON
compliant") reaches the catch-all and the client receives a 500
`deploy_internal_error`: "The quote could not be scored. The service's log
holds the detail under this request_id." The log holds the encoder's message
only, with no column and no quote. A division by a zero exposure, or zero by
zero, in one quote of a batch reaches this, and whoever runs the service
cannot tell which output or which quote. The NDJSON path fails the same way.
Separately, the route's answer to a body that is neither an object nor an
array carries no `error_code`, unlike every other refusal it gives.

**Plan:** Check the output frame's float columns before rendering. Answer a
non-finite value as a public contract error with its own code, naming the
output column and the quote's index, and log the same. Give the
neither-object-nor-array refusal an `error_code`.

**Acceptance:** A served pipeline whose output divides by a zero input
answers that quote with a 4xx whose payload names the column and the quote
index, in both the JSON and the NDJSON modes, and the log line carries the
same. Every refusal of `/quote` has an `error_code`, asserted over the
generated app's source.

**Dependencies:** None.

**Evidence:** `src/haute/deploy/_response_json.py::response_json`;
`src/haute/deploy/_container.py::_generate_app_source` holds the route, its
`_DocumentResponse` and its catch-all;
`src/haute/_output_assembler.py::render_output_document`.
