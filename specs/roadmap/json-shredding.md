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
| JSON-R06 | Planned | P1 | OUTPUT preserves object hops before arrays, distinguishes arrays at different full paths, and refuses incompatible leaf/container mappings before collecting. |
| JSON-R07 | Planned | P1 | Structured requests refuse malformed elements in non-emitting ancestor arrays as well as directly emitted tables. |

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

### JSON-R06 — OUTPUT preserves the complete destination path
**Why:** The [28 October review](codebase-review-2026-10-28.md) reproduced
`$[:].cover.vehicles[:].id` assembling as `vehicles` at the root, losing `cover`.
`_array_prefix` retains only array segment names and both assembly and schema
construction attach each child using only its final name. Consequently
`$[:].left.items[:].value` and `$[:].right.items[:].value` are falsely treated as
the same array level. A second structural gap allows one frame's scalar
`$[:].items` and another frame's `$[:].items[:].value`: assembly silently replaces
the scalar with the array. Schema-only execution reproduces the same wrong shape,
so comparing schema-only and collected schemas does not detect the defect.

**Plan:** Identify an array level by its complete parsed path through its final
array selector, preserving intervening object segments without creating extra
relational tables. Use one structural path model for validation, nesting,
row-limited assembly and schema derivation. Validate leaf/object/array conflicts
over all active mappings, including across ports, before reading source data.
Keep shared ancestor relation keys and one frame per actual array level.
Update the JSON-shredding high- and low-level contracts before implementation.

**Acceptance:** A mapping through `cover.vehicles[:]` emits exactly
`{"cover":{"vehicles":[{"id":"v1"}]}}` inside its root record, with the
matching nested schema. Arrays named `items` under `left` and `right` remain
separate. Cover object hops between two array levels and row-limited assembly.
Scalar-versus-array and scalar-versus-object conflicts across ports fail with
both paths named before collection; repeated shared ancestor keys remain valid.
Assert literal expected JSON, not just agreement between two implementations.

**Dependencies:** None. `JSON-R04` owns grammar documentation; these paths are
already accepted and do not require adding bracket-name syntax.

**Evidence:** `src/haute/_output_assembler.py::_array_prefix`;
`src/haute/_output_assembler.py::validate_v2_output_mapping`;
`src/haute/_output_assembler.py::_assemble_document`;
`src/haute/_output_assembler.py::output_document_schema`;
`tests/test_output_assembler.py`; `tests/test_output_schema_only.py`.

### JSON-R07 — Malformed ancestors cannot hide skipped request data

**Why:** When only `drivers[:].claims[:]` emits, `_walk` silently ignores a scalar, null or
nested list in `drivers`. The real request validator and deployed scorer accept all three
shapes and return the valid sibling's total of 100. The skipped ancestor never reaches the
descendant table's malformed-row accounting, so a successful price can omit supplied data.

**Plan:** Validate each traversed array element against its declared object/array position,
including intermediate tables that do not emit. Carry an attributed shape failure through the
normal strict-request boundary. Preserve the documented treatment of genuinely absent and empty
arrays; do not confuse them with a malformed present element.

**Acceptance:** Real `QuoteRequest` and `score_graph` cases with valid siblings plus scalar,
null and nested-list ancestors refuse with the offending path. Equivalent all-valid nested
requests price correctly; omitted/empty optional arrays retain their specified behavior. Cover
direct emitting and non-emitting ancestors with the same shared walker.

**Dependencies:** None.

**Evidence:** `src/haute/_json_shred/_shred.py::shred_to_buffers._emit_at`; `src/haute/deploy/_scorer.py`; the 28 October report's deployed ancestor-shape probe.
