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

`IOL-03` to `IOL-06` come from the review plan's sweep of 10 November 2026,
which gave 79 awkward files to the Data Input, the Quote Input and Load
File through the preview and `haute run`. Each was reproduced on that day,
as were the additions that sweep made to `IOL-01` and `IOL-02`.

A cause several packages here share has its own file and is taken first:
[column kinds](root-cause-column-kinds.md).

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| IOL-01 | Planned | P2 | An input whose file is wrong says what is wrong with it, in the run's output and in the input's panel. |
| IOL-02 | Decision | P3 | A JSON input that starts with a byte-order mark, one that is empty and one that repeats a key are each read or refused by a stated rule. |
| IOL-03 | Planned | P2 | A Quote Input whose sample is a CSV file runs from the command line as it previews in the editor. |
| IOL-04 | Planned | P2 | A CSV file saved as UTF-16 is refused naming its encoding, instead of being read as garbled columns. |
| IOL-05 | Planned | P2 | Load File refuses a JSON file that is not UTF-8, instead of replacing the bytes it cannot read. |
| IOL-06 | Planned | P3 | Two XML children that share a name only once their namespaces are stripped are refused, as two such attributes are. |

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
of the analyst's own file holds no locator and no secret. The fixed text
also hides a cause that is Haute's own to state: on an install without the
Excel reader's package, an Excel Data Input fails with it, and the worker's
message, "Reading format 'excel' needs an engine package this haute install
lacks (required package 'fastexcel' not found", never arrives (reproduced
on 10 November 2026). The editor's format list names the package a format
needs; a run on another machine does not. A file that is not there is
told the same way: on Linux, a Data Input whose path is spelt
`data/Synthetic.csv` for the file `data/synthetic.csv`, a spelling Windows
opens, ends `haute run` with "This Data Input's source is unavailable and
no published snapshot exists." and no file's name (run on 13 November
2026; `PCF-05` is the Windows side).

**Plan:** Let a failure that is about the file's content through: Haute's
schema errors as written, and a JSON syntax error as its line and column
with the file's project-relative name. Keep the fixed text for everything
else, where a provider's message may hold a connection string. Let Haute's
own configuration errors through as well, such as a format whose reader
package is not installed. Give
`reason_code` a value for a content failure, so the remedy no longer speaks
of memory. Print the remediation in the CLI's output.

**Acceptance:** With text in an integer column of the example's request
file, `haute run` and the input's panel each name the column, its declared
type and the two remedies, as `haute deploy --dry-run` does today. A truncated file is
reported with its line and column. A database input whose connection fails
still answers the fixed text, asserted with a provider error holding a
password. An Excel input on an install without its reader package names
the package to install.

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
no quotes. Neither rule is written down in the specifications. The readers
also disagree on a record that repeats a key, reproduced on 10 November
2026: for `{"region": "north", "region": "south"}` a Data Input keeps the
first value, and a Quote Input and Load File keep the last, each without a
word. Node settings, global constants and pipeline settings refuse a
repeated key.

**Plan:** Decide both. The recommended rules: read a file through a leading
byte-order mark, since feeds are not the analyst's to control; refuse a
zero-byte file by name, as a truncated one is refused, and keep `[]` as the
way to say no quotes; refuse a record that repeats a key, as Haute's own
settings files do. Record the rules in the JSON shredding specification.

**Acceptance:** A request file with a byte-order mark gives the same tables
as the file without it, and the JSON Lines test that asserts the refusal
changes with the rule. A zero-byte file is refused naming the file and
saying it is empty; a file holding `[]` still gives an empty table. A record
that repeats a key gets one answer from a Data Input, a Quote Input and
Load File: the refusal, naming the key.

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

### IOL-03 — A Quote Input on a CSV file runs from the command line
**Why:** Reproduced on 10 November 2026. A Quote Input's sample may be a
flat file, and a CSV with every column's type declared in the node's table
previews in the editor: two rows. `haute run` on the same pipeline exits 1:
"CSV sources require declared dtypes for bounded-memory execution profiles.
Add schema_overrides, dtypes, column_dtypes, or schema for every CSV column
read by this execution." The remedy cannot be followed. A Quote Input's
settings hold a path and its tables, and adding `schema_overrides` is
refused: "Node 'quote_input' has apiInput config keys the node type does not
declare". The types the reader asks for are the ones the node's table
already declares; the run's reader looks for them under a Data Input's
keys. In the sweep the same node on a Parquet file ran.

**Plan:** Give the flat-file reader the column types a Quote Input's table
declares, as its schema, on every execution profile. Where a column has no
declared type, refuse naming the node and the column and saying to confirm
its type in the node's table.

