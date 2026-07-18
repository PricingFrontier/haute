# IO04 — apiInput: inference approves what the build rejects, and one intruder shape slips through silently

**Severity: HIGH (workflow dead-end) + MEDIUM (silent record loss) · Effort: M · Review mode: pair**

Findings from the JSON-input reviewer, all reproduced against the pinned Polars 1.39.2 with the
real `infer_v2_schema_from_data` → `build_per_port_cache` code in the loop.

---

## IO04-a — Mixed `int`/`str` columns: inference widens to `str`, the build refuses to coerce (HIGH, verified E2E)

`_widen_type` (`src/haute/_json_shred.py:1281`) documents its purpose: *"so the strict parquet
build doesn't fail on a value that appears past an early sample."* That promise holds for
scalar-array `$value` columns (`_emit_row` runs `_coerce_scalar`, `:733`) — but **object
columns get no coercion**: `_buffer_to_frame` (`:814`) strict-builds
`pl.Series(values, dtype=pl.String, strict=True)` (`:862`), and Polars rejects a raw int/bool
in a String column.

Reproduced:

```
[{"code": 100}, {"code": "A1"}]  → infer types 'code' = str
  → build RAISES ApiInputSchemaError: "column 'code' has values that don't match its
    declared type 'str'; re-infer the schema or change the column's type to match the data"
[{"flag": true}, {"flag": 7}]    → same dead end
[{"amt": 5},   {"amt": 2.5}]     → widened 'float' → builds fine (int→float coerces)
```

The advice in the error **loops**: re-inferring yields `str` again; no type the user can pick
makes the build pass. A numeric-or-string code field (`100` vs `"A1"`) is bread-and-butter
insurance JSON, so the documented workflow (Infer Tables → Cache as Parquet) is unusable on
common data. Fail-loud, but a dead end — the two halves of one feature contradict each other.

**Fix.** In `_buffer_to_frame`, for `str`-declared columns stringify **genuine scalars only**
(int/float/bool — same rule the scalar path's `_scalar_to_str` already applies); a dict/list
leaf must still fail loud (stringifying a container into a str column would be new silent
wrongness). Keep the int→float behaviour and the existing bool-in-numeric / int-in-date guards
untouched (those guards are CLEARED — see CLEARED.md). State the "declared str stringifies
scalars" rule in the docstring.

**TDD.** Failing tests: build on `[{"code":100},{"code":"A1"}]` with the inferred schema
succeeds and the parquet column is `["100","A1"]`; bool+int→str variant; dict-in-str-column
still raises. Property test: for any records typed by `infer_v2_schema_from_data`,
`build_per_port_cache` never raises "values don't match declared type" (the infer⇒buildable
round-trip is the contract this package exists to establish).

## IO04-b — A `list` element in a scalar-array table becomes a fabricated null row, uncounted (MEDIUM, verified)

`_emit_at`'s shape guard (`_json_shred.py:751-756`) tests `is_scalar_table != (not is_dict)`.
A `list` element (`is_dict=False`) passes the scalar-table guard; `_resolve_leaf(list, "$value")`
returns `None` (`:574-575`) — so the nested array's contents vanish and a fabricated
`{"value": None}` row is emitted, indistinguishable from a real JSON `null`:

```
scalar table $[:].tags[:] on {"tags": ["a", ["nested","list"], "b"]}
  → [{'value':'a'}, {'value':None}, {'value':'b'}]   # list → silent null, NOT counted
{"tags": ["a", {"k":"v"}, "b"]}
  → [{'value':'a'}, {'value':'b'}]                    # dict → correctly skipped + counted
```

This breaks the W2 "zero silent record loss" invariant the shred is built around — the dict
intruder is accounted in `ShredSkipStats`, the list intruder is not, and the fabricated null
can feed a rating step as a genuine missing value.

**Coverage gap (why tests are green):**
`test_json_shred_properties.py::test_shred_scalar_child_row_count_equals_total_elements` (`:83`)
draws only `text|none` elements. Note for the fixer: adding lists to the *row-count* assertion
would NOT catch this (list→null is still one row) — the test must assert on the **skip count**.

**Fix.** Treat an element as scalar-shaped iff `not isinstance(record, (dict, list))` —
symmetric with `_resolve_leaf` — and count list intruders as skips, in both `_emit_at` and
`_walk_array`'s per-element path.

**TDD.** Failing test: scalar table on `["a", ["x"], "b"]` yields two rows and
`stats.skipped_rows_by_table["tags"] == 1`. Extend the property strategy to include lists and
dicts; assert `emitted + skipped == total` and `emitted == count(scalar elements)`.

## IO04-c — Non-identifier / non-ASCII keys pass inference, then fail the build with a machine-path error (MEDIUM, verified)

`_reject_unexpressible_key` (`_json_shred.py:1331`) fails loud at infer time only for `$value`
and `.`-containing keys (clear message: *"rename this field in the source data"*). Everything
else defers to `validate_v2_schema`, whose error names the **synthesized path**, not the source
key:

```
key "foo-bar"   → infer OK → build: "malformed output path (output_path=$[:].foo-bar)"
key "123abc"    → infer OK → build: "unsupported output-path selector …"
key "café" / "名前" (grammar is ASCII-only, _jsonpath.py:61) → same late, mis-attributed failure
```

The user sees the offending column populate in the editor, then gets a grammar error about
text they never wrote, and re-inferring reproduces it.

**Fix.** Extend `_reject_unexpressible_key` to reject at infer time any key that doesn't match
the grammar's identifier charset, naming the raw source key with the rename advice — the same
treatment `$value`/dot already get. (Stretch, design-note only: the grammar accepts `['name']`
bracket segments, so safe non-identifier keys could someday be *emitted* as bracket paths —
but see IO11 on the bracket/dot mis-resolution before going there.)

**TDD.** Failing tests: infer on `[{"foo-bar":1}]`, `[{"has space":1}]`, `[{"café":1}]` raises
`ApiInputSchemaError` naming the raw key. Property: every path inference emits parses under
`parse_column_path` (infer⇒validate round-trip).

---

## Order within the package

IO04-a first (unblocks the primary workflow and removes the infer/build contradiction), then
IO04-b (silent-loss class), then IO04-c. All three land in `_json_shred.py` + its property
suites; one dev/reviewer pair can carry the package end-to-end.
