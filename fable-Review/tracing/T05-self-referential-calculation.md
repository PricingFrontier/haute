# T05 — Self-referential factor steps display arithmetically wrong substitutions

**Severity:** HIGH (silent wrongness in the headline story) · **Effort:** S
**Dev/reviewer pair: REQUIRED** (silent-wrongness class)
**Files:** `src/haute/_trace_enrichment.py` (one function); no frontend change required
**Origin:** UX-01 (informativeness review), reproduced end-to-end; independently re-run by the lead session.
**Repros:** `repros/gen_trace.py` + `repros/analyze.py`

## The defect

For the single most common rating pattern — a column multiplied in place along a chain
(`premium = premium * area_factor`, then `* age_factor`) — the per-step `calculation` payload
substitutes the step's **output** value for the self-referenced column, because the eval map merges
`output_values` over `input_values`:

`_trace_enrichment.py:1578-1584`:
```python
evaluate_expression(code, column, {**step.input_values, **step.output_values}, ...)
```

Reproduced payload (base 100 → ×1.2 → ×0.9, header/waterfall correctly show 100 → 120 → 108):

| Step | real out | `substituted_text` | `result_value` shown |
|---|---|---|---|
| Area Loading | 120.0 | `'120.0 * 1.2'` | **144.0** |
| Age Discount | 108.0 | `'108.0 * 0.9'` | **97.2** |

The CalculationHero renders `= premium 144.00` under a panel header that says `premium = 120`; with
3+ factors the waterfall *bars* are right but the bold total takes the corrupted `result_value`
(97.20 vs header 108). The InputSourceTree shows `premium = 100 (Base Rate)` directly above a
formula substituting 120 — self-contradiction inside one box. This is the exact "value in,
operation, value out" narrative the feature exists to tell, and it is wrong for every sequential
ratebook.

The correct handling **already exists 400 lines up**: the input-sources path detects
`self_referential_modification` and uses `input_values[ref_col]`
(`_trace_enrichment.py:1170-1184`). It was never applied to the primary calculation.

## Fix design

In `enrich_steps`, before the primary `evaluate_expression` call, build the eval map with
input-priority for self-referenced columns:

```python
eval_values = {**step.input_values, **step.output_values}
refs = (step.expression or {}).get("referenced_columns", [])
modified = set(step.schema_diff.columns_modified)
for ref in refs:
    if ref in modified and ref in step.input_values:
        eval_values[ref] = step.input_values[ref]   # pre-transform value
```

(Equivalently: factor the `self_referential_modification` logic at `:1170-1184` into a helper and
call it from both sites — preferred, keeps one definition of the rule.) `substituted_text` /
`result_value` for the Area step become `'100.0 * 1.2'` / `120.0`, agreeing with the observed
output, the waterfall, and the header. Columns that are referenced but *not* self-modified keep the
merged-map behaviour (they may legitimately come from the same node's other assignments — preserve
the existing multi-assignment semantics; the chain evaluation via `parse_expression_chain` already
orders intra-node assignments).

Guard: when the self-referenced input value is missing (upstream correlation failed), do not
substitute a null — leave the calculation unpopulated rather than wrong (existing enrichment
error-annotation convention).

## Failing tests first

1. Three-node chain `base=100` → `premium=premium*1.2` → `premium=premium*0.9`, trace `premium` at
   the sink: for the middle step assert `calculation["substituted_text"] == "100.0 * 1.2"` and
   `result_value == 120.0` (currently `"120.0 * 1.2"` / 144.0); for the last step `"120.0 * 0.9"` /
   108.0. Assert `result_value == output_values["premium"]` for every step — that equality is the
   invariant this fix restores; add it as a property-style assertion across the golden suite
   (`tests/test_trace_golden.py`).
2. Non-self-referential step (`tax = premium * 0.1`) unchanged: substitution still uses the current
   (post-node) premium — pin to prevent over-correction.
3. Multi-assignment node (`premium = premium * f` then `premium = premium + levy` in one node):
   chain substitution remains internally consistent (input value feeds the first assignment only).

## Acceptance

- `repros/analyze.py` shows `substituted == input × factor == result_value == observed output` for
  every step of the chain; hero, waterfall total, and header agree.
- Golden trace suite green with the new invariant assertion enabled.
