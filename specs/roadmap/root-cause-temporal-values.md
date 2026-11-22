# Root cause: a temporal value read into Python

## Scope

Haute's own code that collects a datetime, time or duration column into
Python objects and then keys, matches, rebuilds or shows those objects.
Python's `datetime`, `time` and `timedelta` hold microseconds. A Polars
`Datetime("ns")`, the unit pandas writes to Parquet, a `Duration("ns")` and
every `Time` hold nanoseconds. So two values that differ below a
microsecond become one value once they are read into Python, and a value's
last three digits are lost. Polars' own `cast(pl.String)` is no way round
it: it drops a `Time`'s whole fraction and refuses a `Duration`.

`XPL-03` removed the cause from Explore on 22 November 2026, in its pivot
and its profile. It added the mechanism this file names,
`src/haute/_temporal_text.py::temporal_text`, which writes the exact text
inside the query. The pivot's `_temporal_literal` reads a member's text back
as a literal of its column's own type. The sites below still read through
Python.

This file is one pull request. It holds one package, `IOL-08`, which names
a cause that packages in other files share, and it is taken before them.
The pull request makes the shared mechanism the only path and adds the
test that keeps it so. It then checks each package listed below against
that package's own acceptance, removes the ones it has met, and removes
this file.

## Packages this resolves

"Resolves" means the package's acceptance should be met by this work
alone. "In part" means this work removes the shared cause and the package
keeps something of its own to do.

| Package | Roadmap | What it records | Expected |
|---|---|---|---|
| `SRV-19` | [server-api](server-api.md) | An OUTPUT document keeps two objects whose keys differ below a microsecond | Resolves |
| `SRV-20` | [server-api](server-api.md) | Previews and JSON answers carry a nanosecond value's digits, and a request's are not dropped | Resolves |
| `TRC-15` | [tracing](tracing.md) | A traced row whose timestamp has nanoseconds is found by its own values | Resolves |
| `OPM-06` | [optimiser](optimiser.md) | Ratebook factor levels that differ below a microsecond are counted apart | Resolves |
| `MDL-04` | [modelling](modelling.md) | Evaluation planning does not walk the key column in Python | In part: the plan's speed and memory are its own |

## Planned improvements

### IOL-08 — A temporal value reaches Python only as its exact text
**Why:** Read on 22 November 2026 by a sweep of `src/haute` for the cause
`XPL-03` removed from Explore, and reproduced where its package says so.
The output assembler groups an array's objects by values read with
`iter_rows`, so two events 100 ns apart become one object in a preview and
in a deployed answer (`SRV-19`, reproduced). The preview grid, the file
preview and the deployed scorer write `isoformat()` of a Python value, and
a request's date-time is read with `datetime.fromisoformat`, which on
Python 3.11 drops digits past the sixth without refusing (`SRV-20`, the
request reproduced). A trace matches its row again by values that went
through JSON at microseconds (`TRC-15`). A ratebook factor's levels and an
evaluation plan's group and date keys are made from Python values
(`OPM-06`, `MDL-04`). The assistant's value profiles write a temporal
bound through `json_safe_scalar`, `str()` of a Python value, so a provider
is shown six digits; that site has no package of its own and is part of
this one. Each is planned as its own fix at its own site. The next site
that collects a temporal column will lose the digits again.

**Plan:** Make `haute._temporal_text` the one way Haute's own code turns a
temporal value into Python and back. `temporal_text` writes a value out,
inside the query and before the collect. The pivot's `_temporal_literal`
moves beside it as the one way a value's text becomes a literal of its
column's own type. Each site in the table writes its temporal columns
through `temporal_text` before it reads them, and builds a filter or a
match from the shared literal builder rather than from a Python
`datetime`. A request value finer than its column holds is refused by
name. Update the IO layer specification first.

**Acceptance:** A test runs each site in the table over a frame whose
`Datetime("ns")`, `Time` and `Duration("ns")` columns hold values that
differ below a microsecond. It requires each site to keep those values
apart and to give back all nine digits, and the assistant's value
profiles to show them. The pivot reads its member literals through the
shared builder. The acceptance of each package marked
Resolves is met.

**Dependencies:** None. Deliver before the packages its table names.

**Evidence:** `src/haute/_temporal_text.py`;
`src/haute/routes/_pivot_service.py::_temporal_literal`;
`src/haute/_output_assembler.py::_rows_from_dataframe`;
`src/haute/_json_values.py::_datetime`;
`src/haute/_trace_correlation.py::_typed_value_match_expr`;
`src/haute/routes/_optimiser_solver.py`;
`src/haute/modelling/_evaluation.py::_json_key`;
`src/haute/_column_summary.py::json_safe_scalar`; `src/haute/assistant/_tools.py`;
`specs/explore-eda/low-level.md`.
