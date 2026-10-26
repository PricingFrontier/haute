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
| JSON-R02 | Planned | P2 | A root JSON array is tokenised in chunks, not one byte per Python call. |
| JSON-R03 | Planned | P2 | Declared types hold exactly: a `date` column parses ISO-8601 only, `coalesce` is validated, a null-only array does not widen its table to strings, and `$value` is accepted only as an element leaf. |
| JSON-R04 | Planned | P3 | The path grammar's specification matches the parser, and an input path's error names a table or column path. |
| JSON-R05 | Planned | P3 | The counted shred, the JSONL decode and the root-array loop exist once each, and the two write-only symbols are gone. |

## Planned improvements

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
