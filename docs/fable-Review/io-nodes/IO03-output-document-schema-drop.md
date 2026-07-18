# IO03 — OUTPUT silently drops any field that is null for the first 100 rows

**Severity: HIGH (silent wrongness — the worst class) · Effort: S · Review mode: pair**

Verified end-to-end against the pinned Polars 1.39.2 by the output-side reviewer, with the real
assembler in the loop. This is the single most dangerous finding in this review.

## Evidence

`src/haute/_builders.py:824` (`_build_output`):

```python
document = assemble_output_from_mapping(frames, mapping)
return pl.LazyFrame(document)
```

`_assemble_document` returns a **pruned** list of dicts — `_prune`
(`src/haute/_output_assembler.py:358-390`) deletes null-valued keys, so a row whose mapped
column is null simply *lacks that key*. `pl.LazyFrame(list_of_dicts)` then infers the schema
from the default `infer_schema_length=100` rows. Any output path whose first non-null value
occurs at row ≥ 101 is **silently dropped from the frame**. `infer_schema_length` appears
nowhere in `src/` or `tests/`.

Reproduced with a realistic mapping (`policy_id` + optional `discount`, null for the first 100
policies):

```python
document = assemble_output_from_mapping({"src": frame}, mapping)   # real assembler
# object[110] == {"policy_id": 110, "discount": "D110"}  — the data is there
df = pl.LazyFrame(document).collect()
# df.columns == ["policy_id"]        <-- 'discount' silently gone
```

Nested variant confirmed too: 120×`{"a": {"x": …}}` + one `{"a": {"x": …, "y": 999}}`
collapses `a` to `Struct({x})`, dropping `y`.

## Impact

- The deployed `/quote` response and the canvas/deploy preview omit a real priced field for
  **every** row whenever an optional field is sparse at the head of the data.
- Observability is inverted: a canvas preview at the default `row_limit=100` shows the field
  as *legitimately absent* (all previewed rows are null), so the truncation only manifests in
  production output. Exactly the silent-wrongness class CLAUDE.md forbids.

## Fix design

Construct the frame without data-dependent schema sampling — in `_build_output`
(`_builders.py:824`) so the executor, the deploy path, and the OUTPUT dry-run route
(`routes/output_assemble.py`) are all covered:

1. Minimum: `pl.LazyFrame(document, infer_schema_length=None)` — scans the whole
   already-in-memory document; verified to retain both the flat `discount` and the nested
   `Struct({x, y})`.
2. Better (matches the assembler's own A4 "schema-determined" axiom): derive an explicit
   top-level schema from the mapping — every `output_path` is known schema-side, independent
   of data. The mapping already carries everything needed to enumerate top-level keys and
   array-prefix structure.

Do 1 immediately; note 2 as the follow-up that also protects `render_output_document`
consumers from ragged inference.

## TDD plan (failing tests first)

- `tests/test_output_assembler.py` (or the OUTPUT route tests): 150-row frame, mapped column
  null for rows 0–100, set for 101–149 → assert the collected OUTPUT frame **contains** the
  column and `render_output_document` emits it for the late rows. Pin the current drop in the
  arrange phase (prove the repro), then flip with the fix.
- Nested variant: `$[:].obj.late` present only in row 120 → struct field retained.
- Dry-run route contract test: same fixture through `POST /api/output-assemble/dry-run`
  returns the late field.

## Cross-references

- Do **not** touch the assembler's `_prune` semantics — the null-prune/empty-collection rule
  is deliberate and documented (`_output_assembler.py:358-390`); the bug is purely the
  frame-construction sampling on top of it.
- `fable-Review/polars-backend/P04` owns the assembler's O(rows²) build cost; this package is
  orthogonal (correctness of the frame boundary, not cost). Fixing IO03 first is required
  before any new output format work (IO09) — a JSON/JSONL output format built on this boundary
  would inherit the same silent drop.
