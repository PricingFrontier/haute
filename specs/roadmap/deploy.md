# Deploy roadmap

## Scope

Bundling a pipeline and serving it, as [the deploy
specification](../deploy/high-level.md) describes it: which files a deploy
ships for the paths a pipeline names, how the served `/quote` route answers
a quote it cannot price, what `haute deploy --dry-run` checks, and what a
test quote can expect. The packages below come from the 9 November
2026 codebase review.
`DPL-02` is read from the source and was not run against a built image;
`DPL-03` was reproduced by running the command, and `DPL-04` by calling the
comparison a deploy's validation uses. `DPL-05` comes from the review
plan's sweep of the commands on 11 November 2026 and was reproduced on
fresh copies of the shipped examples.

`DPL-06` and `DPL-07` come from the review plan's phase 5, a reader from a second
model family (Codex) over the code changed since the 31 October audit. A
worker built each run and the main session ran each again on 14 November
2026.

`DPL-11` and `DPL-12` come from the review plan's phase 4, the deploy
image run in Docker under memory and CPU limits. A worker built each run
and the main session ran each again on 16 November 2026.

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| DPL-02 | Planned | P3 | An output value that is not a finite number is refused naming the output column and the quote. |
| DPL-03 | Planned | P3 | A dry run refuses a deploy target, a model name and a target setting that no deploy can use, and a missing pipeline file is reported without a traceback. |
| DPL-04 | Planned | P3 | A test quote can expect a date or a date-time output, written as the response writes it. |
| DPL-05 | Planned | P2 | `haute deploy --dry-run`, and the pipeline file run as Python, pass on a clean checkout of a pipeline whose input is a CSV Data Input, as a CI job runs them, without `haute run` first. |
| DPL-06 | Planned | P2 | `haute impact` fails, naming the output, when staging answers no premium where production answers one. |
| DPL-07 | Planned | P3 | `haute impact` sends a Date column as ISO text instead of ending in a traceback. |
| DPL-11 | Planned | P2 | A deployed container with less than 8 GiB of memory answers a request of several quotes, or refuses it in words that name the limit and the setting. |
| DPL-12 | Planned | P2 | A deployed container with one CPU answers a request of several quotes in seconds, not with a timeout after five minutes. |

## Planned improvements

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

The same cause reaches the pipeline file run as Python (found on 18 November
2026 by `tests/test_run_parity.py`): on a fresh copy of `minimal_batch`,
`pipeline.run(source="live")` raises the same `input_snapshot_missing` until a
preview or `haute run` has built the snapshot, since a standalone run reads a
snapshot-backed Data Input only from a published snapshot.

**Plan:** Have the deploy's schema read use the inferred schema when the
input's snapshot is missing, as the engine's other schema-only callers do;
or build the snapshot first, as `haute run` does. Where neither is possible,
say what a CI job can run.

**Acceptance:** On a fresh copy of each shipped example with a Data Input,
`haute deploy --dry-run` and `pipeline.run()` pass without any command run
before them. A test runs the dry run on clean copies of the examples
(`EQ-04`), and `tests/test_run_parity.py::test_a_clean_checkout_runs_every_way`
passes with its expected-failure mark removed.

**Dependencies:** None. `EQ-04` plans the test that would have caught it.

**Evidence:** `src/haute/deploy/_schema.py::infer_input_schema`;
`src/haute/_input_providers.py::resolve_data_input`;
`src/haute/_input_providers.py::recording_schema_tiers`;
`src/haute/_scaffold.py::_DRY_RUN_CHECK`.

### DPL-06 — An impact report says when staging no longer answers an output
**Why:** Reproduced on 14 November 2026 with the real `haute impact`
against two stand-in endpoints. Production answers a premium and a tax for
each quote. Staging answers the same tax and a null premium, or no premium
at all. The command exits 0 and reports only "Output: tax", no rows
changed, premium impact 0.0%. The premium is not mentioned. The
specification has the command raise rather than report when predictions
hold missing values. The outputs compared are staging's numeric columns,
and a column that is all null, or absent, is not one of them.

**Plan:** Compare the outputs production answers. Fail, naming the
output, when staging lacks one or answers null where production answers a
number.

**Acceptance:** Both runs above exit 1 and name `premium`.

**Dependencies:** None.

**Evidence:** `src/haute/deploy/_impact.py`; `specs/deploy/high-level.md`.

### DPL-07 — `haute impact` sends a date column as text
**Why:** Reproduced on 14 November 2026. With an impact dataset whose
Parquet file holds a Date column, `haute impact` exits 1 with a traceback
ending "TypeError: Object of type date is not JSON serializable", and
neither endpoint receives a request. With the same column as text the
quotes are sent and the report is written. A served request takes a date
as ISO text.

**Plan:** Build the request rows with the JSON writer the service's own
callers use, which writes dates and times as ISO text.

**Acceptance:** The run above sends `2026-11-01` for the date and writes
its report.

**Dependencies:** None.

**Evidence:** `src/haute/deploy/_impact.py`; `src/haute/cli/_impact.py`;
`specs/deploy/high-level.md`.

### DPL-11 — A container with less than 8 GiB answers a request of several quotes
**Why:** Reproduced on 16 November 2026 with the deploy image of the
`minimal_live_quote` example in Docker. One quote answers 200 at every
size. An array of two quotes answers 507
`in_flight_memory_budget_exceeded` in a container started with 2 GiB and
with 6 GiB of memory, and 200 with both rows at 8 GiB. The answer carries
numbers and no words, and the container logs only "507". A request of
several quotes reserves a fixed 4 GiB whatever its size, and the
reservation must fit in the container's memory less a reserve. The
variable that lowers it, `HAUTE_DEPLOY_BATCH_MEMORY_LIMIT_MB`, is set by
nothing the image ships and named by nothing the answer says. A worker's
sweep saw the same in five examples' images.

**Plan:** Size the reservation for several quotes from the container's
memory limit, or from the request, instead of a fixed 4 GiB. Where a
request is still refused, say in words what limit refused it and the
setting that changes it.

**Acceptance:** The array of two quotes answers 200 in the 2 GiB
container. A request that is refused for memory names the limit and the
setting.

**Dependencies:** None.

**Evidence:** `src/haute/_execution_admission.py`;
`specs/deploy/high-level.md`.

### DPL-12 — A container with one CPU answers a request of several quotes
**Why:** Reproduced on 16 November 2026 with the same image and 8 GiB of
memory. With two CPUs an array of two quotes answers in 2.5 seconds. With
`--cpus 1`, the size of the smallest hosted containers, it gets no answer
for 317 seconds and then 504 `deploy_batch_timeout`; one quote still
answers at once. Setting `POLARS_MAX_THREADS=1` on two CPUs does the
same, and `--cpus 1` with `POLARS_MAX_THREADS=2` answers in 3 seconds: the
hang follows a Polars pool of one thread, which Polars sizes from the
container's CPU quota. A worker saw the batch's process asleep with its
result file open, and the next request refused with 507.

**Plan:** Find why the batch worker's result write waits on a pool of one
thread, and remove the wait. Until then give the worker at least two
Polars threads.

**Acceptance:** The array of two quotes answers 200 within seconds under
`--cpus 1` and under `--cpus 0.5`.

**Dependencies:** None.

**Evidence:** `src/haute/_worker_isolation.py`;
`specs/deploy/high-level.md`.
