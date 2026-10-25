# JSON shredding roadmap

## Scope

The API Input node's structured path: how a JSON, JSONL or XML file, or a served
request, is shredded into the tables a pipeline reads, the schema inference the
editor offers, the JSONPath grammar the tables and columns are addressed with,
and the Edge Join that reads child tables back. Current behaviour is specified in
[the JSON shredding specification](../json-shredding/high-level.md). These
packages come from the [25 October 2026 codebase review](codebase-review-2026-10-25.md).

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| JSON-R01 | Planned | P1 | A value that is not a list at a declared array key is a counted shape mismatch that a build logs and a served request refuses, and an XML element that repeats anywhere in the file is a list everywhere, so no quote's child rows vanish silently. |
| JSON-R02 | Planned | P2 | A root JSON array is tokenised in chunks, not one byte per Python call. |
| JSON-R03 | Planned | P2 | Declared types hold exactly: a `date` column parses ISO-8601 only, `coalesce` is validated, a null-only array does not widen its table to strings, and `$value` is accepted only as an element leaf. |
| JSON-R04 | Planned | P3 | The path grammar's specification matches the parser, and an input path's error names a table or column path. |
| JSON-R05 | Planned | P3 | The counted shred, the JSONL decode and the root-array loop exist once each, and the two write-only symbols are gone. |

## Planned improvements