**Acceptance:** A Quote Input on a CSV file with declared types gives the
same rows in the editor's preview and from `haute run`, and deploys. With a
column left untyped, both refuse naming the node and the column, and the
message names no key the node's settings cannot hold. A test runs one Quote
Input through both for CSV, JSON, JSON Lines and Parquet samples.

**Dependencies:** None.

**Evidence:** `src/haute/_io.py::_validate_csv_declared_schema_for_profile`;
`src/haute/_io.py::build_data_source_adapter` reads the types from keys a
Quote Input cannot hold; `src/haute/_node_config_models.py::ApiInputConfig`;
`docs/building-models/nodes/quote-input.md` (the sample may be a flat file).

### IOL-04 — A CSV file that is not UTF-8 text is refused by name
**Why:** Reproduced on 10 November 2026. A CSV saved as UTF-16 with its
byte-order mark, which Windows PowerShell 5.1 writes when output is
redirected to a file, is read without a word by a Data Input. The columns
come out as `r\x00o\x00w\x00_\x00i\x00d\x00` behind two replacement
characters, every value carries a null character between its letters, and
a third row appears that the file does not hold. The status is ok and no
warning is given. In the sweep both of the Data Input's CSV readers and a
Quote Input did the same, and `haute run` printed the garbled frame. A CSV
in Latin-1 is refused as invalid UTF-8, so a wrong encoding is caught in
one case and read as data in the other.

**Plan:** Before a CSV is read, look at its first bytes: a UTF-16 or UTF-32
byte-order mark, or null bytes in the header line, is a file in another
encoding. Refuse it naming the file and the encoding found and saying to
save it as UTF-8. Read through a UTF-8 byte-order mark, as `IOL-02`
proposes for JSON.

**Acceptance:** A UTF-16 CSV, with and without its mark, is refused by a
Data Input and by a Quote Input in a message naming the file and the
encoding, in the preview and from `haute run`. The same data saved as UTF-8,
with or without a mark, reads to the same two rows. No reader returns a
column name holding a null character.

**Dependencies:** `IOL-01` carries the refusal's text to the analyst.

**Evidence:** `src/haute/_polars_io_registry.py::_invoke_polars_input`;
`src/haute/_io.py::read_source`; `src/haute/_io.py::_csv_header_columns`.

### IOL-05 — Load File refuses bytes it cannot read
**Why:** Reproduced on 10 November 2026. Load File opens a JSON file as
UTF-8 with undecodable bytes replaced. A file holding `{"name": "Crème"}`
in Latin-1 loads with status ok, its `è` turned into the replacement
character U+FFFD: the letter is gone and nothing is said. In the sweep a
Data Input and a Quote Input refused the same file, and Python's own JSON
reader refuses its bytes. The value could as easily be a key that a lookup
then never matches. The same line makes Load File the one reader that
refuses a UTF-8 byte-order mark in the JSON library's own words
("Unexpected UTF-8 BOM"), which `IOL-02` covers.

**Plan:** Open the file strictly. Refuse a file that is not UTF-8 naming
the file and the position of the first byte that is not, and say to save
it as UTF-8. Follow `IOL-02`'s rule for a byte-order mark.

**Acceptance:** The Latin-1 file is refused by Load File in a message that
names the file; no loaded object holds a replacement character the file did
not. A UTF-8 file with non-ASCII text loads as written.

**Dependencies:** None.

**Evidence:** `src/haute/_io.py::_load_external_object_uncached` opens with
`errors="replace"`.

### IOL-06 — XML children that collide once namespaces are stripped are refused
**Why:** Reproduced on 10 November 2026. The XML reader strips namespaces
from names, and the specification says the collisions that follow "are
rejected rather than interpreted ambiguously". By the source, two
attributes that share a name once stripped are refused ("duplicate XML
attribute name"), and so is an attribute sharing a child's name. Two
children are not: for
`<policy><a:code>1</a:code><b:code>2</b:code></policy>`, with `a` and `b`
two namespaces, Infer Tables offers one repeated table `code`, as though
one element had been written twice. Two different fields
become one list with nothing to tell them apart. A feed that mixes
vocabularies under one parent is what namespaces exist for.

**Plan:** Group an element's children by their full names. Where two
groups share a name once stripped, refuse naming the element and both full
names, as two such attributes are refused.

**Acceptance:** The document above is refused by Infer Tables and by the
run, in a message naming `code` and the two namespaces. The same children
in one namespace still read as a repeated table. A test holds the attribute
and the child case to the same refusal.

**Dependencies:** None.

**Evidence:** `src/haute/_json_shred/_records.py::_xml_element_value` groups
children by their stripped names; `src/haute/_json_shred/_records.py::_xml_local_name`;
`specs/json-shredding/high-level.md`.
