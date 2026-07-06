# P04 — OUTPUT assembler: quadratic nesting, null-key mis-nesting, silent column drop

**Severity:** HIGH (measured O(rows²)) + 2× MEDIUM silent-wrongness · **Effort:** M · **Dev/reviewer pair: REQUIRED**

File: `src/haute/_output_assembler.py`

---

## FR-12 [HIGH] — `build()` re-filters every level's rows once per parent group → O(rows²)
**`_output_assembler.py:442-479`**

### Evidence
Line ~464:
```python
rows = [r for r in level_rows if all(r.get(k) == v for k, v in scope.items())]
```
scans the child level's **entire** `level_rows`, and the recursion at :472-476 calls
`build(child, child_scope)` once for **every** parent object group. N parents × M children each →
child level (N·M rows) scanned N times → O(N²·M).

Empirically confirmed by the reviewing agent: n=100/200/400/800 rows → 0.014/0.050/0.188/0.737 s
(~3.7× per doubling — quadratic). n=800 is only 2,400 rows; ~8k parents extrapolates to ~75 s.

The module's own docstring (:405-409) promises "cost is the sum, not the product". The implementation
does not deliver it. This bites any OUTPUT assembly over a non-trivial frame (batch output documents,
deploy responses with many records, the OUTPUT-node canvas preview via `render_output_document`).

### Fix design
Hoist the grouping: for each node prefix, group that level's rows **once** by the key tuple children
match on (the parent `own` columns each child `carries`) into `dict[tuple[values], list[row]]`. Then
each parent group's `build(child, …)` is an O(1) dict lookup instead of a filter. Preserve row order
within groups via the existing `_group_rows` ordering (dict preserves insertion order — group in a
single ordered pass). Total per-level cost becomes O(rows).

Key detail: the grouping key must be exactly the columns the current `scope` would have matched —
derive it per (parent-prefix → child-prefix) pair before recursing, not per parent group.

### TDD plan
1. **Failing scaling test (structural, CI-safe):** count scope-match evaluations, not seconds. Wrap the
   row-match predicate in a counter (monkeypatch or a module-level counter injected for tests); build
   documents at n and 2n; assert `count(2n) / count(n) <= ~2.5` (linear + slack). Today the ratio is ~4.
2. Golden output test: assert the assembled document for a 3-level fixture is byte-identical before and
   after (order included).
3. Edge fixtures: empty child level; parent with zero children; multiple children per parent.

---

## FR-13 [MEDIUM, silent wrongness] — Python `None == None` matches where the Polars join doesn't
**`_output_assembler.py:464`**

### Evidence
The nester matches child rows with `r.get(k) == v`. When an ancestor key value is `None`,
`None == None` is `True` for every null-keyed child. But the module's own join contract
(`_execute_plan`, and the `_prune`/H3 comment at :358-371) relies on Polars semantics where a
`how="full"` join on a null key does **not** match — verified: 2+2 rows with null keys → 3 rows,
nulls stay separate. So the Python nester and the Polars join disagree: all null-keyed children attach
to **all** null-keyed parents (a cartesian on nulls) — silent mis-nesting.

### Fix design
Make the nester consistent with the join: a `None` scope value matches nothing — skip attaching
children under a null key (they become orphans of that parent). Decide the orphan policy explicitly
and loudly: either (a) null-keyed child rows are dropped **with a raised error naming the key** (fail
loud), or (b) they are excluded from nesting with a documented, tested contract. Option (a) fits the
codebase mandate unless there's a legitimate ragged-data case in the fixtures — check
`validate_v2_output_mapping` tests before choosing.

**Failing test first:** parent frame and child frame each with a null in the nesting key; assert the
current cartesian mis-nesting is gone (document shape matches the Polars-join expectation, or a loud
error per (a)).

---

## FR-14 [MEDIUM, silent wrongness] — one-emit-prefix-per-frame assumption silently drops columns
**`_output_assembler.py:419-437`**

### Evidence
`emit_prefix[port] = max((_array_prefix(p) for p in pp.values()), key=len, default=())` keeps a single
longest array prefix per port; `nodes` (:425-428) is built only from these prefixes plus ancestors. A
frame mapping columns into **sibling arrays** — `$[:].drivers[:].name` AND `$[:].vehicles[:].make` —
has two divergent length-2 prefixes; `max` keeps one, the other's prefix never becomes a node, and at
:466 `own = [c for c, p in all_paths.items() if _array_prefix(p) == prefix]` never selects the losing
columns. `validate_v2_output_mapping` (:586-630) checks prefix-incomparability + injectivity only, so
this mapping **passes validation** and then silently loses data.

### Fix design
Fail loud at validation: after computing `emit_prefix`, assert every path's `_array_prefix` is a
prefix (component-wise) of its port's `emit_prefix`; otherwise raise `OutputMappingSchemaError` naming
the offending paths and the port. (Genuinely supporting multiple emit prefixes per port is a feature,
not a fix — out of scope.) Put the check in `validate_v2_output_mapping` so the editor's dry-run route
(422 path in `routes/output_assemble.py`) surfaces it before any data is assembled.

**Failing test first:** mapping with sibling-array paths from one port → expect
`OutputMappingSchemaError` (today: silently missing column in the document — assert current behaviour
in the test's arrange phase to prove the repro, then flip the assertion).

---

## Acceptance for the package
- Scope-match evaluations scale linearly with rows (counter test).
- Null-keyed nesting matches the Polars join contract or fails loud (chosen policy documented in the
  module docstring next to H3).
- Sibling-array mappings are rejected at validation with a message naming the paths.
- Existing output-assembler and output_assemble route tests green; golden documents unchanged for
  valid mappings.