### JSON-R01 — No child row vanishes silently
**Why:** `_walk_array` returns on any value that is not a list ("a missing key or
non-array value yields nothing"), `_emit_at` hands it whatever the container
holds, `_assert_root_conservation` checks root tables only and `shred_request`
refuses only counted skips, so an object or a scalar at a declared array key
produces no child rows, no skip count and no error, in the build walk and in the
deployed request path (reproduced: records `{"drivers": {"age": 50}}` and
`{"drivers": "x"}` under a `$[:].drivers[:]` table give two driver rows for four
quotes and empty skip statistics, and `shred_request` answers without raising).
XML makes it routine: `_xml_element_value` turns a single same-name child into a
dict and several into a list, so a quote with one `<driver>` loses its driver row
while a quote with two keeps both (reproduced), and inference of such a file
emits both a folded parent column and a child table for the same data, a schema
that fails its own build with the array-crossing error; deselecting the folded
column lands on the silent drop. The specification promises that a deployed
answer never loses a quote's rows silently and that no element disappears.

**Plan:** In `_walk_array`, treat a non-null, non-list value at an array position
as a shape mismatch: count a row skip for every table registered at that position
or under it, so builds log it and `shred_request` refuses naming the table;
extend conservation to child tables whose parent carries the array. For XML,
normalise singleton elements at the schema-aware record-to-shred boundary before
the array shape check: where the persisted schema declares an array path, wrap
a single present element as a one-element list, including scalar child elements.
Do not wrap absent, null or empty arrays, or values at unrelated paths. Carry the
source format explicitly so JSON objects/scalars at declared array paths remain
shape errors. Infer XML list-ness by structural path, never by global tag name,
and persist the decision in the schema so later singleton-only files behave the
same. The build is streaming; do not assume a separate whole-file validation pass.
Where a JSON key is genuinely mixed object and array, prefer the child table and
report the key as mixed-shape instead of emitting both.

**Acceptance:** The reproduction's build logs two skipped rows in `drivers` and
the deployed request is refused naming the table; the XML quote with one
`<driver>` yields its driver row and the inferred schema builds. A later file with
only singleton drivers still yields rows under that schema; the same tag under
an unrelated parent is unchanged. Cover object and scalar XML children and test
missing, null, empty and invalid array values separately, including the JSON
refusals. The existing conservation tests pass.

**Dependencies:** None.

**Evidence:** `src/haute/_json_shred/_shred.py::_walk_array`;
`src/haute/_json_shred/_shred.py::_assert_root_conservation`;
`src/haute/_json_shred/_shred.py::shred_request`;
`src/haute/_json_shred/_records.py::_xml_element_value`;
`src/haute/_json_shred/_inference.py::walk`; `tests/test_xml_api_input.py`.

### JSON-R02 — Root arrays tokenised in chunks
**Why:** The root JSON array tokeniser reads the file one byte per Python call
(`f.read(1)`, one `bytearray.extend` per byte), so every build and every complete
inference of a `.json` root array spends most of its time tokenising: on 167,103
records (41.9 MB) the JSONL path took 0.18 s and the root-array path 7.60 s,
forty-one times slower, against 0.66 s for `orjson.loads` of the whole file. These
are the original audit's throughput measurements; the approximately three-minute
extrapolation for 1 GB is total scan time. `_iter_records` yields each decoded
record immediately, so it is not a delay before the first row can be walked.
The specification asks for serial scanning, not byte-at-a-time scanning,
and the sampling twin duplicates the closures.

**Plan:** Scan 64 KiB chunks and find the structural bytes with a compiled
pattern, carrying string, escape and depth state across chunks and slicing each
element out of the chunk buffer; keep the record-byte bound and the trailing-data
check; share the scanner between `_iter_records` and
`_iter_sampled_json_array_records`.

**Acceptance:** The 41.9 MB probe file tokenises within three times the JSONL
time; the element boundaries, the record-byte bound and the trailing-data refusal
are unchanged under the existing tests. Tokens, strings, multibyte UTF-8 and escapes
split across 64 KiB chunk boundaries decode identically; the first record is yielded
without reading the whole file. Repeat the benchmark with the recorded fixture and
environment when implementing; the validation review did not rerun that speed ratio.

**Dependencies:** None.

**Evidence:** `src/haute/_json_shred/_records.py::_read_root_array_value`;
`src/haute/_json_shred/_records.py::_iter_records`;
`src/haute/_json_shred/_records.py::_iter_sampled_json_array_records`.

### JSON-R03 — Declared types hold exactly
**Why:** A `date` column is built with `pl.Series(values, dtype=pl.Date,
strict=True)`, which lets Polars guess the format, so `15/01/2024` becomes 15
January and `01/02/2024` becomes 1 February with no warning (probed), while the
guard's comment and error text promise ISO-8601 only. `coalesce` is the one Edge
Join option `build_edge_join_kwargs` passes through unvalidated, so a non-bool
value in a sidecar or decorator fails inside Polars with a bare `TypeError`
instead of the `ConfigError` the specification promises before any join. A
null-only scalar array (`[null]`) widens an already-typed scalar child table to
`str`, where an empty array and a bare null deliberately leave the type alone, so
one record turns an `int` table into strings (probed). The reserved `$value` leaf
is accepted after an object hop (`$[:].a.$value`), where only an exact `$value`
leaf is the sentinel, so such a column silently reads a literal `"$value"` key.

**Plan:** Parse date columns explicitly (`str.to_date("%Y-%m-%d", strict=True)`,
wrapping the failure in the column-named error with the offending value); refuse
a non-bool `coalesce` with `ConfigError`; treat a null-only array like the empty
array; accept `$value` only when the path core is the root or ends with `[:]`.

**Acceptance:** `15/01/2024` in a `date` column is refused naming the column and
the value; `coalesce: "yes"` fails at parse with `ConfigError`; `[1, 2]` then
`[null]` keeps `int`; `$[:].a.$value` is refused.

**Dependencies:** None.

**Evidence:** `src/haute/_json_shred/_shred.py::_rows_to_frame`;
`src/haute/_edge_join.py::build_edge_join_kwargs`;
`src/haute/_json_shred/_inference.py::_widen_type`;
`src/haute/_jsonpath.py::parse_data_path`.

### JSON-R04 — The path grammar as specified and worded
**Why:** The specification says bracket-quoted names (`['a.b']`, `["2024"]`) are
accepted for interoperability, but the parser (both sides) accepts only
`.identifier` and `[:]`, and inference refuses any non-identifier key outright,
so a source with a hyphenated or spaced key cannot be inferred or addressed at
all. Every grammar rejection is worded for the OUTPUT side ("output path must
start with '$[:]'", "malformed output path") and reaches the Quote Input editor
as the 422 detail for a table or column path.

**Plan:** Rewrite the specification's acceptance paragraphs to the real set and
say in the Quote Input docs which key spellings a source may use (or implement
bracket names end to end); give the parser a side-specific noun so input errors
read "table path" or "column path".

**Acceptance:** The specification, the parser and the TypeScript mirror agree;
an invalid input path's error names an input path.

**Dependencies:** None.

**Evidence:** `src/haute/_jsonpath.py::parse_path`;
`frontend/src/panels/editors/jsonpath.ts`;
`src/haute/_json_shred/_inference.py`; `specs/json-shredding/high-level.md`.

### JSON-R05 — One counted shred, one decode, one loop
**Why:** The counted-record shred (a counting generator wrapping the record
iterator, `shred_to_buffers` into the writer, root conservation, flush and close)
is written three times, differing only in the iterator and the conservation
location; the JSONL line decode (strip, skip blank, `orjson.loads`, dict or
count) three times; the root-array element loop twice.
`_BoundedParquetRowGroupWriter.schema_frames` is assigned and never read, and
`ApiInputV2Config` is referenced nowhere outside its definition and the
specification.

**Plan:** One `_shred_records_into(writer, records, ...)` for the three callers,
one `_decode_jsonl_line`, one root-array value generator the sampler slices;
delete the two write-only symbols and drop `ApiInputV2Config` from the
specification's key types.

**Acceptance:** jscpd reports none of the pairs; the symbols are gone; the shred
tests pass unchanged.

**Dependencies:** None.

**Evidence:** `src/haute/_json_shred/_writer.py::_shred_chunk`;
`src/haute/_json_shred/_writer.py::_write_tables_streaming`;
`src/haute/_json_shred/_writer.py::_shred_data_file_to_direct_spill`;
`src/haute/_json_shred/_records.py`;
`src/haute/_api_input_schema.py::ApiInputV2Config`.
