# Server API roadmap

## Scope

The local server's routes and the deployed app's answers, as [the server API
specification](../server-api/high-level.md) describes them: what they show of a
temporal value finer than a microsecond.
`SRV-19` and `SRV-20` come from the 22 November 2026
sweep of `src/haute` for the cause `XPL-03` removed from Explore, a temporal
value read into Python; the [temporal values root cause](root-cause-temporal-values.md)
is taken before them. `SRV-20`
also holds the deployed app's JSON answers and requests, whose own roadmap
is retired.

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| SRV-19 | Planned | P1 | An OUTPUT document keeps two objects whose keys differ below a microsecond, in the preview and in the deployed answer, and carries every digit of a nanosecond timestamp. |
| SRV-20 | Planned | P3 | The node preview, the file preview and the deployed app's answers show a nanosecond value's nine digits, and a request's date-time with more than six digits is read whole or refused, never cut short. |

## Planned improvements

### SRV-19 — An OUTPUT document keeps objects that differ below a microsecond
**Why:** Reproduced on 22 November 2026 with `assemble_output_from_mapping`.
A frame holds policy `P1` with two events of amount 5, one at
09:00:00.000000100 and one at 09:00:00.000000200, in a `Datetime("ns")`
column, the unit pandas writes to Parquet. The mapping `$[:].policy`,
`$[:].events[:].at`, `$[:].events[:].amount` gives one policy with one
event, at 09:00:00, with nothing said. The assembler reads the rows with
`iter_rows` into Python datetimes, which hold microseconds, and groups an
array's objects by their own values (`_group_rows`, `_index_rows`). The
two events become one object, in the OUTPUT node's preview and in the
deployed answer alike. A preview's row limit filters the child rows by
`is_in` of the parent keys read through Python (`_limit_level_plan`), which
misses a key with digits below a microsecond. Every timestamp the document
carries is cut to six digits.

**Plan:** Key an object's identity and the preview's parent filter by the
column's own value, through `IOL-08`'s shared mechanism, not a Python
`datetime`; filter child rows by a semi-join on the parent keys, as the
many-key case already does. Write a temporal leaf with all its digits.
Update the JSON shredding specification first.

**Acceptance:** The reproduction above gives one policy with two events
whose `at` carries nine digits. A preview limited to one parent keeps both
children of a parent whose key has nanoseconds.

**Dependencies:** `IOL-08` (the [temporal values root cause](root-cause-temporal-values.md)) first.

**Evidence:** `src/haute/_output_assembler.py::_rows_from_dataframe`;
`src/haute/_output_assembler.py::_group_rows`;
`src/haute/_output_assembler.py::_index_rows`;
`src/haute/_output_assembler.py::_limit_level_plan`;
`specs/json-shredding/low-level.md`.

### SRV-20 — Previews and JSON answers carry a nanosecond value's digits
**Why:** Read on 22 November 2026, and the request reproduced. The node
preview grid reads its rows with `to_dicts` (`executor.py::_frame_rows`),
the file preview with `rows_to_json_safe(preview_df.to_dicts())`, and the
deployed app's answers and its impact and batch-scoring rows through Python
values, and each writes `isoformat()`: a pandas-written timestamp of
09:00:00.000000100 shows as 09:00:00. A request's date-time is read by
`_json_values._datetime` with `datetime.fromisoformat`, which on Python
3.11 drops digits past the sixth without refusing:
`2024-03-01T09:00:00.000000100` reads as 09:00:00.

**Plan:** Write each preview's and answer's temporal columns through
`IOL-08`'s `temporal_text` before the rows are read, and read a request's
date-time through the shared literal builder, refusing a value finer than
its column holds. Update the server API and deploy specifications first.

**Acceptance:** A node preview, a file preview and a deployed answer of a
`Datetime("ns")`, a `Time` and a `Duration("ns")` value show nine digits;
a request carrying nine digits reaches the pipeline whole, and one finer
than its column is refused naming the field.

**Dependencies:** `IOL-08` (the [temporal values root cause](root-cause-temporal-values.md)) first.

**Evidence:** `src/haute/executor.py::_frame_rows`;
`src/haute/_json_safe.py`; `src/haute/routes/files.py`;
`src/haute/deploy/_response_json.py`; `src/haute/deploy/_batch_scoring.py`;
`src/haute/deploy/_impact.py`; `src/haute/deploy/_schema.py`;
`src/haute/_json_values.py::_datetime`;
`specs/deploy/low-level.md`.
