# Deploy roadmap

## Scope

Bundling a pipeline and serving it, as [the deploy
specification](../deploy/high-level.md) describes it: which files a deploy
ships for the paths a pipeline names, how the served `/quote` route answers
a quote it cannot price, what `haute deploy --dry-run` checks, and what a
test quote can expect. The packages below come from the 9 November
2026 codebase review. `DPL-01` was reproduced against `HEAD` on that day;
`DPL-02` is read from the source and was not run against a built image;
`DPL-03` was reproduced by running the command, and `DPL-04` by calling the
comparison a deploy's validation uses.

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| DPL-01 | Planned | P1 | A relative path means the same file in the editor, in a run and in the deployed bundle. |
| DPL-02 | Planned | P3 | An output value that is not a finite number is refused naming the output column and the quote. |
| DPL-03 | Planned | P3 | A dry run refuses a deploy target that does not exist, and a missing pipeline file is reported without a traceback. |
| DPL-04 | Planned | P3 | A test quote can expect a date or a date-time output, written as the response writes it. |

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

### DPL-03 — A dry run checks the target, and a missing pipeline is reported plainly
**Why:** Reproduced by running the command. The command tells the analyst to
"Use --dry-run to validate locally without deploying", and a dry run parses,
prunes, infers the schemas, validates and scores the test quotes. It never
looks at the target: with `target = "nowhere"` under `[deploy]`, a shipped
example answered "Validation passed" and "Dry run complete" with exit code
0. The target is checked only on the path that deploys, so the typo is found
by the CI run. Separately, when `haute.toml` exists and names a pipeline file
that does not, the command ends in a Python traceback whose last line is the
(good) message; the same failure without a `haute.toml` is caught and printed
as an error with a hint.

**Plan:** Validate the target in the dry run with the check the deploy path
uses, before any other work. Catch the pipeline file's resolution in the
branch that loads `haute.toml` as the other branch does.

**Acceptance:** `haute deploy --dry-run` with an unknown target exits 1
naming the target and the targets that exist. With a `haute.toml` naming a
missing pipeline file it prints one error line and the hint, with no
traceback.

**Dependencies:** None.

**Evidence:** `src/haute/cli/_deploy.py::handle_deploy`;
`src/haute/deploy/__init__.py::_validate_target` is called by
`src/haute/deploy/__init__.py::deploy_resolved` and
`src/haute/deploy/__init__.py::deploy`, the two functions that deploy, and
by nothing the dry run reaches.

### DPL-04 — A test quote can expect a date output
**Why:** Reproduced. A test quote's `expected` values come from a JSON file,
where a date can only be text, and the served response writes a date as its
ISO text. The validation compares each expected value with the scored
frame's own Python value, so a date is compared with a `datetime.date`
object and never matches. For a frame holding `valid_until` of 31 December
2026, which the response serves as `"2026-12-31"`, expecting `"2026-12-31"`
failed with "row 0 column 'valid_until' mismatch: expected='2026-12-31'
actual=datetime.date(2026, 12, 31)"; a date-time failed the same way, while
a number matched. The same leaf rule sits under the comparison of a priced
response with an accepted one, which reported an unchanged date as changed
when given the object. A pipeline that returns a date, such as how long a
quote is valid, cannot have that output held by a test quote, and the
message does not say why.

**Plan:** Compare a date, date-time or time leaf by the text the response
serves for it, with the encoder the response uses. Numbers, decimals
included, stay numeric and keep their tolerance.

**Acceptance:** A test quote expecting a date and a date-time output as the
response writes them passes, and one expecting another day fails naming both
days as text. A decimal output is still compared as a number within the
tolerance. The accepted-response comparison reports no change for an equal
date.

**Dependencies:** None.

**Evidence:** `src/haute/deploy/_validators.py::_expected_value_matches`;
`src/haute/deploy/_validators.py::_validate_expected_outputs` and
`src/haute/deploy/_validators.py::_score_test_quote_cases` pass the scored
frame's rows; `src/haute/deploy/_validators.py::response_changes`;
`src/haute/deploy/_response_json.py::_encode_value` is the served encoding.
