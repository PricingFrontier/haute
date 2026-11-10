# IO layer roadmap

## Scope

Reading an input's file into the tables a pipeline runs on, as [the IO layer
specification](../io-layer/high-level.md) and [the JSON shredding
specification](../json-shredding/high-level.md) describe it: what the analyst
is told when the file cannot be read, and two file shapes the JSON reader
treats unlike their neighbours. The packages below come from the 9 November
2026 codebase review and were reproduced against `HEAD` on that day, by
giving the shipped live-quote example twenty awkward request files and
running it. None of the twenty crashed the run.

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| IOL-01 | Planned | P2 | An input whose file is wrong says what is wrong with it, in the run's output and in the input's panel. |
| IOL-02 | Decision | P3 | A JSON input that starts with a byte-order mark, and one that is empty, are each read or refused by a stated rule. |

## Planned improvements

### IOL-01 — An input that cannot be prepared says why
**Why:** Reproduced. When a Quote Input's or a Data Input's snapshot cannot
be built, the run is told "Preparing this API Input's tables failed.
(node_id=quote_input, identity_digest=..., build_class=bounded,
reason_code=build_failed)", and the route behind the input's own panel
answers "Input snapshot build failed." Both are fixed texts by design: the
error carries "digests and codes only: never a configured locator or a
secret". The builder knew the cause each time. For text in an integer column
the worker raised Haute's own message: "column 'fixture_value' has values
that don't match its declared type 'int'; re-infer the schema or change the
column's type to match the data". For a truncated file it raised "unexpected
end of data: line 1 column 36", and for a byte-order mark "unexpected
character: line 1 column 1". None of the three reaches the analyst in
`haute run`; the remedy the error carries, which the run does not print, is
to build the input from its panel or give the run more memory, which is no
help for a wrong value. A deploy's dry run, which reads the same file without the
snapshot layer, prints the first message in full, so the same mistake is
explained by one command and not by the other. A message about the content
of the analyst's own file holds no locator and no secret.

**Plan:** Let a failure that is about the file's content through: Haute's
schema errors as written, and a JSON syntax error as its line and column
with the file's project-relative name. Keep the fixed text for everything
else, where a provider's message may hold a connection string. Give
`reason_code` a value for a content failure, so the remedy no longer speaks
of memory. Print the remediation in the CLI's output.

**Acceptance:** With text in an integer column of the example's request
file, `haute run` and the input's panel each name the column, its declared
type and the two remedies, as `haute deploy --dry-run` does today. A truncated file is
reported with its line and column. A database input whose connection fails
still answers the fixed text, asserted with a provider error holding a
password.

**Dependencies:** None.

**Evidence:** `src/haute/_input_preparation.py::_failure` and
`src/haute/_input_preparation.py::_run_build`;
`src/haute/errors.py::InputPreparationError`;
`src/haute/routes/input_cache.py::_build_failure`;
`src/haute/_api_input_schema.py::ApiInputSchemaError` is the message
withheld; `src/haute/routes/input_cache.py::_provider_error_diagnostic` is
where the cause is logged.

### IOL-02 — A byte-order mark and an empty file follow a stated rule
**Why:** Reproduced. A JSON request file that starts with a UTF-8
byte-order mark is refused as malformed ("unexpected character: line 1
column 1"), with the fixed text of `IOL-01` as all the analyst sees. Windows
PowerShell 5.1 and some exports write that mark by default, and the JSON
standard lets a reader ignore it. Haute's own readers disagree among
themselves, each with a test that holds it: the pipeline settings file, the
editor's sidecar and the workbench parameters are read through the mark; a
node's config file is refused with a message that names the mark; and a
JSON Lines input is asserted to fail exactly as the JSON library does, with
a message that does not. A file of no bytes at all, which is not
JSON, is read as no quotes: the run succeeds with an empty table, where a
truncated file is refused and an empty CSV is refused for having no header.
A failed export that left a zero-byte file therefore looks like a day with
no quotes. Neither rule is written down in the specifications.

**Plan:** Decide both. The recommended rules: read a file through a leading
byte-order mark, since feeds are not the analyst's to control; refuse a
zero-byte file by name, as a truncated one is refused, and keep `[]` as the
way to say no quotes. Record the two rules in the JSON shredding
specification.

**Acceptance:** A request file with a byte-order mark gives the same tables
as the file without it, and the JSON Lines test that asserts the refusal
changes with the rule. A zero-byte file is refused naming the file and
saying it is empty; a file holding `[]` still gives an empty table.

**Dependencies:** The decision above; `IOL-01` carries the refusal's text
to the analyst.

**Evidence:** `src/haute/_io.py` refuses an empty CSV;
`src/haute/assistant/assets/examples/minimal_live_quote/pipeline.py` and its
`src/haute/assistant/assets/examples/minimal_live_quote/config/request.json`
are the example the files were given to; `specs/json-shredding/high-level.md`;
`tests/test_pipeline_settings.py::test_a_file_saved_with_a_byte_order_mark_reads`,
`tests/test_pipeline_recovery.py::test_an_editor_sidecar_with_a_byte_order_mark_still_reads`
and
`tests/test_workbench_params.py::test_a_file_saved_with_a_byte_order_mark_reads_as_written`
hold the readers that accept the mark;
`tests/test_config_io.py::test_config_file_with_bom_raises` the one that
names it;
`tests/test_scalar_array_and_inference.py::test_fused_json_range_preserves_orjson_error_evidence_for_known_shapes`
asserts the refusal for a JSON Lines input.
