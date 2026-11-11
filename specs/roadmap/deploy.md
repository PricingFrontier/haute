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
comparison a deploy's validation uses. `DPL-05` comes from the review
plan's sweep of the commands on 11 November 2026 and was reproduced on
fresh copies of the shipped examples.

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| DPL-01 | Planned | P1 | A relative path means the same file in the editor, in a run and in the deployed bundle. |
| DPL-02 | Planned | P3 | An output value that is not a finite number is refused naming the output column and the quote. |
| DPL-03 | Planned | P3 | A dry run refuses a deploy target, a model name and a target setting that no deploy can use, and a missing pipeline file is reported without a traceback. |
| DPL-04 | Planned | P3 | A test quote can expect a date or a date-time output, written as the response writes it. |
| DPL-05 | Planned | P2 | `haute deploy --dry-run` passes on a clean checkout of a pipeline whose input is a CSV Data Input, as a CI job runs it, without `haute run` first. |

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

Run again on 11 November 2026 on a project made by `haute init`, the dry
run passes more settings that no deploy can use: an empty model name, in
`haute.toml` and as `--model-name ""`; `min_approvers = -1`; and a serving
workload size that does not exist, `serving_workload_size = "Enormous"`.
Each answered "Dry run complete - no model was deployed." with exit code 0.

**Plan:** Validate the target in the dry run with the check the deploy path
uses, before any other work, and with it the model name and the target's
own settings. Catch the pipeline file's resolution in the branch that loads
`haute.toml` as the other branch does.

**Acceptance:** `haute deploy --dry-run` with an unknown target exits 1
naming the target and the targets that exist. With an empty model name, a
negative number of approvers or an unknown workload size it exits 1 naming
the setting. With a `haute.toml` naming a missing pipeline file it prints
one error line and the hint, with no traceback.

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

### DPL-05 — A dry run on a clean checkout reads its input's schema
**Why:** Reproduced on 11 November 2026 by the plan's sweep of the commands,
and again by the main session on fresh copies of the shipped examples. A
pipeline whose input is a Data Input reading a CSV file is checked out
clean, as a CI job has it, and `haute deploy --dry-run` runs. It exits 1:
"Resolution failed: Failed to read schema from 'data/synthetic.csv' for
input node 'quotes': input_snapshot_missing: This Data Input runs from a
snapshot that has not been built yet. Build the snapshot (or run a preview,
which builds it automatically) and try again." A CI job has no editor to
preview in. After one `haute run`, which builds the snapshot under the
ignored cache folder, the same dry run passes: "Dry run complete - no model
was deployed."

Five of the eight shipped examples tried fail this way on a fresh copy
(`minimal_batch`, `rating_step`, `linear_pricing`, `trace_audit`,
`reusable_submodel`); the three whose input is an API input pass. The CI
workflows that `haute init` generates run `uv run haute deploy --dry-run`
as their check, "Dry-run deploy (score test quotes)", and `haute deploy`
after it, each on a clean checkout. The real deploy was not run; it reads
the schema through the same function (read from the source).

The engine already has the way round this: a caller that wants only the
schema can ask for it to be inferred from the file when no snapshot exists.
The deploy's schema read does not ask.

**Plan:** Have the deploy's schema read use the inferred schema when the
input's snapshot is missing, as the engine's other schema-only callers do;
or build the snapshot first, as `haute run` does. Where neither is possible,
say what a CI job can run.

**Acceptance:** On a fresh copy of each shipped example with a Data Input,
`haute deploy --dry-run` passes without any command run before it. A test
runs the dry run on clean copies of the examples (`EQ-04`).

**Dependencies:** None. `EQ-04` plans the test that would have caught it.

**Evidence:** `src/haute/deploy/_schema.py::infer_input_schema`;
`src/haute/_input_providers.py::resolve_data_input`;
`src/haute/_input_providers.py::recording_schema_tiers`;
`src/haute/_scaffold.py::_DRY_RUN_CHECK`.
